"""Tests for the transport choke point (_http) and endpoint path builders."""

import pytest
import requests
from requests_ntlm import HttpNtlmAuth

from mbu_getorganized_integration import endpoints
from mbu_getorganized_integration._http import DEFAULT_TIMEOUT, request, session


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class _RecordingSession:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.last = None

    def request(self, method, url, **kwargs):
        self.last = {"method": method, "url": url, "kwargs": kwargs}
        return _FakeResponse(self.status_code)


def test_session_sets_ntlm_auth_and_header():
    s = session("DOMAIN\\user", "secret")
    assert isinstance(s.auth, HttpNtlmAuth)
    assert s.headers["Content-Type"] == "application/json"


def test_request_applies_default_timeout_and_passes_through():
    s = _RecordingSession()
    request(s, "POST", "https://go.example/x", json={"a": 1})
    assert s.last["method"] == "POST"
    assert s.last["kwargs"]["timeout"] == DEFAULT_TIMEOUT
    assert s.last["kwargs"]["json"] == {"a": 1}


def test_request_raises_on_http_error():
    s = _RecordingSession(status_code=500)
    with pytest.raises(requests.HTTPError):
        request(s, "GET", "https://go.example/x")


def test_endpoints_build_expected_paths():
    b = "https://go.example"
    assert endpoints.cases(b) == f"{b}/_goapi/Cases"
    assert endpoints.case_metadata(b, "PER-1") == f"{b}/_goapi/Cases/Metadata/PER-1"
    assert endpoints.find_case(b) == f"{b}/_goapi/cases/findbycaseproperties"
    assert endpoints.contact_lookup(b) == f"{b}/borgersager/_goapi/contacts/readitem"
    assert endpoints.contact_lookup(b, site="andet") == f"{b}/andet/_goapi/contacts/readitem"
    assert endpoints.document_data(b, "42") == f"{b}/_goapi/Documents/Data/42"
    assert endpoints.convert_to_pdf(b, "42", "3.0") == f"{b}/_goapi/Documents/ConvertToPDF/42/3.0"
    assert endpoints.modern_search(b) == f"{b}/_goapi/search/ExecuteModernSearch"
