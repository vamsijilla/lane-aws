"""Builders for test emails, archives and files. All synthetic; no packet data."""

import io
import zipfile
from email.message import EmailMessage

PDF = b"%PDF-1.7\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def ooxml(marker: str) -> bytes:
    """A minimal Office Open XML package identified by its marker part."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(marker, "<x/>")
    return buf.getvalue()


def make_zip(files: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_email(
    attachments: list[tuple[str, bytes]] | None = None,
    *,
    body: str = "Please see attached.",
    html: str | None = None,
    message_id: str | None = "<test-1@agency.example>",
    subject: str = "New submission",
    sender: str = "Agent <agent@agency.example>",
    headers: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "submissions@carrier.example"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 22 Oct 2026 10:00:00 -0700"
    if message_id:
        msg["Message-ID"] = message_id
    for name, value in (headers or {}).items():
        msg[name] = value
    if html is not None:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(body)
    for name, data in attachments or []:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()
