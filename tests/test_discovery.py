"""Tests for the personalesag discovery chain (discovery.py). No network."""

import pytest

from mbu_getorganized_integration import discovery
from mbu_getorganized_integration.models import GoDocument


def _metadata_resp(make_response, case_url):
    xml = f'<z:row xmlns:z="#RowsetSchema" ows_CaseUrl="{case_url}" />'
    return make_response(json_data={"Metadata": xml})


def test_case_lookup_by_cpr_matches_on_dashed_cpr(fake_session, make_response):
    results = {"results": {"Results": [
        {"title": "Anden person 010101-0000", "caseurl": "cases/PER9/PER-2026-000111"},
        {"title": "Navn Navnesen 140382-0209", "caseurl": "cases/PER55/PER-2026-000123"},
    ]}}
    s = fake_session({"/ExecuteModernSearch": make_response(json_data=results)})

    sags_id, akt = discovery.case_lookup_by_cpr(s, base_url="https://go.example", cpr="1403820209")

    assert sags_id == "PER-2026-000123"
    assert akt == "PER55"


def test_case_lookup_by_cpr_raises_without_leaking_cpr(fake_session, make_response):
    empty = make_response(json_data={"results": {"Results": []}})
    s = fake_session({"/ExecuteModernSearch": empty})
    with pytest.raises(LookupError) as exc:
        discovery.case_lookup_by_cpr(s, base_url="https://go.example", cpr="1403820209")
    assert "1403820209" not in str(exc.value)  # CPR must not leak into errors


def test_list_subcases_parses_rows(fake_session, make_response):
    detail = make_response(json_data={"d": {"ListId": "GUID-1"}})
    stream = make_response(json_data={"Row": [
        {"CaseID": "PER-2026-000200", "Title": "Ansættelse"},
        {"CaseID": "PER-2026-000201", "Title": "Løn"},
    ]})
    s = fake_session({"/CaseDetailsInternal": detail, "lists(guid'": stream})

    folders = discovery.list_subcases(s, base_url="https://go.example",
                                      personale_sags_id="PER-2026-000123", akt_id="PER55")

    assert [(f.case_id, f.title) for f in folders] == [
        ("PER-2026-000200", "Ansættelse"),
        ("PER-2026-000201", "Løn"),
    ]


def test_list_documents_in_case_paginates_and_dedupes(fake_session, make_response):
    counter = make_response(json_data=[
        {"ViewName": "IkkeJournaliseret.aspx", "ViewId": "V1"},
        {"ViewName": "Journaliseret.aspx", "ViewId": "V2"},
    ])
    # V1 returns two pages (page-2 URL is rebuilt from NextHref and carries no
    # View=), then V2 repeats DocID 1001 — which must be de-duped. All three hit
    # the GetList(@listUrl) base, so route them as one ordered queue.
    page1 = make_response(json_data={
        "Row": [{"DocID": 1001, "CaseRecordNumber": 1, "Title": "Brev",
                 "FileRef": "/cases/x/a.pdf", "FileLeafRef.Name": "a.pdf"}],
        "NextHref": "?Paged=TRUE&p_ID=1001",
    })
    page2 = make_response(json_data={
        "Row": [{"DocID": 1002, "CaseRecordNumber": 2, "Title": "Bilag",
                 "FileRef": "/cases/x/b.docx", "FileLeafRef.Name": "b.docx"}],
    })
    v2 = make_response(json_data={
        "Row": [{"DocID": 1001, "CaseRecordNumber": 1, "Title": "Brev (dublet)",
                 "FileRef": "/cases/x/a.pdf", "FileLeafRef.Name": "a.pdf"}],
    })
    s = fake_session({
        "/_goapi/Cases/Metadata/": _metadata_resp(make_response, "cases/PER55/PER-2026-000123-001"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": [page1, page2, v2],
    })

    docs = discovery.list_documents_in_case(s, base_url="https://go.example", sags_id="PER-2026-000123-001")

    assert [d.dok_id for d in docs] == ["1001", "1002"]  # 1001 not duplicated
    assert docs[0].name == "a.pdf" and docs[0].ext == "pdf"
    assert docs[1].ext == "docx"

    # The @listUrl addresses the *parent* case's Dokumenter library (endelse
    # "-001" stripped, hyphens as %2D); the "001" survives only as the folder in
    # RootFolder + the CCMSubID filter. Regression guard for the FileNotFound bug.
    render_calls = [c for c in s.calls if "GetList(@listUrl)" in c]
    assert "%27%2Fcases%2FPER55%2FPER%2D2026%2D000123%2FDokumenter%27" in render_calls[0]
    assert "PER%2D2026%2D000123%2D001%2FDokumenter" not in render_calls[0]
    assert "RootFolder=%2Fcases%2FPER55%2FPER%2D2026%2D000123%2FDokumenter%2F001" in render_calls[0]
    assert "FilterField1=CCMSubID&FilterValue1=001" in render_calls[0]


def test_list_documents_in_case_skips_missing_view(fake_session, make_response):
    counter = make_response(json_data=[{"ViewName": "Journaliseret.aspx", "ViewId": "V2"}])
    s = fake_session({
        "/_goapi/Cases/Metadata/": _metadata_resp(make_response, "cases/PER55/PER-2026-000123"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": make_response(json_data={"Row": [
            {"DocID": 7, "CaseRecordNumber": 1, "Title": "X", "FileLeafRef.Name": "x.pdf"}
        ]}),
    })

    docs = discovery.list_documents_in_case(s, base_url="https://go.example", sags_id="PER-2026-000123")
    assert [d.dok_id for d in docs] == ["7"]


def test_find_documents_composes_to_godocuments(fake_session, make_response):
    search = {"results": {"Results": [
        {"title": "Navn Navnesen 140382-0209", "caseurl": "cases/PER55/PER-2026-000123"},
    ]}}
    detail = make_response(json_data={"d": {"ListId": "GUID-1"}})
    subcases = make_response(json_data={"Row": [{"CaseID": "PER-2026-000200", "Title": "Løn"}]})
    counter = make_response(json_data=[{"ViewName": "Journaliseret.aspx", "ViewId": "V2"}])
    docs_stream = make_response(json_data={"Row": [
        {"DocID": 55, "CaseRecordNumber": 1, "Title": "Lønseddel", "FileLeafRef.Name": "loen.pdf"}
    ]})
    s = fake_session({
        "/ExecuteModernSearch": make_response(json_data=search),
        "/CaseDetailsInternal": detail,
        "lists(guid'": subcases,                       # sub-case listing
        "/_goapi/Cases/Metadata/": _metadata_resp(make_response, "cases/PER55/PER-2026-000200"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": docs_stream,  # document listing
    })

    found = discovery.find_documents(s, base_url="https://go.example", cpr="1403820209")

    assert len(found) == 1
    assert isinstance(found[0], GoDocument)
    assert (found[0].dok_id, found[0].name, found[0].ext) == ("55", "loen.pdf", "pdf")
