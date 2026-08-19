"""Tests for the GoClient façade (client.py).

The client builds a real (offline) NTLM session in __init__; tests swap in a
fake session so delegation + payload-building can be checked with no network.
"""


from mbu_getorganized_integration import GoClient
from mbu_getorganized_integration.config import GoConfig
from mbu_getorganized_integration.models import Case, ContactResult, UploadResult


def _client(fake_session, routes):
    c = GoClient("https://go.example/", "DOMAIN\\user", "secret")
    c._session = fake_session(routes)
    return c


def test_from_config_strips_trailing_slash_and_keeps_libreoffice():
    cfg = GoConfig(
        base_url="https://go.example/", username="u", password="p", libreoffice_path="/x/soffice"
    )
    c = GoClient.from_config(cfg)
    assert c.base_url == "https://go.example"
    assert c.libreoffice_path == "/x/soffice"


def test_health_check_delegates(fake_session, make_response):
    c = _client(fake_session, {"https://go.example": make_response(status_code=200)})
    assert c.health_check() is True


def test_contact_lookup_delegates(fake_session, make_response):
    c = _client(fake_session, {"/contacts/readitem": make_response(json_data={"FullName": "N", "ID": 7})})
    res = c.contact_lookup("1403820209")
    assert isinstance(res, ContactResult) and res.full_name == "N" and res.id == "7"


def test_create_case_folder_builds_borgermappe_xml(fake_session, make_response):
    c = _client(fake_session, {"/_goapi/Cases": make_response(json_data={"CaseID": "BOR-2026-000001"})})
    case = c.create_case_folder(
        case_type_prefix="BOR", person_full_name="Navn", person_id="42", person_ssn="1403820209"
    )
    assert isinstance(case, Case) and case.id == "BOR-2026-000001"


def test_create_case_returns_case(fake_session, make_response):
    c = _client(fake_session, {"/_goapi/Cases": make_response(json_data={"CaseID": "PER-2026-000123"})})
    case = c.create_case(
        case_type_prefix="PER", title="Sag", case_category="Personalesag",
        case_owner_id="1", case_owner_name="Ejer", case_profile_id="2", case_profile_name="Profil",
        parent_case_id="BOR-2026-000001",
    )
    assert case.id == "PER-2026-000123"


def test_find_citizen_folder_delegates(fake_session, make_response):
    resp = make_response(json_data={"CasesInfo": [{"CaseID": "BOR-2026-000001"}]})
    c = _client(fake_session, {"/findbycaseproperties": resp})
    found = c.find_citizen_folder(person_full_name="N", person_id="42", person_ssn="1403820209")
    assert [x.id for x in found] == ["BOR-2026-000001"]


def test_open_and_close_case_hit_right_endpoints(fake_session, make_response):
    c = _client(fake_session, {
        "/OpenCase": make_response(json_data={}),
        "/CloseCase": make_response(json_data={}),
    })
    c.open_case("EMN-1", reason="r")
    c.close_case("EMN-1")
    assert any(u.endswith("/OpenCase") for u in c._session.calls)
    assert any(u.endswith("/CloseCase") for u in c._session.calls)


def test_upload_document_passthrough_metadata(fake_session, make_response):
    c = _client(fake_session, {"/AddToCase": make_response(json_data={"DocId": 501})})
    res = c.upload_document(case_id="BOR-2026-000001", filename="f.pdf", data=b"%PDF")
    assert isinstance(res, UploadResult) and res.document_id == 501


def test_upload_document_builds_metadata_from_dict(fake_session, make_response):
    c = _client(fake_session, {"/AddToCase": make_response(json_data={"DocId": 2})})
    res = c.upload_document(
        case_id="BOR-2026-000001",
        filename="brev.pdf",
        data=b"%PDF",
        metadata={"title": "Brev", "date": "2026-08-19"},
    )
    assert isinstance(res, UploadResult) and res.document_id == 2
    body = c._session.last_json
    assert 'ows_Title="Brev"' in body["Metadata"]
    assert 'ows_Dato="2026-08-19"' in body["Metadata"]


def test_journalize_and_finalize_delegate(fake_session, make_response):
    c = _client(fake_session, {
        "/MarkMultipleAsCaseRecord/ByDocumentId": make_response(json_data={}),
        "/FinalizeMultiple/ByDocumentId": make_response(json_data={}),
    })
    c.journalize_documents([1, 2])
    c.finalize_documents([1, 2])
    assert any("MarkMultipleAsCaseRecord" in u for u in c._session.calls)
    assert any("FinalizeMultiple" in u for u in c._session.calls)


def test_modern_search_delegates(fake_session, make_response):
    resp = make_response(json_data={"results": {"Results": [{"title": "Doc", "CCMDocID": "9"}]}})
    c = _client(fake_session, {"/ExecuteModernSearch": resp})
    hits = c.modern_search("q", case_type_prefix="PER")
    assert [h.title for h in hits] == ["Doc"] and hits[0].document_id == "9"


def test_document_read_methods_delegate(fake_session, make_response):
    import json as _json
    c = _client(fake_session, {
        "/Documents/Data/": make_response(text=_json.dumps(
            {"ItemProperties": 'ows_File_x0020_Type="pdf" ows__UIVersionString="2.0"'})),
        "/Documents/Children/": make_response(json_data={"ChildrenData": [{"DocumentId": 5}]}),
    })
    meta = c.document_metadata("42")
    assert meta["ext"] == "pdf" and meta["version_ui"] == "2.0"
    assert c.children("42") == ["5"]


def test_find_documents_delegates(fake_session, make_response):
    def _meta(case_url):
        xml = f'<z:row xmlns:z="#RowsetSchema" ows_CaseUrl="{case_url}" />'
        return make_response(json_data={"Metadata": xml})

    c = _client(fake_session, {
        "/ExecuteModernSearch": make_response(json_data={"results": {"Results": [
            {"title": "Navn Navnesen 140382-0209", "caseurl": "cases/PER55/PER-2026-000123"}]}}),
        "/CaseDetailsInternal": make_response(json_data={"d": {"ListId": "G1"}}),
        "lists(guid'": make_response(json_data={"Row": [{"CaseID": "PER-2026-000200", "Title": "Løn"}]}),
        "/_goapi/Cases/Metadata/": _meta("cases/PER55/PER-2026-000200"),
        "/GetLeftMenuCounter/": make_response(json_data=[{"ViewName": "Journaliseret.aspx", "ViewId": "V2"}]),
        "GetList(@listUrl)/RenderListDataAsStream": make_response(json_data={"Row": [
            {"DocID": 55, "CaseRecordNumber": 1, "Title": "L", "FileLeafRef.Name": "loen.pdf"}]}),
    })
    found = c.find_documents(cpr="1403820209")
    assert (found[0].dok_id, found[0].ext) == ("55", "pdf")
