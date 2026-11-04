"""Identify a file's container format from its bytes. Filenames and declared MIME types are ignored.

This decides how a file is opened (PDF, spreadsheet, image, archive), not what business
document it is. Business document type is decided later from content.
"""

import io
import zipfile
from dataclasses import dataclass
from enum import StrEnum


class FileKind(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"
    DOCX = "docx"
    ZIP = "zip"
    JPEG = "jpeg"
    PNG = "png"
    GIF = "gif"
    TIFF = "tiff"
    HEIC = "heic"
    WEBP = "webp"
    EML = "eml"
    TEXT = "text"
    UNKNOWN = "unknown"


MIME: dict[FileKind, str] = {
    FileKind.PDF: "application/pdf",
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.ZIP: "application/zip",
    FileKind.JPEG: "image/jpeg",
    FileKind.PNG: "image/png",
    FileKind.GIF: "image/gif",
    FileKind.TIFF: "image/tiff",
    FileKind.HEIC: "image/heic",
    FileKind.WEBP: "image/webp",
    FileKind.EML: "message/rfc822",
    FileKind.TEXT: "text/plain",
    FileKind.UNKNOWN: "application/octet-stream",
}

IMAGE_KINDS = frozenset(
    {FileKind.JPEG, FileKind.PNG, FileKind.GIF, FileKind.TIFF, FileKind.HEIC, FileKind.WEBP}
)
# Kinds the pipeline can read. Everything else is accepted, stored and reported as unsupported.
SUPPORTED_KINDS = frozenset({FileKind.PDF, FileKind.XLSX, FileKind.TEXT, *IMAGE_KINDS})


@dataclass(frozen=True)
class Sniffed:
    kind: FileKind
    mime: str


def sniff(data: bytes) -> Sniffed:
    kind = _kind(data)
    return Sniffed(kind, MIME[kind])


def _kind(data: bytes) -> FileKind:
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return FileKind.PDF
    if head.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return FileKind.GIF
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return FileKind.TIFF
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return FileKind.WEBP
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heim"):
        return FileKind.HEIC
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _zip_flavour(data)
    if _looks_like_email(data):
        return FileKind.EML
    if _looks_like_text(data):
        return FileKind.TEXT
    return FileKind.UNKNOWN


def _zip_flavour(data: bytes) -> FileKind:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return FileKind.ZIP  # let the archive expander report the corruption
    if "xl/workbook.xml" in names:
        return FileKind.XLSX
    if "word/document.xml" in names:
        return FileKind.DOCX
    return FileKind.ZIP


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    if not sample or b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# Mail that crossed the internet carries long relay headers (Received, ARC, DKIM, the provider's
# own) before From: one Gmail message through SES had 6 KB of them, so the whole header block is
# read, up to this much
EMAIL_HEADER_MAX = 64 * 1024


def _looks_like_email(data: bytes) -> bool:
    if not _looks_like_text(data):
        return False
    head = data[:EMAIL_HEADER_MAX].replace(b"\r\n", b"\n")
    header_block = b"\n" + head.split(b"\n\n", 1)[0].lower()
    return b"\nfrom:" in header_block and (
        b"\nmessage-id:" in header_block or b"\nmime-version:" in header_block
    )
