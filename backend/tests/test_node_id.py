"""Node id is stable across restarts via a persisted file; env still overrides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import resolve_node_id


def test_node_id_persists_and_env_overrides(tmp_path, monkeypatch):
    first = resolve_node_id(tmp_path)
    assert first.startswith("node-") and len(first) > 8
    second = resolve_node_id(tmp_path)
    assert first == second, "id must be stable across calls (restarts)"
    assert (tmp_path / ".node_id").read_text().strip() == first
    monkeypatch.setenv("NODE_ID", "operator-laptop")
    assert resolve_node_id(tmp_path) == "operator-laptop"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        resolve_node_id(Path(d))
    print("PASS node id")
