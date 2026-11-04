"""Send an .eml through SES to the real inbox, as the demo's agent: the way an agent's email
arrives over the internet. SES can only send as this domain, so the sender becomes the domain's
agent address; the subject, body and attachments are kept as they are.

    python scripts/send_ses.py var/sample/cobalt_mesa.eml --to submissions@laneunder.online \
        --sender "Daniel Okafor <agent@laneunder.online>"

Runs where AWS credentials allow sending as that address (the instance's role does).
"""

import argparse
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eml", type=Path)
    parser.add_argument("--to", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    msg = message_from_bytes(args.eml.read_bytes(), policy=policy.SMTP)
    assert isinstance(msg, EmailMessage)
    domain = parseaddr(args.sender)[1].rpartition("@")[2]
    for header in ("From", "To", "Cc", "Date", "Message-ID", "Return-Path", "Sender", "Reply-To"):
        del msg[header]
    msg["From"] = args.sender
    msg["To"] = args.to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)

    import boto3

    ses = boto3.client("sesv2", region_name=args.region)  # pyright: ignore[reportUnknownMemberType]
    sent = ses.send_email(  # pyright: ignore[reportUnknownMemberType]
        FromEmailAddress=parseaddr(args.sender)[1],
        Destination={"ToAddresses": [args.to]},
        Content={"Raw": {"Data": msg.as_bytes()}},
    )
    print(f"Sent {args.eml.name} to {args.to} through SES: {msg['Subject']} ({sent['MessageId']})")


if __name__ == "__main__":
    main()
