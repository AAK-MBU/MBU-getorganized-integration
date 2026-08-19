# MBU-getorganized-integration

A focused Python package for integrating with Aarhus Kommune's **GetOrganized
(GO)** ESDH: a GO API client plus PDF conversion. Linux-first.

It consolidates the GO + PDF-conversion functionality previously spread across
RPA processes, vendoring the production-tested GO primitives from
[`mtm-aarhus/oomtm`](https://github.com/mtm-aarhus/oomtm). **Out of scope (by
design):** SharePoint, Nova, OCR, and the Windows-only auto-install tooling —
OCR runs in the consuming application, not here.

## Modules

- **`go`** — NTLM-authenticated GO client: `session()`, `fetch_metadata()`,
  `fetch_parents()` / `fetch_children()`, `download_file()` (chunked, with the
  SharePoint-blob fallback for large files), `pdf_convert()` (GO's native
  converter), and the AktPerson document discovery — `case_lookup_by_cpr()`,
  `list_subcases()`, `list_documents_in_case()`, composed by
  `find_documents(cpr, tjenestenummer)` — see the discovery note below.
- **`pdf`** — convert the files GO yields into PDF: passthrough for PDFs,
  LibreOffice headless for office/email/HTML, Pillow for images. `convert_to_pdf`
  is the orchestrator.
- **`config`** — `go_config_from_env()` reads `go_api_endpoint` /
  `go_api_username` / `go_api_password` (+ optional `LIBREOFFICE_PATH`).

## AktPerson document discovery (personalemapper)

A person's documents live as files under the sub-cases ("mapper") of their PER
personalesag in the *personalemapper* site, so `find_documents` resolves them in
three steps, each exposed as its own function:

1. **`case_lookup_by_cpr(cpr)`** — GO modern search (PER scope) → the
   personalesag id + its `PER` web prefix.
2. **`list_subcases(personale_sags_id, akt_id)`** — the folders, each a child
   case (`CCMParentCase` filter), paginated.
3. **`list_documents_in_case(sags_id)`** — the documents in one folder, across
   the journalised + not-journalised views (`CCMSubID` filter, `NextHref`
   paginated, de-duplicated), as rich `CaseDocument`s.

`find_documents(cpr, tjenestenummer)` composes these into `list[GoDocument]`
(the minimal contract the gather consumer depends on). `tjenestenummer` is
accepted for API stability but not yet used to narrow the result — the
personalesag is keyed by CPR. The endpoint shapes encode the personalemapper
deployment's conventions, verified against the legacy HentFiler robot.

## Install

```bash
pip install "mbu-getorganized-integration @ git+https://github.com/AAK-MBU/MBU-getorganized-integration"
# with the PDF-conversion extras (Pillow, extract-msg):
pip install "mbu-getorganized-integration[pdf] @ git+https://github.com/AAK-MBU/MBU-getorganized-integration"
```

Office/email conversion also needs LibreOffice on the host:

```bash
apt-get install libreoffice
```

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The LibreOffice-dependent test is skipped automatically when `soffice` is absent.

## Usage sketch

```python
from mbu_getorganized_integration import go, pdf, go_config_from_env

cfg = go_config_from_env()
s = go.session(cfg.username, cfg.password)
meta = go.fetch_metadata(s, base_url=cfg.base_url, dok_id="123")
go.download_file(s, base_url=cfg.base_url, dok_id="123", local_path="/tmp/raw")
pdf_path, status, note = pdf.convert_to_pdf("/tmp/raw", meta["ext"], "/tmp/out")
```
