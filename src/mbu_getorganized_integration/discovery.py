"""AktPerson document discovery — the personalemapper / PER chain (plan §2).

A person's documents live as files under the sub-cases ("mapper") of their PER
personalesag in the *personalemapper* site. Discovery is therefore:

    CPR  -> personalesag           (case_lookup_by_cpr, GO modern search)
         -> sub-cases / folders     (list_subcases, CCMParentCase filter)
         -> documents per folder    (list_documents_in_case, CCMSubID filter)

:func:`find_documents` composes these into the minimal :class:`GoDocument`
contract the gather consumer depends on. The RenderListDataAsStream /
GetLeftMenuCounter URLs encode the personalemapper deployment's conventions
(verified against the legacy HentFiler robot) and are built inline here — they
are dynamic, site-specific, and not the stable ``/_goapi`` paths that
:mod:`endpoints` centralizes. The plain ``/_goapi`` calls (modern search, case
metadata) do use :mod:`endpoints` and go through :func:`_http.request`.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import requests

from . import endpoints
from ._http import request
from .models import CaseDocument, GoDocument, Subcase


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
    r = request(s, "POST", endpoints.modern_search(base_url), data=json.dumps(payload))
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
    r = request(s, "POST", detail_url, json={}, timeout=30)
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
        r = request(s, "POST", url, headers=headers, data=payload)
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
    meta = request(s, "GET", endpoints.case_metadata(base_url, sags_id))
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

    counter = request(
        s, "GET", f"{base_url}/{sags_url}/_goapi/Administration/GetLeftMenuCounter/{endelse}"
    )
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
            r = request(s, "POST", url, timeout=120)
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
