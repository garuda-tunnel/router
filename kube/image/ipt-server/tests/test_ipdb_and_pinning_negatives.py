"""Remaining negative paths of the IPDB layer and the pinning HTTP index (T-pool).

Closes the last uncovered branches: invalid IP ranges and database errors
in the query layer, validation failures in the fallback builder, naive
datetime handling, the manager's empty-table/fallback-missing paths, and
the pinning UI endpoints.
"""

import datetime
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest

from ipdb.build_fallback import _validate_db, build_fallback_db
from ipdb.manager import IptDbManager
from ipdb.query import IPDatabase
from pinning.api import create_app


def _write_db(path: Path, rows=(("192.0.2.0", "192.0.2.255", "AU"),)):
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE ip_db(start_ip VARCHAR, end_ip VARCHAR, country VARCHAR)"
        )
        if rows:
            conn.executemany("INSERT INTO ip_db VALUES (?, ?, ?)", list(rows))
    finally:
        conn.close()


class TestQueryLayerNegatives(unittest.TestCase):
    def test_invalid_range_rows_are_skipped_with_warning(self):
        """Unparseable IP range rows warn and are skipped; valid rows yield."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ipt.db"
            _write_db(
                db,
                rows=(
                    ("garbage", "192.0.2.255", "AU"),
                    ("192.0.2.0", "192.0.2.255", "AU"),
                ),
            )
            query_db = IPDatabase(str(db))
            nets = list(query_db.country_nets(["AU"]))

        self.assertEqual([str(n) for n in nets], ["192.0.2.0/24"])

    def test_database_error_is_raised(self):
        """A duckdb failure during the country query is re-raised."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ipt.db"
            _write_db(db)
            query_db = IPDatabase(str(db))
            with patch.object(
                duckdb, "connect", side_effect=duckdb.Error("boom")
            ):
                with self.assertRaises(duckdb.Error):
                    list(query_db.country_nets(["AU"]))

    def test_string_country_form_is_accepted(self):
        """A single country string is normalized to a one-element list."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ipt.db"
            _write_db(db)
            query_db = IPDatabase(str(db))
            nets = list(query_db.country_nets("AU"))
        self.assertEqual([str(n) for n in nets], ["192.0.2.0/24"])

    def test_empty_country_list_is_rejected(self):
        """An explicit empty country list is rejected."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ipt.db"
            _write_db(db)
            query_db = IPDatabase(str(db))
            with self.assertRaises(ValueError):
                list(query_db.country_nets([]))

    def test_too_many_countries_are_rejected(self):
        """More than 100 countries is treated as a config error."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ipt.db"
            _write_db(db)
            query_db = IPDatabase(str(db))
            with self.assertRaisesRegex(ValueError, "Too many"):
                list(query_db.country_nets(["AU"] * 101))


class TestFallbackBuilderNegatives(unittest.TestCase):
    def test_validate_rejects_empty_file(self):
        """A zero-byte file fails validation with db_missing_or_empty."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty.db"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "db_missing_or_empty"):
                _validate_db(empty)

    def test_validate_rejects_wrong_schema(self):
        """A duckdb file with an unrelated schema fails validation."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            wrong = Path(td) / "wrong.db"
            conn = duckdb.connect(str(wrong))
            try:
                conn.execute("CREATE TABLE other(v VARCHAR)")
            finally:
                conn.close()
            with self.assertRaisesRegex(RuntimeError, "db_invalid_schema"):
                _validate_db(wrong)

    def test_validate_rejects_empty_table(self):
        """A schema-correct but empty table fails validation."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            empty_table = Path(td) / "empty.db"
            _write_db(empty_table, rows=())
            with self.assertRaisesRegex(RuntimeError, "db_empty_table"):
                _validate_db(empty_table)

    def test_build_uses_wall_clock_when_now_missing(self):
        """Without now_utc the builder uses the current wall-clock month."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fallback.db"
            with (
                patch("ipdb.build_fallback._build_for_month") as build,
                patch("ipdb.build_fallback.datetime") as dt,
            ):
                dt.datetime.now.return_value = datetime.datetime(
                    2026, 4, 1, tzinfo=datetime.UTC
                )
                build_fallback_db(output_db=out)
        build.assert_called_once()
        month = build.call_args.args[0]
        self.assertEqual(month, "2026-04")

    def test_build_accepts_naive_datetime(self):
        """A naive now_utc is treated as UTC rather than rejected."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fallback.db"
            with patch("ipdb.build_fallback._build_for_month") as build:
                build_fallback_db(
                    output_db=out,
                    now_utc=datetime.datetime(2026, 4, 1, 12, 0),
                )
        self.assertEqual(build.call_args.args[0], "2026-04")


class TestManagerNegatives(unittest.TestCase):
    def test_validate_rejects_empty_runtime_table(self):
        """A runtime DB with the right schema but no rows is rejected."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "ipt.db"
            fallback = Path(td) / "fallback.db"
            _write_db(fallback)
            _write_db(runtime, rows=())
            manager = IptDbManager(runtime_db=runtime, fallback_db=fallback)
            with self.assertRaisesRegex(RuntimeError, "db_empty_table"):
                manager._validate_db(runtime)

    def test_restore_missing_fallback_raises(self):
        """Restoring from a missing fallback fails with a clear reason."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "ipt.db"
            fallback = Path(td) / "missing.db"
            manager = IptDbManager(runtime_db=runtime, fallback_db=fallback)
            with self.assertRaisesRegex(RuntimeError, "fallback_db_missing"):
                manager._restore_fallback()

    def test_prepare_fails_when_every_source_fails(self):
        """All candidates failing raises prepare_failed_no_valid_source."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "ipt.db"
            fallback = Path(td) / "fallback.db"
            _write_db(fallback)
            manager = IptDbManager(runtime_db=runtime, fallback_db=fallback)
            with (
                patch.object(
                    manager,
                    "_write_download_to_runtime",
                    side_effect=urllib.error.URLError("down"),
                ),
                patch.object(
                    manager,
                    "_restore_fallback",
                    side_effect=RuntimeError("restore broke"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "prepare_failed_no_valid_source"
                ):
                    manager.prepare(
                        now_utc=datetime.datetime(2026, 4, 1, tzinfo=datetime.UTC)
                    )

    def test_prepare_without_now_uses_wall_clock(self):
        """prepare() without now_utc uses the current time (fresh runtime)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runtime = Path(td) / "ipt.db"
            fallback = Path(td) / "fallback.db"
            _write_db(runtime)
            _write_db(fallback)
            runtime.touch()
            manager = IptDbManager(runtime_db=runtime, fallback_db=fallback)
            result = manager.prepare()
        self.assertEqual(result.source, "existing_fresh")


@pytest.mark.asyncio
class TestPinningUiEndpoints:
    async def test_index_renders_current_pin_and_catalog(self, aiohttp_client):
        """The index page renders the caller's current pin and expiry."""
        import time

        from pinning.manager import PinEntry

        manager = AsyncMock()
        manager.get.return_value = PinEntry(
            saddr="127.0.0.1", egress="outer-pt", expires_at=time.time() + 60
        )
        reconciler = AsyncMock()
        app = create_app(
            manager=manager,
            reconciler=reconciler,
            catalog={"outer-de": object(), "outer-pt": object()},
        )
        client = await aiohttp_client(app)

        resp = await client.get("/")

        assert resp.status == 200
        assert resp.content_type == "text/html"
        body = await resp.text()
        assert "outer-de" in body
        assert "outer-pt" in body

    async def test_index_renders_unpinned_state(self, aiohttp_client):
        """With no pin the index renders the unpinned state."""
        manager = AsyncMock()
        manager.get.return_value = None
        reconciler = AsyncMock()
        app = create_app(
            manager=manager, reconciler=reconciler, catalog={"outer-de": object()}
        )
        client = await aiohttp_client(app)

        resp = await client.get("/")

        assert resp.status == 200
        body = await resp.text()
        assert "outer-de" in body

    async def test_clear_pin_with_html_return_redirects(self, aiohttp_client):
        """clear with return=html redirects to the index."""
        manager = AsyncMock()
        manager.snapshot.return_value = {}
        reconciler = AsyncMock()
        app = create_app(
            manager=manager, reconciler=reconciler, catalog={"outer-de": object()}
        )
        client = await aiohttp_client(app)

        resp = await client.get("/api/pin/clear?return=html", allow_redirects=False)

        assert resp.status == 303
        assert resp.headers["Location"] == "/"

    async def test_get_pin_reports_expiry_fields(self, aiohttp_client):
        """get_pin reports saddr, egress and remaining expiry."""
        import time

        from pinning.manager import PinEntry

        manager = AsyncMock()
        manager.get.return_value = PinEntry(
            saddr="127.0.0.1", egress="outer-de", expires_at=time.time() + 3600
        )
        reconciler = AsyncMock()
        app = create_app(
            manager=manager, reconciler=reconciler, catalog={"outer-de": object()}
        )
        client = await aiohttp_client(app)

        resp = await client.get("/api/pin")

        assert resp.status == 200
        body = await resp.json()
        assert body["egress"] == "outer-de"
        assert body["saddr"] == "127.0.0.1"
        assert 0 < body["expires_in"] <= 3600
