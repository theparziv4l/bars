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

@app.post("/cancel/{execution_id}", dependencies=[Depends(require_token)])
async def cancel_command(execution_id: str):
    """Cancel a running command."""
    if execution_id in active_tasks:
        process = active_tasks[execution_id]
        try:
            # Terminate the process group to ensure shell and children are killed
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass # Already gone
        except Exception as e:
             logger.error(f"Failed to kill process {execution_id}: {e}")
             # Fallback to simple terminate
             try:
                 process.terminate()
             except:
                 pass

        # Escalate to SIGKILL if the process ignores SIGTERM within the grace period,
        # so a SIGTERM-trapping child can't keep the stream (and a concurrency slot) alive.
        async def _escalate_kill():
            await asyncio.sleep(5)
            if process.returncode is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
        asyncio.create_task(_escalate_kill())

        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Execution not found")

@app.post("/run_and_stream", dependencies=[Depends(require_token)])
async def run_and_stream(request: ExecuteRequest):
    """
    Combined endpoint to start and stream. 
    Simplifies the connection logic for the backend.
    """
    async def event_generator():
        process = await asyncio.create_subprocess_shell(
            request.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.cwd or os.getcwd(),
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        execution_id = request.execution_id or str(uuid.uuid4())
        active_tasks[execution_id] = process

        try:
            queue: asyncio.Queue[str] = asyncio.Queue()

            async def pipe_to_queue(stream, prefix: str):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    await queue.put(
                        f"data: {prefix}:{line.decode('utf-8', errors='replace').rstrip('\n')}\n\n"
                    )

            t1 = asyncio.create_task(pipe_to_queue(process.stdout, "stdout"))
            t2 = asyncio.create_task(pipe_to_queue(process.stderr, "stderr"))

            async def wait_process():
                try:
                    await asyncio.wait_for(process.wait(), timeout=request.timeout)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except Exception:
                        try:
                            process.terminate()
                        except Exception:
                            pass
                    # Bound the pipe drain; if the child ignores SIGTERM and holds the
                    # pipes open, escalate to SIGKILL so this can't hang forever.
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(t1, t2, return_exceptions=True), timeout=5
                        )
                    except asyncio.TimeoutError:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except Exception:
                            pass
                        await asyncio.gather(t1, t2, return_exceptions=True)
                    await queue.put(
                        f"data: stderr:[Timeout] Command exceeded {request.timeout} seconds\n\n"
                    )
                    await queue.put("event: exit\ndata: -1\n\n")
                    await queue.put(None)
                    return

                await t1
                await t2
                await queue.put(
                    "event: exit\ndata: " + str(process.returncode) + "\n\n"
                )
                await queue.put(None)

            asyncio.create_task(wait_process())

            while True:
                data = await queue.get()
                if data is None:
                    break
                yield data

        finally:
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
