"""Tests for ipdb manager, query layer, and fallback builder."""

import datetime
import gzip
import ipaddress
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import duckdb


class TestIpdbManager(unittest.TestCase):
    def _write_valid_ipdb(self, path: Path, rows=None):
        rows = rows or [("1.1.1.0", "1.1.1.255", "AU")]
        conn = duckdb.connect(str(path))
        try:
            conn.execute(
                "CREATE TABLE ip_db(start_ip VARCHAR, end_ip VARCHAR, country VARCHAR)"
            )
            conn.executemany("INSERT INTO ip_db VALUES (?, ?, ?)", rows)
        finally:
            conn.close()

    def _make_csv_gz_payload(self, rows):
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".csv.gz"
        ) as tmp:
            gz_path = Path(tmp.name)
        try:
            with gzip.open(gz_path, "wt", encoding="utf-8", newline="") as gz:
                for start_ip, end_ip, country in rows:
                    gz.write(f"{start_ip},{end_ip},{country}\n")
            return gz_path.read_bytes()
        finally:
            if gz_path.exists():
                gz_path.unlink()

    def test_fallback_builder_tries_current_then_previous_month(self):
        from ipdb.build_fallback import build_fallback_db

        with tempfile.TemporaryDirectory() as td:
            output_db = Path(td) / "fallback.db"
            payload_bytes = self._make_csv_gz_payload([("1.1.1.0", "1.1.1.255", "AU")])

            with patch("ipdb.build_fallback.urlopen") as mock_urlopen:
                previous_response = MagicMock()
                previous_response.read.side_effect = [
                    payload_bytes[:5],
                    payload_bytes[5:],
                    b"",
                ]
                mock_urlopen.side_effect = [
                    urllib.error.URLError("current failed"),
                    MagicMock(__enter__=MagicMock(return_value=previous_response)),
                ]

                build_fallback_db(
                    output_db=output_db,
                    now_utc=datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC),
                )

            self.assertEqual(mock_urlopen.call_count, 2)
            self.assertIn("2026-04", mock_urlopen.call_args_list[0].args[0].full_url)
            self.assertIn("2026-03", mock_urlopen.call_args_list[1].args[0].full_url)
            self.assertTrue(output_db.exists())

    def test_fallback_builder_fails_when_both_candidates_fail(self):
        from ipdb.build_fallback import build_fallback_db

        with tempfile.TemporaryDirectory() as td:
            output_db = Path(td) / "fallback.db"

            with patch(
                "ipdb.build_fallback.urlopen",
                side_effect=[
                    urllib.error.URLError("current failed"),
                    urllib.error.URLError("previous failed"),
                ],
            ):
                with self.assertRaises(RuntimeError) as exc_info:
                    build_fallback_db(
                        output_db=output_db,
                        now_utc=datetime.datetime(
                            2026, 4, 1, 12, 0, tzinfo=datetime.UTC
                        ),
                    )

        self.assertIn("failed to build fallback ipdb", str(exc_info.exception))

    def test_fallback_builder_propagates_unexpected_errors(self):
        from ipdb.build_fallback import build_fallback_db

        with patch("ipdb.build_fallback._build_for_month") as mock_build:
            mock_build.side_effect = ValueError("unexpected")

            with self.assertRaises(ValueError):
                build_fallback_db(
                    output_db=Path("/tmp/unused.db"),
                    now_utc=datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC),
                )

        self.assertEqual(mock_build.call_count, 1)

    def test_query_layer_init_has_no_download_side_effects(self):
        from ipdb.query import IPDatabase

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            self._write_valid_ipdb(runtime_db)

            with patch("urllib.request.urlopen") as mock_urlopen:
                query_db = IPDatabase(str(runtime_db))

            self.assertIsInstance(query_db, IPDatabase)
            mock_urlopen.assert_not_called()

    def test_query_layer_country_lookup_has_no_download_side_effects(self):
        from ipdb.query import IPDatabase

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            self._write_valid_ipdb(
                runtime_db,
                rows=[
                    ("1.1.1.0", "1.1.1.255", "AU"),
                    ("2.2.2.0", "2.2.2.255", "US"),
                ],
            )

            with patch("urllib.request.urlopen") as mock_urlopen:
                query_db = IPDatabase(str(runtime_db))
                country_networks = list(query_db["AU"])

            self.assertIn(ipaddress.IPv4Network("1.1.1.0/24"), country_networks)
            mock_urlopen.assert_not_called()

    def test_query_layer_rejects_empty_country_list(self):
        from ipdb.query import IPDatabase

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            self._write_valid_ipdb(runtime_db)
            query_db = IPDatabase(str(runtime_db))

            with self.assertRaises(ValueError):
                list(query_db.country_nets([]))

    def test_query_layer_rejects_non_string_country_items(self):
        from ipdb.query import IPDatabase

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            self._write_valid_ipdb(runtime_db)
            query_db = IPDatabase(str(runtime_db))

            with self.assertRaises(ValueError):
                list(query_db.country_nets(["AU", 123]))

    def test_query_layer_country_lookup_uses_bound_sql_parameters(self):
        from ipdb.query import IPDatabase

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            self._write_valid_ipdb(
                runtime_db,
                rows=[
                    ("1.1.1.0", "1.1.1.255", "AU"),
                    ("2.2.2.0", "2.2.2.255", "US"),
                ],
            )
            query_db = IPDatabase(str(runtime_db))

            sql_payload = "AU') OR 1=1 --"
            country_networks = list(query_db.country_nets([sql_payload]))

            self.assertEqual(country_networks, [])

    def test_prepare_fallback_manager_default_path_is_embedded(self):
        from ipdb.manager import IptDbManager

        manager = IptDbManager()
        self.assertEqual(str(manager.fallback_db_path), "/opt/fallback/ipt.db")

    def test_prepare_fallback_validates_readability_before_download_attempts(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "missing-fallback.db"
            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch.object(
                manager,
                "_write_download_to_runtime",
                side_effect=AssertionError("download should not be attempted"),
            ) as mock_download:
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                with self.assertRaises(RuntimeError) as exc_info:
                    manager.prepare(now_utc=now)

            self.assertIn("fallback_db_unreadable", str(exc_info.exception))
            mock_download.assert_not_called()

    def test_prepare_fallback_uses_one_request_per_month_candidate(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)
            with (
                patch.object(
                    manager,
                    "_write_download_to_runtime",
                    side_effect=[
                        urllib.error.URLError("current failed"),
                        urllib.error.URLError("previous failed"),
                    ],
                ) as mock_write_download,
                patch.object(manager, "_restore_fallback") as mock_restore_fallback,
            ):
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "fallback")
            self.assertEqual(result.reason, "download_failed_restored_fallback")
            self.assertEqual(mock_write_download.call_count, 2)
            self.assertEqual(mock_write_download.call_args_list[0].args[0], "2026-04")
            self.assertEqual(mock_write_download.call_args_list[1].args[0], "2026-03")
            mock_restore_fallback.assert_called_once_with()

    def test_prepare_fallback_restore_uses_atomic_replace(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(
                fallback_db,
                rows=[("11.11.11.0", "11.11.11.255", "DE")],
            )

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with (
                patch(
                    "ipdb.manager.urlopen",
                    side_effect=[
                        urllib.error.URLError("current failed"),
                        urllib.error.URLError("previous failed"),
                    ],
                ),
                patch(
                    "ipdb.manager.os.replace", side_effect=os.replace
                ) as mock_replace,
            ):
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "fallback")
            self.assertEqual(mock_replace.call_count, 1)
            replace_src, replace_dst = mock_replace.call_args.args
            self.assertNotEqual(str(replace_src), str(fallback_db))
            self.assertEqual(Path(replace_dst), runtime_db)

            conn = duckdb.connect(str(runtime_db), read_only=True)
            try:
                self.assertEqual(
                    conn.execute("SELECT country FROM ip_db LIMIT 1").fetchone()[0],
                    "DE",
                )
            finally:
                conn.close()

    def test_prepare_uses_existing_fresh_file(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(runtime_db)
            self._write_valid_ipdb(fallback_db)

            now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)
            result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "existing_fresh")
            self.assertEqual(result.reason, "fresh_runtime_db")

    def test_prepare_fresh_uses_utc_age_lt_86400(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(runtime_db)
            self._write_valid_ipdb(fallback_db)

            now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
            runtime_db.touch()
            age_just_fresh = now.timestamp() - 86399
            os.utime(runtime_db, (age_just_fresh, age_just_fresh))

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)
            self.assertTrue(manager._is_fresh(runtime_db, now))

            age_stale = now.timestamp() - 86400
            os.utime(runtime_db, (age_stale, age_stale))
            self.assertFalse(manager._is_fresh(runtime_db, now))

    def test_prepare_current_then_previous_then_fallback(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch(
                "ipdb.manager.urlopen",
                side_effect=[
                    urllib.error.URLError("current failed"),
                    urllib.error.URLError("previous failed"),
                ],
            ) as mock_urlopen:
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "fallback")
            self.assertEqual(result.reason, "download_failed_restored_fallback")
            self.assertEqual(mock_urlopen.call_count, 2)

            first_call = mock_urlopen.call_args_list[0]
            second_call = mock_urlopen.call_args_list[1]
            self.assertIn("2026-04", first_call.args[0].full_url)
            self.assertIn("2026-03", second_call.args[0].full_url)
            self.assertEqual(first_call.kwargs["timeout"], 30)
            self.assertEqual(second_call.kwargs["timeout"], 30)

    def test_prepare_uses_previous_when_current_download_fails(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            previous_payload_bytes = self._make_csv_gz_payload(
                [("1.1.1.0", "1.1.1.255", "AU")]
            )

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch("ipdb.manager.urlopen") as mock_urlopen:
                previous_response = MagicMock()
                previous_response.read.side_effect = [
                    previous_payload_bytes[:7],
                    previous_payload_bytes[7:],
                    b"",
                ]
                mock_urlopen.side_effect = [
                    urllib.error.URLError("current failed"),
                    MagicMock(__enter__=MagicMock(return_value=previous_response)),
                ]

                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "previous")
            self.assertEqual(result.reason, "downloaded_previous")
            self.assertEqual(mock_urlopen.call_count, 2)
            self.assertIn("2026-04", mock_urlopen.call_args_list[0].args[0].full_url)
            self.assertIn("2026-03", mock_urlopen.call_args_list[1].args[0].full_url)

            conn = duckdb.connect(str(runtime_db), read_only=True)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM ip_db").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT country FROM ip_db LIMIT 1").fetchone()[0],
                    "AU",
                )
            finally:
                conn.close()

    def test_prepare_fails_when_downloaded_and_fallback_dbs_are_invalid(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            fallback_db.write_bytes(b"not-a-duckdb-file")

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch("ipdb.manager.urlopen") as mock_urlopen:
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                with self.assertRaises(RuntimeError) as exc_info:
                    manager.prepare(now_utc=now)

            self.assertIn("fallback_db_unreadable", str(exc_info.exception))
            mock_urlopen.assert_not_called()

    def test_prepare_fails_when_fallback_missing(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "missing.db"
            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch("ipdb.manager.urlopen") as mock_urlopen:
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                with self.assertRaises(RuntimeError) as exc_info:
                    manager.prepare(now_utc=now)

            self.assertIn("fallback_db_unreadable", str(exc_info.exception))
            mock_urlopen.assert_not_called()

    def test_prepare_uses_atomic_replace_on_download_success(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            payload_bytes = self._make_csv_gz_payload(
                [
                    ("8.8.8.0", "8.8.8.255", "US"),
                    ("9.9.9.0", "9.9.9.255", "US"),
                ]
            )

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)
            with (
                patch("ipdb.manager.urlopen") as mock_urlopen,
                patch(
                    "ipdb.manager.os.replace", side_effect=os.replace
                ) as mock_replace,
            ):
                mock_response = MagicMock()
                mock_response.read.side_effect = [
                    payload_bytes[:10],
                    payload_bytes[10:],
                    b"",
                ]
                mock_urlopen.return_value.__enter__.return_value = mock_response

                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "current")
            self.assertTrue(mock_replace.called)
            self.assertTrue(runtime_db.exists())
            self.assertGreaterEqual(mock_response.read.call_count, 2)
            read_arg = mock_response.read.call_args_list[0].args[0]
            self.assertIsInstance(read_arg, int)
            self.assertGreater(read_arg, 0)

            conn = duckdb.connect(str(runtime_db), read_only=True)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM ip_db").fetchone()[0], 2
                )
            finally:
                conn.close()

    def test_validate_db_rejects_missing_or_wrong_schema(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with self.assertRaises(RuntimeError) as missing_exc:
                manager._validate_db(runtime_db)
            self.assertEqual(str(missing_exc.exception), "db_missing_or_empty")

            wrong_schema = Path(td) / "wrong_schema.db"
            conn = duckdb.connect(str(wrong_schema))
            try:
                conn.execute("CREATE TABLE probe(v INTEGER)")
                conn.execute("INSERT INTO probe VALUES (1)")
            finally:
                conn.close()

            with self.assertRaises(RuntimeError) as schema_exc:
                manager._validate_db(wrong_schema)
            self.assertEqual(str(schema_exc.exception), "db_invalid_schema")

    def test_prepare_propagates_unexpected_exception_from_current(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)
            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch.object(
                manager,
                "_write_download_to_runtime",
                side_effect=ValueError("unexpected-non-operational-error"),
            ) as download_mock:
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                with self.assertRaises(ValueError):
                    manager.prepare(now_utc=now)

            self.assertEqual(download_mock.call_count, 1)

    def test_prepare_creates_runtime_parent_for_download_path(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "nested" / "runtime" / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            payload_bytes = self._make_csv_gz_payload([("8.8.8.0", "8.8.8.255", "US")])

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)
            with patch("ipdb.manager.urlopen") as mock_urlopen:
                mock_response = MagicMock()
                mock_response.read.side_effect = [
                    payload_bytes[:8],
                    payload_bytes[8:],
                    b"",
                ]
                mock_urlopen.return_value.__enter__.return_value = mock_response

                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "current")
            self.assertTrue(runtime_db.parent.exists())
            self.assertTrue(runtime_db.exists())

    def test_prepare_creates_runtime_parent_for_fallback_restore(self):
        from ipdb.manager import IptDbManager

        with tempfile.TemporaryDirectory() as td:
            runtime_db = Path(td) / "nested" / "restore" / "ipt.db"
            fallback_db = Path(td) / "fallback.db"
            self._write_valid_ipdb(fallback_db)

            manager = IptDbManager(runtime_db=runtime_db, fallback_db=fallback_db)

            with patch(
                "ipdb.manager.urlopen",
                side_effect=[
                    urllib.error.URLError("current failed"),
                    urllib.error.URLError("previous failed"),
                ],
            ):
                now = datetime.datetime(2026, 4, 1, 12, 0, tzinfo=datetime.UTC)
                result = manager.prepare(now_utc=now)

            self.assertEqual(result.source, "fallback")
            self.assertTrue(runtime_db.parent.exists())
            self.assertTrue(runtime_db.exists())


if __name__ == "__main__":
    unittest.main()
