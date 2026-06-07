"""Tests for CountryRoute pure-data model and from_country factory."""

from unittest.mock import MagicMock

from Config import CountryRoute


def test_model_construction_does_not_touch_ipdb():
    """Constructing CountryRoute must not call any IPDB — pure data model."""
    # If model_post_init still imports IPDB, this will fail because
    # /data/ipt.db doesn't exist in the test environment.
    route = CountryRoute(country="US", route={"gw": "10.0.0.1"})
    assert route.country == "US"
    # No subnets populated — pure data, no side effects
    assert route.routes == []


def test_from_country_populates_subnets_via_ipdb():
    """from_country factory must call ipdb[country] and populate subnets."""
    fake_ipdb = MagicMock()
    fake_ipdb.__getitem__.return_value = ["1.2.3.0/24", "5.6.7.0/24"]

    route = CountryRoute.from_country(
        ipdb=fake_ipdb,
        country="US",
        route={"gw": "10.0.0.1"},
    )

    fake_ipdb.__getitem__.assert_called_once_with("US")
    subnet_strings = [str(r.net) for r in route.routes]
    assert "1.2.3.0/24" in subnet_strings
    assert "5.6.7.0/24" in subnet_strings
