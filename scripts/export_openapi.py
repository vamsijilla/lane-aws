"""Write the API's OpenAPI schema for the frontend's generated types. No server needed.

cd backend && uv run python ../scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

from lane.api.main import create_app

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api" / "openapi.json"


def schema_text() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(schema_text())
    print(f"Wrote {OUT.relative_to(Path.cwd().parent) if Path.cwd().name == 'backend' else OUT}")
    sys.exit(0)
