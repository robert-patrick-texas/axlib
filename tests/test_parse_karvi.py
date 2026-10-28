# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for parsing karvi's collection files (docs/KARVI.md, examples/karvi)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axlib.parse import TemplateCatalog, compare, parse_paths, split_capture
from axlib.parse.diff import SnapshotDiff
from axlib.parse.snapshot import Snapshot

pytestmark = pytest.mark.usefixtures("clean_parse_environment")

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "karvi"
# The --marker docs/KARVI.md gives for karvi's "! COMMAND" lines.
MARKER = r"^! (?P<command>(?:sh|sho|show)\s.+)$"
# Timers the built-in profiles drop; naming them again changes nothing.
TIMERS = ("age", "uptime", "dead_time")


def collections(platform: str) -> tuple[Snapshot, Snapshot]:
    """The example's before and after collections of one platform, parsed."""
    catalog = TemplateCatalog.load(platform=platform, environ={})
    return (
        parse_paths(
            [str(EXAMPLES / "before" / platform)], catalog=catalog, marker=MARKER
        ),
        parse_paths(
            [str(EXAMPLES / "after" / platform)], catalog=catalog, marker=MARKER
        ),
    )


def test_karvi_files_are_split_on_the_marker_only() -> None:
    text = (EXAMPLES / "before" / "cisco_ios" / "sw1").read_text(encoding="utf-8")

    blocks = split_capture(text, source="sw1", marker=MARKER)

    assert [(block.command, block.host) for block in blocks] == [
        ("show interfaces status", None),
        ("show mac address-table", None),
        ("show ip arp", None),
        ("show cdp neighbors", None),
    ]
    # Without the marker nothing in the file looks like a command.
    assert split_capture(text, source="sw1") == []


def test_every_example_command_parses_and_names_the_device_by_file() -> None:
    for platform in ("cisco_ios", "cisco_xr"):
        for snapshot in collections(platform):
            assert snapshot.problems == ()
            assert {result.device for result in snapshot.results} <= {
                "sw1",
                "rtr1",
                "core1",
            }

    before, _ = collections("cisco_ios")
    assert [(r.device, r.template_command) for r in before.results] == [
        ("rtr1", "show cdp neighbors"),
        ("rtr1", "show ip arp"),
        ("rtr1", "show ip interface brief"),
        ("rtr1", "show ip ospf neighbor"),
        ("rtr1", "show ip route"),
        ("sw1", "show cdp neighbors"),
        ("sw1", "show interfaces status"),
        ("sw1", "show ip arp"),
        ("sw1", "show mac-address-table"),
    ]


def test_example_change_shows_only_real_changes() -> None:
    diff = compare(*collections("cisco_ios"))

    changed = {(item.device, item.command): item.changes for item in diff.differences}
    assert len(changed) == 9
    # The neighbor check: the access point is the one neighbor that did not
    # come back, and core1 is a neighbor again on rtr1's new port.  The other
    # neighbors were listed in another order with new hold times.
    assert [
        (change.kind, change.key) for change in changed["sw1", "show cdp neighbors"]
    ] == [
        (
            "removed",
            {
                "local_interface": "GigabitEthernet1/0/5",
                "neighbor_name": "ap-lobby.example.net",
            },
        )
    ]
    assert [
        (change.kind, change.key) for change in changed["rtr1", "show cdp neighbors"]
    ] == [
        (
            "removed",
            {"local_interface": "GigabitEthernet0/0/1", "neighbor_name": "core1"},
        ),
        (
            "added",
            {"local_interface": "GigabitEthernet0/0/2", "neighbor_name": "core1"},
        ),
    ]
    status = changed["sw1", "show interfaces status"]
    assert [(change.key, change.fields) for change in status] == [
        (
            {"port": "GigabitEthernet1/0/5"},
            {
                "status": ("connected", "notconnect"),
                "duplex": ("a-full", "auto"),
                "speed": ("a-1000", "auto"),
            },
        ),
        ({"port": "GigabitEthernet1/0/12"}, {"vlan_id": ("20", "30")}),
    ]
    # ARP entries outlive the hosts, so sw1 only gained the moved host's address.
    arp = changed["sw1", "show ip arp"]
    assert [
        (change.kind, change.after["ip_address"]) for change in arp if change.after
    ] == [("added", "10.30.0.112")]
    assert len(arp) == 1
    # The neighbor sw1 kept its dead time out of the report; core1 moved.
    ospf = changed["rtr1", "show ip ospf neighbor"]
    assert [
        (change.kind, (change.before or change.after or {})["ip_address"])
        for change in ospf
    ] == [("removed", "10.0.0.1"), ("added", "10.0.0.5")]
    routes = changed["rtr1", "show ip route"]
    assert sorted(change.kind for change in routes) == ["added"] * 5 + ["removed"] * 5
    assert {
        (change.after or {}).get("nexthop_ip") for change in routes if change.after
    } == {"10.0.0.5", ""}


def test_timers_need_no_ignore() -> None:
    def changes(
        diff: SnapshotDiff,
    ) -> list[tuple[str, str, list[tuple[str, dict[str, str]]]]]:
        return [
            (
                item.device,
                item.command,
                [(change.kind, change.key) for change in item.changes],
            )
            for item in diff.differences
        ]

    before, after = collections("cisco_ios")

    assert changes(compare(before, after)) == changes(
        compare(before, after, ignore=TIMERS)
    )
    mac = {
        (item.device, item.command): item.changes
        for item in compare(before, after).differences
    }["sw1", "show mac-address-table"]
    # The moved host is a MAC address that left VLAN 20 and appeared in VLAN 30.
    assert [(change.kind, change.key) for change in mac] == [
        ("removed", {"vlan_id": "20", "destination_address": "0050.56a1.1012"}),
        ("removed", {"vlan_id": "30", "destination_address": "00a2.eeb0.5505"}),
        ("added", {"vlan_id": "30", "destination_address": "0050.56a1.1012"}),
    ]


def test_ios_xr_reports_its_interface_brief_under_the_ios_name() -> None:
    before, after = collections("cisco_xr")

    assert [(r.command, r.template_command) for r in before.results] == [
        ("show ipv4 interface brief", "show ip interface brief"),
        ("show lldp neighbors", "show lldp neighbors"),
    ]
    interfaces, lldp = compare(before, after).differences
    assert interfaces.command == "show ip interface brief"
    assert [(change.key, change.fields["status"]) for change in interfaces.changes] == [
        ({"vrf": "default", "interface": "GigabitEthernet0/0/0/1"}, ("Up", "Shutdown")),
        ({"vrf": "default", "interface": "GigabitEthernet0/0/0/2"}, ("Shutdown", "Up")),
    ]
    assert [
        (change.kind, change.key["local_interface"]) for change in lldp.changes
    ] == [
        ("removed", "GigabitEthernet0/0/0/1"),
        ("added", "GigabitEthernet0/0/0/2"),
    ]
