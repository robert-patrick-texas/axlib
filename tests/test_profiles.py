# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for credential record profiles and their standalone listing."""

from __future__ import annotations

import json
from io import StringIO

import pytest

from axlib.credentials.profiles import (
    INFOBLOX_PROFILE,
    NETWORK_PROFILE,
    get_profile,
    main,
    missing_required,
    profile_for_fields,
    validate_values,
)


def test_profiles_match_the_lookup_helpers() -> None:
    assert NETWORK_PROFILE.field_names == ("netuser", "netpass", "netenable")
    assert NETWORK_PROFILE.required_names == ("netuser", "netpass")
    assert INFOBLOX_PROFILE.required_names == ("ibgrid", "ibuser", "ibpass")
    assert get_profile("infoblox") is INFOBLOX_PROFILE
    with pytest.raises(ValueError, match="Unknown credential profile"):
        get_profile("radius")


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (("ibgrid", "ibpass"), INFOBLOX_PROFILE),
        (("netuser",), NETWORK_PROFILE),
        (("legacy",), NETWORK_PROFILE),
        ((), NETWORK_PROFILE),
    ],
)
def test_profile_for_fields_prefers_best_match_then_network(
    fields: tuple[str, ...], expected: object
) -> None:
    assert profile_for_fields(fields) is expected


def test_missing_required_keeps_display_order() -> None:
    assert missing_required(["netenable"], NETWORK_PROFILE) == ("netuser", "netpass")
    assert missing_required(["netuser", "netpass"], NETWORK_PROFILE) == ()


def test_validate_values_rules() -> None:
    with pytest.raises(ValueError, match="at least one"):
        validate_values({}, NETWORK_PROFILE, creating=False)
    with pytest.raises(ValueError, match="received: ibpass"):
        validate_values({"ibpass": "x"}, NETWORK_PROFILE, creating=False)
    with pytest.raises(ValueError, match="missing: netpass"):
        validate_values({"netuser": "ops"}, NETWORK_PROFILE, creating=True)
    # A partial update does not need the required fields.
    assert validate_values({"netpass": "x"}, NETWORK_PROFILE, creating=False) == {
        "netpass": "x"
    }


def test_standalone_listing_as_table_and_json() -> None:
    table = StringIO()
    assert main([], stream=table) == 0
    lines = table.getvalue().splitlines()
    assert lines[0].split() == ["PROFILE", "FIELD", "REQUIRED", "SECRET", "DESCRIPTION"]
    assert any(
        line.split()[:4] == ["network", "netpass", "yes", "yes"] for line in lines
    )

    as_json = StringIO()
    assert main(["--json"], stream=as_json) == 0
    payload = json.loads(as_json.getvalue())
    assert [profile["name"] for profile in payload] == ["network", "infoblox"]
    assert payload[0]["fields"][2] == {
        "name": "netenable",
        "label": "Enable secret",
        "secret": True,
        "required": False,
    }
