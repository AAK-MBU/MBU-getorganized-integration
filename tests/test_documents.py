"""Tests for the document read surface (documents.py). No network."""

import json

from mbu_getorganized_integration import documents


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
