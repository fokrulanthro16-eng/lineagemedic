"""Shared pytest fixtures.

The healthcare database is built once per session into a temp directory, so the
suite never depends on a developer having run the setup script and never mutates
a checked-out database.

Time is pinned to the seed module's reference clock. Freshness checks compare
against "now", so without a fixed clock the warning scenario's verdict would
drift as real time passed and the suite would rot.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lineagemedic.adapters.fixture import FixtureMetadataAdapter, FixtureWritebackAdapter
from lineagemedic.data.seed_healthcare import REFERENCE_NOW, build_database
from lineagemedic.workflow import Workflow


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A freshly seeded healthcare database, shared across the session.

    The clock is pinned to :data:`REFERENCE_NOW` and the ``now`` fixture hands
    the Quality Agent the same instant, so freshness deltas are exact rather
    than dependent on when the suite happens to run. ``build_database`` itself
    defaults to wall-clock, which is what keeps the demo's HEALTHY control from
    ageing into a warning.
    """
    target = tmp_path_factory.mktemp("warehouse") / "healthcare.db"
    return build_database(target, reference_now=REFERENCE_NOW)


@pytest.fixture()
def now():
    """The fixed reference clock the seeded data was generated against."""
    return REFERENCE_NOW


@pytest.fixture()
def metadata() -> FixtureMetadataAdapter:
    return FixtureMetadataAdapter()


@pytest.fixture()
def writeback() -> FixtureWritebackAdapter:
    return FixtureWritebackAdapter()


@pytest.fixture()
def workflow(db_path: Path, metadata, writeback, now) -> Workflow:
    return Workflow(metadata=metadata, writeback=writeback, db_path=db_path, now=now)


@pytest.fixture()
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient pointed at the session's seeded database.

    Settings are reset before and after so environment changes made here cannot
    leak into other tests.
    """
    from lineagemedic_api import config, main

    monkeypatch.setenv("LINEAGEMEDIC_DB_PATH", str(db_path))
    monkeypatch.setenv("LINEAGEMEDIC_MODE", "fixture")
    config.reset_settings()

    main.store.reset()
    with TestClient(main.app) as test_client:
        yield test_client

    main.store.reset()
    config.reset_settings()
