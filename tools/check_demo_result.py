"""Verify native demo or equivalent piped snapshot output from a JSON file or stdin."""

import json
from pathlib import Path
import sys


def check(result):
    rows = result["move_comparison"]["caps"]
    expected = [
        (0, "2026-12-01T15:00Z", None, "optimal"),
        (1, "2026-12-01T09:30Z", 330, "optimal"),
        (2, "2026-12-01T09:00Z", 30, "optimal"),
    ]
    actual = [(r["cap"], r["start"], r["minutes_gained"], r["status"]) for r in rows]
    if result["view"] != "brief" or actual != expected:
        raise ValueError(f"Unexpected native demo result: {actual!r}")
    print("Verified native move caps 0/1/2: 15:00, 09:30, 09:00 UTC; gains 330 and 30 minutes.")


if __name__ == "__main__":
    raw = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else sys.stdin.buffer.read()
    check(json.loads(raw))
