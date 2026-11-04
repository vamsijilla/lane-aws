#!/usr/bin/env python3
"""Build the sample agent email from the files in a packet folder.

The output is byte-for-byte deterministic (fixed Message-ID, Date, MIME boundary and zip
timestamps), so building twice and ingesting both copies must yield one submission.

By default the files are attached individually and also as a zip, the way agents often send
"in case your system strips attachments". That exercises archive expansion and dedupe.
"""

import argparse
import io
import mimetypes
import zipfile
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKET = REPO / "data" / "packet_cobalt_mesa"
ZIP_NAME = "Sample Submission - Cobalt Mesa Transport.zip"
ZIP_EPOCH = (2026, 10, 22, 16, 30, 0)

BODY = """\
Hi team,

Please see attached new business submission for Cobalt Mesa Transport,
requesting an effective date of 12/15/2026.

Coverages requested: auto liability $1,000,000 and motor truck cargo $100,000.

Attached: application, quick quote form, driver and vehicle lists, MVRs,
loss runs for the prior terms and the last four quarters of IFTA. I have also
zipped everything in case your system strips attachments.

Let me know if you need anything else to quote.

Thanks,
Daniel Okafor
(925) 555-0164
"""


def build(packet: Path, *, with_zip: bool, shuffle_names: bool = False) -> bytes:
    files = sorted(p for p in packet.iterdir() if p.is_file() and not p.name.startswith((".", "~$")))
    if not files:
        raise SystemExit(f"No files found in {packet}")
    # Perturbation: every file is sent under another file's name (same bytes, wrong label).
    names = [p.name for p in files]
    if shuffle_names:
        names = names[7:] + names[:7]

    msg = EmailMessage()
    msg["From"] = "Daniel Okafor <dokafor@baylineinsurance.example>"
    msg["To"] = "Knight Submissions <submissions@knight-casestudy.example>"
    # Neutral: the email names the applicant without a legal form and the agency only by its
    # address, so no fact about entity form or agency name rests on it
    msg["Subject"] = "New business submission: Cobalt Mesa Transport"
    msg["Date"] = "Thu, 22 Oct 2026 16:42:00 -0700"
    suffix = "-renamed" if shuffle_names else ""
    msg["Message-ID"] = f"<cobalt-mesa-2026-10-22{suffix}.dokafor@baylineinsurance.example>"
    msg.set_content(BODY)

    for path, name in zip(files, names, strict=True):
        maintype, subtype = _mime(path)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=name)
    if with_zip:
        msg.add_attachment(_zip(files), maintype="application", subtype="zip", filename=ZIP_NAME)

    msg.set_boundary("lane-sample-boundary-7f3a")
    return msg.as_bytes(policy=SMTP)


def _mime(path: Path) -> tuple[str, str]:
    guessed, _ = mimetypes.guess_type(path.name)
    maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
    return maintype, subtype


def _zip(files: list[Path]) -> bytes:
    folder = ZIP_NAME.removesuffix(".zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(f"{folder}/{path.name}", date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packet", type=Path, default=PACKET)
    parser.add_argument("--out", type=Path, default=REPO / "var" / "sample" / "cobalt_mesa.eml")
    parser.add_argument("--no-zip", action="store_true", help="attach files individually only")
    parser.add_argument(
        "--shuffle-names", action="store_true", help="send each file under another file's name"
    )
    args = parser.parse_args()

    raw = build(args.packet, with_zip=not args.no_zip, shuffle_names=args.shuffle_names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(raw)
    print(f"Wrote {args.out} ({len(raw):,} bytes)")


if __name__ == "__main__":
    main()
