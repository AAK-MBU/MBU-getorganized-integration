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
