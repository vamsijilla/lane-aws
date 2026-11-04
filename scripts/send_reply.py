#!/usr/bin/env python3
"""The agent answers Lane's request for the sample packet: a reply with the reference token in
the subject, the FEIN in the body, the signed application and an owner exclusion letter.

    uv run python ../scripts/send_reply.py [--send] [--submission ID] [--out var/demo_reply.eml]

The letter and the FEIN are written for the demo (docs/ASSUMPTIONS.md, A-DEMO-01); everything
else (names, DOT, MC, the application date, the agent) is the sample packet's own. With --send it
goes to the local Mailpit (127.0.0.1:1025), the way an agent's reply would arrive. Nothing leaves
the machine.

Expected (click Mark as sent on Next actions first): the reply attaches by its reference;
APP-01 refer to pass; DRV-01 potential decline to can't check yet (the owner is excluded, but
Kevin Brandt, found only in loss history, still has no MVR), pending underwriter confirmation:
the route stays a potential decline until someone clicks Confirm, then becomes a plain refer to
Small Fleet. Measured against the request marked sent: 3 of 24 points answered, before quote 2 of
21 (the FEIN; who drives, one question), before binding 1 of 3 (the signature), no new questions.
Without Mark as sent it is measured against the last draft, and the Brief says so.
"""

import argparse
import json
import os
import smtplib
import sys
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from lane.intake.link import reference  # noqa: E402

# Inside the deploy stack these name the containers (deploy/send-sample.sh); locally, this Mac
API = os.environ.get("LANE_LOCAL_API", "http://127.0.0.1:8000") + "/api/v1/submissions"
SMTP_HOST = os.environ.get("LANE_LOCAL_SMTP", "127.0.0.1")
if SMTP_HOST not in ("127.0.0.1", "localhost", "::1", "mailpit"):
    raise SystemExit("This sends to a local test server only")
AGENT = "Daniel Okafor <dokafor@baylineinsurance.example>"
TO = "Knight Submissions <submissions@knight-casestudy.example>"
ORIGINAL_ID = "<cobalt-mesa-2026-10-22.dokafor@baylineinsurance.example>"
INSURED = "Cobalt Mesa Transport Inc"
OWNER = "Devraj Sandhu"
DOT, MC = "9481307", "1987405"
FEIN = "84-2917365"  # written for the demo (A-DEMO-01)
APPLICATION_DATE = "10/22/2026"
FORM_BRAND = "Onward Insurance"  # the brand of the sample's own application form
AGENCY = "Bayline Insurance Agency Inc"


def _pdf(lines: list[tuple[str, int, bool]]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 72.0
    for text, size, bold in lines:
        if text:
            page.insert_text((72, y), text, fontsize=size, fontname="helv" if not bold else "hebo")
        y += size * 1.7
    data = doc.tobytes()
    doc.close()
    return data


def signed_application() -> bytes:
    return _pdf(
        [
            (FORM_BRAND, 11, True),
            ("Commercial Auto Insurance Application", 16, True),
            ("Applicant information and signature", 11, False),
            ("", 11, False),
            (f"Named Insured: {INSURED}", 11, False),
            (f"USDOT Number: {DOT}", 11, False),
            (f"MC Number: {MC}", 11, False),
            (f"FEIN: {FEIN}", 11, False),
            ("", 11, False),
            (
                "Fraud statement: any person who knowingly presents a false or fraudulent claim",
                9,
                False,
            ),
            ("or application may be guilty of a crime.", 9, False),
            ("", 11, False),
            (f"Signature of Applicant: {OWNER}", 11, False),
            ("Title: Owner", 11, False),
            (f"Date: {APPLICATION_DATE}", 11, False),
        ]
    )


def exclusion_letter() -> bytes:
    return _pdf(
        [
            ("Named Driver Exclusion", 16, True),
            ("", 11, False),
            (f"Named Insured: {INSURED}", 11, False),
            (f"USDOT Number: {DOT}", 11, False),
            ("", 11, False),
            (f"Excluded Driver: {OWNER}", 11, False),
            ("", 11, False),
            (
                "The named insured requests that the person named above be excluded from coverage",
                10,
                False,
            ),
            (
                "as a driver. The excluded driver will not operate any vehicle insured under this",
                10,
                False,
            ),
            ("policy, and no coverage applies while the excluded driver operates one.", 10, False),
            ("", 11, False),
            (f"Named Insured Signature: {OWNER}, Owner", 11, False),
            (f"Date: {APPLICATION_DATE}", 11, False),
        ]
    )


def build(submission_id: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = AGENT
    msg["To"] = TO
    msg["Subject"] = (
        f"Re: {INSURED}: information needed before we can quote [Ref {reference(submission_id)}]"
    )
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="baylineinsurance.example")
    msg["References"] = ORIGINAL_ID
    msg.set_content(
        "Hi,\n\n"
        f"Answers for {INSURED}:\n\n"
        f"- FEIN: {FEIN}\n"
        "- The signed application is attached.\n"
        f"- {OWNER}, the owner, does not drive. The signed named driver exclusion is attached.\n\n"
        "The loss runs and the vehicle schedule will follow separately.\n\n"
        f"Thanks,\nDaniel Okafor\n{AGENCY}\n(925) 555-0164\n"
    )
    msg.add_attachment(
        signed_application(),
        maintype="application",
        subtype="pdf",
        filename=f"{INSURED} - SIGNED APPLICATION.pdf",
    )
    msg.add_attachment(
        exclusion_letter(),
        maintype="application",
        subtype="pdf",
        filename=f"DRIVER EXCLUSION - {OWNER.upper()}.pdf",
    )
    return msg


def current_submission() -> str:
    """The sample's submission as the local API lists it now (Start over gives it a new id)."""
    if not API.startswith("http://"):
        raise SystemExit("The local API is plain http")
    # Where the API requires sign-in (AWS), the deploy script passes a short-lived token
    token = os.environ.get("LANE_LOCAL_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(API, headers=headers)  # noqa: S310 (http only, checked above)
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (http only, checked above)
        rows = json.load(r)
    for row in rows:
        if row.get("applicant") == INSURED and not row.get("relation"):
            return str(row["id"])
    raise SystemExit(f"No submission for {INSURED} at {API}; pass --submission")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", help="the submission being answered (default: looked up)")
    parser.add_argument("--out", type=Path, default=ROOT / "var" / "demo_reply.eml")
    parser.add_argument("--send", action="store_true", help="send to Mailpit on 127.0.0.1:1025")
    args = parser.parse_args()
    msg = build(args.submission or current_submission())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(msg.as_bytes())
    print(f"Wrote {args.out}: {msg['Subject']}")
    if args.send:
        with smtplib.SMTP(SMTP_HOST, 1025) as smtp:
            smtp.send_message(msg)
        print("Sent to Mailpit")


if __name__ == "__main__":
    main()
