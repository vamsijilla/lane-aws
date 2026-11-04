"""The demo's agent emails, sent from inside the deploy stack (deploy/send-sample.sh):

    python scripts/demo_send.py mailpit sample      the sample packet, to the stack's Mailpit
    python scripts/demo_send.py mailpit reply       the agent's reply to the marked-sent request
    python scripts/demo_send.py ses sample --inbox submissions@example.com
    python scripts/demo_send.py ses reply --inbox submissions@example.com

The reply looks up the current submission through the API with a short-lived token minted from
the stack's own settings, since the API requires sign-in on AWS.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

SAMPLE = Path("/app/var/sample/cobalt_mesa.eml")
REPLY = Path(__file__).resolve().parents[1] / "var" / "demo_reply.eml"  # the image's own var/
SCRIPTS = Path(__file__).resolve().parent


def token() -> str:
    from lane.api.auth import issue_token
    from lane.config import get_settings

    return issue_token("viewer", get_settings(), name="Demo script")


def run(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run([sys.executable, *args], check=True, env={**os.environ, **(env or {})})  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("via", choices=["mailpit", "ses"])
    parser.add_argument("what", choices=["sample", "reply"])
    parser.add_argument("--inbox", help="the real inbox address (ses)")
    args = parser.parse_args()
    if args.via == "ses" and not args.inbox:
        parser.error("--inbox is required with ses")

    eml = SAMPLE
    if args.what == "reply":
        env = {
            "LANE_LOCAL_API": "http://api:8000",
            "LANE_LOCAL_TOKEN": token(),
            "LANE_LOCAL_SMTP": "mailpit",
        }
        flags = ["--send"] if args.via == "mailpit" else []
        run(str(SCRIPTS / "send_reply.py"), *flags, "--out", str(REPLY), env=env)
        if args.via == "mailpit":
            return
        eml = REPLY
    if args.via == "mailpit":
        run(str(SCRIPTS / "send_eml.py"), str(eml), "--host", "mailpit")
        return
    domain = args.inbox.rpartition("@")[2]
    run(
        str(SCRIPTS / "send_ses.py"),
        str(eml),
        "--to",
        args.inbox,
        "--sender",
        f"Daniel Okafor <agent@{domain}>",
    )


if __name__ == "__main__":
    main()
