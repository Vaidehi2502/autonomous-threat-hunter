import sys
from pathlib import Path

import pytest

# Make the project root importable so `from src.detect import ...` works
# regardless of the directory pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def disable_rate_limit_by_default():
    """Turn the limiter off for the suite as a whole.

    Every test shares one client address, so otherwise the suite would start
    failing purely by growing past the per-minute limit. test_security.py sets
    its own limits per test and is unaffected by this.
    """
    from src import api

    api.RATE_LIMIT_REQUESTS = 0
