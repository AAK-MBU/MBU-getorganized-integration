"""Tests for the package's public API surface (__init__ re-exports)."""

import mbu_getorganized_integration as m


def test_session_is_reexported_from_private_http():
    # The public name is the exact symbol from the private module, not a copy.
    assert m.session is m._http.session


def test_session_is_in_dunder_all():
    assert "session" in m.__all__


def test_session_is_callable():
    assert callable(m.session)
