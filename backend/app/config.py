"""Application configuration settings."""
import os
import socket
import uuid
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional


def resolve_node_id(storage_dir=None) -> str:
    """Stable per-node id: explicit env wins, else a persisted generated id.

    Hostname defaults collide across cloned images/containers (shared cursors,
    broken LWW tiebreaks), so the generated id is written to ``.node_id`` once
    and reused on every later start.
    """
    env = os.environ.get("NODE_ID")
    if env and env.strip():
        return env.strip()
    directory = Path(storage_dir) if storage_dir else (Path(__file__).resolve().parent.parent / "storage")
    path = directory / ".node_id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    generated = f"node-{uuid.uuid4().hex[:12]}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(generated)
    except OSError:
        return f"node-{socket.gethostname() or 'local'}"
    return generated


_DEFAULT_NODE_ID = resolve_node_id()


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
    # Shared secret for node-to-node pull/push (X-Sync-Token). Sync endpoints
    # fail closed (503) when SYNC_ENABLED without a token configured.
    SYNC_PEER_TOKEN: str = ""
    # Delete sync events older than this many days (None = keep forever).
    # Only enable when every peer syncs more often than this window.
    SYNC_EVENT_RETENTION_DAYS: int | None = None
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
