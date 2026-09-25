"""Import-order regression tests: any entry point must import standalone.

H3 added ``from app.core.utils import utcnow`` to models; the eager imports in
``app.core.__init__`` turned that into an order-dependent circular import
(models-first entry points died at collection). These tests pin standalone
importability via fresh subprocess interpreters.
"""
import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = [
    "from app.models.project import Project",
    "from app.models import Project, User, Execution",
    "import app.core.sync",
    "import app.core.orchestrator",
    "import app.main",
]


def _check(statement: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"{statement}; print('IMPORT_OK')"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"standalone import failed: {statement}\n{proc.stderr[-1500:]}"
    )
    assert "IMPORT_OK" in proc.stdout


def test_models_first_import():
    _check("from app.models.project import Project")


def test_models_package_import():
    _check("from app.models import Project, User, Execution")


def test_core_modules_import():
    _check("import app.core.sync")
    _check("import app.core.orchestrator")


def test_main_import():
    _check("import app.main")
