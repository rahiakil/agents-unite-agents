"""Print the N most recent completed NYSE sessions (newest first), space separated."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_nightly as C  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
now = datetime.now(timezone.utc)
d, out = now.astimezone(C.NY).date(), []
while len(out) < n:
    if C.is_trading_day(d) and C.close_dt(d) <= now:
        out.append(d.isoformat())
    d -= timedelta(days=1)
print(" ".join(out))
