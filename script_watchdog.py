#!/usr/bin/env python3
"""Wrapper to run the ops watchdog from the repo root so imports resolve."""
from services.ops.watchdog import main
if __name__ == "__main__":
    raise SystemExit(main())
