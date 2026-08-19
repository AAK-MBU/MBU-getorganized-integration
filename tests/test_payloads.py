"""Tests for the payload builders (plan §5). Pure functions — no network."""

from mbu_getorganized_integration import payloads


# ----- JSON wrappers ---------------------------------------------------------


def test_case_data_json_shape():
    body = payloads.case_data_json("PER", "<z:row/>", return_when_fully_created=True)
    assert body == {
        "CaseTypePrefix": "PER",
        "MetadataXml": "<z:row/>",
        "ReturnWhenCaseFullyCreated": True,
    }


def test_document_data_json_shape():
    body = payloads.document_data_json(
        case_id="BOR-2026-000001",
        list_name="Dokumenter",
        folder_path="",
        filename="brev.pdf",
        metadata="<z:row/>",
        overwrite=False,
        data=b"%PDF-1.4",
    )
    assert body["CaseId"] == "BOR-2026-000001"
    assert body["FileName"] == "brev.pdf"
    assert body["Bytes"] == b"%PDF-1.4"
    assert body["Overwrite"] is False


# ----- Search builders -------------------------------------------------------


def test_generic_search_includes_name_by_default():
    body = payloads.generic_search_case_data("BOR", "Navn Navnesen", "42", "1403820209")
    fp = body["FieldProperties"][0]
    assert fp["InternalName"] == "ows_CCMContactData"
    assert fp["Value"] == "Navn Navnesen;#42;#1403820209;#;#"
    assert body["CaseTypePrefixes"] == ["BOR"]


def test_generic_search_can_omit_name():
    body = payloads.generic_search_case_data(
        "BOR", "Navn Navnesen", "42", "1403820209", include_name=False
    )
    assert body["FieldProperties"][0]["Value"] == ";#42;#1403820209;#;#"


def test_generic_search_extra_field_properties_string_and_dict():
    body = payloads.generic_search_case_data(
        "BOR",
        "N",
        "42",
        "1403820209",
        field_properties={
            "ows_Title": {"value": "Kørsel til ", "comparison": "Contains"},
            "ows_CCMContactData_CPR": "1403820209",
        },
    )
    by_name = {fp["InternalName"]: fp for fp in body["FieldProperties"]}
    assert by_name["ows_Title"]["ComparisonType"] == "Contains"
    assert by_name["ows_CCMContactData_CPR"]["ComparisonType"] == "Equal"


def test_simple_search_has_no_contact_data():
    body = payloads.simple_search_case_data(
        "BOR", {"ows_Title": {"value": "Kørsel", "comparison": "Contains"}}
    )
    names = [fp["InternalName"] for fp in body["FieldProperties"]]
    assert "ows_CCMContactData" not in names
    assert names == ["ows_Title"]
    assert body["ExcludeDeletedCases"] == "True"


def test_search_citizen_folder_matches_category():
    body = payloads.search_citizen_folder_data("BOR", "Navn", "42", "1403820209")
    names = {fp["InternalName"]: fp["Value"] for fp in body["FieldProperties"]}
    assert names["ows_CaseCategory"] == "Borgermappe"
    assert names["ows_CCMContactData"] == "Navn;#42;#1403820209;#;#"


# ----- MetadataXml builders --------------------------------------------------


def test_case_folder_metadata_xml_escapes_name():
    xml = payloads.case_folder_metadata_xml("Ann & Bo", "42", "1403820209")
    assert 'ows_CaseCategory="Borgermappe"' in xml
    assert "Ann &amp; Bo;#42;#1403820209;#;#" in xml
    assert xml.startswith('<z:row xmlns:z="#RowsetSchema"') and xml.endswith("/>")


def test_case_metadata_xml_core_fields_and_profile_suffix():
    xml = payloads.case_metadata_xml(
        case_type_prefix="PER",
        case_category="Personalesag",
        title="Ansættelse",
        case_owner_id="7",
        case_owner_name="Ejer Ejersen",
        case_profile_id="9",
        case_profile_name="Profil",
        parent_case_id="BOR-2026-000001",
    )
    assert 'ows_Sagsprofil_PER="9;#Profil"' in xml
    assert 'ows_CaseOwner="7;#Ejer Ejersen"' in xml
    assert 'ows_CCMParentCase="BOR-2026-000001;#PER"' in xml
    # always-emitted empties
    assert 'ows_KLENummer=""' in xml
    # start_date omitted -> no Modtaget
    assert "ows_Modtaget" not in xml


def test_case_metadata_xml_omits_parent_when_absent_and_emits_date():
    xml = payloads.case_metadata_xml(
        case_type_prefix="BOR",
        case_category="Sag",
        title="T",
        case_owner_id="1",
        case_owner_name="O",
        case_profile_id="2",
        case_profile_name="P",
        start_date="2026-08-19",
    )
    assert "ows_CCMParentCase" not in xml
    assert 'ows_Modtaget="2026-08-19"' in xml


def test_case_metadata_xml_escapes_special_chars():
    xml = payloads.case_metadata_xml(
        case_type_prefix="BOR",
        case_category="A&B",
        title="<tag>",
        case_owner_id="1",
        case_owner_name="O&O",
        case_profile_id="2",
        case_profile_name="P",
    )
    assert "A&amp;B" in xml
    assert "&lt;tag&gt;" in xml
    assert "O&amp;O" in xml


def test_document_metadata_xml_maps_friendly_keys_and_skips_empty():
    xml = payloads.document_metadata_xml(
        {"date": "2026-08-19", "title": "Brev", "receiver": "", "category": None}
    )
    assert xml.startswith('<z:row xmlns:z="#RowsetSchema"') and xml.endswith("/>")
    assert 'ows_Dato="2026-08-19"' in xml
    assert 'ows_Title="Brev"' in xml
    # empty / None fields are skipped entirely
    assert "ows_Modtagere" not in xml
    assert "ows_Korrespondance" not in xml


def test_document_metadata_xml_passes_raw_ows_names_and_escapes():
    xml = payloads.document_metadata_xml({"ows_Custom": "A & B", "title": "<x>"})
    assert 'ows_Custom="A &amp; B"' in xml
    assert 'ows_Title="&lt;x&gt;"' in xml


def test_document_metadata_xml_empty_dict_is_bare_row():
    assert payloads.document_metadata_xml({}) == '<z:row xmlns:z="#RowsetSchema" />'
