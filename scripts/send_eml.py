#!/usr/bin/env python3
"""Send an .eml file to a local SMTP server (Mailpit on 127.0.0.1:1025), the way an agent's email
would arrive. Nothing leaves the machine. The Date header is set to the send time, so the email
reads as just received, not as old as the day the sample was written; its Message-ID is kept.

    uv run python ../scripts/send_eml.py path/to/message.eml [--host 127.0.0.1 --port 1025]
"""

import argparse
import smtplib
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Send an .eml to a local SMTP server")
    parser.add_argument("eml", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1025)
    args = parser.parse_args()
    # Local test servers only: this machine, or the Mailpit container in the deploy stack
    if args.host not in ("127.0.0.1", "localhost", "::1", "mailpit"):
        raise SystemExit("This sends to a local test server only")
    msg = message_from_bytes(args.eml.read_bytes(), policy=policy.SMTP)
    assert isinstance(msg, EmailMessage)
    del msg["Date"]
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP(args.host, args.port) as smtp:
        smtp.send_message(msg)
    print(f"Sent {args.eml.name}: {msg['Subject']} (dated {msg['Date']})")


if __name__ == "__main__":
    main()
