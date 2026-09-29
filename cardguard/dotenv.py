"""Load KEY=value lines from the repo's .env into os.environ (existing variables win). No dependency."""
from __future__ import annotations

import os

from cardguard import ROOT


def load(path: str | None = None) -> list[str]:
    """Returns the names that were set. Lines starting with # and blank lines are ignored;
    values may be quoted with single or double quotes."""
    path = path or str(ROOT / ".env")
    if not os.path.exists(path):
        return []
    loaded = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded
