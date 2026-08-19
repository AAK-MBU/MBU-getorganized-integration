# GO consolidation — plan & status

> **Status: READY TO BUILD (handoff written 2026-08-19).** Layout decision: the
> **LAYERED ~9-file layout in §2** (owner's choice). The 3 open questions in §8 are
> now answered (see §8). Build in a fresh session: read this file top-to-bottom,
> then start at §7 step 1.
>
> **Base:** work on branch **`task/go-consolidation`**, already created off commit
> **`c78b94c`** (which committed the prior working state — email→PDF WIP, the
> `go_api_*` env rename, the personalesag doc-list fix). Local-only repo: nothing
> is pushed; `origin/main` is still `f2b7933 "Initial commit"`. Commit freely on
> this task branch; do NOT push or open a PR unless the owner asks.
>
> ### §0.5 Reconciliation with the actual repo (READ THIS — it overrides stale bits below)
> The body of this plan predates the current code. Where they differ, the repo wins:
> - **Env vars are already `go_api_endpoint` / `go_api_username` / `go_api_password`**
>   (config.py, committed) — NOT `GO_API_URL/GO_USERNAME/GO_PASSWORD`. Keep the new names.
> - **`pdf.py` carries an UNFINISHED email→PDF / `merge_pdfs` feature** (rode along in
>   the base commit). The consolidation does not touch `pdf.py` — leave that WIP alone.
> - **Consumer retarget:** `MBU_Journalisering_service` (referenced in §6/§9) is **NOT on
>   this machine**. The real integration target is **`projects/ats_fratag_formynd`**.
>   The hand-built `ows_...` XML to move into `payloads.py` now lives in
>   `ats_fratag_formynd/processes/subprocesses/opret_esdh/esdh_client.py`
>   (`_build_metadata_xml`, the citizen-folder `<z:row>`, the search-json usage) plus
>   `mbu_dev_shared_components/getorganized/objects.py`. Treat §6/§9 as historical.
> - **API gap — add `close_case`** (see §4): `ats_fratag_formynd/luk` needs it, and it
>   exists in NEITHER this package NOR `mbu_dev_shared_components`. The close endpoint
>   lived inside the old external `mbu_getorganized_integration.CaseHandler` (not
>   installed here). **BUILD-SESSION RESEARCH ITEM:** determine the GO close endpoint
>   (likely `/_goapi/Cases/CloseCase` or similar) and confirm before implementing.
> - After build: owner tests on host, THEN integrate into `ats_fratag_formynd` (swap
>   `esdh_client.py` + luk's `CaseHandler` for `GoClient`; drop that repo's
>   `mbu-dev-shared-components[getorganized]` and the undeclared `mbu_getorganized_integration`).

Goal: make `MBU-getorganized-integration` the single home for GetOrganized (GO)
logic. Standardize on the **session** approach, port the shared-components
**write** surface into the package, move the **payload/metadata builders** in,
and add a **`GoClient` façade** so consuming processes stop declaring their own
`CaseHandler`/`DocumentHandler` with hard-coded endpoint paths.

**Decisions locked in:** clean typed API (do NOT mirror the old handler
signatures); `MBU_Journalisering_service` migrated in a **separate** follow-up PR.

---

## 0. Where things stand (status at pause)

Package is real and green (`31 passed, 2 skipped`) on branch
`task/find-documents-personalesag`. It covers the **read / discovery** half of
the mission; the **write / journalization** half and the **façade** are not started.

| Area | Status |
|---|---|
| Session-based GO client (`go.py`) | ✅ `session`, `fetch_metadata`, `fetch_parents/children`, `download_file` (+ SharePoint-blob fallback), `pdf_convert` |
| Personalesag document gathering | ✅ `case_lookup_by_cpr` → `list_subcases` → `list_documents_in_case`, composed by `find_documents(cpr)` |
| PDF conversion (`pdf.py`) | ✅ Linux-first LibreOffice/Pillow/email→PDF pipeline |
| Env config (`config.py`) | ✅ `go_config_from_env()` |
| **Write / journalization surface** | ❌ missing — no create_case, upload, mark-as-record, finalize, search, contact_lookup, health_check |
| **Metadata/payload builders** | ❌ missing — XML/data-json still built in the consumer |
| **Client façade (`GoClient`)** | ❌ missing — nothing yet replaces per-process handlers |

So: personalesag gathering ≈ done; "absorb all GO functionality from
shared-components" ≈ half done (read half); "resolve the package so logic stops
living in each process" = not started.

## 1. The two approaches, head to head

| | **shared-components** (`mbu_dev_shared_components.getorganized`) | **this repo** (`go.py`) |
|---|---|---|
| **Auth** | Fresh `HttpNtlmAuth` **per call** via `requests.request(...)`. Every call redoes the NTLM handshake; no connection reuse. | One `requests.Session`, NTLM set once, **reused** → pooling, one handshake per host. |
| **Endpoint** | Caller passes the **full URL** incl. `/_goapi/...`. Path knowledge lives in the consumer. | Caller passes `base_url` only; path built **in-package**. |
| **Return** | Raw `requests.Response`; caller does `.raise_for_status()` / `.json()`. | Parsed **typed dataclasses**; raises typed errors. |
| **Payload building** | `objects.py` builds dicts; the `ows_...` **XML is hand-built in the consumer's `case_handler.py`**. | N/A yet (read-only surface). |
| **Coverage** | Write-heavy: create/open case, upload, mark-as-record, finalize, search, contact, health. | Read-heavy: discovery, download, convert, metadata. |

**The leak we're fixing:** `MBU_Journalisering_service` declares its own
`CaseHandler`/`DocumentHandler` holding creds, hard-coding endpoint paths
(`/_goapi/Cases`, `/_goapi/Documents/AddToCase`,
`/_goapi/Documents/MarkMultipleAsCaseRecord/ByDocumentId`, …) and building
`ows_...` XML by hand. Every new process re-implements all of that.

---

## 2. Target module layout

```
src/mbu_getorganized_integration/
  __init__.py       # exports: GoClient, GoConfig, go_config_from_env, models
  config.py         # GoConfig, go_config_from_env                    (unchanged)
  models.py         # NEW  GoDocument, Subcase, CaseDocument,
                    #      + Case, UploadResult, ContactResult, SearchHit
  endpoints.py      # NEW  endpoint path templates (the /_goapi/... knowledge)
  payloads.py       # NEW  XML + data-json builders (from shared-components objects.py)
  _http.py          # NEW  session() factory + _request helper (raise + parse)
  cases.py          # NEW  session-based case funcs (create/open/find/metadata/folder)
  documents.py      # NEW  session-based doc funcs (upload/mark/finalize/search/
                    #      metadata/parents/children/download/pdf_convert)
  discovery.py      # NEW  personalesag chain (moved from go.py)
  client.py         # NEW  GoClient façade composing the above
  pdf.py            # unchanged
  go.py             # thin re-export shim (deprecation window) — or delete once tests move
```

`go.py` today (~520 lines) mixes transport + read surface + personalesag chain.
It gets split by concern; the shim keeps `from ... import go` alive while our own
tests migrate.

## 3. Three layers

- **Layer 0 — transport (`_http.py`)**: `session(username, password)` (moved from
  go.py) + `_request(...)` choke point (default timeout, `raise_for_status`, home
  for retry/logging hooks). Error-tolerant reads (parents/children) keep their
  own try/except.
- **Layer 1 — stateless funcs (`cases.py`/`documents.py`/`discovery.py`)**:
  `f(session, *, base_url, ...) -> <typed model>`. Path in-package, parse +
  raise. Testable with the existing `_FakeSession` (URL-substring routing).
- **Layer 2 — façade (`GoClient`)**: holds `session` + `base_url`
  (+ `libreoffice_path`). One method per op; builds payloads via `payloads.py`;
  delegates to Layer 1. The only thing a consuming process touches.

## 4. `GoClient` API (the replacement for per-process handlers)

```python
class GoClient:
    def __init__(self, base_url, username, password, *, libreoffice_path=None): ...
    @classmethod
    def from_config(cls, cfg: GoConfig) -> "GoClient": ...
    @classmethod
    def from_env(cls) -> "GoClient": ...           # wraps go_config_from_env()

    # health / contacts
    def health_check(self) -> bool
    def contact_lookup(self, ssn, *, site="borgersager") -> ContactResult

    # cases (write)
    def find_case(self, *, case_type_prefix, field_properties=None, person=None, ...) -> list[Case]
    def create_case_folder(self, *, case_type_prefix, person_full_name, person_id,
                           person_ssn, category="Borgermappe") -> Case
    def create_case(self, *, case_type_prefix, title, owner, profile,
                    department=None, kle=None, parent_case=None, ...) -> Case
    def open_case(self, case_id, *, reason=None) -> None
    def close_case(self, case_id, *, reason=None) -> None   # ats_fratag_formynd/luk needs this — see §0.5
    def get_case_metadata(self, case_id) -> dict

    # documents (write)
    def upload_document(self, *, case_id, filename, data: bytes, overwrite=False,
                        list_name="Dokumenter", folder_path="",
                        title="", date="", receiver="", category="") -> UploadResult
    def journalize_documents(self, document_ids: list[int]) -> None   # mark-as-record
    def finalize_documents(self, document_ids: list[int]) -> None
    def search_documents(self, term, *, limit=500) -> list[SearchHit]
    def modern_search(self, term, *, case_type_prefix, start=None, end=None) -> list[SearchHit]

    # documents (read) — current surface, now on the client
    def document_metadata(self, dok_id) -> dict
    def download(self, dok_id, local_path) -> None
    def convert_to_pdf(self, dok_id, version_ui) -> bytes | None
    def parents(self, dok_id) -> list[str]
    def children(self, dok_id) -> list[str]

    # personalesag discovery
    def find_documents(self, *, cpr, tjenestenummer=None) -> list[GoDocument]
```

Endpoint paths consumers pass today all become internal `endpoints.py` constants.
The `ows_...` XML `case_handler.py` builds by hand moves into `payloads.py`.

## 5. Payload builders (`payloads.py`)

Port from shared-components `objects.py`, keeping the proven XML/JSON shapes as
plain functions the client calls internally:
- `case_data_json(case_type_prefix, metadata_xml, return_when_fully_created)`
- `document_data_json(case_id, list_name, folder_path, filename, metadata, overwrite, data)`
- `case_folder_metadata_xml(person_full_name, person_id, person_ssn, category)`
- `case_metadata_xml(**fields)`  (the big `create_case_data` XML, escaped)
- `document_metadata_xml(date, title, receiver, category)`
- search-payload builders (generic / simple / citizen-folder)

All XML values go through `xml.sax.saxutils.escape` (the consumer only escapes on
some paths today — this fixes that inconsistency).

## 6. Consumer migration (separate PR, later)

`MBU_Journalisering_service` shrinks from two ~100–300-line handler classes to:

```python
from mbu_getorganized_integration import GoClient
go = GoClient.from_env()
go.health_check()
case = go.create_case_folder(case_type_prefix="BOR", person_full_name=..., ...)
res  = go.upload_document(case_id=case.id, filename=..., data=pdf_bytes, ...)
go.journalize_documents([res.document_id])
go.finalize_documents([res.document_id])
```

No endpoint paths, no XML, no NTLM plumbing in the process.
`case_handler.py` / `document_handler.py` are deleted.

## 7. Sequencing (each a reviewable PR)

1. **Transport + models + builders** — `_http.py`, `models.py`, `endpoints.py`,
   `payloads.py`; move `session()`; no behavior change. Builder tests.
2. **Read surface relocate** — split go.py read/discovery into `documents.py` /
   `discovery.py`; `go.py` becomes a shim. Existing tests pass (or repointed).
3. **Write surface** — port create/open/find case, upload/mark/finalize/search,
   contact, health as session-based Layer-1 funcs. New `_FakeSession` tests.
4. **`GoClient` façade** — compose everything; client-level tests.
5. **(separate repo) consumer migration** — swap handlers for `GoClient`.

## 8. Open questions — ANSWERED (2026-08-19)

1. **`go.py`**: **hard-cut.** Split it into `_http.py` / `documents.py` / `discovery.py`
   and delete `go.py`; repoint the existing tests. The package is unpublished — there
   is no external API to preserve, so skip the deprecation shim (this overrides the
   "go.py-shim" note in §2's layout).
2. **Write-op return types**: **rich typed dataclasses everywhere** (`models.py`) —
   consistent with the locked "clean typed API" decision. No raw-dict returns.
3. **Multi-site paths**: **per-method `site=` default** now (e.g. `contact_lookup(..,
   site="borgersager")`); do NOT bake a global site concept into `GoClient`. Add one
   only if a second site is actually required.

## 9. Reference pointers

- shared-components GO surface: `mbu_dev_shared_components/getorganized/`
  — `api.py` (health), `auth.py` (NTLM), `cases.py`, `documents.py`,
  `contacts.py`, `objects.py` (payload builders).
- consumer handlers to be replaced:
  `MBU_Journalisering_service/case_manager/{case_handler,document_handler}.py`;
  usage + endpoint paths in `case_manager/journalize_process.py`.
