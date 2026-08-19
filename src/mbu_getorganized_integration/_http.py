"""Transport layer for the GO client (plan §3, Layer 0).

GO authenticates via NTLM. This module owns the two transport concerns:

* :func:`session` — a preconfigured NTLM ``requests.Session`` (moved from
  ``go.py``, unchanged). One session, NTLM set once, reused → connection
  pooling and a single handshake per host.
* :func:`request` — the single request choke point: applies a default timeout
  and raises on HTTP error, so the stateless funcs above don't each repeat
  ``timeout=`` / ``raise_for_status()``. It is also the natural home for future
  retry / logging hooks.

Error-tolerant reads (parents / children) deliberately do *not* go through
:func:`request` — they need to swallow transport errors and return ``[]``, so
they keep their own try/except.
"""

from __future__ import annotations

import requests
from requests_ntlm import HttpNtlmAuth

#: Default per-request timeout (seconds). Long-running calls (downloads, PDF
#: conversion) pass their own larger value explicitly.
DEFAULT_TIMEOUT = 60


def session(username: str, password: str) -> requests.Session:
    """Return a fresh NTLM-authenticated session for GO."""
    s = requests.Session()
    s.auth = HttpNtlmAuth(username, password)
    s.headers.update({"Content-Type": "application/json"})
    return s


def request(
    s: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int | None = DEFAULT_TIMEOUT,
    **kwargs,
) -> requests.Response:
    """Send a request through the session and raise on HTTP error.

    A thin wrapper over ``session.request`` that supplies the default timeout
    and calls ``raise_for_status()``. Returns the (successful) response so the
    caller can ``.json()`` / read ``.content``. Extra keyword args are passed
    straight through to ``requests`` (``json=``, ``data=``, ``headers=``,
    ``stream=`` …).
    """
    resp = s.request(method, url, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp
