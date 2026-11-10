"""The error the engine raises when an operation has to stop with a message."""
from __future__ import annotations


class PruneError(Exception):
    """A refusal or failure the user needs to read. The text is the message."""
