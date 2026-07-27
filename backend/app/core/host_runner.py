"""
Host Runner functionality for communicating with the Host Agent.
"""
import aiohttp
import asyncio
from typing import Optional, AsyncGenerator, Dict, Any


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
        
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as response:
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
                        decoded = raw_line.decode("utf-8", errors="replace").strip()
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
            yield {"stream": "stderr", "text": f"Connection Error: {str(e)}"}
            yield {"exit_code": -1}

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel an execution on the host agent."""
        url = f"{self.agent_url}/cancel/{execution_id}"
        try:
            session = await self._get_session()
            async with session.post(url) as response:
                return response.status == 200
        except Exception as e:
            print(f"Failed to cancel execution {execution_id}: {e}")
            return False

    async def execute_sync(self, command: str, cwd: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
        """
        Execute command synchronously (wait for completion) and return result.
        Returns: {"exit_code": int, "stdout": str, "stderr": str}

        Bounded by ``timeout`` (default 60s) so an output-extraction/browse command
        can't hang the finalizer for the agent's 3600s default.
        """
        stdout_buf = []
        stderr_buf = []
        exit_code = -1

        async for event in self.execute_and_stream(command, cwd, timeout=timeout):
            if "stream" in event:
                if event["stream"] == "stdout":
                    stdout_buf.append(event["text"])
                elif event["stream"] == "stderr":
                    stderr_buf.append(event["text"])
            elif "exit_code" in event:
                exit_code = event["exit_code"]
                
        return {
            "exit_code": exit_code,
            "stdout": "".join(stdout_buf),
            "stderr": "".join(stderr_buf)
        }

host_runner = HostRunner()
