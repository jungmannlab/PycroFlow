"""``python -m PycroFlow.perf`` entry point — delegates to the CLI."""

from __future__ import annotations

import sys

from PycroFlow.perf.cli import main

if __name__ == "__main__":
    sys.exit(main())
