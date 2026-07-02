"""Shared .env loader for init scripts."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(relative_to: Path | None = None) -> None:
    """Load project root .env into os.environ if it exists."""
    here = (relative_to or Path(__file__)).resolve().parent
    root = here.parent
    env_file = root / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key, val)
