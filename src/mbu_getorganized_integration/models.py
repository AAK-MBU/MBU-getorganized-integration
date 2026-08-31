"""Typed models returned by the GO client surface.

Every GO operation returns a small dataclass rather than a raw dict / raw
``requests.Response`` (the "clean typed API" decision, plan §8). The read-side
models (:class:`GoDocument`, :class:`Subcase`, :class:`CaseDocument`) moved here
verbatim from ``go.py``; the write-side models (:class:`Case`,
:class:`UploadResult`, :class:`ContactResult`, :class:`SearchHit`) are new and
are populated by the write surface ported in a later step.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Read side — personalesag discovery (moved from go.py, unchanged)
# ---------------------------------------------------------------------------


@dataclass
class GoDocument:
    """A document discovered in GO for an AktPerson.

    The minimal shape the gathering process needs: an id to download by, a
    display name, and the bare file extension (drives PDF conversion). ``raw``
    carries the unparsed GO metadata for callers that need more.
    """

    dok_id: str
    name: str
    ext: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Subcase:
    """A folder ("mappe") in a personalesag — itself a GO child case."""

    case_id: str
    title: str


@dataclass
class CaseDocument:
    """A document in a (sub-)case, as returned by RenderListDataAsStream."""

    dok_id: str
    akt_id: str
    title: str
    file_ref: str
    file_name: str
    dato: str | None = None
    korrespondance: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        """The real file name if present, else the document title."""
        return self.file_name or self.title

    @property
    def ext(self) -> str | None:
        """Bare file extension derived from the file name (drives conversion)."""
        return self.file_name.rsplit(".", 1)[-1].lower() if "." in self.file_name else None


# ---------------------------------------------------------------------------
# Write side — new; populated by the ported write surface (later step)
# ---------------------------------------------------------------------------


@dataclass
class Case:
    """A GO case, as returned by create/find/create-folder.

    ``id`` is the GO ``CaseID`` (e.g. ``"BOR-2026-000123"``). ``raw`` carries the
    original GO row/response for callers that need the other columns.
    """

    id: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class UploadResult:
    """The outcome of uploading a document to a case.

    ``document_id`` is the GO document id used by mark-as-record / finalize.
    """

    document_id: int
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class ContactResult:
    """A GO contact resolved from a citizen's SSN (``contacts/readitem``)."""

    full_name: str
    id: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class SearchHit:
    """A single hit from document search / modern search.

    Kept intentionally loose — the two search endpoints return different column
    sets — so the common fields are optional and ``raw`` always carries the full
    row.
    """

    title: str | None = None
    case_id: str | None = None
    document_id: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_row(cls, row: dict) -> "SearchHit":
        """Build a hit from one search row, pulling the common (loosely cased)
        columns and always keeping the full row in ``raw``.

        ``case_id`` falls back to the trailing segment of ``caseurl``
        (``cases/<akt-prefix>/<case-id>``), which case rows carry even when the
        ``CCMCaseID`` column was not selected.
        """
        case_id = row.get("caseid") or row.get("CCMCaseID") or row.get("CaseID")
        if not case_id:
            case_url = row.get("caseurl") or ""
            case_id = case_url.rstrip("/").split("/")[-1] or None
        return cls(
            title=row.get("title") or row.get("Title"),
            case_id=case_id,
            document_id=row.get("docid") or row.get("CCMDocID") or row.get("DocID"),
            raw=row,
        )
