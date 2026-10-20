# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for making parsed records comparable."""

from __future__ import annotations

import pytest

from axlib.parse.normalize import (
    PROFILES,
    canonical_interface,
    choose_key,
    natural_key,
    normalize_records,
    sort_records,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Gig 1/0/48", "GigabitEthernet1/0/48"),
        ("Gi1/0/48", "GigabitEthernet1/0/48"),
        ("GigabitEthernet1/0/48", "GigabitEthernet1/0/48"),
        ("Gig 0", "GigabitEthernet0"),
        ("Ten 1/1/1", "TenGigabitEthernet1/1/1"),
        ("Te1/1/1", "TenGigabitEthernet1/1/1"),
        ("Tw1/0/1", "TwoGigabitEthernet1/0/1"),
        ("Twe1/1/1", "TwentyFiveGigE1/1/1"),
        ("Fa0/1", "FastEthernet0/1"),
        ("Hu1/0/49", "HundredGigE1/0/49"),
        ("Eth1/1", "Ethernet1/1"),
        ("Po10", "Port-channel10"),
        ("Gi1/0/1.100", "GigabitEthernet1/0/1.100"),
        ("  Vl10 ", "Vlan10"),
        # Left alone: a phone's port, Junos, management, and unknown names.
        ("Port 1", "Port 1"),
        ("ge-0/0/0", "ge-0/0/0"),
        ("mgmt0", "mgmt0"),
        ("Management1", "Management1"),
        ("TenGigE0/0/0/0", "TenGigE0/0/0/0"),
        ("T1/0/1", "T1/0/1"),
        ("aabb.ccdd.eeff", "aabb.ccdd.eeff"),
        ("", ""),
    ],
)
def test_canonical_interface(name: str, expected: str) -> None:
    assert canonical_interface(name) == expected


def test_natural_key_orders_numbers_by_value() -> None:
    names = ["Gi1/0/10", "Gi1/0/2", "Vlan100", "gi1/0/1", "Vlan20"]

    assert sorted(names, key=natural_key) == [
        "gi1/0/1",
        "Gi1/0/2",
        "Gi1/0/10",
        "Vlan20",
        "Vlan100",
    ]
    assert natural_key(["a", "b"]) == natural_key('["a", "b"]')


def test_normalize_drops_volatile_fields_and_sorts_by_profile_key() -> None:
    records = [
        {"neighbor_name": "b ", "local_interface": "Gig 1/0/10", "holdtime": "150"},
        {"neighbor_name": "a", "local_interface": "Gi1/0/2", "holdtime": "120"},
    ]

    normalized, key = normalize_records(records, command="show cdp neighbors")

    assert key == ("local_interface", "neighbor_name")
    assert normalized == [
        {"neighbor_name": "a", "local_interface": "GigabitEthernet1/0/2"},
        {"neighbor_name": "b", "local_interface": "GigabitEthernet1/0/10"},
    ]


def test_template_keys_win_and_missing_profile_keys_are_skipped() -> None:
    records = [{"neighbor": "10.0.0.2", "interface": "Vlan10", "mode": "DR"}]

    by_template, template_key = normalize_records(
        records, command="show ip pim neighbor", template_keys=("neighbor",)
    )
    by_profile, profile_key = normalize_records(
        [{"interface": "Gi1/0/1", "status": "up"}], command="show ip interface brief"
    )
    whole, whole_key = normalize_records(
        [{"x": ["  a ", "b"]}], command="show something else"
    )

    assert template_key == ("neighbor",)
    assert by_template == records
    # The profile key is (vrf, interface); IOS output has no vrf column.
    assert profile_key == ("interface",)
    assert by_profile == [{"interface": "GigabitEthernet1/0/1", "status": "up"}]
    assert whole_key == ()
    assert whole == [{"x": ["a", "b"]}]


def test_ignore_removes_fields_from_records_and_key() -> None:
    records = [{"port": "Gi1/0/1", "name": "uplink", "status": "connected"}]

    normalized, key = normalize_records(
        records, command="show interfaces status", ignore={"name"}
    )

    assert normalized == [{"port": "GigabitEthernet1/0/1", "status": "connected"}]
    assert key == ("port",)


def test_sort_records_breaks_ties_on_the_whole_record() -> None:
    records = [{"k": "1", "v": "b"}, {"k": "1", "v": "a"}, {"k": "0", "v": "z"}]

    assert sort_records(records, ("k",)) == [
        {"k": "0", "v": "z"},
        {"k": "1", "v": "a"},
        {"k": "1", "v": "b"},
    ]


def test_choose_key_falls_back_to_whole_record() -> None:
    assert choose_key({"a"}, profile=PROFILES["show cdp neighbors"]) == ()
    assert choose_key({"a", "b"}, template_keys=("b", "c")) == ("b",)
