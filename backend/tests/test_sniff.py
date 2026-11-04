import pytest

from lane.intake.sniff import FileKind, sniff
from tests.factories import JPEG, PDF, PNG, make_email, make_zip, ooxml


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (PDF, FileKind.PDF),
        (PNG, FileKind.PNG),
        (JPEG, FileKind.JPEG),
        (ooxml("xl/workbook.xml"), FileKind.XLSX),
        (ooxml("word/document.xml"), FileKind.DOCX),
        (make_zip({"a.txt": b"hi"}), FileKind.ZIP),
        (make_email(), FileKind.EML),
        (b"FEIN 12-3456789\nplain notes", FileKind.TEXT),
        (b"\x00\x01\x02binary", FileKind.UNKNOWN),
        (b"", FileKind.UNKNOWN),
    ],
)
def test_sniff_by_content(data: bytes, kind: FileKind) -> None:
    assert sniff(data).kind is kind


def test_sniff_ignores_what_the_file_claims_to_be() -> None:
    # A PDF that someone named and labelled as a spreadsheet is still a PDF.
    assert sniff(PDF).mime == "application/pdf"


def test_an_email_with_long_relay_headers_is_still_an_email() -> None:
    """Mail delivered over the internet (here: Gmail through SES) carries kilobytes of Received,
    ARC and DKIM headers before From, with CRLF line ends."""
    relay = "".join(
        f"Received: from relay{i}.example.net (relay{i}.example.net [192.0.2.{i}])\r\n"
        f" by inbound-smtp.us-east-1.amazonaws.com with SMTP id {'x' * 40}\r\n"
        for i in range(40)
    )
    raw = (
        relay + "MIME-Version: 1.0\r\nFrom: Agent <agent@agency.example>\r\n"
        "Message-ID: <abc@agency.example>\r\nSubject: New submission\r\n\r\nPlease quote.\r\n"
    ).encode()
    assert raw.index(b"From:") > 4096
    assert sniff(raw).kind is FileKind.EML
