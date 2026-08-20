#!/usr/bin/env python3
"""Manual live smoke tests against a real GetOrganized (GO) instance.

This file is NOT part of the automated pytest suite (it is not a ``test_*.py``
file and lives outside ``tests/``, so pytest never collects it). It exists so a
human can exercise each ``GoClient`` method against a live GO.

All the values the tests need — credentials, SSNs, case ids, document ids — live
in ``manual_tests/.env`` (gitignored), NOT in this file. This file is tracked and
value-free; ``manual_tests/.env.example`` is the tracked template.

--------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------
1. Create your ``.env`` from the template and fill in what you need::

       cp manual_tests/.env.example manual_tests/.env
       $EDITOR manual_tests/.env

   Only the variables for the test(s) you want to run need values; leave the
   rest blank. Real environment variables (e.g. an exported ``go_api_password``)
   override the ``.env`` file for the same key.

2. Run one, several, or all tests::

       python manual_tests/live_go.py --list                 # show all tests
       python manual_tests/live_go.py contact_lookup          # run one
       python manual_tests/live_go.py find_citizen_folder open_case
       python manual_tests/live_go.py --all                   # run everything filled in

   A test whose required variables are still blank fails fast with a clear
   message telling you which variable to set — it will not silently hit GO with
   empty values.

WARNING: the write tests (create_case, upload_document, journalize/finalize,
open/close) mutate the live GO instance. Only run them against a test case you
own. Read tests (health_check, contact_lookup, find_*, *_metadata, download,
parents/children, search) are safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Import the installed package. Run from the repo root (or `pip install -e .`).
from mbu_getorganized_integration import GoClient  # noqa: E402


# ==============================================================================
# Config — loaded from manual_tests/.env (see .env.example). No values live in
# this file; the real environment overrides the .env for the same key.
# ==============================================================================

_ENV_PATH = Path(__file__).with_name(".env")


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``.env`` reader: ``KEY=value`` per line, ``#`` comments, optional
    surrounding quotes stripped. No dependency on python-dotenv."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        data[key] = val
    return data


_FILE_ENV = _load_dotenv(_ENV_PATH)

# Make GoClient.from_env() work off the .env too: seed the process environment
# with any credential the .env supplies and the real environment does not.
for _k in ("go_api_endpoint", "go_api_username", "go_api_password", "LIBREOFFICE_PATH"):
    if not os.environ.get(_k) and _FILE_ENV.get(_k):
        os.environ[_k] = _FILE_ENV[_k]


def _cfg(key: str, default: str = "") -> str:
    """A config string: real env wins over .env; empty falls back to default."""
    val = os.environ.get(key)
    if not val:
        val = _FILE_ENV.get(key, "")
    return val if val != "" else default


def _opt(key: str) -> str | None:
    """Optional string — empty becomes None."""
    return _cfg(key) or None


def _json(key: str, default):
    """A structured value stored as JSON in the .env (empty -> default)."""
    raw = _cfg(key)
    if raw == "":
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"  ✗ {key} in {_ENV_PATH.name} is not valid JSON: {exc}")


# --- citizen / contact -------------------------------------------------------
SSN = _cfg("SSN")                       # contact_lookup, find_cases_by_contact,
                                        # find_citizen_folder, find_documents
PERSON_FULL_NAME = _cfg("PERSON_FULL_NAME")
PERSON_ID = _cfg("PERSON_ID")
CONTACT_SITE = _cfg("CONTACT_SITE", "borgersager")

# --- case search -------------------------------------------------------------
CASE_TYPE_PREFIX = _cfg("CASE_TYPE_PREFIX", "BOR")
CATEGORY = _cfg("CATEGORY", "Borgermappe")
RETURNED_CASES_NUMBER = _cfg("RETURNED_CASES_NUMBER", "1")
SEARCH_FIELD_PROPERTIES: dict = _json("SEARCH_FIELD_PROPERTIES", {})

# --- existing case id --------------------------------------------------------
CASE_ID = _cfg("CASE_ID")               # open_case, close_case,
                                        # get_case_metadata, upload_document
CASE_REASON: str | None = _opt("CASE_REASON")

# --- create_case (write!) — JSON dict of GoClient.create_case kwargs ---------
CREATE_CASE: dict = _json("CREATE_CASE", {})

# --- documents ---------------------------------------------------------------
DOK_ID = _cfg("DOK_ID")                 # document_metadata, download,
                                        # convert_to_pdf, parents, children
VERSION_UI = _cfg("VERSION_UI")         # convert_to_pdf (e.g. "1.0")
DOWNLOAD_PATH = _cfg("DOWNLOAD_PATH")   # local file path for download()
DOCUMENT_IDS: list[int] = _json("DOCUMENT_IDS", [])  # journalize/finalize
SEARCH_TERM = _cfg("SEARCH_TERM")       # search_documents, modern_search
SEARCH_START: str | None = _opt("SEARCH_START")
SEARCH_END: str | None = _opt("SEARCH_END")

# upload_document (write!) — JSON dict. Provide a local file via "path" (bytes
# are read from it). metadata is a FIELD DICT (the <z:row> is built for you);
# friendly keys date/title/receiver/category map to ows_Dato/ows_Title/
# ows_Modtagere/ows_Korrespondance, raw "ows_..." keys pass through, null = none.
UPLOAD: dict = _json(
    "UPLOAD",
    {
        "filename": "",
        "path": "",
        "metadata": None,
        "overwrite": False,
        "list_name": "Dokumenter",
        "folder_path": "",
    },
)

# --- personalesag discovery --------------------------------------------------
TJENESTENUMMER: str | None = _opt("TJENESTENUMMER")


# ==============================================================================
# Harness — you should not need to edit below here.
# ==============================================================================

_TESTS: dict[str, "callable"] = {}


def test(fn):
    """Register a function as a runnable manual test."""
    _TESTS[fn.__name__] = fn
    return fn


def _req(name: str, value):
    """Fail fast if a required variable is still blank/empty."""
    if value is None or value == "" or value == [] or value == {}:
        raise SystemExit(
            f"  ✗ variable {name!r} is not set — set it in {_ENV_PATH} "
            f"(copy manual_tests/.env.example) before running this test."
        )
    return value


def _client() -> GoClient:
    # Credentials come from the environment (seeded from .env above) via
    # go_api_endpoint / go_api_username / go_api_password (+ LIBREOFFICE_PATH).
    return GoClient.from_env()


# ---- health / contacts ------------------------------------------------------


@test
def health_check(c: GoClient):
    """GET the health endpoint — safe, needs no variables."""
    print("  ok:", c.health_check())


@test
def contact_lookup(c: GoClient):
    """Resolve a citizen contact from an SSN."""
    _req("SSN", SSN)
    print("  ->", c.contact_lookup(SSN, site=CONTACT_SITE))


# ---- cases (search) ---------------------------------------------------------


@test
def find_case(c: GoClient):
    """Search cases by arbitrary field properties (no citizen tie-in)."""
    _req("SEARCH_FIELD_PROPERTIES", SEARCH_FIELD_PROPERTIES)
    hits = c.find_case(
        case_type_prefix=CASE_TYPE_PREFIX,
        field_properties=SEARCH_FIELD_PROPERTIES,
        returned_cases_number=RETURNED_CASES_NUMBER,
    )
    for h in hits:
        print("  ->", h.id)


@test
def find_cases_by_contact(c: GoClient):
    """Search cases by a citizen — only SSN is required."""
    _req("SSN", SSN)
    hits = c.find_cases_by_contact(
        case_type_prefix=CASE_TYPE_PREFIX,
        person_ssn=SSN,
        person_full_name=PERSON_FULL_NAME,
        person_id=PERSON_ID,
        returned_cases_number=RETURNED_CASES_NUMBER,
    )
    for h in hits:
        print("  ->", h.id)


@test
def find_citizen_folder(c: GoClient):
    """Search a citizen's folder (Borgermappe) — only SSN is required."""
    _req("SSN", SSN)
    hits = c.find_citizen_folder(
        person_ssn=SSN,
        person_full_name=PERSON_FULL_NAME,
        person_id=PERSON_ID,
        case_type_prefix=CASE_TYPE_PREFIX,
        category=CATEGORY,
    )
    for h in hits:
        print("  ->", h.id)


# ---- cases (write) ----------------------------------------------------------


@test
def create_case_folder(c: GoClient):
    """WRITE: create a citizen folder (Borgermappe)."""
    _req("SSN", SSN)
    _req("PERSON_FULL_NAME", PERSON_FULL_NAME)
    _req("PERSON_ID", PERSON_ID)
    case = c.create_case_folder(
        case_type_prefix=CASE_TYPE_PREFIX,
        person_full_name=PERSON_FULL_NAME,
        person_id=PERSON_ID,
        person_ssn=SSN,
        category=CATEGORY,
    )
    print("  created ->", case.id)


@test
def create_case(c: GoClient):
    """WRITE: create a case from the CREATE_CASE dict."""
    _req("CREATE_CASE", CREATE_CASE)
    case = c.create_case(**CREATE_CASE)
    print("  created ->", case.id)


@test
def open_case(c: GoClient):
    """WRITE: open a case."""
    _req("CASE_ID", CASE_ID)
    c.open_case(CASE_ID, reason=CASE_REASON)
    print("  opened", CASE_ID)


@test
def close_case(c: GoClient):
    """WRITE: close a case."""
    _req("CASE_ID", CASE_ID)
    c.close_case(CASE_ID, reason=CASE_REASON)
    print("  closed", CASE_ID)


@test
def get_case_metadata(c: GoClient):
    """Read a case's metadata."""
    _req("CASE_ID", CASE_ID)
    print("  ->", c.get_case_metadata(CASE_ID))


# ---- documents (write) ------------------------------------------------------


@test
def upload_document(c: GoClient):
    """WRITE: upload a document to CASE_ID."""
    _req("CASE_ID", CASE_ID)
    _req("UPLOAD['filename']", UPLOAD.get("filename"))
    data = UPLOAD.get("data") or b""
    if UPLOAD.get("path"):
        with open(UPLOAD["path"], "rb") as fh:
            data = fh.read()
    _req("UPLOAD data/path", data)
    result = c.upload_document(
        case_id=CASE_ID,
        filename=UPLOAD["filename"],
        data=data,
        metadata=UPLOAD.get("metadata"),
        overwrite=UPLOAD.get("overwrite", False),
        list_name=UPLOAD.get("list_name", "Dokumenter"),
        folder_path=UPLOAD.get("folder_path", ""),
    )
    print("  uploaded, document_id ->", result.document_id)


@test
def journalize_documents(c: GoClient):
    """WRITE: mark documents as case records."""
    _req("DOCUMENT_IDS", DOCUMENT_IDS)
    c.journalize_documents(DOCUMENT_IDS)
    print("  journalized", DOCUMENT_IDS)


@test
def finalize_documents(c: GoClient):
    """WRITE: finalize documents."""
    _req("DOCUMENT_IDS", DOCUMENT_IDS)
    c.finalize_documents(DOCUMENT_IDS)
    print("  finalized", DOCUMENT_IDS)


@test
def search_documents(c: GoClient):
    """Search documents by term."""
    _req("SEARCH_TERM", SEARCH_TERM)
    for h in c.search_documents(SEARCH_TERM):
        print("  ->", h.document_id, h.title)


@test
def modern_search(c: GoClient):
    """Modern document search (optionally date-bounded)."""
    _req("SEARCH_TERM", SEARCH_TERM)
    hits = c.modern_search(
        SEARCH_TERM, case_type_prefix=CASE_TYPE_PREFIX, start=SEARCH_START, end=SEARCH_END
    )
    for h in hits:
        print("  ->", h.document_id, h.title)


# ---- documents (read) -------------------------------------------------------


@test
def document_metadata(c: GoClient):
    """Read a document's metadata."""
    _req("DOK_ID", DOK_ID)
    print("  ->", c.document_metadata(DOK_ID))


@test
def download(c: GoClient):
    """Download a document to DOWNLOAD_PATH."""
    _req("DOK_ID", DOK_ID)
    _req("DOWNLOAD_PATH", DOWNLOAD_PATH)
    c.download(DOK_ID, DOWNLOAD_PATH)
    print("  saved ->", DOWNLOAD_PATH)


@test
def convert_to_pdf(c: GoClient):
    """Convert a document version to PDF (needs LibreOffice configured)."""
    _req("DOK_ID", DOK_ID)
    _req("VERSION_UI", VERSION_UI)
    pdf = c.convert_to_pdf(DOK_ID, VERSION_UI)
    print("  pdf bytes ->", None if pdf is None else len(pdf))


@test
def parents(c: GoClient):
    """List a document's parent ids."""
    _req("DOK_ID", DOK_ID)
    print("  ->", c.parents(DOK_ID))


@test
def children(c: GoClient):
    """List a document's child ids."""
    _req("DOK_ID", DOK_ID)
    print("  ->", c.children(DOK_ID))


# ---- personalesag discovery -------------------------------------------------


@test
def find_documents(c: GoClient):
    """Discover a citizen's personalesag documents by CPR."""
    _req("SSN", SSN)
    for d in c.find_documents(cpr=SSN, tjenestenummer=TJENESTENUMMER):
        print("  ->", d.dok_id, d.name, d.ext)


# ==============================================================================
# Entry point
# ==============================================================================


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tests", nargs="*", help="names of tests to run")
    parser.add_argument("--list", action="store_true", help="list available tests and exit")
    parser.add_argument("--all", action="store_true", help="run every test (skips blank ones)")
    args = parser.parse_args(argv)

    if args.list or (not args.tests and not args.all):
        print("Available tests (pass one or more names, or --all):\n")
        for name, fn in _TESTS.items():
            doc = (fn.__doc__ or "").splitlines()[0]
            print(f"  {name:<24} {doc}")
        return 0

    names = list(_TESTS) if args.all else args.tests
    unknown = [n for n in names if n not in _TESTS]
    if unknown:
        print(f"Unknown test(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    client = _client()
    failures = 0
    for name in names:
        print(f"\n=== {name} ===")
        try:
            _TESTS[name](client)
        except SystemExit as exc:  # unset variable, when running --all: skip, don't abort
            print(exc)
            if args.all:
                continue
            failures += 1
        except Exception as exc:  # noqa: BLE001 — this is a manual diagnostic harness
            print(f"  ✗ {type(exc).__name__}: {exc}")
            failures += 1
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
