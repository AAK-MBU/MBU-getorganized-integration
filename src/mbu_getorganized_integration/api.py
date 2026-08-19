"""GO API health check (plan §2, Layer 1).

Ported from shared-components ``getorganized/api.py``. Deliberately does not go
through :func:`_http.request` — a health check must report a down API as
``False``, not raise — so it swallows transport errors and reads ``response.ok``.
"""

from __future__ import annotations

import requests

from ._http import DEFAULT_TIMEOUT


def health_check(s: requests.Session, *, base_url: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Return ``True`` if the GO API answers OK, ``False`` if it is down.

    Use before a run that needs GO, so an unavailable API is registered up
    front rather than surfacing mid-process.
    """
    try:
        resp = s.request("GET", base_url, timeout=timeout)
    except requests.RequestException:
        return False
    return bool(resp.ok)
