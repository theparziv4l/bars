"""
Host Agent for Bars.
Executes commands on the host system and streams output back to the caller.
"""
import asyncio
import os
import secrets
import signal
import uuid
import logging
from typing import Dict, Optional
from pydantic import BaseModel
from fastapi import Depends, FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import subprocess

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HostAgent")

# Shared secret with the backend. Every endpoint here runs arbitrary shell commands as this
# user, so the agent refuses to serve without one — see require_token() and __main__.
AGENT_TOKEN = os.getenv("HOST_AGENT_TOKEN", "")


def require_token(x_bars_token: str = Header(default="")) -> None:
    """Reject any request that doesn't present the shared agent token."""
    if not AGENT_TOKEN or not secrets.compare_digest(x_bars_token, AGENT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Bars-Token")


app = FastAPI(title="Bars Host Agent")

# State
active_tasks: Dict[str, asyncio.subprocess.Process] = {}

class ExecuteRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout: int = 3600
    execution_id: Optional[str] = None
    stdin: Optional[str] = None

# asyncio's default StreamReader limit is 64 KiB per line; tools that print one huge line
# (minified JSON, base64) would raise ValueError in readline().
STREAM_LIMIT = 16 * 1024 * 1024
KILL_GRACE = 5

# Strong refs so fire-and-forget escalation tasks aren't garbage-collected mid-sleep.
_bg_tasks: set = set()


def _signal_group(process, sig) -> None:
    """Signal the command's whole process group. preexec_fn=os.setsid makes pgid == pid, so we
    use process.pid directly: os.getpgid() raises ProcessLookupError once the shell has been
    reaped, even while background children (`cmd &`) still hold the group (and our pipes)."""
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass  # group already gone
    except Exception as e:
        logger.error(f"Failed to signal process group {process.pid}: {e}")
        try:
            process.terminate() if sig == signal.SIGTERM else process.kill()
        except Exception:
            pass


def _escalate_kill(process) -> None:
    """SIGKILL the group after the grace period if anything ignored SIGTERM, so a
    SIGTERM-trapping child can't keep the stream (and a concurrency slot) alive."""
    async def _later():
        await asyncio.sleep(KILL_GRACE)
        _signal_group(process, signal.SIGKILL)
    task = asyncio.create_task(_later())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@app.post("/cancel/{execution_id}", dependencies=[Depends(require_token)])
async def cancel_command(execution_id: str):
    """Cancel a running command."""
    if execution_id in active_tasks:
        process = active_tasks[execution_id]
        # Terminate the process group to ensure shell and children are killed
        _signal_group(process, signal.SIGTERM)
        _escalate_kill(process)
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Execution not found")

@app.post("/run_and_stream", dependencies=[Depends(require_token)])
async def run_and_stream(request: ExecuteRequest):
    """
    Combined endpoint to start and stream.
    Simplifies the connection logic for the backend.
    """
    async def event_generator():
        try:
            process = await asyncio.create_subprocess_shell(
                request.command,
                stdin=asyncio.subprocess.PIPE if request.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.cwd or os.getcwd(),
                preexec_fn=os.setsid if os.name != "nt" else None,
                limit=STREAM_LIMIT,
            )
        except OSError as e:
            # Bad cwd / unexecutable shell: headers are already sent, so report in-band
            # instead of crashing the response mid-stream.
            msg = f"[Spawn failed] {e}".replace("\n", " ")
            yield f"data: stderr:{msg}\n\n"
            yield "event: exit\ndata: -1\n\n"
            return

        execution_id = request.execution_id or str(uuid.uuid4())
        active_tasks[execution_id] = process

        waiter = None
        stdin_task = None
        try:
            queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

            async def pipe_to_queue(stream, prefix: str):
                try:
                    while True:
                        try:
                            line = await stream.readline()
                        except ValueError:
                            # Line over STREAM_LIMIT: asyncio dropped it; keep reading the rest.
                            await queue.put(f"data: stderr:[line exceeded {STREAM_LIMIT} bytes, dropped]\n\n")
                            continue
                        if not line:
                            break
                        await queue.put(
                            f"data: {prefix}:{line.decode('utf-8', errors='replace').rstrip('\n')}\n\n"
                        )
                except Exception as e:
                    logger.error(f"{prefix} reader failed for {execution_id}: {e}")

            t1 = asyncio.create_task(pipe_to_queue(process.stdout, "stdout"))
            t2 = asyncio.create_task(pipe_to_queue(process.stderr, "stderr"))

            if request.stdin is not None:
                # Separate task: a large write blocks on pipe capacity until the command
                # reads, while we must keep draining its stdout (else deadlock).
                async def feed_stdin():
                    try:
                        process.stdin.write(request.stdin.encode("utf-8"))
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # command exited without reading all of stdin
                    finally:
                        try:
                            process.stdin.close()
                        except Exception:
                            pass
                stdin_task = asyncio.create_task(feed_stdin())

            async def wait_process():
                exit_line = "event: exit\ndata: -1\n\n"
                try:
                    timed_out = False
                    try:
                        await asyncio.wait_for(process.wait(), timeout=request.timeout)
                    except asyncio.TimeoutError:
                        timed_out = True
                        _signal_group(process, signal.SIGTERM)
                    # Bound the pipe drain. Background children (`cmd &`) or SIGTERM-ignoring
                    # commands can hold the pipes open after the shell is gone; SIGKILL the
                    # group instead of hanging forever.
                    # ponytail: this also kills deliberately backgrounded daemons KILL_GRACE s after
                    # the shell exits; the stream can't tell them from a hung pipe.
                    _, pending = await asyncio.wait({t1, t2}, timeout=KILL_GRACE)
                    if pending:
                        _signal_group(process, signal.SIGKILL)
                        _, pending = await asyncio.wait(pending, timeout=KILL_GRACE)
                        for t in pending:
                            t.cancel()
                    if timed_out:
                        await queue.put(
                            f"data: stderr:[Timeout] Command exceeded {request.timeout} seconds\n\n"
                        )
                    elif process.returncode is not None:
                        exit_line = "event: exit\ndata: " + str(process.returncode) + "\n\n"
                except Exception as e:
                    logger.error(f"wait_process failed for {execution_id}: {e}")
                finally:
                    # Always terminate the stream, even if a reader/kill step blew up.
                    await queue.put(exit_line)
                    await queue.put(None)

            waiter = asyncio.create_task(wait_process())

            while True:
                data = await queue.get()
                if data is None:
                    break
                yield data

        finally:
            # Client disconnected (GeneratorExit/CancelledError) before the command finished:
            # don't leave it running until its timeout.
            if waiter is not None and not waiter.done():
                _signal_group(process, signal.SIGTERM)
                _escalate_kill(process)
            if stdin_task is not None:
                stdin_task.cancel()
            active_tasks.pop(execution_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import sys
    import uvicorn

    if not AGENT_TOKEN:
        sys.exit(
            "HOST_AGENT_TOKEN is not set. This agent executes arbitrary shell commands, so it "
            "refuses to start unauthenticated. Set the same value here and in the backend's .env "
            "(./bars start generates one for you), then retry."
        )

    # Must stay reachable from the backend container via host.docker.internal, which resolves
    # to the docker bridge gateway (172.x.0.1) — NOT loopback. Firewall port 8001 to your
    # docker bridge and VPN interfaces; the token is what keeps the rest of the LAN out.
    uvicorn.run(app, host=os.getenv("BARS_AGENT_HOST", "0.0.0.0"), port=8001)
