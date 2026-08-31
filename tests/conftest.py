"""Shared test doubles for the GO HTTP surface.

The GO client is stateless — every function takes a ``requests.Session`` — so
tests inject a fake session that replays canned responses keyed by URL
substring. Exposed as fixtures (``fake_session`` / ``make_response``) returning
the classes, so each test builds its own routing table.
"""

import json

import pytest
import requests


class FakeResponse:
    def __init__(self, *, text="", content=b"", status_code=200, json_data=None):
        self.text = text
        self.content = content
        self.status_code = status_code
        self._json = json_data

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


def _decode(data):
    """Best-effort decode of a pre-serialized request body; ``None`` if it is
    absent or not JSON (e.g. a raw file upload)."""
    if not isinstance(data, (str, bytes)):
        return None
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None


class FakeSession:
    """Replays canned responses keyed by URL substring, shared by
    get/post/request. A route value may be a single response or a list returned
    in order (for paginated calls)."""

    def __init__(self, routes):
        self.routes = {k: (v if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls = []
        self.bodies = []          # decoded body of each call, in order
        self.last_json = None     # decoded body of the most recent call
        self.auth = None

    def _resolve(self, url, **kwargs):
        self.calls.append(url)
        self.last_json = kwargs.get("json")
        if self.last_json is None:
            # Some calls pre-serialize the payload into data= instead of json=;
            # decode it so assertions can reach the body either way.
            self.last_json = _decode(kwargs.get("data"))
        self.bodies.append(self.last_json)
        for needle, responses in self.routes.items():
            if needle in url:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        return FakeResponse(status_code=404)

    def get(self, url, **kwargs):
        return self._resolve(url, **kwargs)

    def post(self, url, **kwargs):
        return self._resolve(url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._resolve(url, **kwargs)


@pytest.fixture
def make_response():
    return FakeResponse


@pytest.fixture
def fake_session():
    return FakeSession
