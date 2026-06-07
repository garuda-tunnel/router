"""Query-only access helpers for IPT country network lookups."""

from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import logging
from typing import Iterator, List, Union

import duckdb


logger = logging.getLogger(__name__)


class IPDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _connection(self):
        conn = duckdb.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def __getitem__(
        self, countries: Union[str, List[str]]
    ) -> Iterator[ipaddress.IPv4Network]:
        return self.country_nets(countries)

    def country_nets(
        self, countries: Union[str, List[str]]
    ) -> Iterator[ipaddress.IPv4Network]:
        countries = self._validate_countries(countries)

        with self._connection() as conn:
            try:
                placeholders = ",".join(["?"] * len(countries))

                query = f"""
                    SELECT start_ip, end_ip
                    FROM ip_db
                    WHERE country IN ({placeholders})
                """
                cursor = conn.execute(query, countries)
                while rows := cursor.fetchmany(1000):
                    for start_ip, end_ip in rows:
                        try:
                            start_ip_obj = ipaddress.ip_address(start_ip)
                            end_ip_obj = ipaddress.ip_address(end_ip)
                            if isinstance(
                                start_ip_obj, ipaddress.IPv4Address
                            ) and isinstance(end_ip_obj, ipaddress.IPv4Address):
                                for network in ipaddress.summarize_address_range(
                                    start_ip_obj, end_ip_obj
                                ):
                                    yield network
                        except ValueError:
                            logger.warning(
                                "Invalid IP address range: %s - %s",
                                start_ip,
                                end_ip,
                            )
                            continue
            except duckdb.Error as exc:
                logger.error("Database error when fetching country nets: %s", exc)
                raise

    def _validate_countries(self, countries: Union[str, List[str]]) -> List[str]:
        if isinstance(countries, str):
            countries = [countries]
        elif isinstance(countries, list):
            countries = countries.copy()
        else:
            raise ValueError("countries must be string or list of strings")

        if not countries:
            raise ValueError("countries must not be empty")
        if len(countries) > 100:
            raise ValueError("Too many countries specified")

        for country in countries:
            if not isinstance(country, str):
                raise ValueError("countries must contain only strings")
            if not country.strip():
                raise ValueError("country values must not be empty")

        return countries
