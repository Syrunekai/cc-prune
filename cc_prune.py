#!/usr/bin/env python3
"""Run cc-prune from a checkout, no install needed: python3 cc_prune.py COMMAND ..."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from ccprune.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
