"""OpenCode-free provider plugin for CMDAI CODE.

Adds an `opencode_local` provider to a stock CMDAI CODE checkout without
touching the checkout. See patch.py for how, bridge.py for the server client.
"""

from .patch import describe, install

__all__ = ["install", "describe"]