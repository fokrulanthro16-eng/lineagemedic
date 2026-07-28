"""Write the FastAPI OpenAPI schema to scripts/openapi.json.

The frontend's TypeScript types are generated from this file, so it is the
join between the Pydantic models and the React app. Generating it from the
imported application (rather than curling a running server) means CI can
check for drift without starting anything.

If this file changes and src/api/schema.ts is not regenerated, the frontend
types no longer describe the API. check_api_types.ps1 enforces that.

    py -3.11 scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from lineagemedic_api.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "scripts" / "openapi.json"


def main() -> int:
    # sort_keys keeps the diff stable: FastAPI builds the schema from dicts
    # whose ordering can shift between versions, which would otherwise show up
    # as a spurious change on every regeneration.
    schema = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    TARGET.write_text(schema, encoding="utf-8")
    print(f"  wrote {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
