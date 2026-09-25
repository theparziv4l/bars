"""utcnow() preserves naive-UTC semantics (SQLite compares naive)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.utils import utcnow


def test_utcnow_naive_utc():
    ts = utcnow()
    assert ts.tzinfo is None, "must stay naive for SQLite comparisons"
    delta = abs((datetime.now() - ts).total_seconds())
    assert delta < 3600 * 25, f"not UTC? delta={delta}"


if __name__ == "__main__":
    test_utcnow_naive_utc()
