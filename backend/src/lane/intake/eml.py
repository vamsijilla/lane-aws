"""Parse an RFC 5322 email into headers, a readable body and attachment parts."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath, PureWindowsPath

# Headers kept for audit and display. Everything else stays in the raw .eml blob.
KEPT_HEADERS = (
    "From",
    "To",
    "Cc",
    "Reply-To",
    "Subject",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
)


@dataclass(frozen=True)
class EmailPart:
    filename: str
    declared_mime: str
    data: bytes


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str | None
    from_addr: str
    to_addrs: list[str]
    subject: str
    sent_at: datetime | None
    headers: dict[str, str]
    body_text: str
    parts: list[EmailPart] = field(default_factory=lambda: list[EmailPart]())


class EmailParseError(ValueError):
    pass


def parse_eml(raw: bytes) -> ParsedEmail:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    if not msg.keys():
        raise EmailParseError("No email headers found")
    if msg.defects and not msg.get("From"):
        raise EmailParseError(f"Unreadable email: {msg.defects[0].__class__.__name__}")

    body_part = msg.get_body(preferencelist=("plain", "html"))
    return ParsedEmail(
        message_id=_normalize_message_id(msg.get("Message-ID")),
        from_addr=_first_address(msg, "From"),
        to_addrs=_addresses(msg, "To") + _addresses(msg, "Cc"),
        subject=str(msg.get("Subject", "")).strip(),
        sent_at=_parse_date(msg.get("Date")),
        headers={h: str(msg[h]) for h in KEPT_HEADERS if msg.get(h) is not None},
        body_text=_body_text(body_part),
        parts=_attachment_parts(msg, body_part),
    )


def _attachment_parts(msg: EmailMessage, body_part: MIMEPart | None) -> list[EmailPart]:
    parts: list[EmailPart] = []
    for index, part in enumerate(msg.walk()):
        if part.is_multipart() or part is body_part:
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        # Alternative bodies (the HTML twin of a plain-text body) are not attachments.
        if (
            filename is None
            and disposition != "attachment"
            and part.get_content_maintype() == "text"
        ):
            continue
        payload = part.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else part.as_bytes()
        parts.append(
            EmailPart(
                filename=safe_filename(filename, fallback=f"attachment-{index}"),
                declared_mime=content_type,
                data=data,
            )
        )
    return parts


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(name: str | None, *, fallback: str) -> str:
    """Display name only. Never used as a filesystem path."""
    if not name:
        return fallback
    base = PureWindowsPath(PurePosixPath(name).name).name
    cleaned = _CONTROL.sub("", base).strip()
    return cleaned[:255] or fallback


def _normalize_message_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.strip("<>").strip().lower() or None


def _addresses(msg: EmailMessage, header: str) -> list[str]:
    value = msg.get(header)
    if value is None:
        return []
    addresses: tuple[Address, ...] = getattr(value, "addresses", ())
    return [a.addr_spec.lower() for a in addresses if a.addr_spec]


def _first_address(msg: EmailMessage, header: str) -> str:
    found = _addresses(msg, header)
    return found[0] if found else ""


def _parse_date(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None


def _body_text(part: MIMEPart | None) -> str:
    if part is None:
        return ""
    content = part.get_content()
    text = content if isinstance(content, str) else ""
    if part.get_content_subtype() == "html":
        text = html_to_text(text)
    return text.replace("\r\n", "\n").strip()


class _TextExtractor(HTMLParser):
    BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"})

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.chunks)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
