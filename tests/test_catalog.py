from dataclasses import replace

import pandas as pd
import pytest

from psdata import catalog


@pytest.fixture
def sources():
    return catalog.load_sources()


@pytest.fixture
def series():
    return catalog.load_series()


def test_repository_catalog_meets_criteria(series, sources):
    assert catalog.validate(series, sources) == []


def test_empty_used_by_is_rejected(series, sources):
    bad = [replace(series[0], used_by=())]
    assert any("used_by" in e for e in catalog.validate(bad, sources))


def test_unknown_creator_is_rejected(series, sources):
    bad = [replace(series[0], creator="sp_global")]
    assert any("creators" in e for e in catalog.validate(bad, sources))


def test_disallowed_license_is_rejected(series, sources):
    sources["creators"]["frb"]["license"] = "proprietary"
    assert any("allowed_licenses" in e for e in catalog.validate(series, sources))


def test_id_prefix_must_match_distributor(series, sources):
    bad = [replace(series[0], id=f"x{series[0].distributor}.X")]
    assert any("接頭辞" in e for e in catalog.validate(bad, sources))


@pytest.mark.parametrize("w,expected", [("10D", pd.DateOffset(days=10)), ("5Y", pd.DateOffset(years=5))])
def test_parse_window(w, expected):
    assert catalog.parse_window(w) == expected


def test_parse_window_rejects_garbage():
    with pytest.raises(ValueError):
        catalog.parse_window("5 years")


def test_series_table_carries_credit(series, sources):
    t = catalog.series_table(series, sources)
    assert t["credit"].notna().all() and t["license"].notna().all()


def test_missing_definition_or_source_url_is_rejected(series, sources):
    bad = [replace(series[0], definition=""), replace(series[1], id=series[1].id + "X", source_url="")]
    errors = catalog.validate(bad, sources)
    assert any("definition" in e for e in errors) and any("source_url" in e for e in errors)
