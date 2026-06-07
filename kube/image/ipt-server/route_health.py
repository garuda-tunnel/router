"""OSPF-based route health source for ipt_server.

Provides an abstraction for querying FRR OSPF neighbor state and reporting
per-interface health.  Shell execution and JSON parsing are kept strictly
separate so that _parse_neighbor_health can be tested as a pure function
without any subprocess involvement.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class HealthSource(Protocol):
    """Abstract interface for route health sources."""

    def get_interface_health(self, gated_interfaces: dict) -> dict[str, bool]:
        """Return health status per interface name.

        Parameters
        ----------
        gated_interfaces:
            Dict mapping logical interface name to an interface config object
            that exposes at least ``.required_state`` and
            ``.neighbor_interface`` attributes.

        Returns
        -------
        dict[str, bool]
            Mapping of interface name -> True (healthy) / False (unhealthy).
        """
        ...


class FrrVtyshOspfHealthSource:
    """Query FRR OSPF neighbor state via vtysh and report per-interface health.

    Fail-closed: any error reading or parsing FRR output causes *all* gated
    interfaces to be reported as unhealthy.
    """

    def __init__(
        self,
        vtysh_command: list[str] | None = None,
    ) -> None:
        if vtysh_command is None:
            vtysh_command = ["vtysh", "-c", "show ip ospf neighbor json"]
        self._vtysh_command = vtysh_command

    # ------------------------------------------------------------------
    # Internal helpers – kept separate for testability
    # ------------------------------------------------------------------

    def _fetch_ospf_state(self) -> dict:
        """Run vtysh and return the parsed JSON dict.

        Returns an empty dict on *any* error (subprocess failure, non-zero
        exit code, or invalid JSON).  The caller treats an empty dict as
        all-unhealthy, so this is fail-closed.
        """
        try:
            result = subprocess.run(
                self._vtysh_command,
                capture_output=True,
                text=True,
            )
            return json.loads(result.stdout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch OSPF state: %s", exc)
            return {}

    @staticmethod
    def _parse_neighbor_health(
        ospf_data: dict,
        gated_interfaces: dict,
    ) -> dict[str, bool]:
        """Determine per-interface health from raw FRR OSPF JSON.

        Pure function – no side effects, no I/O.

        An interface is *healthy* when there is at least one OSPF neighbor
        entry whose ``ifaceName`` matches the interface's configured
        ``neighbor_interface`` (allowing FRR's ``iface:address`` suffix form)
        and whose OSPF state starts with the ``required_state`` prefix
        (e.g. ``"Full"`` matches ``"Full/-"``, ``"Full/DR"`` and
        ``"Full/Backup"``).

        Parameters
        ----------
        ospf_data:
            Parsed FRR JSON as returned by ``_fetch_ospf_state``.
        gated_interfaces:
            Dict mapping logical interface name to a config object with
            ``.required_state`` and ``.neighbor_interface`` attributes.

        Returns
        -------
        dict[str, bool]
        """
        # Collect all neighbor entries into a flat list for easy scanning.
        neighbors: list[dict] = []
        try:
            raw_neighbors = ospf_data.get("neighbors", {})
            if not isinstance(raw_neighbors, dict):
                raise ValueError("neighbors is not a dict")
            for entry_list in raw_neighbors.values():
                if isinstance(entry_list, list):
                    neighbors.extend(entry_list)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Malformed OSPF neighbors data: %s", exc)
            # Fall through with empty neighbors -> all unhealthy

        health: dict[str, bool] = {}
        for iface_name, iface_cfg in gated_interfaces.items():
            required_state: str = iface_cfg.required_state
            target_iface: str | None = iface_cfg.neighbor_interface

            healthy = any(
                isinstance(n, dict)
                and isinstance(n.get("ifaceName"), str)
                and (
                    n["ifaceName"] == target_iface
                    or n["ifaceName"].split(":", 1)[0] == target_iface
                )
                and isinstance(n.get("nbrState") or n.get("state"), str)
                and (n.get("nbrState") or n.get("state")).startswith(required_state)
                for n in neighbors
            )
            health[iface_name] = healthy

        return health

    # ------------------------------------------------------------------
    # Public interface (HealthSource protocol)
    # ------------------------------------------------------------------

    def get_interface_health(self, gated_interfaces: dict) -> dict[str, bool]:
        """Return health status per interface name by querying FRR OSPF."""
        ospf_data = self._fetch_ospf_state()
        return self._parse_neighbor_health(ospf_data, gated_interfaces)
