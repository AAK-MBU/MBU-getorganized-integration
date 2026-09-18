"""``GoClient`` — the façade consuming processes talk to (plan §2/§4, Layer 2).

Holds one NTLM session + the ``base_url`` (+ optional LibreOffice path) and
exposes one method per GO operation. Each method builds request payloads via
:mod:`payloads` and delegates to the stateless Layer-1 functions
(:mod:`cases` / :mod:`documents` / :mod:`contacts` / :mod:`api` / :mod:`discovery`).
Consuming processes never see an endpoint path, an ``ows_...`` XML string, or the
NTLM plumbing — that was the leak the consolidation removes (plan §1).
"""

from __future__ import annotations

import requests

from . import _http, api, cases, contacts, discovery, documents, payloads
from .config import GoConfig, go_config_from_env
from .models import Case, ContactResult, GoDocument, SearchHit, UploadResult


class GoClient:
    """Session-backed façade over the GO API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        libreoffice_path: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.libreoffice_path = libreoffice_path
        # Username/password are kept because GO's PDF converter wants a fresh
        # NTLM auth rather than the shared session (see documents.pdf_convert).
        self._username = username
        self._password = password
        self._session: requests.Session = _http.session(username, password)

    @classmethod
    def from_config(cls, cfg: GoConfig) -> "GoClient":
        return cls(
            cfg.base_url,
            cfg.username,
            cfg.password,
            libreoffice_path=cfg.libreoffice_path,
        )

    @classmethod
    def from_env(cls) -> "GoClient":
        """Build from ``go_api_endpoint`` / ``go_api_username`` / ``go_api_password``."""
        return cls.from_config(go_config_from_env())

    # ------------------------------------------------------------------ health / contacts

    def health_check(self) -> bool:
        return api.health_check(self._session, base_url=self.base_url)

    def contact_lookup(self, ssn: str, *, site: str = "borgersager") -> ContactResult:
        return contacts.contact_lookup(
            self._session, base_url=self.base_url, ssn=ssn, site=site
        )

    # ------------------------------------------------------------------ cases (search)

    def find_case(
        self,
        *,
        case_type_prefix: str,
        field_properties: dict,
        returned_cases_number: str = "1",
        exclude_deleted: bool = True,
    ) -> list[Case]:
        """Search cases by arbitrary field properties (no citizen tie-in)."""
        search = payloads.simple_search_case_data(
            case_type_prefix,
            field_properties,
            returned_cases_number=returned_cases_number,
            exclude_deleted=exclude_deleted,
        )
        return cases.find_case(
            self._session, base_url=self.base_url, search_data=search
        )

    def find_cases_by_contact(
        self,
        *,
        case_type_prefix: str,
        person_full_name: str,
        person_id: str,
        person_ssn: str,
        include_name: bool = True,
        returned_cases_number: str = "1",
        field_properties: dict | None = None,
    ) -> list[Case]:
        """Search cases by a person's contact data (+ optional extra fields)."""
        search = payloads.generic_search_case_data(
            case_type_prefix,
            person_full_name,
            person_id,
            person_ssn,
            include_name=include_name,
            returned_cases_number=returned_cases_number,
            field_properties=field_properties,
        )
        return cases.find_case(
            self._session, base_url=self.base_url, search_data=search
        )

    def find_citizen_folder(
        self,
        *,
        person_full_name: str,
        person_id: str,
        person_ssn: str,
        case_type_prefix: str = "BOR",
        category: str = "Borgermappe",
    ) -> list[Case]:
        """Search for a citizen's folder (Borgermappe) by contact data + category."""
        search = payloads.search_citizen_folder_data(
            case_type_prefix, person_full_name, person_id, person_ssn, category=category
        )
        return cases.find_case(
            self._session, base_url=self.base_url, search_data=search
        )

    # ------------------------------------------------------------------ cases (write)

    def create_case_folder(
        self,
        *,
        case_type_prefix: str,
        person_full_name: str,
        person_id: str,
        person_ssn: str,
        category: str = "Borgermappe",
    ) -> Case:
        """Create a citizen folder (Borgermappe)."""
        xml = payloads.case_folder_metadata_xml(
            person_full_name, person_id, person_ssn, category=category
        )
        return cases.create_case(
            self._session,
            base_url=self.base_url,
            case_type_prefix=case_type_prefix,
            metadata_xml=xml,
        )

    def create_case(
        self,
        *,
        case_type_prefix: str,
        title: str,
        case_category: str,
        case_owner_id: str,
        case_owner_name: str,
        case_profile_id: str,
        case_profile_name: str,
        parent_case_id: str | None = None,
        department_id: str = "",
        department_name: str = "",
        kle_number: str = "",
        facet: str = "",
        special_group: str = "",
        start_date: str | None = None,
    ) -> Case:
        """Create a case from its fields (builds the ``<z:row>`` MetadataXml)."""
        xml = payloads.case_metadata_xml(
            case_type_prefix=case_type_prefix,
            case_category=case_category,
            title=title,
            case_owner_id=case_owner_id,
            case_owner_name=case_owner_name,
            case_profile_id=case_profile_id,
            case_profile_name=case_profile_name,
            parent_case_id=parent_case_id,
            department_id=department_id,
            department_name=department_name,
            kle_number=kle_number,
            facet=facet,
            special_group=special_group,
            start_date=start_date,
        )
        return cases.create_case(
            self._session,
            base_url=self.base_url,
            case_type_prefix=case_type_prefix,
            metadata_xml=xml,
        )

    def open_case(self, case_id: str, *, reason: str | None = None) -> None:
        cases.open_case(
            self._session, base_url=self.base_url, case_id=case_id, reason=reason
        )

    def close_case(self, case_id: str, *, reason: str | None = None) -> None:
        cases.close_case(
            self._session, base_url=self.base_url, case_id=case_id, reason=reason
        )

    def get_case_metadata(self, case_id: str) -> dict:
        return cases.get_case_metadata(
            self._session, base_url=self.base_url, case_id=case_id
        )

    def case_modern_search(self, term: str, *, case_type_prefix: str) -> list[SearchHit]:
        return cases.case_modern_search(
            self._session,
            base_url=self.base_url,
            term=term,
            case_type_prefix=case_type_prefix,
        )

    # ------------------------------------------------------------------ documents (write)

    def upload_document(
        self,
        *,
        case_id: str,
        filename: str,
        data: bytes,
        metadata: dict | None = None,
        overwrite: bool = False,
        list_name: str = "Dokumenter",
        folder_path: str = "",
    ) -> UploadResult:
        """Upload a document to a case.

        ``metadata`` is a field dict — the ``<z:row>`` MetadataXml is built from
        it via :func:`payloads.document_metadata_xml`. Keys may be the friendly
        names ``date`` / ``title`` / ``receiver`` / ``category`` or any raw
        ``ows_...`` GO field name. Pass ``None`` / ``{}`` to upload without extra
        metadata.
        """
        metadata_xml = payloads.document_metadata_xml(metadata) if metadata else ""
        return documents.upload_document(
            self._session,
            base_url=self.base_url,
            case_id=case_id,
            filename=filename,
            data=data,
            metadata=metadata_xml,
            overwrite=overwrite,
            list_name=list_name,
            folder_path=folder_path,
        )

    def journalize_documents(self, document_ids: list[int]) -> None:
        """Mark documents as case records."""
        documents.mark_as_case_record(
            self._session, base_url=self.base_url, document_ids=document_ids
        )

    def finalize_documents(self, document_ids: list[int]) -> None:
        documents.finalize_documents(
            self._session, base_url=self.base_url, document_ids=document_ids
        )

    def search_documents(self, term: str, *, limit: int = 500) -> list[SearchHit]:
        """Legacy document search. NOT IMPLEMENTED yet (raises) — its endpoint /
        response shape is unverified on host; use :meth:`modern_search` instead.
        """
        return documents.search_documents(
            self._session, base_url=self.base_url, term=term, limit=limit
        )

    def document_modern_search(
        self,
        term: str,
        *,
        case_type_prefix: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[SearchHit]:
        return documents.modern_search(
            self._session,
            base_url=self.base_url,
            term=term,
            case_type_prefix=case_type_prefix,
            start_date=start,
            end_date=end,
        )

    # ------------------------------------------------------------------ documents (read)

    def document_metadata(self, dok_id: str) -> dict:
        return documents.fetch_metadata(
            self._session, base_url=self.base_url, dok_id=dok_id
        )

    def download(self, dok_id: str, local_path: str) -> None:
        documents.download_file(
            self._session, base_url=self.base_url, dok_id=dok_id, local_path=local_path
        )

    def convert_to_pdf(
        self, dok_id: str, version_ui: str, *, timeout: int | None = None
    ) -> bytes | None:
        return documents.pdf_convert(
            username=self._username,
            password=self._password,
            base_url=self.base_url,
            dok_id=dok_id,
            version_ui=version_ui,
            timeout=timeout,
        )

    def parents(self, dok_id: str) -> list[str]:
        return documents.fetch_parents(
            self._session, base_url=self.base_url, dok_id=dok_id
        )

    def children(self, dok_id: str) -> list[str]:
        return documents.fetch_children(
            self._session, base_url=self.base_url, dok_id=dok_id
        )

    # ------------------------------------------------------------------ personalesag discovery

    def find_documents(
        self, *, cpr: str, tjenestenummer: str | None = None
    ) -> list[GoDocument]:
        return discovery.find_documents(
            self._session,
            base_url=self.base_url,
            cpr=cpr,
            tjenestenummer=tjenestenummer,
        )
