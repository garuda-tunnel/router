from __future__ import annotations

import datetime
import logging
import os
import shutil
import tempfile
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrepareResult:
    source: str
    reason: str


class IptDbManager:
    def __init__(
        self,
        runtime_db: Path | str = "/data/ipt.db",
        fallback_db: Path | str = "/opt/fallback/ipt.db",
    ) -> None:
        self.runtime_db_path = Path(runtime_db)
        self.fallback_db_path = Path(fallback_db)

    def _is_fresh(self, path: Path, now_utc: datetime.datetime) -> bool:
        age_seconds = now_utc.timestamp() - path.stat().st_mtime
        return age_seconds < 86400

    def _month_candidates(self, now_utc: datetime.datetime) -> tuple[str, str]:
        current = now_utc.strftime("%Y-%m")
        first_day = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous = (first_day - datetime.timedelta(days=1)).strftime("%Y-%m")
        return current, previous

    def _make_request(self, month: str) -> Request:
        url = f"https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"
        return Request(url, headers={"User-Agent": "ipt-server-ipdb-manager/1"})

    def _validate_db(self, db_path: Path) -> None:
        if not db_path.exists() or db_path.stat().st_size == 0:
            raise RuntimeError("db_missing_or_empty")

        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            schema_rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'main' AND table_name = 'ip_db'
                ORDER BY ordinal_position
                """
            ).fetchall()
            expected_columns = [("start_ip",), ("end_ip",), ("country",)]
            if schema_rows != expected_columns:
                raise RuntimeError("db_invalid_schema")

            row_count = conn.execute("SELECT COUNT(*) FROM ip_db").fetchone()[0]
            if row_count <= 0:
                raise RuntimeError("db_empty_table")
        finally:
            conn.close()

    def _atomic_replace(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)

    def _write_download_to_runtime(self, month: str) -> None:
        self.runtime_db_path.parent.mkdir(parents=True, exist_ok=True)

        req = self._make_request(month)
        csv_gz_path: Path | None = None
        tmp_path: Path | None = None

        try:
            with urlopen(req, timeout=30) as resp:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    suffix=".csv.gz",
                    dir=str(self.runtime_db_path.parent),
                ) as payload_file:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        payload_file.write(chunk)
                    csv_gz_path = Path(payload_file.name)

            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                suffix=".db",
                dir=str(self.runtime_db_path.parent),
            ) as tmp:
                tmp_path = Path(tmp.name)

            tmp_path.unlink(missing_ok=True)
            conn = duckdb.connect(str(tmp_path))
            try:
                conn.execute(
                    "CREATE TABLE ip_db(start_ip VARCHAR, end_ip VARCHAR, country VARCHAR)"
                )
                csv_path_sql = str(csv_gz_path).replace("'", "''")
                conn.execute(
                    f"""
                    COPY ip_db (start_ip, end_ip, country)
                    FROM '{csv_path_sql}'
                    (FORMAT CSV, HEADER FALSE, COMPRESSION 'gzip')
                    """
                )
            finally:
                conn.close()

            self._validate_db(tmp_path)
            self._atomic_replace(tmp_path, self.runtime_db_path)
            self._validate_db(self.runtime_db_path)
        finally:
            if csv_gz_path is not None and csv_gz_path.exists():
                csv_gz_path.unlink()
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    def _restore_fallback(self) -> None:
        if not self.fallback_db_path.exists():
            raise RuntimeError("fallback_db_missing")

        self.runtime_db_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".db", dir=str(self.runtime_db_path.parent)
        ) as tmp:
            shutil.copyfile(self.fallback_db_path, tmp.name)
            tmp_path = Path(tmp.name)

        try:
            self._validate_db(tmp_path)
            self._atomic_replace(tmp_path, self.runtime_db_path)
            self._validate_db(self.runtime_db_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _validate_fallback_readable(self) -> None:
        try:
            self._validate_db(self.fallback_db_path)
        except Exception as exc:
            raise RuntimeError(
                f"fallback_db_unreadable ({type(exc).__name__}:{exc})"
            ) from exc

    def prepare(self, now_utc: datetime.datetime | None = None) -> PrepareResult:
        now_utc = now_utc or datetime.datetime.now(datetime.UTC)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=datetime.UTC)

        self._validate_fallback_readable()

        if self.runtime_db_path.exists() and self._is_fresh(
            self.runtime_db_path, now_utc
        ):
            self._validate_db(self.runtime_db_path)
            return PrepareResult(source="existing_fresh", reason="fresh_runtime_db")

        current, previous = self._month_candidates(now_utc)

        failures: list[tuple[str, Exception]] = []

        try:
            self._write_download_to_runtime(current)
            return PrepareResult(source="current", reason="downloaded_current")
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            RuntimeError,
            duckdb.Error,
        ) as exc:
            failures.append(("current", exc))
            logger.warning(
                "ipdb_prepare_attempt_failed",
                extra={"source": "current", "error": repr(exc)},
            )

        try:
            self._write_download_to_runtime(previous)
            return PrepareResult(source="previous", reason="downloaded_previous")
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            RuntimeError,
            duckdb.Error,
        ) as exc:
            failures.append(("previous", exc))
            logger.warning(
                "ipdb_prepare_attempt_failed",
                extra={"source": "previous", "error": repr(exc)},
            )

        try:
            self._restore_fallback()
            return PrepareResult(
                source="fallback", reason="download_failed_restored_fallback"
            )
        except Exception as exc:
            failures.append(("fallback", exc))
            details = ", ".join(
                f"{source}={type(error).__name__}:{error}" for source, error in failures
            )
            raise RuntimeError(f"prepare_failed_no_valid_source ({details})") from exc
