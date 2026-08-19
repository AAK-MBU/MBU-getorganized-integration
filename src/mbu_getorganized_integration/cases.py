"""Session-based GO case functions — write/read surface (plan §2, Layer 1).

Stateless ``f(session, *, base_url, ...)`` functions for cases: create (a case
or a citizen folder — same ``/_goapi/Cases`` endpoint and body shape), search by
properties, open / close, and fetch metadata. Ported from shared-components
``getorganized/cases.py`` (NTLM-per-call → session) and the
``ats_fratag_formynd/esdh_client.py`` consumer, which pinned the response shapes
(``CaseID`` on create, ``CasesInfo`` on search).

The caller builds the request bodies with :mod:`payloads` (the ``<z:row>``
MetadataXml and the search field-property dicts) and passes them in; these
functions own only the transport + typed parse.
"""

from __future__ import annotations

import requests

from . import endpoints, payloads
from ._http import request
from .models import Case


def create_case(
    s: requests.Session,
    *,
    base_url: str,
    case_type_prefix: str,
    metadata_xml: str,
    return_when_fully_created: bool = True,
) -> Case:
    """Create a case (or citizen folder) from a pre-built ``<z:row>`` MetadataXml.

    Wraps the XML with :func:`payloads.case_data_json` and POSTs it to
    ``/_goapi/Cases``. Both a normal case and a Borgermappe folder go through
    here — they differ only in the MetadataXml the caller supplies. Returns the
    created :class:`Case` (``id`` = GO ``CaseID``). Confirmed shape (esdh_client).
    """
    body = payloads.case_data_json(case_type_prefix, metadata_xml, return_when_fully_created)
    r = request(s, "POST", endpoints.cases(base_url), json=body)
    data = r.json()
    return Case(id=data["CaseID"], raw=data)


def find_case(s: requests.Session, *, base_url: str, search_data: dict) -> list[Case]:
    """Search for cases by properties (``/_goapi/cases/findbycaseproperties``).

    ``search_data`` is one of the :mod:`payloads` search builders' output.
    Returns one :class:`Case` per ``CasesInfo`` row (empty list when none match).
    Confirmed shape (esdh_client).
    """
    r = request(s, "POST", endpoints.find_case(base_url), json=search_data)
    rows = r.json().get("CasesInfo", []) or []
    return [Case(id=row["CaseID"], raw=row) for row in rows]


def open_case(
    s: requests.Session, *, base_url: str, case_id: str, reason: str | None = None
) -> None:
    """Reopen a closed case (``/_goapi/Cases/OpenCase``). Confirmed request shape
    (shared-components ``cases.open_case``)."""
    payload = {"CaseId": case_id}
    if reason:
        payload["Reason"] = reason
    request(s, "POST", endpoints.open_case(base_url), json=payload)


def close_case(
    s: requests.Session, *, base_url: str, case_id: str, reason: str | None = None
) -> None:
    """Close a case. ``ats_fratag_formynd/luk`` needs this (plan §0.5).

    TODO(confirm on host): the ``CloseCase`` endpoint path (see
    :func:`endpoints.close_case`) and this ``{CaseId, Reason}`` body mirror the
    confirmed ``OpenCase`` shape but are UNVERIFIED against GO — confirm before
    relying on it in production.
    """
    payload = {"CaseId": case_id}
    if reason:
        payload["Reason"] = reason
    request(s, "POST", endpoints.close_case(base_url), json=payload)


def get_case_metadata(s: requests.Session, *, base_url: str, case_id: str) -> dict:
    """Fetch a case's metadata (``/_goapi/Cases/Metadata/{id}``).

    Returns the raw GO response dict (``{"Metadata": "<z:row .../>", ...}``);
    parsing the XML is the caller's job. Confirmed shape (discovery chain).
    """
    r = request(s, "GET", endpoints.case_metadata(base_url, case_id))
    return r.json()
