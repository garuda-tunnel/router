"""Tests for FrrVtyshOspfHealthSource._fetch_ospf_state."""

import unittest
from unittest.mock import patch, MagicMock
from route_health import FrrVtyshOspfHealthSource


# FRR JSON sample with one Full/DR neighbor on wg_uk
_SAMPLE_OSPF_JSON = {
    "neighbors": {
        "10.9.19.2": [
            {
                "priority": 1,
                "state": "Full/DR",
                "address": "10.9.19.2",
                "ifaceName": "wg_uk",
                "retransmitCounter": 0,
                "requestCounter": 0,
                "dbSummaryCounter": 0,
            }
        ]
    }
}


class TestFetchOspfState(unittest.TestCase):
    """Tests for FrrVtyshOspfHealthSource._fetch_ospf_state."""

    def test_subprocess_error_returns_empty_dict(self):
        """If subprocess raises, _fetch_ospf_state returns empty dict (fail-closed)."""
        src = FrrVtyshOspfHealthSource(vtysh_command=["false"])
        with patch("subprocess.run", side_effect=OSError("command not found")):
            result = src._fetch_ospf_state()
        self.assertEqual(result, {})

    def test_invalid_json_output_returns_empty_dict(self):
        """If vtysh returns non-JSON output, _fetch_ospf_state returns empty dict."""
        mock_result = MagicMock()
        mock_result.stdout = "not json at all"
        src = FrrVtyshOspfHealthSource()
        with patch("subprocess.run", return_value=mock_result):
            result = src._fetch_ospf_state()
        self.assertEqual(result, {})

    def test_valid_json_output_returns_parsed_dict(self):
        """Valid JSON output from vtysh is returned as a parsed dict."""
        import json

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(_SAMPLE_OSPF_JSON)
        src = FrrVtyshOspfHealthSource()
        with patch("subprocess.run", return_value=mock_result):
            result = src._fetch_ospf_state()
        self.assertEqual(result, _SAMPLE_OSPF_JSON)


if __name__ == "__main__":
    unittest.main()
