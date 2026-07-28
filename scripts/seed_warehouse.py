"""Seed the local SQLite warehouse used by the demo.

The destination comes from the API's own Settings, so this script and the
running server can never disagree about which file is the warehouse. Rerunning
is safe: build_database recreates the tables deterministically from a fixed
seed, so the generated data is identical every time.
"""

from __future__ import annotations

from lineagemedic.data.seed_healthcare import build_database
from lineagemedic_api.config import Settings


def main() -> int:
    settings = Settings()
    path = build_database(settings.db_path)
    print(f"  seeded {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
