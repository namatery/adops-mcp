"""Environment loading and backend selection.

One env var (``BACKEND``) decides which :class:`~adops_mcp.backends.base.AdPlatform`
implementation the server talks to. Everything above the backend layer is
oblivious to the choice. The default is the zero-credential mock backend, so the
server boots and works with no ``.env`` at all.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env once at import time if present. Absent .env is fine — mock is default.
load_dotenv()

DEFAULT_AUDIT_LOG_PATH = "audit_log.jsonl"


def get_backend_name() -> str:
    """Return the configured backend name, normalized and defaulted to ``mock``."""
    return os.getenv("BACKEND", "mock").strip().lower() or "mock"


def get_audit_log_path() -> Path:
    """Return the path to the JSONL audit log (default ``./audit_log.jsonl``)."""
    return Path(os.getenv("AUDIT_LOG_PATH", DEFAULT_AUDIT_LOG_PATH)).expanduser()


@lru_cache(maxsize=1)
def get_backend():
    """Construct and cache the selected backend.

    Imports are done lazily so that, e.g., importing the mock-only test suite
    never pulls in the live Taboola client (and vice versa).
    """
    name = get_backend_name()
    if name == "mock":
        from .backends.mock import MockBackend

        return MockBackend()
    if name == "taboola":
        from .backends.taboola import TaboolaBackend

        client_id = os.getenv("TABOOLA_CLIENT_ID")
        client_secret = os.getenv("TABOOLA_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "BACKEND=taboola requires TABOOLA_CLIENT_ID and TABOOLA_CLIENT_SECRET. "
                "Set them in .env, or use BACKEND=mock (the default) for a credential-free demo."
            )
        return TaboolaBackend(
            client_id=client_id,
            client_secret=client_secret,
            account_id=os.getenv("TABOOLA_ACCOUNT_ID") or None,
        )
    raise RuntimeError(f"Unknown BACKEND={name!r}. Use 'mock' (default) or 'taboola'.")
