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
    """Records GET calls and replays canned responses keyed by URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.auth = None

    def get(self, url, **kwargs):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return _FakeResponse(status_code=404)


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


def test_find_documents_is_a_stub():
    s = _FakeSession({})
    with pytest.raises(NotImplementedError):
        go.find_documents(s, base_url="https://go.example", cpr="1403820209")
