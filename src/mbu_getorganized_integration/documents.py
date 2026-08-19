"""Session-based GO document functions — read surface (plan §2, Layer 1).

Stateless ``f(session, *, base_url, ...)`` functions for a single document:
metadata, bilag relationships (parents / children), download (with the
SharePoint-blob fallback), and GO's native PDF conversion. Paths come from
:mod:`endpoints`; the plain raise-and-parse calls go through
:func:`_http.request`. The error-tolerant reads (parents / children) keep their
own try/except so a missing relationship yields ``[]`` rather than raising.

The write surface (upload / mark-as-record / finalize / search) is added to this
module in a later step.
"""

from __future__ import annotations

import base64
import json
import re
import time

import requests
from requests_ntlm import HttpNtlmAuth

from . import endpoints, payloads
from ._http import request
from .models import SearchHit, UploadResult


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


_RE_FILE_TYPE = re.compile(r'ows_File_x0020_Type="([^"]+)"')
_RE_VERSION_UI = re.compile(r'ows__UIVersionString="([^"]+)"')


def fetch_metadata(s: requests.Session, *, base_url: str, dok_id: str) -> dict:
    """Fetch a document's metadata from GO.

    Returns a dict with at least:

    * ``ext`` — bare file extension (e.g. ``"docx"``, ``"pdf"``, ``"goref"``)
    * ``version_ui`` — UI version string needed by GO's PDF converter
    * ``raw`` — the parsed GO response

    Raises ``requests.HTTPError`` if GO refuses or times out.
    """
    r = request(s, "GET", endpoints.document_data(base_url, dok_id))
    data = json.loads(r.text)
    item_props = data.get("ItemProperties", "") or ""

    ext_m = _RE_FILE_TYPE.search(item_props)
    ver_m = _RE_VERSION_UI.search(item_props)
    return {
        "ext": ext_m.group(1) if ext_m else None,
        "version_ui": ver_m.group(1) if ver_m else None,
        "raw": data,
    }


def fetch_parents(s: requests.Session, *, base_url: str, dok_id: str) -> list[str]:
    """Return list of parent DocumentIds (bilag-til relationships)."""
    try:
        r = request(s, "GET", endpoints.document_parents(base_url, dok_id))
        return [str(it.get("DocumentId") or "") for it in (r.json().get("ParentsData") or [])]
    except requests.RequestException:
        return []


def fetch_children(s: requests.Session, *, base_url: str, dok_id: str) -> list[str]:
    """Return list of child DocumentIds (bilag-relationships)."""
    try:
        r = request(s, "GET", endpoints.document_children(base_url, dok_id))
        return [str(it.get("DocumentId") or "") for it in (r.json().get("ChildrenData") or [])]
    except requests.RequestException:
        return []


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_file(
    s: requests.Session,
    *,
    base_url: str,
    dok_id: str,
    local_path: str,
    max_retries: int = 30,
    retry_interval: int = 5,
    chunk_size: int = 8192,
) -> None:
    """Download a document from GO and write it to ``local_path``.

    Strategy:

    1. Try ``/Documents/DocumentBytes/{id}`` (fast, but silently truncates
       large files into an HTML 503 page). Retry up to ``max_retries`` times.
    2. If that fails or returns nonsense, fall back to fetching
       ``/Documents/MetadataWithSystemFields/{id}``, extracting the
       ``ows_EncodedAbsUrl`` (the underlying SharePoint blob URL), and
       streaming from there in ``chunk_size`` chunks.

    Step 2 is the only way to reliably pull files above ~10 MB from GO.
    Raises ``RuntimeError`` if both paths fail.

    This function manages its own retries and inspects raw responses, so it
    deliberately does not go through the :func:`_http.request` choke point.
    """
    # --- Primary path: /DocumentBytes ---
    url = endpoints.document_bytes(base_url, dok_id)
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            r = s.get(url, timeout=180)
            r.raise_for_status()
            if r.status_code == 200:
                content = r.content
                # GO returns an HTML 503 page disguised as 200 when the file
                # is too big for the binary endpoint.
                if b"HTTP Error 503. The service is unavailable." in content:
                    last_err = RuntimeError("GO returned 503 page in 200 body (likely oversize)")
                    break  # fall straight to SP-blob fallback; retrying won't help
                with open(local_path, "wb") as fh:
                    fh.write(content)
                return
        except Exception as exc:  # pylint: disable=broad-except
            last_err = exc
        if attempt < max_retries - 1:
            time.sleep(retry_interval)

    # --- Fallback path: extract SP-blob URL from metadata ---
    meta_url = endpoints.document_metadata_with_system_fields(base_url, dok_id)
    try:
        r = s.get(meta_url, timeout=60)
        r.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"GO download failed for {dok_id}: primary error {last_err!r}; "
            f"metadata fallback also failed: {exc!r}"
        ) from exc

    content = r.text
    if "ows_EncodedAbsUrl=" not in content:
        raise RuntimeError(
            f"GO download failed for {dok_id}: no ows_EncodedAbsUrl in metadata "
            f"and primary path failed: {last_err!r}"
        )

    doc_url = content.split("ows_EncodedAbsUrl=")[1].split('"')[1]
    # GO sometimes returns the public hostname; the cert-authenticated one is
    # the "ad." prefixed variant. Force it.
    doc_url = doc_url.split("\\")[0].replace("go.aarhus", "ad.go.aarhus")

    # Fresh session for the blob fetch — matches the legacy hack, in case
    # session state interferes with SP's direct blob endpoint.
    fresh = requests.Session()
    fresh.auth = s.auth
    with fresh.get(doc_url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(local_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    fh.write(chunk)


# ---------------------------------------------------------------------------
# PDF conversion (GO's native converter)
# ---------------------------------------------------------------------------


def pdf_convert(
    *,
    username: str,
    password: str,
    base_url: str,
    dok_id: str,
    version_ui: str,
    timeout: int | None = None,
) -> bytes | None:
    """Convert a GO document to PDF using GO's built-in converter.

    Returns the PDF bytes on success, or ``None`` if GO declines (e.g. the
    file format isn't supported by GO's converter). Callers should fall back
    to local conversion (``pdf.convert_to_pdf``) when this returns ``None``.

    Uses a fresh NTLM auth (matching legacy ``GOPDFConvert``) rather than the
    shared session — GO's converter endpoint has historically been picky
    about session state.
    """
    url = endpoints.convert_to_pdf(base_url, dok_id, version_ui)
    try:
        r = requests.get(
            url,
            auth=HttpNtlmAuth(username, password),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException:
        return None

    if r.status_code != 200:
        return None

    # GO sometimes returns 200 with an error message in the body for
    # unconvertible files.
    try:
        text_head = r.content[:512].decode("utf-8", errors="ignore")
    except Exception:  # pylint: disable=broad-except
        text_head = ""
    if "Document could not be converted" in text_head:
        return None

    return r.content


# ---------------------------------------------------------------------------
# Write surface — upload / journalize / finalize (plan §4)
# ---------------------------------------------------------------------------


def upload_document(
    s: requests.Session,
    *,
    base_url: str,
    case_id: str,
    filename: str,
    data: bytes,
    metadata: str = "",
    overwrite: bool = False,
    list_name: str = "Dokumenter",
    folder_path: str = "",
) -> UploadResult:
    """Upload a document to a case (``/_goapi/Documents/AddToCase``).

    ``data`` is the raw file bytes; they are base64-encoded into the ``Bytes``
    field (GO's AddToCase expects base64, and raw bytes are not JSON-encodable).
    ``metadata`` is a pre-built document ``<z:row>`` MetadataXml — the caller
    builds it (note :func:`payloads.document_metadata_xml` is still a step-3
    research item), or passes ``""`` to upload without extra metadata.

    Returns an :class:`UploadResult` with the new GO document id.

    TODO(confirm on host): the response id key. GO's AddToCase is expected to
    return ``DocId``; ``DocumentId`` / ``Id`` are accepted as fallbacks. Confirm
    the real key against a live instance.
    """
    encoded = base64.b64encode(data).decode("ascii")
    body = payloads.document_data_json(
        case_id=case_id,
        list_name=list_name,
        folder_path=folder_path,
        filename=filename,
        metadata=metadata,
        overwrite=overwrite,
        data=encoded,
    )
    r = request(s, "POST", endpoints.add_to_case(base_url), json=body)
    resp = r.json()
    raw_id = resp.get("DocId", resp.get("DocumentId", resp.get("Id")))
    return UploadResult(document_id=int(raw_id) if raw_id is not None else 0, raw=resp)


def mark_as_case_record(s: requests.Session, *, base_url: str, document_ids: list[int]) -> None:
    """Journalize documents — mark them as case records
    (``/_goapi/Documents/MarkMultipleAsCaseRecord/ByDocumentId``). Confirmed
    request shape (shared-components ``mark_file_as_case_record``)."""
    request(
        s,
        "POST",
        endpoints.mark_as_case_record(base_url),
        json={"DocumentIds": document_ids},
    )


def finalize_documents(s: requests.Session, *, base_url: str, document_ids: list[int]) -> None:
    """Finalize documents. Confirmed request shape (shared-components
    ``finalize_file``: ``{DocumentIds, ShouldCloseOpenTasks: False}``).

    TODO(confirm on host): the endpoint path (see :func:`endpoints.finalize`)."""
    request(
        s,
        "POST",
        endpoints.finalize(base_url),
        json={"DocumentIds": document_ids, "ShouldCloseOpenTasks": False},
    )


# ---------------------------------------------------------------------------
# Search (plan §4)
# ---------------------------------------------------------------------------


def _hits(rows) -> list[SearchHit]:
    """Wrap raw search rows in :class:`SearchHit`, pulling the common (loosely
    cased) columns and always keeping the full row in ``raw``."""
    out: list[SearchHit] = []
    for row in rows or []:
        out.append(
            SearchHit(
                title=row.get("title") or row.get("Title"),
                case_id=row.get("caseid") or row.get("CCMCaseID") or row.get("CaseID"),
                document_id=row.get("docid") or row.get("CCMDocID") or row.get("DocID"),
                raw=row,
            )
        )
    return out


def search_documents(
    s: requests.Session, *, base_url: str, term: str, limit: int = 500
) -> list[SearchHit]:
    """Legacy document search (shared-components ``search_documents`` shape).

    NOT IMPLEMENTED yet — unlike the rest of the surface, this endpoint has not
    been verified against a live GO instance, so both the endpoint path (see
    :func:`endpoints.search_documents`) and the response row container remain
    unconfirmed. It raises rather than silently POST to an unverified endpoint;
    to be wired up in a later change once confirmed on host.

    The intended shape (from shared-components): POST
    ``{"SearchPhrase", "AdditionalColumns": [], "ResultLimit", "StartRow": 0}``
    and read rows from ``Rows`` (fallback ``results.Results``), mapped via
    :func:`_hits`. For a working document search today use :func:`modern_search`.
    """
    raise NotImplementedError(
        "search_documents: the legacy Documents/Search endpoint and response "
        "shape are unverified on a live GO instance and will be wired up later. "
        "Use modern_search for document search in the meantime."
    )


def modern_search(
    s: requests.Session,
    *,
    base_url: str,
    term: str,
    case_type_prefix: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page_index: int = 1,
    only_items: bool = True,
) -> list[SearchHit]:
    """Modern (document-library) search (shared-components ``modern_search`` shape).

    Reads results from ``results.Results`` — the same container the confirmed
    personalesag modern search returns (:func:`discovery.case_lookup_by_cpr`).

    TODO(confirm on host): the per-row column keys used to populate
    :class:`SearchHit` (``title`` etc.) — the full row is always in ``raw``.
    """
    date_filter = []
    if start_date and end_date:
        date_filter = [
            {
                "DisplayName": "Sag oprettet",
                "Guid": None,
                "InternalName": "Created",
                "Type": "SPFieldType.DateTime",
                "Value": f"{start_date}T22:00:00.000Z",
                "ToValue": f"{end_date}T22:00:00.000Z",
                "IsTaxId": False,
                "DecodeCrawledName": False,
                "IsOrCondition": None,
                "MappedName": "CCMCreatedCASEPROP",
            }
        ]
    payload = {
        "QueryPageIndex": page_index,
        "PageSize": 500,
        "QueryPhrase": term,
        "QueryType": "DocumentLibrary",
        "TrimToOpenedCases": False,
        "ResultTypeName": "Dokumenter",
        "SearchContentDefinitionEntryType": 0,
        "AdditionalSelectColumns": [],
        "ResultTypeListNameOrType": None,
        "ResultTypeSearchOnlyItems": only_items,
        "ResultTypeQueryFilter": None,
        "CaseQueryFieldCollection": date_filter,
        "QueryFieldCollection": [],
        "CaseTypePrefixes": [case_type_prefix],
        "SortDirection1": 1,
        "ResultViewSortOrder1": 2,
        "ResultViewSortOrder2": 2,
        "QueryScope": 0,
    }
    r = request(s, "POST", endpoints.modern_search(base_url), data=json.dumps(payload))
    results = r.json().get("results", {}).get("Results", [])
    return _hits(results)
