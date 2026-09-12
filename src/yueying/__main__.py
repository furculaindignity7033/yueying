"""`python -m yueying ...` behaves exactly like the `yueying` console script (incl. `python -m yueying mcp`)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
