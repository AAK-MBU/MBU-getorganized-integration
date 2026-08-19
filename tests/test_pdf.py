"""Tests for the PDF conversion helpers. The LibreOffice-dependent path is
skipped when soffice isn't installed so the suite is green on any Linux box."""

import shutil

import pytest

from mbu_getorganized_integration import pdf


@pytest.mark.parametrize(
    "ext,expected",
    [
        ("pdf", "pdf"),
        (".PDF", "pdf"),
        ("docx", "office"),
        ("png", "image"),
        ("msg", "email"),
        ("eml", "email"),
        ("mp4", "skip"),
        ("weirdext", "unknown"),
    ],
)
def test_classify(ext, expected):
    assert pdf.classify(ext) == expected


def test_user_installation_arg_windows_and_posix():
    # Windows must get file:///C:/... (three slashes) or LibreOffice rejects
    # bootstrap.ini; Linux keeps file:///tmp/... — regression for that crash.
    from pathlib import PureWindowsPath, PurePosixPath
    assert pdf._user_installation_arg(PureWindowsPath(r"C:\Temp\lo")) == \
        "-env:UserInstallation=file:///C:/Temp/lo"
    assert pdf._user_installation_arg(PurePosixPath("/tmp/lo")) == \
        "-env:UserInstallation=file:///tmp/lo"


def test_convert_pdf_passthrough(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 x")
    out, status, note = pdf.convert_to_pdf(src, "pdf", tmp_path / "out")
    assert status == "ready"
    assert out == src


def test_convert_skip_type(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00\x00")
    out, status, note = pdf.convert_to_pdf(src, "mp4", tmp_path / "out")
    assert status == "skipped"
    assert out is None


def test_image_to_pdf_roundtrip(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "pic.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(src)

    out, status, note = pdf.convert_to_pdf(src, "png", tmp_path / "out")
    assert status == "ready", note
    assert out.exists() and out.read_bytes().startswith(b"%PDF")


@pytest.mark.skipif(
    not (shutil.which("soffice") or shutil.which("libreoffice")),
    reason="LibreOffice not installed",
)
def test_office_to_pdf_with_libreoffice(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("Hej med dig\n", encoding="utf-8")
    out, status, note = pdf.convert_to_pdf(src, "txt", tmp_path / "out")
    assert status == "ready", note
    assert out.exists() and out.read_bytes().startswith(b"%PDF")


# ----- PDF merge -------------------------------------------------------------


def _blank_pdf(path, pages=1):
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_merge_pdfs_concatenates_pages(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    a = _blank_pdf(tmp_path / "a.pdf", pages=1)
    b = _blank_pdf(tmp_path / "b.pdf", pages=2)
    out = pdf.merge_pdfs([a, b], tmp_path / "merged.pdf")
    assert out is not None and out.exists()
    assert len(pypdf.PdfReader(str(out)).pages) == 3


def test_merge_pdfs_empty_returns_none(tmp_path):
    assert pdf.merge_pdfs([], tmp_path / "x.pdf") is None
    assert pdf.merge_pdfs([None], tmp_path / "x.pdf") is None


# ----- Email extraction (.eml, no LibreOffice needed) ------------------------


def _eml_with_attachment():
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = "afsender@example.dk"
    m["To"] = "modtager@example.dk"
    m["Subject"] = "Emne med bilag"
    m.set_content("Hej, se vedhæftet.\n")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                     filename="bilag.pdf")
    return m.as_bytes()


def test_extract_email_pulls_attachment_bytes(tmp_path):
    src = tmp_path / "mail.eml"
    src.write_bytes(_eml_with_attachment())
    headers, body_html, body_text, attachments = pdf._extract_email(src, "eml")
    assert headers["Emne"] == "Emne med bilag"
    assert [name for name, _ in attachments] == ["bilag.pdf"]
    assert attachments[0][1].startswith(b"%PDF")  # bytes extracted


def test_email_to_pdf_orchestration_merges(monkeypatch, tmp_path):
    # Exercise the real email_to_pdf orchestration (extract → convert each
    # attachment → merge → move) + pypdf merge, faking only the LibreOffice
    # step so it runs without soffice installed.
    pypdf = pytest.importorskip("pypdf")

    att_pdf = _blank_pdf(tmp_path / "att.pdf", pages=1).read_bytes()
    from email.message import EmailMessage
    m = EmailMessage()
    m["Subject"] = "Emne"
    m.set_content("Hej\n")
    m.add_attachment(att_pdf, maintype="application", subtype="pdf", filename="bilag.pdf")
    src = tmp_path / "mail.eml"
    src.write_bytes(m.as_bytes())

    def fake_office(src_in, out_dir, **kw):
        from pathlib import Path
        out = Path(out_dir) / (Path(src_in).stem + ".pdf")
        _blank_pdf(out, pages=1)
        return out

    monkeypatch.setattr(pdf, "office_to_pdf", fake_office)

    out = pdf.email_to_pdf(src, "eml", tmp_path / "out")
    assert out is not None and out.exists()
    assert len(pypdf.PdfReader(str(out)).pages) == 2  # body + merged attachment


def test_is_msg_obj_ducktyping():
    # How an embedded .msg attachment (att.data is a Message, not bytes) is
    # detected so it recurses instead of being listed as "ikke medtaget".
    class FakeMsg:
        attachments = []
        body = "x"
    assert pdf._is_msg_obj(FakeMsg()) is True
    assert pdf._is_msg_obj(b"bytes") is False
    assert pdf._is_msg_obj(None) is False


def test_convert_targets_spreadsheet_vs_other():
    # Spreadsheets try SinglePageSheets first, plain pdf as fallback; others plain.
    xlsx = pdf._convert_targets("xlsx")
    assert xlsx[0].startswith("pdf:calc_pdf_Export") and "SinglePageSheets" in xlsx[0]
    assert xlsx[-1] == "pdf"
    assert pdf._convert_targets("csv")[0].startswith("pdf:calc_pdf_Export")
    assert pdf._convert_targets("docx") == ["pdf"]


def _eml_with_embedded_message():
    from email.message import EmailMessage
    att = _blank_pdf_bytes()
    inner = EmailMessage()
    inner["From"] = "indre@example.dk"
    inner["Subject"] = "Indre besked"
    inner.set_content("Indre tekst\n")
    inner.add_attachment(att, maintype="application", subtype="pdf", filename="indre_bilag.pdf")

    outer = EmailMessage()
    outer["From"] = "ydre@example.dk"
    outer["Subject"] = "Ydre besked"
    outer.set_content("Se vedhæftet besked\n")
    outer.add_attachment(inner, filename="videresendt.eml")  # → message/rfc822
    return outer.as_bytes()


def _blank_pdf_bytes(pages=1):
    import io
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_extract_email_recurses_embedded_message(tmp_path):
    src = tmp_path / "outer.eml"
    src.write_bytes(_eml_with_embedded_message())
    headers, body_html, body_text, attachments = pdf._extract_email(src, "eml")
    assert headers["Emne"] == "Ydre besked"
    assert len(attachments) == 1
    name, payload = attachments[0]
    # embedded message parsed into a nested ("email", parsed) payload
    assert isinstance(payload, tuple) and payload[0] == "email"
    inner_headers, _, _, inner_atts = payload[1]
    assert inner_headers["Emne"] == "Indre besked"
    assert [n for n, _ in inner_atts] == ["indre_bilag.pdf"]


def test_email_to_pdf_recurses_embedded_message(monkeypatch, tmp_path):
    # Deep merge: outer body + (inner body + inner attachment) = 3 pages, with a
    # faked LibreOffice so it runs without soffice.
    pypdf = pytest.importorskip("pypdf")
    src = tmp_path / "outer.eml"
    src.write_bytes(_eml_with_embedded_message())

    def fake_office(src_in, out_dir, **kw):
        from pathlib import Path
        out = Path(out_dir) / (Path(src_in).stem + ".pdf")
        _blank_pdf(out, pages=1)
        return out

    monkeypatch.setattr(pdf, "office_to_pdf", fake_office)
    out = pdf.email_to_pdf(src, "eml", tmp_path / "out")
    assert out is not None and out.exists()
    assert len(pypdf.PdfReader(str(out)).pages) == 3


@pytest.mark.skipif(
    not (shutil.which("soffice") or shutil.which("libreoffice")),
    reason="LibreOffice not installed",
)
def test_email_to_pdf_merges_attachment(tmp_path):
    pytest.importorskip("pypdf")
    import pypdf
    src = tmp_path / "mail.eml"
    src.write_bytes(_eml_with_attachment())
    out = pdf.email_to_pdf(src, "eml", tmp_path / "out")
    assert out is not None and out.exists()
    # body (>=1 page) + the 1-page attachment PDF merged in
    assert len(pypdf.PdfReader(str(out)).pages) >= 2
