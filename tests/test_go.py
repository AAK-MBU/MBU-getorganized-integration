"""Tests for the GO client. GO's HTTP is mocked with a tiny fake session so the
suite runs with no network and no GO instance."""

import json

import pytest
import requests
from requests_ntlm import HttpNtlmAuth

from mbu_getorganized_integration import go


class _FakeResponse:
    def __init__(self, *, text="", content=b"", status_code=200, json_data=None):
        self.text = text
        self.content = content
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


class _FakeSession:
    """Replays canned responses keyed by URL substring, shared by get/post. A
    route value may be a single response or a list returned in order (for
    paginated calls)."""

    def __init__(self, routes):
        self.routes = {k: (v if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls = []
        self.auth = None

    def _resolve(self, url):
        self.calls.append(url)
        for needle, responses in self.routes.items():
            if needle in url:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        return _FakeResponse(status_code=404)

    def get(self, url, **kwargs):
        return self._resolve(url)

    def post(self, url, **kwargs):
        return self._resolve(url)


def test_session_sets_ntlm_auth():
    s = go.session("DOMAIN\\user", "secret")
    assert isinstance(s.auth, HttpNtlmAuth)
    assert s.headers["Content-Type"] == "application/json"


def test_fetch_metadata_parses_ext_and_version():
    item_props = 'ows_File_x0020_Type="docx" ows__UIVersionString="3.0"'
    resp = _FakeResponse(text=json.dumps({"ItemProperties": item_props}))
    s = _FakeSession({"/Documents/Data/": resp})

    meta = go.fetch_metadata(s, base_url="https://go.example", dok_id="42")

    assert meta["ext"] == "docx"
    assert meta["version_ui"] == "3.0"
    assert "/_goapi/Documents/Data/42" in s.calls[0]


def test_fetch_metadata_missing_props_returns_none_fields():
    resp = _FakeResponse(text=json.dumps({"ItemProperties": ""}))
    s = _FakeSession({"/Documents/Data/": resp})

    meta = go.fetch_metadata(s, base_url="https://go.example", dok_id="42")

    assert meta["ext"] is None
    assert meta["version_ui"] is None


def test_fetch_children_swallows_errors():
    s = _FakeSession({"/Documents/Children/": _FakeResponse(status_code=500)})
    assert go.fetch_children(s, base_url="https://go.example", dok_id="42") == []


def test_download_file_writes_bytes(tmp_path):
    payload = b"%PDF-1.4 hello"
    s = _FakeSession({"/Documents/DocumentBytes/": _FakeResponse(content=payload)})
    dest = tmp_path / "out.pdf"

    go.download_file(s, base_url="https://go.example", dok_id="42", local_path=str(dest))

    assert dest.read_bytes() == payload


# ----- AktPerson document discovery (personalemapper) ------------------------


def _metadata_resp(case_url):
    xml = f'<z:row xmlns:z="#RowsetSchema" ows_CaseUrl="{case_url}" />'
    return _FakeResponse(json_data={"Metadata": xml})


def test_case_lookup_by_cpr_matches_on_dashed_cpr():
    results = {"results": {"Results": [
        {"title": "Anden person 010101-0000", "caseurl": "cases/PER9/PER-2026-000111"},
        {"title": "Navn Navnesen 140382-0209", "caseurl": "cases/PER55/PER-2026-000123"},
    ]}}
    s = _FakeSession({"/ExecuteModernSearch": _FakeResponse(json_data=results)})

    sags_id, akt = go.case_lookup_by_cpr(s, base_url="https://go.example", cpr="1403820209")

    assert sags_id == "PER-2026-000123"
    assert akt == "PER55"


def test_case_lookup_by_cpr_raises_without_leaking_cpr():
    empty = _FakeResponse(json_data={"results": {"Results": []}})
    s = _FakeSession({"/ExecuteModernSearch": empty})
    with pytest.raises(LookupError) as exc:
        go.case_lookup_by_cpr(s, base_url="https://go.example", cpr="1403820209")
    assert "1403820209" not in str(exc.value)  # CPR must not leak into errors


def test_list_subcases_parses_rows():
    detail = _FakeResponse(json_data={"d": {"ListId": "GUID-1"}})
    stream = _FakeResponse(json_data={"Row": [
        {"CaseID": "PER-2026-000200", "Title": "Ansættelse"},
        {"CaseID": "PER-2026-000201", "Title": "Løn"},
    ]})
    s = _FakeSession({"/CaseDetailsInternal": detail, "lists(guid'": stream})

    folders = go.list_subcases(s, base_url="https://go.example",
                               personale_sags_id="PER-2026-000123", akt_id="PER55")

    assert [(f.case_id, f.title) for f in folders] == [
        ("PER-2026-000200", "Ansættelse"),
        ("PER-2026-000201", "Løn"),
    ]


def test_list_documents_in_case_paginates_and_dedupes():
    counter = _FakeResponse(json_data=[
        {"ViewName": "IkkeJournaliseret.aspx", "ViewId": "V1"},
        {"ViewName": "Journaliseret.aspx", "ViewId": "V2"},
    ])
    # V1 returns two pages (page-2 URL is rebuilt from NextHref and carries no
    # View=), then V2 repeats DocID 1001 — which must be de-duped. All three hit
    # the GetList(@listUrl) base, so route them as one ordered queue.
    page1 = _FakeResponse(json_data={
        "Row": [{"DocID": 1001, "CaseRecordNumber": 1, "Title": "Brev",
                 "FileRef": "/cases/x/a.pdf", "FileLeafRef.Name": "a.pdf"}],
        "NextHref": "?Paged=TRUE&p_ID=1001",
    })
    page2 = _FakeResponse(json_data={
        "Row": [{"DocID": 1002, "CaseRecordNumber": 2, "Title": "Bilag",
                 "FileRef": "/cases/x/b.docx", "FileLeafRef.Name": "b.docx"}],
    })
    v2 = _FakeResponse(json_data={
        "Row": [{"DocID": 1001, "CaseRecordNumber": 1, "Title": "Brev (dublet)",
                 "FileRef": "/cases/x/a.pdf", "FileLeafRef.Name": "a.pdf"}],
    })
    s = _FakeSession({
        "/_goapi/Cases/Metadata/": _metadata_resp("cases/PER55/PER-2026-000123"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": [page1, page2, v2],
    })

    docs = go.list_documents_in_case(s, base_url="https://go.example", sags_id="PER-2026-000123")

    assert [d.dok_id for d in docs] == ["1001", "1002"]  # 1001 not duplicated
    assert docs[0].name == "a.pdf" and docs[0].ext == "pdf"
    assert docs[1].ext == "docx"


def test_list_documents_in_case_skips_missing_view():
    counter = _FakeResponse(json_data=[{"ViewName": "Journaliseret.aspx", "ViewId": "V2"}])
    s = _FakeSession({
        "/_goapi/Cases/Metadata/": _metadata_resp("cases/PER55/PER-2026-000123"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": _FakeResponse(json_data={"Row": [
            {"DocID": 7, "CaseRecordNumber": 1, "Title": "X", "FileLeafRef.Name": "x.pdf"}
        ]}),
    })

    docs = go.list_documents_in_case(s, base_url="https://go.example", sags_id="PER-2026-000123")
    assert [d.dok_id for d in docs] == ["7"]


def test_find_documents_composes_to_godocuments():
    search = {"results": {"Results": [
        {"title": "Navn Navnesen 140382-0209", "caseurl": "cases/PER55/PER-2026-000123"},
    ]}}
    detail = _FakeResponse(json_data={"d": {"ListId": "GUID-1"}})
    subcases = _FakeResponse(json_data={"Row": [{"CaseID": "PER-2026-000200", "Title": "Løn"}]})
    counter = _FakeResponse(json_data=[{"ViewName": "Journaliseret.aspx", "ViewId": "V2"}])
    docs_stream = _FakeResponse(json_data={"Row": [
        {"DocID": 55, "CaseRecordNumber": 1, "Title": "Lønseddel", "FileLeafRef.Name": "loen.pdf"}
    ]})
    s = _FakeSession({
        "/ExecuteModernSearch": _FakeResponse(json_data=search),
        "/CaseDetailsInternal": detail,
        "lists(guid'": subcases,                       # sub-case listing
        "/_goapi/Cases/Metadata/": _metadata_resp("cases/PER55/PER-2026-000200"),
        "/GetLeftMenuCounter/": counter,
        "GetList(@listUrl)/RenderListDataAsStream": docs_stream,  # document listing
    })

    found = go.find_documents(s, base_url="https://go.example", cpr="1403820209")

    assert len(found) == 1
    assert isinstance(found[0], go.GoDocument)
    assert (found[0].dok_id, found[0].name, found[0].ext) == ("55", "loen.pdf", "pdf")
