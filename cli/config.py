from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BARS_DIR = PROJECT_ROOT / ".bars"
STATE_FILE = BARS_DIR / "state.json"
LOG_DIR = BARS_DIR / "logs"

HOST_AGENT_DIR = PROJECT_ROOT / "host_agent"
HOST_AGENT_VENV = HOST_AGENT_DIR / "venv"
HOST_AGENT_REQUIREMENTS = HOST_AGENT_DIR / "requirements.txt"
HOST_AGENT_MAIN = HOST_AGENT_DIR / "main.py"
HOST_AGENT_LOG = LOG_DIR / "host_agent.log"

STORAGE_DIR = PROJECT_ROOT / "storage"
REPORTS_DIR = PROJECT_ROOT / "reports"

COMPOSE_FILE_PROD = PROJECT_ROOT / "docker-compose.yml"
COMPOSE_FILE_DEV = PROJECT_ROOT / "docker-compose.dev.yml"

COMPOSE_PROJECT_NAME = "bars"

BACKEND_PORT = 8000
FRONTEND_PORT = 3000
AGENT_PORT = 8001

SERVICES = {
    "backend": {"port": BACKEND_PORT, "url": f"http://localhost:{BACKEND_PORT}"},
    "frontend": {"port": FRONTEND_PORT, "url": f"http://localhost:{FRONTEND_PORT}"},
    "agent": {"port": AGENT_PORT, "url": f"http://localhost:{AGENT_PORT}"},
}

DB_PATTERNS = ["pentest_toolbox.db", "pentest_toolbox.db-shm", "pentest_toolbox.db-wal"]
