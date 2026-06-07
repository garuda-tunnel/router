from __future__ import annotations

import datetime
import argparse
import tempfile
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import duckdb

OPERATIONAL_BUILD_ERRORS = (URLError, HTTPError, OSError, duckdb.Error, RuntimeError)


def _month_candidates(now_utc: datetime.datetime) -> tuple[str, str]:
    current = now_utc.strftime("%Y-%m")
    first_day = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (first_day - datetime.timedelta(days=1)).strftime("%Y-%m")
    return current, previous


def _request_for_month(month: str) -> Request:
    url = f"https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"
    return urllib.request.Request(
        url, headers={"User-Agent": "ipt-server-ipdb-manager/1"}
    )


def _validate_db(db_path: Path) -> None:
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


def _build_for_month(month: str, output_db: Path) -> None:
    req = _request_for_month(month)
    csv_gz_path: Path | None = None
    tmp_db_path: Path | None = None

    try:
        with urlopen(req, timeout=30) as resp:
            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, suffix=".csv.gz"
            ) as payload:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    payload.write(chunk)
                csv_gz_path = Path(payload.name)

        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".db"
        ) as tmp_db:
            tmp_db_path = Path(tmp_db.name)

        tmp_db_path.unlink(missing_ok=True)

        conn = duckdb.connect(str(tmp_db_path))
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

        _validate_db(tmp_db_path)
        output_db.parent.mkdir(parents=True, exist_ok=True)
        tmp_db_path.replace(output_db)
    finally:
        if csv_gz_path is not None and csv_gz_path.exists():
            csv_gz_path.unlink()
        if tmp_db_path is not None and tmp_db_path.exists():
            tmp_db_path.unlink()


def build_fallback_db(
    output_db: Path | str = "/opt/fallback/ipt.db",
    now_utc: datetime.datetime | None = None,
) -> Path:
    output_path = Path(output_db)
    now = now_utc or datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)

    current, previous = _month_candidates(now)

    last_error: Exception | None = None
    for month in (current, previous):
        try:
            _build_for_month(month, output_path)
            return output_path
        except OPERATIONAL_BUILD_ERRORS as exc:
            last_error = exc

    raise RuntimeError(
        f"failed to build fallback ipdb from current/previous candidates: {last_error!r}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build fallback DB-IP duckdb file")
    parser.add_argument(
        "--output",
        default="/opt/fallback/ipt.db",
        help="Destination path for the built fallback duckdb file",
    )
    args = parser.parse_args()
    build_fallback_db(output_db=args.output)


if __name__ == "__main__":
    main()
