"""Session-based GO contact lookup (plan §2, Layer 1).

Ported from shared-components ``getorganized/contacts.py``. The endpoint is
site-scoped (``/{site}/_goapi/contacts/readitem``, default ``borgersager`` —
plan §8 answer 3) and, unlike the JSON endpoints, expects a form-urlencoded
body, so this overrides the session's default JSON content-type per call.
"""

from __future__ import annotations

import requests

from . import endpoints
from ._http import request
from .models import ContactResult


def contact_lookup(
    s: requests.Session, *, base_url: str, ssn: str, site: str = "borgersager"
) -> ContactResult:
    """Resolve a citizen's GO contact (FullName + ID) from their SSN.

    Returns a :class:`ContactResult`; an empty ``full_name`` / ``id`` means GO
    has no contact for that SSN (the caller decides whether that is an error).
    Confirmed shape (esdh_client reads ``FullName`` / ``ID``). The SSN is sent in
    the request body but never logged here.
    """
    body = {"Id": ssn, "ContactDataFieldName": "CCMContactData"}
    encoded = "&".join(f"{k}={v}" for k, v in body.items())
    r = request(
        s,
        "POST",
        endpoints.contact_lookup(base_url, site=site),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=encoded,
    )
    data = r.json()
    person_id = data.get("ID")
    return ContactResult(
        full_name=data.get("FullName") or "",
        id="" if person_id is None else str(person_id),
        raw=data,
    )
