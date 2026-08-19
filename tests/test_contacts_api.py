"""Tests for contact lookup (contacts.py) and health check (api.py). No network."""

from mbu_getorganized_integration import api, contacts
from mbu_getorganized_integration.models import ContactResult


def test_contact_lookup_returns_typed_result(fake_session, make_response):
    resp = make_response(json_data={"FullName": "Navn Navnesen", "ID": 4711})
    s = fake_session({"/contacts/readitem": resp})

    res = contacts.contact_lookup(s, base_url="https://go.example", ssn="1403820209")

    assert isinstance(res, ContactResult)
    assert res.full_name == "Navn Navnesen"
    assert res.id == "4711"  # coerced to str
    assert s.calls[0] == "https://go.example/borgersager/_goapi/contacts/readitem"


def test_contact_lookup_site_override(fake_session, make_response):
    s = fake_session({"/contacts/readitem": make_response(json_data={"FullName": "N", "ID": 1})})
    contacts.contact_lookup(s, base_url="https://go.example", ssn="x", site="andet")
    assert s.calls[0] == "https://go.example/andet/_goapi/contacts/readitem"


def test_contact_lookup_empty_when_not_found(fake_session, make_response):
    s = fake_session({"/contacts/readitem": make_response(json_data={})})
    res = contacts.contact_lookup(s, base_url="https://go.example", ssn="x")
    assert res.full_name == "" and res.id == ""


def test_health_check_true_when_ok(fake_session, make_response):
    s = fake_session({"https://go.example": make_response(status_code=200)})
    assert api.health_check(s, base_url="https://go.example") is True


def test_health_check_false_when_down(fake_session, make_response):
    s = fake_session({"https://go.example": make_response(status_code=503)})
    assert api.health_check(s, base_url="https://go.example") is False
