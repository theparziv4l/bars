"""Application configuration settings."""
import socket
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional

# Machine-unique-ish default so two sync nodes don't share "node-local" (which breaks
# the last-write-wins tiebreak). Operators SHOULD still set NODE_ID explicitly per node.
_DEFAULT_NODE_ID = f"node-{socket.gethostname() or 'local'}"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    APP_NAME: str = "Bars"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Base Path
    BASE_DIR: Path = Path(__file__).resolve().parent.parent

    # Database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/pentest_toolbox.db"
    SQL_ECHO: bool = False
    
    # File storage
    STORAGE_PATH: Path = BASE_DIR / "storage"
    REPORTS_PATH: Path = BASE_DIR / "reports"
    
    # Execution
    MAX_CONCURRENT_TASKS: int = 10
    BATCH_CONCURRENCY: int = 5  # parallel children per batch (e.g. 900 targets, 5 at a time)
    TASK_TIMEOUT: int = 3600  # 1 hour default
    
    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_REQUIRE_AUTH: bool = False

    # Authentication
    AUTH_REQUIRED: bool = False
    AUTH_SECRET_KEY: str = "ptf-dev-change-me"
    AUTH_TOKEN_TTL_MINUTES: int = 480

    # Collaboration / claim handling
    CLAIMS_ENABLED: bool = True
    DEFAULT_CLAIM_LEASE_SECONDS: int = 900

    # Multi-node sync
    SYNC_ENABLED: bool = False
    NODE_ID: str = _DEFAULT_NODE_ID
    SYNC_PEERS: str = ""
    SYNC_PULL_INTERVAL_SECONDS: int = 5
    SYNC_BATCH_SIZE: int = 200
    # Optional URL to show in UI as "your node address" for collaborators (e.g. http://your-ip:8000)
    PUBLIC_BASE_URL: str = ""
    # Current user's home directory resolved at runtime.
    HOME_DIR: str = str(Path.home())
    CURRENT_USER_HOME_DIR: str = str(Path.home())

    # Host user's home directory. Inside Docker this must be passed via env/docker-compose
    # so we know the real host path (not the container's /root).
    HOST_HOME_DIR: str = str(Path.home())

    # Host agent
    HOST_AGENT_URL: str = "http://host.docker.internal:8001"
    # Shared secret sent as X-Bars-Token. Must match BARS_AGENT_TOKEN in the agent's environment.
    HOST_AGENT_TOKEN: str = ""

    # Project folders base path (used as parent for /home/kali/Pentests/<projectname>)
    PROJECTS_BASE_PATH: str = ""
    
    # Host-side storage path (for commands sent to host agent in Docker setups)
    # When running in Docker, STORAGE_PATH is the container path (/app/storage)
    # but the host agent needs the host-equivalent path. If unset, defaults to STORAGE_PATH.
    HOST_STORAGE_PATH: Optional[str] = None
    
    def model_post_init(self, __context) -> None:
        if not self.PROJECTS_BASE_PATH:
            object.__setattr__(
                self, "PROJECTS_BASE_PATH",
                str(Path(self.HOST_HOME_DIR) / "Pentests"),
            )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def get_sync_peer_urls() -> list[str]:
    """Parse comma-separated peer URLs from settings."""
    return [p.strip().rstrip("/") for p in settings.SYNC_PEERS.split(",") if p.strip()]

# Ensure directories exist
settings.STORAGE_PATH.mkdir(parents=True, exist_ok=True)
settings.REPORTS_PATH.mkdir(parents=True, exist_ok=True)
