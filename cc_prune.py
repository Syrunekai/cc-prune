#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""The cc-prune command line. Run it directly, no install needed: ./cc_prune.py COMMAND ..."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "src"))

from ccprune.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
