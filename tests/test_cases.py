"""Tests for the case write/read surface (cases.py). No network."""

import json

import pytest
import requests

from mbu_getorganized_integration import cases, payloads
from mbu_getorganized_integration.models import Case


def test_create_case_posts_case_data_and_returns_case(fake_session, make_response):
    s = fake_session({"/_goapi/Cases": make_response(json_data={"CaseID": "PER-2026-000123"})})
    xml = payloads.case_metadata_xml(
        case_type_prefix="PER", case_category="Personalesag", title="T",
        case_owner_id="1", case_owner_name="O", case_profile_id="2", case_profile_name="P",
    )

    case = cases.create_case(s, base_url="https://go.example", case_type_prefix="PER", metadata_xml=xml)

    assert isinstance(case, Case)
    assert case.id == "PER-2026-000123"
    assert s.calls[0] == "https://go.example/_goapi/Cases"


def test_find_case_maps_casesinfo_rows(fake_session, make_response):
    resp = make_response(json_data={"CasesInfo": [
        {"CaseID": "BOR-2026-000001", "Title": "A"},
        {"CaseID": "BOR-2026-000002", "Title": "B"},
    ]})
    s = fake_session({"/FindByCaseProperties": resp})
    search = payloads.search_citizen_folder_data("BOR", "Navn", "42", "1403820209")

    found = cases.find_case(s, base_url="https://go.example", search_data=search)

    assert [c.id for c in found] == ["BOR-2026-000001", "BOR-2026-000002"]


def test_find_case_empty_when_no_casesinfo(fake_session, make_response):
    s = fake_session({"/FindByCaseProperties": make_response(json_data={})})
    assert cases.find_case(s, base_url="https://go.example", search_data={}) == []


def test_open_case_sends_reason(fake_session, make_response):
    s = fake_session({"/OpenCase": make_response(json_data={})})
    cases.open_case(s, base_url="https://go.example", case_id="EMN-1", reason="genåbn")
    assert s.calls[0].endswith("/_goapi/Cases/OpenCase")


def test_close_case_hits_close_endpoint(fake_session, make_response):
    s = fake_session({"/CloseCase": make_response(json_data={})})
    cases.close_case(s, base_url="https://go.example", case_id="EMN-1")
    assert s.calls[0].endswith("/_goapi/Cases/CloseCase")


def test_get_case_metadata_returns_raw(fake_session, make_response):
    s = fake_session({"/_goapi/Cases/Metadata/": make_response(json_data={"Metadata": "<z:row/>"})})
    meta = cases.get_case_metadata(s, base_url="https://go.example", case_id="PER-1")
    assert meta == {"Metadata": "<z:row/>"}


def test_create_case_raises_on_http_error(fake_session, make_response):
    s = fake_session({"/_goapi/Cases": make_response(status_code=500)})
    with pytest.raises(requests.HTTPError):
        cases.create_case(s, base_url="https://go.example", case_type_prefix="PER", metadata_xml="<z:row/>")


def test_case_modern_search_maps_results(fake_session, make_response):
    s = fake_session({"/ExecuteModernSearch": make_response(json_data={"results": {"Results": [
        {"title": "Sag A", "CCMCaseID": "BOR-2026-000001"},
        {"title": "Sag B", "caseurl": "cases/BOR/BOR-2026-000002"},
    ]}})})
    hits = cases.case_modern_search(
        s, base_url="https://go.example", term="Sag", case_type_prefix="BOR"
    )
    assert [h.case_id for h in hits] == ["BOR-2026-000001", "BOR-2026-000002"]
    assert [h.title for h in hits] == ["Sag A", "Sag B"]
    assert hits[0].raw["CCMCaseID"] == "BOR-2026-000001"


def test_case_modern_search_empty_when_no_results(fake_session, make_response):
    s = fake_session({"/ExecuteModernSearch": make_response(json_data={})})
    assert cases.case_modern_search(
        s, base_url="https://go.example", term="x", case_type_prefix="BOR"
    ) == []
