"""GO endpoint path knowledge (plan §2 / §3).

Every ``/_goapi/...`` path GO exposes is built here, so the stateless funcs and
the ``GoClient`` façade never hard-code a path and no consuming process has to
know one (the leak the consolidation fixes — plan §1). Each helper takes the
instance ``base_url`` (no trailing slash) plus whatever ids the path needs and
returns the full URL.

Provenance of each path:
* **Confirmed** — already exercised by ``go.py`` and/or a consumer on this
  machine (``esdh_client.py``).
* **From plan §1** — named in the consolidation plan's description of the
  MBU_Journalisering_service handlers (upload / mark-as-record).
* **TODO(step 3)** — not verifiable on this machine (the handlers that held
  them left with MBU_Journalisering_service). Best-known guess, to be confirmed
  against a live GO instance when the write surface is wired. Marked inline.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def cases(base_url: str) -> str:
    """Create case / create case folder (POST). Confirmed (esdh_client)."""
    return f"{base_url}/_goapi/Cases"


def case_metadata(base_url: str, case_id: str) -> str:
    """Case metadata (GET). Confirmed (go.list_documents_in_case)."""
    return f"{base_url}/_goapi/Cases/Metadata/{case_id}"


def find_case(base_url: str) -> str:
    """Search cases by properties (POST). Confirmed (esdh_client)."""
    return f"{base_url}/_goapi/cases/findbycaseproperties"


def open_case(base_url: str) -> str:
    """Reopen a closed case (POST). From shared-components cases.open_case."""
    return f"{base_url}/_goapi/Cases/OpenCase"


def close_case(base_url: str) -> str:
    """Close a case (POST). TODO(step 3): CONFIRM — ats_fratag_formynd/luk needs
    this and it exists in neither package (plan §0.5). ``CloseCase`` mirrors the
    confirmed ``OpenCase`` shape but is unverified against GO."""
    return f"{base_url}/_goapi/Cases/CloseCase"


# ---------------------------------------------------------------------------
# Contacts (site-scoped — plan §8 answer 3: per-method site= default)
# ---------------------------------------------------------------------------


def contact_lookup(base_url: str, *, site: str = "borgersager") -> str:
    """Resolve a contact by SSN (POST). Confirmed (esdh_client, site=borgersager)."""
    return f"{base_url}/{site}/_goapi/contacts/readitem"


# ---------------------------------------------------------------------------
# Documents — read (all confirmed against go.py)
# ---------------------------------------------------------------------------


def document_data(base_url: str, dok_id: str) -> str:
    return f"{base_url}/_goapi/Documents/Data/{dok_id}"


def document_metadata_with_system_fields(base_url: str, dok_id: str) -> str:
    return f"{base_url}/_goapi/Documents/MetadataWithSystemFields/{dok_id}"


def document_parents(base_url: str, dok_id: str) -> str:
    return f"{base_url}/_goapi/Documents/Parents/{dok_id}"


def document_children(base_url: str, dok_id: str) -> str:
    return f"{base_url}/_goapi/Documents/Children/{dok_id}"


def document_bytes(base_url: str, dok_id: str) -> str:
    return f"{base_url}/_goapi/Documents/DocumentBytes/{dok_id}"


def convert_to_pdf(base_url: str, dok_id: str, version_ui: str) -> str:
    return f"{base_url}/_goapi/Documents/ConvertToPDF/{dok_id}/{version_ui}"


# ---------------------------------------------------------------------------
# Documents — write
# ---------------------------------------------------------------------------


def add_to_case(base_url: str) -> str:
    """Upload a document to a case (POST). From plan §1."""
    return f"{base_url}/_goapi/Documents/AddToCase"


def mark_as_case_record(base_url: str) -> str:
    """Mark documents as case records / journalize (POST). From plan §1."""
    return f"{base_url}/_goapi/Documents/MarkMultipleAsCaseRecord/ByDocumentId"


def finalize(base_url: str) -> str:
    """Finalize documents (POST). TODO(step 3): CONFIRM path — the finalize_file
    handler that held it left with MBU_Journalisering_service."""
    return f"{base_url}/_goapi/Documents/FinalizeMultiple/ByDocumentId"


def search_documents(base_url: str) -> str:
    """Legacy document search (POST). TODO(step 3): CONFIRM path."""
    return f"{base_url}/_goapi/Documents/Search"


# ---------------------------------------------------------------------------
# Search (modern) — confirmed against go.case_lookup_by_cpr
# ---------------------------------------------------------------------------


def modern_search(base_url: str) -> str:
    return f"{base_url}/_goapi/search/ExecuteModernSearch"
