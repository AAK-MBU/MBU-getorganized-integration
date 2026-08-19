"""Tests for the document read + write surface (documents.py). No network."""

import base64
import json

from mbu_getorganized_integration import documents
from mbu_getorganized_integration.models import SearchHit, UploadResult


def test_fetch_metadata_parses_ext_and_version(fake_session, make_response):
    item_props = 'ows_File_x0020_Type="docx" ows__UIVersionString="3.0"'
    resp = make_response(text=json.dumps({"ItemProperties": item_props}))
    s = fake_session({"/Documents/Data/": resp})

    meta = documents.fetch_metadata(s, base_url="https://go.example", dok_id="42")

    assert meta["ext"] == "docx"
    assert meta["version_ui"] == "3.0"
    assert "/_goapi/Documents/Data/42" in s.calls[0]


def test_fetch_metadata_missing_props_returns_none_fields(fake_session, make_response):
    resp = make_response(text=json.dumps({"ItemProperties": ""}))
    s = fake_session({"/Documents/Data/": resp})

    meta = documents.fetch_metadata(s, base_url="https://go.example", dok_id="42")

    assert meta["ext"] is None
    assert meta["version_ui"] is None


def test_fetch_children_swallows_errors(fake_session, make_response):
    s = fake_session({"/Documents/Children/": make_response(status_code=500)})
    assert documents.fetch_children(s, base_url="https://go.example", dok_id="42") == []


def test_download_file_writes_bytes(tmp_path, fake_session, make_response):
    payload = b"%PDF-1.4 hello"
    s = fake_session({"/Documents/DocumentBytes/": make_response(content=payload)})
    dest = tmp_path / "out.pdf"

    documents.download_file(s, base_url="https://go.example", dok_id="42", local_path=str(dest))

    assert dest.read_bytes() == payload


# ----- write surface ---------------------------------------------------------


class _RecordingSession:
    """Captures the request kwargs so body assertions are possible."""

    def __init__(self, response):
        self.response = response
        self.last = None
        self.auth = None

    def request(self, method, url, **kwargs):
        self.last = {"method": method, "url": url, "kwargs": kwargs}
        return self.response


def test_upload_document_base64_encodes_and_returns_id(make_response):
    resp = make_response(json_data={"DocId": 9001})
    s = _RecordingSession(resp)

    res = documents.upload_document(
        s, base_url="https://go.example", case_id="BOR-2026-000001",
        filename="brev.pdf", data=b"%PDF-1.4", metadata="<z:row/>",
    )

    assert isinstance(res, UploadResult)
    assert res.document_id == 9001
    body = s.last["kwargs"]["json"]
    assert body["Bytes"] == base64.b64encode(b"%PDF-1.4").decode("ascii")
    assert body["FileName"] == "brev.pdf"
    assert s.last["url"].endswith("/_goapi/Documents/AddToCase")


def test_upload_document_id_fallback_keys(make_response):
    s = _RecordingSession(make_response(json_data={"DocumentId": 7}))
    res = documents.upload_document(
        s, base_url="https://go.example", case_id="c", filename="f", data=b"x"
    )
    assert res.document_id == 7


def test_mark_as_case_record_payload(make_response):
    s = _RecordingSession(make_response(json_data={}))
    documents.mark_as_case_record(s, base_url="https://go.example", document_ids=[1, 2])
    assert s.last["kwargs"]["json"] == {"DocumentIds": [1, 2]}
    assert s.last["url"].endswith("/MarkMultipleAsCaseRecord/ByDocumentId")


def test_finalize_documents_payload(make_response):
    s = _RecordingSession(make_response(json_data={}))
    documents.finalize_documents(s, base_url="https://go.example", document_ids=[3])
    assert s.last["kwargs"]["json"] == {"DocumentIds": [3], "ShouldCloseOpenTasks": False}


def test_modern_search_maps_results(fake_session, make_response):
    resp = make_response(json_data={"results": {"Results": [
        {"title": "Brev", "CCMCaseID": "PER-1", "CCMDocID": "55"},
    ]}})
    s = fake_session({"/ExecuteModernSearch": resp})

    hits = documents.modern_search(s, base_url="https://go.example", term="x", case_type_prefix="PER")

    assert len(hits) == 1 and isinstance(hits[0], SearchHit)
    assert hits[0].title == "Brev" and hits[0].case_id == "PER-1" and hits[0].document_id == "55"


def test_search_documents_reads_rows(fake_session, make_response):
    s = fake_session({"/Documents/Search": make_response(json_data={"Rows": [{"Title": "Doc"}]})})
    hits = documents.search_documents(s, base_url="https://go.example", term="q")
    assert [h.title for h in hits] == ["Doc"]
