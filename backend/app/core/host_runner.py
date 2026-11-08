"""
Host Runner functionality for communicating with the Host Agent.
"""
import logging
import aiohttp
import asyncio
from typing import Optional, AsyncGenerator, Dict, Any

logger = logging.getLogger(__name__)


class HostRunner:
    """
    Client for the Host Agent. Reuses a single aiohttp.ClientSession
    for connection pooling across all requests.
    """
    
    def __init__(self, agent_url: Optional[str] = None):
        from app.config import settings

        self.agent_url = agent_url or getattr(
            settings, "HOST_AGENT_URL", "http://host.docker.internal:8001"
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            from app.config import settings

            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=10),
                headers={"X-Bars-Token": settings.HOST_AGENT_TOKEN},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def execute_and_stream(
        self,
        command: str,
        cwd: Optional[str] = None,
        execution_id: Optional[str] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Execute command on host and yield outputs.
        Yields dicts like: {"stream": "stdout", "text": "..."} or {"exit_code": 0}
        """
        url = f"{self.agent_url}/run_and_stream"
        payload: Dict[str, Any] = {
            "command": command,
            "cwd": cwd,
            "execution_id": execution_id,
        }
        if timeout is not None:
            payload["timeout"] = timeout
        if stdin is not None:
            payload["stdin"] = stdin

        # The session has no overall deadline (streams are long-lived), so bound each request
        # by the command timeout + grace; the agent's default timeout is 3600s. Otherwise a
        # hung agent leaves the execution RUNNING forever.
        request_timeout = aiohttp.ClientTimeout(
            total=(timeout if timeout is not None else 3600) + 60, connect=10
        )

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, timeout=request_timeout) as response:
                if response.status != 200:
                    yield {"stream": "stderr", "text": f"Agent Error: {response.status}"}
                    yield {"exit_code": -1}
                    return
                
                buffer = b""
                while True:
                    chunk = await response.content.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        # Only drop the line terminator; leading spaces are payload (column parsers).
                        decoded = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if not decoded:
                            continue
                        if not decoded.startswith("data: "):
                            continue

                        content = decoded[6:]

                        if content.startswith("stdout:"):
                            yield {"stream": "stdout", "text": content[7:]}
                        elif content.startswith("stderr:"):
                            yield {"stream": "stderr", "text": content[7:]}
                        else:
                            try:
                                exit_code = int(content)
                                yield {"exit_code": exit_code}
                            except ValueError:
                                pass
                                
        except Exception as e:
            # Transport failure: mark it distinctly and emit NO exit_code, so
            # callers report connection-lost instead of a fake command failure.
            msg = f"Connection Error: {str(e)}"
            yield {"stream": "stderr", "text": msg}
            yield {"transport_error": msg}

    async def cancel_execution(self, execution_id: str, timeout: float = 10) -> bool:
        """Cancel an execution on the host agent (bounded wait)."""
        url = f"{self.agent_url}/cancel/{execution_id}"
        try:
            session = await self._get_session()

            async def _do() -> bool:
                async with session.post(url) as response:
                    return response.status == 200

            return await asyncio.wait_for(_do(), timeout=timeout)
        except Exception as e:
            logger.error(f"Failed to cancel execution {execution_id}: {e}")
            return False

    async def execute_sync(
        self, command: str, cwd: Optional[str] = None, timeout: int = 60, stdin: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute command synchronously (wait for completion) and return result.
        Returns: {"exit_code": int, "stdout": str, "stderr": str}

        Bounded by ``timeout`` (default 60s) so an output-extraction/browse command
        can't hang the finalizer for the agent's 3600s default.
        """
        stdout_buf = []
        stderr_buf = []
        exit_code = -1

        async for event in self.execute_and_stream(command, cwd, timeout=timeout, stdin=stdin):
            if "stream" in event:
                if event["stream"] == "stdout":
                    stdout_buf.append(event["text"])
                elif event["stream"] == "stderr":
                    stderr_buf.append(event["text"])
            elif "exit_code" in event:
                exit_code = event["exit_code"]
                
        return {
            "exit_code": exit_code,
            # Streamed lines arrive without their newline, so rejoin with one.
            "stdout": "\n".join(stdout_buf),
            "stderr": "\n".join(stderr_buf)
        }

host_runner = HostRunner()
