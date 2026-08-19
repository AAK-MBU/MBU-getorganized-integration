"""GO (Aarhus Kommune ESDH / GetOrganized) client.

GO authenticates via NTLM. This module is the stateless GO API surface:

* ``session()`` — preconfigured NTLM ``requests.Session``
* ``fetch_metadata()`` — DocumentType + UIVersionString from
  ``Documents/Data/{id}``
* ``fetch_parents()`` / ``fetch_children()`` — bilag relationships
* ``download_file()`` — chunked-safe download with the SharePoint-blob
  fallback for files GO's ``DocumentBytes`` endpoint truncates (>~10 MB)
* ``pdf_convert()`` — GO's built-in PDF converter
  (``Documents/ConvertToPDF/{id}/{version}``)
* ``case_lookup_by_cpr()`` / ``list_subcases()`` / ``list_documents_in_case()``
  — the personalemapper document-discovery chain (CPR → personalesag → folders
  → documents), composed by ``find_documents()`` into ``list[GoDocument]``.

The GO primitives are vendored from ``mtm-aarhus/oomtm`` (``oomtm.go``), which
is the production-tested implementation; SharePoint/Nova and the Windows-only
tooling are intentionally not carried over. All functions are stateless; the
caller owns the session lifetime.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET

import requests
from requests_ntlm import HttpNtlmAuth

# session() moved to _http.py and the models to models.py (plan §2); re-exported
# here so `from ...go import session, GoDocument, ...` keeps working until the
# read surface is relocated (step 2).
from ._http import session  # noqa: F401  (re-export)
from .models import CaseDocument, GoDocument, Subcase  # noqa: F401  (re-export)


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
    url = f"{base_url}/_goapi/Documents/Data/{dok_id}"
    r = s.get(url, timeout=60)
    r.raise_for_status()
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
    url = f"{base_url}/_goapi/Documents/Parents/{dok_id}"
    try:
        r = s.get(url, timeout=60)
        r.raise_for_status()
        return [str(it.get("DocumentId") or "") for it in (r.json().get("ParentsData") or [])]
    except requests.RequestException:
        return []


def fetch_children(s: requests.Session, *, base_url: str, dok_id: str) -> list[str]:
    """Return list of child DocumentIds (bilag-relationships)."""
    url = f"{base_url}/_goapi/Documents/Children/{dok_id}"
    try:
        r = s.get(url, timeout=60)
        r.raise_for_status()
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
    """
    # --- Primary path: /DocumentBytes ---
    url = f"{base_url}/_goapi/Documents/DocumentBytes/{dok_id}"
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
    meta_url = f"{base_url}/_goapi/Documents/MetadataWithSystemFields/{dok_id}"
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
    url = f"{base_url}/_goapi/Documents/ConvertToPDF/{dok_id}/{version_ui}"
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
# AktPerson document discovery (personalemapper / PER cases)
#
# A person's documents live as files under the sub-cases ("mapper") of their
# PER personalesag in the *personalemapper* site. Discovery is therefore:
#   CPR  -> personalesag           (case_lookup_by_cpr, GO modern search)
#        -> sub-cases / folders     (list_subcases, CCMParentCase filter)
#        -> documents per folder    (list_documents_in_case, CCMSubID filter)
# find_documents() composes these into the minimal GoDocument contract the
# gather consumer depends on. The endpoint shapes encode the personalemapper
# deployment's conventions (verified against the legacy HentFiler robot).
#
# GoDocument / Subcase / CaseDocument now live in models.py (imported above).
# ---------------------------------------------------------------------------


# This view selects the sub-cases' title + CaseID. Static — the proven
# personalemapper "UndersagerOpen" view.
_SUBCASE_VIEW_XML = (
    '<View Name="{BC9CD5F3-B052-4940-A4A4-1CBA4CFC497E}" Type="HTML" Scope="Recursive" '
    'DisplayName="UndersagerOpen" Url="/personalemapper/Lists/Cases3/UndersagerOpen.aspx" '
    'Level="1" BaseViewID="1" ContentTypeID="0x" ImageUrl="/_layouts/15/images/generic.png?rev=23">'
    '<ViewFields><FieldRef Name="CaseLinkTitle" /><FieldRef Name="Afdeling" />'
    '<FieldRef Name="CaseID" /><FieldRef Name="ContentsLastChangedDate" /></ViewFields>'
    '<Toolbar Type="Standard" /><XslLink Default="TRUE">ccm.xsl</XslLink>'
    '<JSLink>clienttemplates.js</JSLink><RowLimit Paged="TRUE">100</RowLimit>'
    '<Query><OrderBy><FieldRef Name="ID" /></OrderBy></Query></View>'
)


def case_lookup_by_cpr(s: requests.Session, *, base_url: str, cpr: str) -> tuple[str, str]:
    """Resolve a person's personalesag from their CPR.

    Runs GO's modern search scoped to PER cases, matches the result whose title
    carries the formatted CPR, and returns ``(personale_sags_id, akt_id)`` — the
    parent-case id and the ``PER`` web prefix that addresses it.

    Raises ``LookupError`` if no case matches. The CPR is never echoed into the
    error (it is personally identifiable).
    """
    cpr = f"{cpr[:6]}{cpr[-4:]}"  # 10 digits, no dash
    url = f"{base_url}/_goapi/search/ExecuteModernSearch"
    payload = {
        "QueryPageIndex": 1,
        "PageSize": 0,
        "QueryPhrase": cpr,
        "QueryType": "Cases",
        "TrimToOpenedCases": True,
        "ResultTypeInternalName": "6376435f-d715-48ad-8e0c-0a35d85f0d5e",
        "ResultTypeName": "Oversager",
        "SearchContentDefinitionEntryType": 2,
        "ResultViewInternalName": "4b82f943-e2bb-48aa-b2b3-6ab8e7d948d6",
        "AdditionalSelectColumns": [
            "CCMTitle", "CCMEmploymentCode", "CCMContactData", "CCMContactDataCPR",
            "CCMAfdeling", "CCMMedarbejdernummer", "CCMCaseOwner", "CCMParentCase",
            "docicon", "CCMDocID", "CCMCaseID",
        ],
        "ResultTypeListNameOrType": None,
        "ResultTypeSearchOnlyItems": True,
        "ResultTypeQueryFilter": 'AND -ccmparentcase:"P*" -ccmparentcase:"B*" -ccmparentcase:"E*"',
        "CaseQueryFieldCollection": [],
        "QueryFieldCollection": [],
        "CaseTypePrefixes": ["PER"],
        "SortDirection1": 1,
        "ResultViewSortField1": None,
        "ResultViewSortField2": None,
        "ResultViewSortOrder1": 2,
        "ResultViewSortOrder2": 2,
        "QueryScope": 0,
    }
    r = s.post(url, data=json.dumps(payload), timeout=60)
    r.raise_for_status()
    results = r.json()["results"]["Results"]

    cpr_dashed = f"{cpr[:6]}-{cpr[-4:]}"
    case_url = next(
        (item["caseurl"] for item in results if cpr_dashed in item.get("title", "")),
        None,
    )
    if not case_url:
        raise LookupError("No personalesag found for the given CPR.")
    # caseurl: "cases/<AKT-prefix>/<PersonaleSagsID>"
    return case_url.split("/")[-1], case_url.split("/")[1]


def list_subcases(
    s: requests.Session, *, base_url: str, personale_sags_id: str, akt_id: str
) -> list[Subcase]:
    """List the folders (sub-cases) under a personalesag.

    Each folder is itself a GO child case; this resolves the case's list id and
    fetches the children filtered on ``CCMParentCase``. Paginated via
    ``NextHref`` so a personalesag with >100 folders is covered fully.
    """
    detail_url = f"{base_url}/cases/{akt_id}/{personale_sags_id}/_goapi/cases/CaseDetailsInternal"
    r = s.post(detail_url, json={}, timeout=30)
    r.raise_for_status()
    list_id = r.json()["d"].get("ListId")

    base = f"{base_url}/personalemapper/_api/web/lists(guid'{list_id}')/RenderListDataAsStream"
    query = (
        "?CCMRequest=true&RootFolder=%2Fpersonalemapper%2FLists%2FCases3"
        f"&FilterField1=CCMParentCase&FilterValue1={personale_sags_id}%3B%23PER"
    )
    payload = json.dumps(
        {
            "parameters": {
                "AddRequiredFields": True,
                "RenderOptions": 2,
                "ViewXml": _SUBCASE_VIEW_XML,
            }
        }
    )
    headers = {"Content-Type": "application/json"}

    url = base + query
    subcases: list[Subcase] = []
    while True:
        r = s.post(url, headers=headers, data=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        for row in data.get("Row") or []:
            subcases.append(Subcase(case_id=row["CaseID"], title=row.get("Title", "")))
        next_href = data.get("NextHref")
        if not next_href:
            break
        url = base + next_href.replace("?", "&", 1)
    return subcases


def list_documents_in_case(
    s: requests.Session, *, base_url: str, sags_id: str
) -> list[CaseDocument]:
    """List every document in a single (sub-)case, across the journalised and
    not-journalised views.

    Mechanics (as in the legacy HentFiler robot): the case web path
    (``ows_CaseUrl``) is read from metadata, ``GetLeftMenuCounter`` yields the
    two view ids, and ``RenderListDataAsStream`` is listed per view, filtered on
    ``CCMSubID`` and paginated via ``NextHref``. Documents appearing in both
    views (same DocID) are de-duplicated.
    """
    meta = s.get(f"{base_url}/_goapi/Cases/Metadata/{sags_id}", timeout=60)
    meta.raise_for_status()
    xdoc = ET.fromstring(meta.json()["Metadata"])
    sags_url = xdoc.attrib.get("ows_CaseUrl")  # e.g. "cases/PER55/PER-2026-000123"
    akt = sags_url.split("cases/")[1].split("/")[0]
    endelse = sags_id.rsplit("-", 1)[-1]
    # Documents live in the *parent* personalesag's "Dokumenter" library; each
    # sub-case is a folder (``endelse``, e.g. "001") within it. So @listUrl /
    # RootFolder address the parent case id — the endelse is stripped off — with
    # hyphens as %2D (matching the legacy HentFiler robot exactly). Using the
    # full sub-case id here yields a non-existent list (FileNotFoundException).
    encoded_sags_id = sags_id.rsplit("-", 1)[0].replace("-", "%2D")

    counter = s.get(
        f"{base_url}/{sags_url}/_goapi/Administration/GetLeftMenuCounter/{endelse}", timeout=60
    )
    counter.raise_for_status()
    views = {it.get("ViewName"): it.get("ViewId") for it in counter.json()}
    view_ids = [views.get("IkkeJournaliseret.aspx"), views.get("Journaliseret.aspx")]

    list_url = f"%27%2Fcases%2F{akt}%2F{encoded_sags_id}%2FDokumenter%27"
    root_folder = f"%2Fcases%2F{akt}%2F{encoded_sags_id}%2FDokumenter%2F{endelse}"
    base = f"{base_url}/{sags_url}/_api/web/GetList(@listUrl)/RenderListDataAsStream"

    docs: list[CaseDocument] = []
    seen: set[str] = set()
    for view_id in view_ids:
        if not view_id:
            continue
        url = (
            f"{base}?@listUrl={list_url}&View={view_id}"
            f"&RootFolder={root_folder}&FilterField1=CCMSubID&FilterValue1={endelse}"
        )
        while True:
            r = s.post(url, timeout=120)
            r.raise_for_status()
            data = r.json()
            for item in data.get("Row", []):
                dok_id = str(item.get("DocID") or "")
                if not dok_id or dok_id in seen:
                    continue
                seen.add(dok_id)
                docs.append(
                    CaseDocument(
                        dok_id=dok_id,
                        akt_id=str(item.get("CaseRecordNumber") or ""),
                        title=item.get("Title") or item.get("FileLeafRef.Name", ""),
                        file_ref=item.get("FileRef", ""),
                        file_name=item.get("FileLeafRef.Name", ""),
                        dato=item.get("Dato"),
                        korrespondance=item.get("Korrespondance"),
                        raw=item,
                    )
                )
            next_href = data.get("NextHref")
            if not next_href:
                break
            url = f"{base}?@listUrl={list_url}{next_href.replace('?', '&')}"
    return docs


def find_documents(
    s: requests.Session,
    *,
    base_url: str,
    cpr: str,
    tjenestenummer: str | None = None,
) -> list[GoDocument]:
    """Resolve the documents belonging to an AktPerson in GO.

    Implemented for the personalemapper deployment: looks up the person's PER
    personalesag by CPR, enumerates its folders (sub-cases), and lists the
    documents in each — returning one :class:`GoDocument` per document across
    all folders (downshifted to the minimal ``dok_id`` / ``name`` / ``ext`` the
    gather consumer needs; the richer per-document fields are available via
    :func:`list_documents_in_case`).

    ``tjenestenummer`` is accepted for API stability but not yet used to narrow
    the result — the personalesag is keyed by CPR. Raises ``LookupError`` if no
    personalesag matches the CPR.
    """
    personale_sags_id, akt_id = case_lookup_by_cpr(s, base_url=base_url, cpr=cpr)
    docs: list[GoDocument] = []
    seen: set[str] = set()
    for folder in list_subcases(
        s, base_url=base_url, personale_sags_id=personale_sags_id, akt_id=akt_id
    ):
        for d in list_documents_in_case(s, base_url=base_url, sags_id=folder.case_id):
            if d.dok_id in seen:
                continue
            seen.add(d.dok_id)
            docs.append(GoDocument(dok_id=d.dok_id, name=d.name, ext=d.ext, raw=d.raw))
    return docs
