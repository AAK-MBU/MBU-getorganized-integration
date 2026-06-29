"""Environment-driven configuration for the GO client.

The package itself is stateless — callers may pass credentials/URLs explicitly —
but :func:`go_config_from_env` is provided as the conventional way to read them
from the environment so consuming apps don't each reinvent it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GoConfig:
    """Connection settings for a GO (GetOrganized) instance."""

    base_url: str
    username: str
    password: str
    libreoffice_path: str | None = None


def go_config_from_env() -> GoConfig:
    """Build a :class:`GoConfig` from ``GO_API_URL`` / ``GO_USERNAME`` /
    ``GO_PASSWORD`` (and optional ``LIBREOFFICE_PATH``).

    Raises ``RuntimeError`` if any required variable is missing — fail fast
    rather than make a half-configured GO call.
    """
    missing = [
        name
        for name in ("GO_API_URL", "GO_USERNAME", "GO_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "Missing required GO settings: " + ", ".join(missing)
        )
    return GoConfig(
        base_url=os.environ["GO_API_URL"].rstrip("/"),
        username=os.environ["GO_USERNAME"],
        password=os.environ["GO_PASSWORD"],
        libreoffice_path=os.environ.get("LIBREOFFICE_PATH"),
    )
