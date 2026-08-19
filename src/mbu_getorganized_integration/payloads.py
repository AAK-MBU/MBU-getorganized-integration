"""Payload builders for GO write operations (plan §5).

Ported from the two proven, in-tree sources so the request shapes stop being
hand-built in each consumer:

* shared-components ``getorganized/objects.py`` — the JSON payload builders
  (``case_data_json``, ``document_data_json``, the three search builders).
* ``ats_fratag_formynd/.../esdh_client.py`` — the ``<z:row>`` MetadataXml the
  consumer built by hand (``case_metadata_xml``, ``case_folder_metadata_xml``).

All XML attribute values go through :func:`xml.sax.saxutils.escape` — the
consumer escaped only on some paths, which this deliberately makes uniform
(plan §5).

TODO(step 3): ``document_metadata_xml`` (date / title / receiver / category) is
NOT ported — its shape lived in MBU_Journalisering_service's
``document_handler.py``, which is not on this machine. It is a research item for
the write-surface step; see :func:`document_metadata_xml`.
"""

from __future__ import annotations

from typing import Literal
from xml.sax.saxutils import escape

#: GO case-type prefixes (from shared-components objects.CaseTypePrefix).
CaseTypePrefix = Literal["BOR", "EMN", "PPR", "AKT", "ELM", "PER", "GEO", "SAM", "MOD"]


# ---------------------------------------------------------------------------
# JSON payload wrappers (POST bodies)
# ---------------------------------------------------------------------------


def case_data_json(
    case_type_prefix: str, metadata_xml: str, return_when_fully_created: bool
) -> dict:
    """Wrap a case ``MetadataXml`` into the ``/_goapi/Cases`` POST body."""
    return {
        "CaseTypePrefix": case_type_prefix,
        "MetadataXml": metadata_xml,
        "ReturnWhenCaseFullyCreated": return_when_fully_created,
    }


def document_data_json(
    *,
    case_id: str,
    list_name: str,
    folder_path: str,
    filename: str,
    metadata: str,
    overwrite: bool,
    data: bytes,
) -> dict:
    """Build the ``/_goapi/Documents/AddToCase`` POST body for one file."""
    return {
        "CaseId": case_id,
        "ListName": list_name,
        "FolderPath": folder_path,
        "FileName": filename,
        "Metadata": metadata,
        "Overwrite": overwrite,
        "Bytes": data,
    }


# ---------------------------------------------------------------------------
# Search payload builders (POST bodies for findbycaseproperties)
# ---------------------------------------------------------------------------


def _field_property(internal_name: str, value: str, comparison: str = "Equal") -> dict:
    return {
        "InternalName": internal_name,
        "Value": value,
        "DataType": "Text",
        "ComparisonType": comparison,
        "IsMultiValue": "False",
    }


def _contact_data(person_full_name: str, person_id: str, person_ssn: str) -> str:
    """Assemble the positional ``ows_CCMContactData`` match string.

    The value is positional (``name;#id;#ssn;#;#``); only ``person_ssn`` need be
    populated. An omitted name or id keeps its position as an empty string, so a
    citizen can be matched on SSN alone (``;#;#{ssn};#;#``)."""
    return f"{person_full_name};#{person_id};#{person_ssn};#;#"


def _extend_field_properties(target: list, field_properties: dict) -> None:
    """Append caller-supplied field properties. A value may be a plain string
    (``ComparisonType`` defaults to ``"Equal"``) or a ``{"value": ...,
    "comparison": ...}`` dict."""
    for internal_name, field_value in field_properties.items():
        if isinstance(field_value, dict):
            value = field_value["value"]
            comparison = field_value.get("comparison", "Equal")
        else:
            value, comparison = field_value, "Equal"
        target.append(_field_property(str(internal_name), value, comparison))


def generic_search_case_data(
    case_type_prefix: str,
    person_ssn: str,
    *,
    person_full_name: str = "",
    person_id: str = "",
    include_name: bool = True,
    returned_cases_number: str = "1",
    field_properties: dict | None = None,
) -> dict:
    """Search for a person's case folder by contact data (+ optional fields).

    Only ``person_ssn`` is required; ``person_full_name`` and ``person_id``
    narrow the match when given and are otherwise left empty. When
    ``include_name`` is False the name is dropped from the match even if
    supplied (``;#{id};#{ssn};#;#``) — matches shared-components exactly.
    """
    name = person_full_name if include_name else ""
    contact_data = _contact_data(name, person_id, person_ssn)
    search: dict = {
        "FieldProperties": [_field_property("ows_CCMContactData", contact_data)],
        "CaseTypePrefixes": [case_type_prefix],
        "LogicalOperator": "AND",
        "ExcludeDeletedCases": "True",
        "ReturnCasesNumber": returned_cases_number,
    }
    if field_properties:
        _extend_field_properties(search["FieldProperties"], field_properties)
    return search


def simple_search_case_data(
    case_type_prefix: str,
    field_properties: dict,
    *,
    returned_cases_number: str = "1",
    exclude_deleted: bool = True,
) -> dict:
    """Minimal search payload with only the given field properties — no contact
    data injected (search by an arbitrary field without tying to a citizen)."""
    search: dict = {
        "FieldProperties": [],
        "CaseTypePrefixes": [case_type_prefix],
        "LogicalOperator": "AND",
        "ExcludeDeletedCases": str(exclude_deleted),
        "ReturnCasesNumber": returned_cases_number,
    }
    _extend_field_properties(search["FieldProperties"], field_properties)
    return search


def search_citizen_folder_data(
    case_type_prefix: str,
    person_ssn: str,
    *,
    person_full_name: str = "",
    person_id: str = "",
    category: str = "Borgermappe",
) -> dict:
    """Search for a citizen folder: contact data + ``CaseCategory`` match.

    Only ``person_ssn`` is required; name and id narrow the match when given.
    """
    return {
        "FieldProperties": [
            _field_property(
                "ows_CCMContactData",
                _contact_data(person_full_name, person_id, person_ssn),
            ),
            _field_property("ows_CaseCategory", category),
        ],
        "CaseTypePrefixes": [case_type_prefix],
        "LogicalOperator": "AND",
        "ExcludeDeletedCases": "True",
        "ReturnCasesNumber": "1",
    }


# ---------------------------------------------------------------------------
# MetadataXml builders (<z:row> — every value escaped)
# ---------------------------------------------------------------------------


def case_folder_metadata_xml(
    person_full_name: str,
    person_id: str,
    person_ssn: str,
    *,
    category: str = "Borgermappe",
    case_status: str = "Åben",
) -> str:
    """``<z:row>`` for creating a citizen folder (Borgermappe).

    Ported from ``esdh_client._create_citizen_folder``. ``person_id`` /
    ``person_ssn`` are numeric GO ids, not escaped; the free-text name is.
    """
    return (
        '<z:row xmlns:z="#RowsetSchema" '
        f'ows_CaseStatus="{escape(case_status)}" '
        f'ows_CaseCategory="{escape(category)}" '
        f'ows_CCMContactData="{escape(person_full_name)};#{person_id};#{person_ssn};#;#" '
        "/>"
    )


def case_metadata_xml(
    *,
    case_type_prefix: str,
    case_category: str,
    title: str,
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
    case_status: str = "Åben",
) -> str:
    """``<z:row>`` MetadataXml for creating a case.

    Ported from ``esdh_client._build_metadata_xml``. The profile field name is
    suffixed with the case-type prefix (``ows_Sagsprofil_{prefix}``), and the
    parent case (when given) carries that same prefix
    (``ows_CCMParentCase="{parent};#{prefix}"``). ``Afdeling`` / ``KLENummer`` /
    ``Facet`` / ``SpecialGroup`` are always emitted (empty when absent) to
    mirror the consumer; ``Modtaget`` and ``CCMParentCase`` are emitted only
    when provided (GO rejects an empty date; not every case has a parent).
    """
    parts = [
        '<z:row xmlns:z="#RowsetSchema" ',
        f'ows_CaseStatus="{escape(case_status)}" ',
        f'ows_CaseCategory="{escape(str(case_category))}" ',
        f'ows_Title="{escape(title)}" ',
        f'ows_CaseOwner="{case_owner_id};#{escape(str(case_owner_name))}" ',
        f'ows_Sagsprofil_{case_type_prefix}="{case_profile_id};#{escape(str(case_profile_name))}" ',
    ]
    if parent_case_id:
        parts.append(f'ows_CCMParentCase="{parent_case_id};#{case_type_prefix}" ')
    parts += [
        f'ows_Afdeling="{department_id};#{escape(str(department_name))}" ',
        f'ows_KLENummer="{escape(str(kle_number))}" ',
        f'ows_Facet="{escape(str(facet))}" ',
        f'ows_SpecialGroup="{escape(str(special_group))}" ',
    ]
    if start_date:
        parts.append(f'ows_Modtaget="{escape(str(start_date))}" ')
    parts.append("/>")
    return "".join(parts)


def document_metadata_xml(
    *, date: str = "", title: str = "", receiver: str = "", category: str = ""
) -> str:
    """``<z:row>`` MetadataXml for an uploaded document.

    TODO(step 3): NOT IMPLEMENTED — the exact ``ows_...`` field names for a
    document's metadata lived in MBU_Journalisering_service's
    ``document_handler.py``, which is not on this machine. Inventing field names
    here would silently mis-journalize, so this raises until the shape is
    confirmed against a live GO instance (plan §5, §0.5).
    """
    raise NotImplementedError(
        "document_metadata_xml: GO document metadata field names are unverified "
        "on this machine (see MBU_Journalisering_service/document_handler.py). "
        "Confirm the ows_ field shape before wiring the upload surface (step 3)."
    )
