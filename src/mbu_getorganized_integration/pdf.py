"""PDF conversion helpers (Linux-first).

Converts the file types GO yields into PDF so downstream OCR/redaction can work:

* already PDF           → passthrough
* office / text / html  → LibreOffice headless (``soffice --convert-to pdf``)
* images                → Pillow (wrapped into a single-page PDF)
* .msg / .eml           → body rendered to HTML then LibreOffice, with each
                          embedded attachment converted and merged in after it
                          (one PDF: message first, then attachments) via ``pypdf``;
                          embedded messages (mail attached to mail) recurse
* spreadsheets          → exported one-sheet-per-page (``SinglePageSheets``) so
                          wide/landscape sheets aren't sliced across pages
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

# Spreadsheets — converted with LibreOffice's Calc "SinglePageSheets" export so
# wide/landscape sheets aren't sliced across pages (each sheet → one PDF page).
SPREADSHEET_EXTS = {
    "xls", "xlsx", "xlsm", "xlsb", "xltx", "ods", "fods", "csv", "tsv",
}

# Formats LibreOffice handles well.
OFFICE_EXTS = {
    "doc", "docx", "docm", "dot", "dotx", "odt", "fodt", "rtf", "txt",
    "ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp", "fodp",
    "htm", "html", "xml", "vsd", "vsdx", "pub",
} | SPREADSHEET_EXTS

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
    # Linux
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
    # Windows (the host that runs the gather script)
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _user_installation_arg(profile) -> str:
    """Build LibreOffice's ``-env:UserInstallation`` as a valid file URI on any OS.

    The naive ``file://{path}`` breaks on Windows: ``file://C:/...`` has only two
    slashes, so LibreOffice treats ``C:`` as a network host and rejects its own
    bootstrap.ini ("Konfigurationsfilen ... indeholder fejl"). ``Path.as_uri()``
    yields the required ``file:///C:/...`` on Windows and ``file:///tmp/...`` on
    Linux.
    """
    return f"-env:UserInstallation={profile.as_uri()}"


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


# LibreOffice Calc PDF export that fits each sheet onto a single page — keeps
# wide/landscape spreadsheets from being sliced across page boundaries.
_CALC_SINGLE_PAGE = (
    'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'
)


def _convert_targets(ext: str) -> list[str]:
    """The ``--convert-to`` argument(s) to try for ``ext``, best first.

    Spreadsheets get the single-page-per-sheet export first (so nothing is cut
    off), with a plain ``pdf`` fallback for LibreOffice builds that don't know
    the ``SinglePageSheets`` option.
    """
    if ext in SPREADSHEET_EXTS:
        return [_CALC_SINGLE_PAGE, "pdf"]
    return ["pdf"]


def office_to_pdf(
    src: str | Path,
    out_dir: str | Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 240,
) -> Path | None:
    """Convert an office/text/html/spreadsheet file to PDF via LibreOffice headless.

    Returns the path to the produced PDF, or None if LibreOffice produced no
    output. Uses a throwaway user-profile dir per call so multiple conversions
    can run in parallel without clobbering each other's profile lock. Wide
    spreadsheets are exported one-sheet-per-page (see :data:`SPREADSHEET_EXTS`)
    so their columns aren't truncated.
    """
    soffice = find_soffice(soffice_path)
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower().lstrip(".")
    produced = out_dir / (src.stem + ".pdf")

    for target in _convert_targets(ext):
        profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
        try:
            cmd = [
                soffice,
                "--headless", "--norestore", "--nolockcheck", "--nodefault",
                _user_installation_arg(profile),
                "--convert-to", target,
                "--outdir", str(out_dir),
                str(src),
            ]
            subprocess.run(
                cmd, check=True, timeout=timeout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if produced.exists() and produced.stat().st_size > 0:
            return produced
    return None


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
# PDF merge
# ---------------------------------------------------------------------------


def merge_pdfs(pdf_paths, out_path: str | Path) -> Path | None:
    """Concatenate ``pdf_paths`` (in order) into a single PDF at ``out_path``.

    Returns the output path, or ``None`` if there was nothing to merge, ``pypdf``
    is unavailable, or the merge failed. Used to fold an email's attachments in
    after its body.
    """
    paths = [Path(p) for p in pdf_paths if p]
    if not paths:
        return None
    try:
        from pypdf import PdfWriter  # lazy
    except ImportError:
        return None

    out_path = Path(out_path)
    writer = PdfWriter()
    try:
        for p in paths:
            writer.append(str(p))
        with open(out_path, "wb") as fh:
            writer.write(fh)
    except Exception:  # pylint: disable=broad-except
        return None
    finally:
        try:
            writer.close()
        except Exception:  # pylint: disable=broad-except
            pass
    return out_path if out_path.exists() and out_path.stat().st_size > 0 else None


# ---------------------------------------------------------------------------
# Email (.msg / .eml) → HTML body → PDF, with embedded attachments merged in
# ---------------------------------------------------------------------------


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _safe_component(name: str) -> str:
    """Filesystem-safe basename for a temp attachment file."""
    keep = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in (name or ""))
    return keep.strip() or "fil"


# An email is parsed into (headers, body_html, body_text, attachments) where each
# attachment is (filename, payload) and payload is one of:
#   * bytes            — a file attachment (converted + merged)
#   * ("email", parsed)— an embedded message, recursively parsed (rendered + merged)
#   * None             — present but not extractable (only listed in the footer)


def _is_msg_obj(x) -> bool:
    """True for an extract_msg Message-like object (an embedded .msg attachment)."""
    return (x is not None and not isinstance(x, (bytes, bytearray))
            and hasattr(x, "attachments") and (hasattr(x, "htmlBody") or hasattr(x, "body")))


def _extract_msg_obj(msg):
    """Extract the parsed tuple from an open ``extract_msg`` Message.

    Embedded message attachments (``.msg`` inside ``.msg``) recurse into nested
    ``("email", parsed)`` payloads — extracted eagerly here, while the parent is
    still open — so they can be rendered and merged like any other attachment.
    """
    headers = {
        "Fra": getattr(msg, "sender", "") or "",
        "Til": getattr(msg, "to", "") or "",
        "Cc": getattr(msg, "cc", "") or "",
        "Dato": str(getattr(msg, "date", "") or ""),
        "Emne": getattr(msg, "subject", "") or "",
    }
    body_html = getattr(msg, "htmlBody", None)
    if isinstance(body_html, bytes):
        body_html = body_html.decode("utf-8", errors="replace")
    body_text = getattr(msg, "body", None)

    attachments: list[tuple[str, object]] = []
    for att in (getattr(msg, "attachments", None) or []):
        name = (getattr(att, "longFilename", None)
                or getattr(att, "shortFilename", None) or "").replace("\x00", "").strip()
        data = getattr(att, "data", None)
        cid = getattr(att, "cid", None) or getattr(att, "contentId", None) or ""
        if isinstance(data, (bytes, bytearray)):
            if cid and body_html and cid in body_html:
                continue  # inline image already shown in the body
            attachments.append((name or "vedhaeftet_fil", bytes(data)))
        elif _is_msg_obj(data):
            attachments.append((name or "vedhaeftet_besked.msg", ("email", _extract_msg_obj(data))))
        else:
            attachments.append((name or "vedhaeftet", None))
    return headers, body_html, body_text, attachments


def _extract_eml_obj(m):
    """Extract the parsed tuple from a stdlib ``email.message`` object.

    ``message/rfc822`` attachments (an email forwarded as an attachment) recurse
    into nested ``("email", parsed)`` payloads, mirroring the .msg path.
    """
    headers = {
        "Fra": m.get("From", ""),
        "Til": m.get("To", ""),
        "Cc": m.get("Cc", ""),
        "Dato": m.get("Date", ""),
        "Emne": m.get("Subject", ""),
    }
    body_html = None
    body_text = None
    html_part = m.get_body(preferencelist=("html",))
    text_part = m.get_body(preferencelist=("plain",))
    if html_part is not None:
        body_html = html_part.get_content()
    if text_part is not None:
        body_text = text_part.get_content()

    attachments: list[tuple[str, object]] = []
    for part in m.iter_attachments():
        fn = part.get_filename()
        if part.get_content_type() == "message/rfc822":
            try:
                sub = part.get_content()  # a nested email.message object
                attachments.append((fn or "vedhaeftet_besked.eml", ("email", _extract_eml_obj(sub))))
                continue
            except Exception:  # pylint: disable=broad-except
                attachments.append((fn or "vedhaeftet_besked.eml", None))
                continue
        payload = part.get_payload(decode=True)
        attachments.append(
            (fn or "vedhaeftet_fil", payload if isinstance(payload, (bytes, bytearray)) else None)
        )
    return headers, body_html, body_text, attachments


def _extract_email(src: str | Path, ext: str):
    """Parse a ``.msg``/``.eml`` file into the parsed tuple (see the note above).

    Returns ``None`` if the type is unsupported, ``extract_msg`` is missing, or
    the file can't be parsed.
    """
    ext = (ext or "").lower().lstrip(".")
    if ext == "msg":
        try:
            import extract_msg  # lazy
        except ImportError:
            return None
        try:
            msg = extract_msg.openMsg(str(src))
        except Exception:  # pylint: disable=broad-except
            return None
        try:
            return _extract_msg_obj(msg)
        finally:
            msg.close()

    if ext == "eml":
        from email import policy
        from email.parser import BytesParser
        try:
            with open(src, "rb") as fh:
                return _extract_eml_obj(BytesParser(policy=policy.default).parse(fh))
        except Exception:  # pylint: disable=broad-except
            return None

    return None


def _email_body_html(headers, body_html, body_text, attachment_notes) -> str:
    """Build the standalone HTML for the message body + an attachment footer."""
    header_rows = "".join(
        f"<tr><td style='font-weight:bold;padding-right:10px;vertical-align:top'>{_esc(k)}</td>"
        f"<td>{_esc(v)}</td></tr>"
        for k, v in headers.items() if v
    )
    body_block = body_html if body_html else (
        "<pre style='white-space:pre-wrap;font-family:inherit'>" + _esc(body_text or "") + "</pre>"
    )
    att_block = ""
    if attachment_notes:
        items = "".join(f"<li>{_esc(a)}</li>" for a in attachment_notes)
        att_block = "<hr><p style='font-weight:bold'>Vedhæftede filer:</p><ul>" + items + "</ul>"
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


def _render_parsed(
    parsed,
    out_dir: str | Path,
    *,
    stem: str,
    soffice_path: str | None = None,
    timeout: int = 240,
    merge_attachments: bool = True,
) -> Path | None:
    """Render a parsed email tuple to a single PDF named ``{stem}.pdf``.

    Each attachment is converted and merged after the message body; embedded
    messages recurse through this same function. Attachments that can't be
    converted are listed in the footer marked "ikke medtaget". Returns the PDF
    path, or ``None`` on failure.
    """
    headers, body_html, body_text, attachments = parsed
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="email_", dir=str(out_dir)))
    try:
        att_pdfs: list[Path] = []
        notes: list[str] = []
        if merge_attachments:
            for idx, (name, payload) in enumerate(attachments):
                # Embedded message → recurse into its own merged PDF.
                if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "email":
                    sub_pdf = _render_parsed(
                        payload[1], work, stem=f"{_safe_component(stem)}_sub{idx}",
                        soffice_path=soffice_path, timeout=timeout,
                    )
                    if sub_pdf is not None:
                        att_pdfs.append(sub_pdf)
                        notes.append(f"{name} (besked)")
                    else:
                        notes.append(f"{name} (ikke medtaget)")
                    continue
                # Regular file attachment.
                aext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if not isinstance(payload, (bytes, bytearray)) or not aext:
                    notes.append(f"{name} (ikke medtaget)")
                    continue
                att_src = work / f"att{idx}_{_safe_component(name)}"
                att_src.write_bytes(bytes(payload))
                pdf_path, status, _ = convert_to_pdf(att_src, aext, work, soffice_path=soffice_path)
                if status == "ready" and pdf_path is not None:
                    att_pdfs.append(pdf_path)
                    notes.append(name)
                else:
                    notes.append(f"{name} (ikke medtaget)")
        else:
            notes = [n for (n, _) in attachments]

        html = _email_body_html(headers, body_html, body_text, notes)
        html_path = work / f"{_safe_component(stem)}.html"
        html_path.write_text(html, encoding="utf-8")
        body_pdf = office_to_pdf(html_path, work, soffice_path=soffice_path, timeout=timeout)
        if body_pdf is None:
            return None

        final = out_dir / f"{_safe_component(stem)}.pdf"
        if att_pdfs:
            merged = merge_pdfs([body_pdf] + att_pdfs, final)
            if merged is not None:
                return merged
            # merge failed → fall back to the body-only PDF
        shutil.move(str(body_pdf), str(final))
        return final if final.exists() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def email_to_pdf(
    src: str | Path,
    ext: str,
    out_dir: str | Path,
    *,
    soffice_path: str | None = None,
    timeout: int = 240,
    merge_attachments: bool = True,
) -> Path | None:
    """Render an email (.msg/.eml) to a single PDF.

    With ``merge_attachments`` (default), each embedded file attachment is
    converted to PDF and appended after the message body — message first, then
    attachments in order — so the whole thing is one PDF. Embedded messages
    (a ``.msg``/``.eml`` attached to the mail) are rendered recursively and
    merged too. Attachments that can't be converted are listed in the message
    footer marked "ikke medtaget". Returns the PDF path, or ``None`` on failure.
    """
    parsed = _extract_email(src, ext)
    if parsed is None:
        return None
    return _render_parsed(
        parsed, out_dir, stem=Path(src).stem,
        soffice_path=soffice_path, timeout=timeout, merge_attachments=merge_attachments,
    )


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
