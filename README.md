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
  converter), and `find_documents(cpr, tjenestenummer)` — see the seam note
  below.
- **`pdf`** — convert the files GO yields into PDF: passthrough for PDFs,
  LibreOffice headless for office/email/HTML, Pillow for images. `convert_to_pdf`
  is the orchestrator.
- **`config`** — `go_config_from_env()` reads `GO_API_URL` / `GO_USERNAME` /
  `GO_PASSWORD` (+ optional `LIBREOFFICE_PATH`).

## `go.find_documents` is a stub (interface seam)

Resolving an AktPerson's documents from a CPR (+ tjenestenummer) depends on GO
search/contact-lookup semantics that aren't yet confirmed for this deployment.
The function's **signature and return type (`list[GoDocument]`) are stable** so
consumers can integrate today; the body raises `NotImplementedError` until the
real query is wired in. See the consuming app's gather pipeline (which uses a
mock backend in the meantime).

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
