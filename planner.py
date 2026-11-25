"""Checkout entry point. The installed commands are schedule-plan and python -m scheduling_planner."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from scheduling_planner.cli import main

__all__ = ["plan"]

if __name__ == "__main__":
    main()
else:
    from scheduling_planner import plan
