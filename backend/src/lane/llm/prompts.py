"""Versioned prompt files: backend/prompts/<name>.v<N>.md. The highest version wins.

The version and a content hash are recorded on every call, so any output can be traced to the
exact prompt text that produced it.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"
_NAME = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str
    sha256: str


@cache
def load_prompt(name: str, directory: Path = PROMPT_DIR) -> Prompt:
    versions: list[tuple[int, Path]] = []
    for path in directory.glob(f"{name}.v*.md"):
        match = _NAME.match(path.name)
        if match and match["name"] == name:
            versions.append((int(match["version"]), path))
    if not versions:
        raise FileNotFoundError(f"No prompt named {name!r} in {directory}")
    version, path = max(versions)
    text = path.read_text(encoding="utf-8").strip()
    return Prompt(name, str(version), text, hashlib.sha256(text.encode()).hexdigest())
