#!/usr/bin/env python3
"""
Shared .env loader for workspace scripts.

Loads /home/juan/.openclaw/workspace/.env once per process and exposes
typed accessors. Fail fast (no silent fallback to hardcoded secrets) so
that a missing or rotated credential surfaces immediately instead of
silently reverting to a leaked default.

Usage:
    from env_loader import get, get_pgvector_dsn, get_ssh_creds, require

    pg_pass = require("PGVECTOR_PASSWORD")
    dsn = get_pgvector_dsn()                # full DSN string
    host, user, pw = get_ssh_creds("JETSON")
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

_ENV_PATH = Path("/home/juan/.openclaw/workspace/.env")
_LOADED = False


def load_env(path: Optional[Path] = None, *, override: bool = False) -> bool:
    """Load .env into os.environ. Returns True if file existed."""
    global _LOADED
    env_path = path or _ENV_PATH
    if not env_path.exists():
        return False

    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
    _LOADED = True
    return True


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a key, loading .env on first access."""
    if not _LOADED:
        load_env()
    return os.environ.get(key, default)


def require(key: str) -> str:
    """Read a key and fail hard if missing. No silent fallback."""
    value = get(key)
    if value is None or value == "":
        print(
            f"[env_loader] FATAL: required env var {key!r} missing. "
            f"Add it to {_ENV_PATH}.",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


def get_pgvector_dsn() -> str:
    """Return full pgvector DSN, preferring MEMORY_DB_URL if set."""
    if not _LOADED:
        load_env()
    url = os.environ.get("MEMORY_DB_URL")
    if url:
        return url
    host = require("PGVECTOR_HOST")
    port = os.environ.get("PGVECTOR_PORT", "5432")
    db = require("PGVECTOR_DATABASE")
    user = require("PGVECTOR_USER")
    pw = require("PGVECTOR_PASSWORD")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def get_ssh_creds(prefix: str) -> Tuple[str, str, str]:
    """Return (host, user, password) for a cred set.

    `prefix` is the env-var prefix without the trailing underscore, e.g.
    "JETSON" -> JETSON_SSH_HOST / JETSON_SSH_USER / JETSON_SSH_PASS.
    """
    return (
        require(f"{prefix}_SSH_HOST"),
        require(f"{prefix}_SSH_USER"),
        require(f"{prefix}_SSH_PASS"),
    )


# Eagerly load on import so any module that does `from env_loader import get`
# sees the populated environment without an extra call.
load_env()
