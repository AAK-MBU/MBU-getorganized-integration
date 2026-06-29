"""PDF conversion helpers (Linux-first).

Converts the file types GO yields into PDF so downstream OCR/redaction can work:

* already PDF           → passthrough
* office / text / html  → LibreOffice headless (``soffice --convert-to pdf``)
* images                → Pillow (wrapped into a single-page PDF)
* .msg / .eml           → rendered to HTML, then LibreOffice
* video / audio / unknown-binary → skipped (caller marks "kan ikke konverteres")

OCR is intentionally **not** here — it runs in the application after conversion.
This module is a slimmed, Linux-first vendoring of ``mtm-aarhus/oomtm``'s
``oomtm.pdf``: the Windows no-admin MSI/7-Zip auto-install, the Tesseract
plumbing, and the MS Office COM path have all been dropped. LibreOffice must be
present on the host (``apt-get install libreoffice``); point at a non-standard
binary with ``LIBREOFFICE_PATH``.

Heavy optional imports (Pillow, extract_msg) are lazy so a missing optional dep
only breaks the path that needs it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Extension classification
# ---------------------------------------------------------------------------

PDF_EXTS = {"pdf"}

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "ico"}

EMAIL_EXTS = {"msg", "eml"}

# Formats LibreOffice handles well.
OFFICE_EXTS = {
    "doc", "docx", "docm", "dot", "dotx", "odt", "fodt", "rtf", "txt",
    "csv", "tsv",
    "xls", "xlsx", "xlsm", "xlsb", "xltx", "ods", "fods",
    "ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp", "fodp",
    "htm", "html", "xml", "vsd", "vsdx", "pub",
}

# Things we won't try to convert. Caller marks these "kan ikke konverteres".
SKIP_EXTS = {
    # video
    "mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "mpg", "mpeg",
    # audio
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "wma",
    # archives / binaries
    "zip", "rar", "7z", "tar", "gz", "exe", "dll", "iso", "bin",
}


def classify(ext: str) -> str:
    """Return one of: 'pdf', 'image', 'email', 'office', 'skip', 'unknown'."""
    e = (ext or "").lower().lstrip(".")
    if e in PDF_EXTS:
        return "pdf"
    if e in IMAGE_EXTS:
        return "image"
    if e in EMAIL_EXTS:
        return "email"
    if e in OFFICE_EXTS:
        return "office"
    if e in SKIP_EXTS:
        return "skip"
    return "unknown"


# ---------------------------------------------------------------------------
# LibreOffice
# ---------------------------------------------------------------------------

_DEFAULT_SOFFICE_PATHS = [
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
]


def find_soffice(soffice_path: str | None = None) -> str:
    """Locate the LibreOffice binary.

    Order: explicit arg, ``LIBREOFFICE_PATH`` env, ``PATH`` lookup, then the
    usual Linux install locations. Raises ``RuntimeError`` if not found.
    """
    candidates: list[str] = []
    if soffice_path:
        candidates.append(soffice_path)
    env = os.getenv("LIBREOFFICE_PATH")
    if env:
        candidates.append(env)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend(_DEFAULT_SOFFICE_PATHS)
    for c in candidates:
        p = Path(c) if c else None
        if p and p.exists():
            return str(p)
    raise RuntimeError(
        "LibreOffice (soffice) not found. Install it (apt-get install "
        "libreoffice) or set LIBREOFFICE_PATH."
    )


def office_to_pdf(
    src: str | Path,
    out_dir: str | Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 240,
) -> Path | None:
    """Convert an office/text/html file to PDF via LibreOffice headless.

    Returns the path to the produced PDF, or None if LibreOffice produced no
    output. Uses a throwaway user-profile dir per call so multiple conversions
    can run in parallel without clobbering each other's profile lock.
    """
    soffice = find_soffice(soffice_path)
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    try:
        cmd = [
            soffice,
            "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation=file://{profile.as_posix()}",
            "--convert-to", "pdf",
            "--outdir", str(out_dir),
            str(src),
        ]
        subprocess.run(
            cmd, check=True, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    produced = out_dir / (src.stem + ".pdf")
    return produced if produced.exists() and produced.stat().st_size > 0 else None


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def image_to_pdf(src: str | Path, out_path: str | Path) -> Path | None:
    """Wrap a raster image into a single-page PDF using Pillow."""
    try:
        from PIL import Image  # lazy
    except ImportError:
        return None
    try:
        from PIL import ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except Exception:  # pylint: disable=broad-except
        pass

    out_path = Path(out_path)
    try:
        with Image.open(src) as im:
            # PDF can't store alpha; flatten onto white.
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                from PIL import Image as _Image
                bg = _Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.save(out_path, "PDF", resolution=150.0)
    except Exception:  # pylint: disable=broad-except
        return None
    return out_path if out_path.exists() and out_path.stat().st_size > 0 else None


# ---------------------------------------------------------------------------
# Email (.msg / .eml) → HTML → PDF
# ---------------------------------------------------------------------------


def _email_to_html(src: str | Path, ext: str) -> str | None:
    """Render an email's headers + body to a standalone HTML string.

    Attachments are NOT extracted (out of scope) — they're listed by name in a
    footer so the reviewer knows they existed.
    """
    ext = (ext or "").lower().lstrip(".")
    headers: dict[str, str] = {}
    body_html = None
    body_text = None
    attachments: list[str] = []

    if ext == "msg":
        try:
            import extract_msg  # lazy
        except ImportError:
            return None
        try:
            msg = extract_msg.openMsg(str(src))
            try:
                headers = {
                    "Fra": msg.sender or "",
                    "Til": msg.to or "",
                    "Cc": msg.cc or "",
                    "Dato": str(msg.date or ""),
                    "Emne": msg.subject or "",
                }
                body_html = getattr(msg, "htmlBody", None)
                if isinstance(body_html, bytes):
                    body_html = body_html.decode("utf-8", errors="replace")
                body_text = msg.body
                for att in (msg.attachments or []):
                    name = (att.longFilename or att.shortFilename or "ukendt").replace("\x00", "").strip()
                    if name:
                        attachments.append(name)
            finally:
                msg.close()
        except Exception:  # pylint: disable=broad-except
            return None

    elif ext == "eml":
        from email import policy
        from email.parser import BytesParser
        try:
            with open(src, "rb") as fh:
                m = BytesParser(policy=policy.default).parse(fh)
            headers = {
                "Fra": m.get("From", ""),
                "Til": m.get("To", ""),
                "Cc": m.get("Cc", ""),
                "Dato": m.get("Date", ""),
                "Emne": m.get("Subject", ""),
            }
            html_part = m.get_body(preferencelist=("html",))
            text_part = m.get_body(preferencelist=("plain",))
            if html_part is not None:
                body_html = html_part.get_content()
            if text_part is not None:
                body_text = text_part.get_content()
            for part in m.iter_attachments():
                fn = part.get_filename()
                if fn:
                    attachments.append(fn)
        except Exception:  # pylint: disable=broad-except
            return None
    else:
        return None

    def esc(s: str) -> str:
        return (str(s or "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    header_rows = "".join(
        f"<tr><td style='font-weight:bold;padding-right:10px;vertical-align:top'>{esc(k)}</td>"
        f"<td>{esc(v)}</td></tr>"
        for k, v in headers.items() if v
    )
    if body_html:
        body_block = body_html  # already HTML
    else:
        body_block = "<pre style='white-space:pre-wrap;font-family:inherit'>" + esc(body_text or "") + "</pre>"
    att_block = ""
    if attachments:
        items = "".join(f"<li>{esc(a)}</li>" for a in attachments)
        att_block = (
            "<hr><p style='font-weight:bold'>Vedhæftede filer "
            "(ikke medtaget i denne PDF):</p><ul>" + items + "</ul>"
        )

    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8">
<style>body{{font-family:Arial,Helvetica,sans-serif;font-size:11pt;color:#000}}
table{{margin-bottom:14px;border-collapse:collapse}}</style></head>
<body>
<table>{header_rows}</table>
<hr>
{body_block}
{att_block}
</body></html>"""


def email_to_pdf(
    src: str | Path,
    ext: str,
    out_dir: str | Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 240,
) -> Path | None:
    html = _email_to_html(src, ext)
    if html is None:
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{Path(src).stem}.html"
    html_path.write_text(html, encoding="utf-8")
    try:
        return office_to_pdf(html_path, out_dir, soffice_path=soffice_path, timeout=timeout)
    finally:
        html_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def convert_to_pdf(
    src: str | Path,
    ext: str,
    out_dir: str | Path,
    *,
    soffice_path: str | None = None,
    log=None,
) -> tuple[Path | None, str, str]:
    """Convert ``src`` to PDF, choosing the method from ``ext``.

    Returns ``(pdf_path, status, note)`` where status is one of:
      * ``"ready"``   — pdf_path points to a usable PDF
      * ``"skipped"`` — deliberately not converted (video/audio/unknown)
      * ``"error"``   — conversion was attempted but failed

    Office/email/unknown documents go through LibreOffice headless (which must be
    installed on the host); images go through Pillow; PDFs pass through.
    """
    log = log or (lambda *_: None)
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = classify(ext)

    if kind == "pdf":
        return src, "ready", ""

    if kind == "image":
        out = out_dir / f"{src.stem}.pdf"
        result = image_to_pdf(src, out)
        if result:
            return result, "ready", ""
        return None, "error", f"Billedet kunne ikke konverteres ({ext})."

    if kind == "skip":
        return None, "skipped", f"Filtypen {ext} kan ikke konverteres til PDF (gennemse manuelt)."

    # office / email / unknown all need LibreOffice.
    try:
        soffice_path = find_soffice(soffice_path)
    except RuntimeError as exc:
        return None, "error", str(exc)

    if kind == "email":
        result = email_to_pdf(src, ext, out_dir, soffice_path=soffice_path)
        if result:
            return result, "ready", ""
        return None, "error", f"E-mailen kunne ikke konverteres ({ext})."

    if kind == "office":
        result = office_to_pdf(src, out_dir, soffice_path=soffice_path)
        if result:
            return result, "ready", ""
        return None, "error", f"Kunne ikke konvertere filen ({ext})."

    # unknown — LibreOffice as a last resort; it handles many odd formats.
    result = office_to_pdf(src, out_dir, soffice_path=soffice_path)
    if result:
        return result, "ready", ""
    return None, "skipped", f"Ukendt filtype ({ext}) — kunne ikke konverteres automatisk."
