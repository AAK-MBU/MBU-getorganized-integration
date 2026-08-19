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
    """Build a :class:`GoConfig` from ``go_api_endpoint`` / ``go_api_username`` /
    ``go_api_password`` (and optional ``LIBREOFFICE_PATH``).

    Raises ``RuntimeError`` if any required variable is missing — fail fast
    rather than make a half-configured GO call.
    """
    missing = [
        name
        for name in ("go_api_endpoint", "go_api_username", "go_api_password")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "Missing required GO settings: " + ", ".join(missing)
        )
    return GoConfig(
        base_url=os.environ["go_api_endpoint"].rstrip("/"),
        username=os.environ["go_api_username"],
        password=os.environ["go_api_password"],
        libreoffice_path=os.environ.get("LIBREOFFICE_PATH"),
    )
