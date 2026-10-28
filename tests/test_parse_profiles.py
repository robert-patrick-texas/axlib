# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for the built-in profiles of neighbor, route, ARP, and MAC tables.

The check operators run before and after a maintenance window is "is every
neighbor still there?"  Between any two views the device lists neighbors in
another order and its timers (hold time, age, dead time, uptime) move, so
these tests parse real template output twice with exactly those differences
and expect no change, then take one neighbor away and expect it reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ntc_templates
import pytest
import textfsm

from axlib.parse import TemplateCatalog, compare, parse_text
from axlib.parse.normalize import PROFILES, choose_key
from axlib.parse.snapshot import Snapshot

pytestmark = pytest.mark.usefixtures("clean_parse_environment")

NTC = Path(ntc_templates.__file__).parent / "templates"


# --- each profile against the fields its templates produce --------------------


@pytest.mark.parametrize(
    ("template", "command", "key", "dropped"),
    [
        # CDP and LLDP: the neighbor on a local port; timers dropped.
        (
            "cisco_ios_show_cdp_neighbors",
            "show cdp neighbors",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_ios_show_cdp_neighbors_detail",
            "show cdp neighbors detail",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_ios_show_lldp_neighbors",
            "show lldp neighbors",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_ios_show_lldp_neighbors_detail",
            "show lldp neighbors detail",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_nxos_show_cdp_neighbors",
            "show cdp neighbors",
            ("local_interface", "neighbor_name"),
            {"holdtime"},
        ),
        (
            "cisco_nxos_show_cdp_neighbors_detail",
            "show cdp neighbors detail",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_nxos_show_lldp_neighbors",
            "show lldp neighbors",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_nxos_show_lldp_neighbors_detail",
            "show lldp neighbors detail",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_xr_show_cdp_neighbors_detail",
            "show cdp neighbors detail",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_xr_show_lldp_neighbors",
            "show lldp neighbors",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        (
            "cisco_xr_show_lldp_neighbors_detail",
            "show lldp neighbors detail",
            ("local_interface", "neighbor"),
            set(),
        ),
        (
            "arista_eos_show_lldp_neighbors_detail",
            "show lldp neighbors detail",
            ("local_interface", "neighbor_name"),
            {"age"},
        ),
        (
            "juniper_junos_show_lldp_neighbors",
            "show lldp neighbors",
            ("local_interface", "neighbor_name"),
            set(),
        ),
        # Routing-protocol neighbors.
        (
            "cisco_ios_show_ip_ospf_neighbor",
            "show ip ospf neighbor",
            ("neighbor_id", "interface"),
            {"dead_time"},
        ),
        (
            "cisco_nxos_show_ip_ospf_neighbor",
            "show ip ospf neighbor",
            ("vrf", "neighbor_id", "interface"),
            {"uptime"},
        ),
        (
            "cisco_xr_show_ospf_neighbor",
            "show ospf neighbor",
            ("neighbor_id", "interface"),
            {"dead_time", "neighbor_uptime"},
        ),
        (
            "cisco_xr_show_ospf_vrf_all_neighbor",
            "show ospf vrf all neighbor",
            ("vrf", "neighbor_id", "interface"),
            {"dead_time", "neighbor_uptime"},
        ),
        (
            "cisco_ios_show_ip_eigrp_neighbors",
            "show ip eigrp neighbors",
            ("as", "ip_address", "interface"),
            {"hold", "uptime", "srtt", "rto", "q_cnt", "seq_num"},
        ),
        (
            "cisco_nxos_show_ip_eigrp_neighbors",
            "show ip eigrp neighbors",
            ("vrf", "as", "ip_address", "interface"),
            {"hold", "uptime", "srtt", "rto", "q_cnt", "seq_num"},
        ),
        (
            "cisco_xr_show_eigrp_neighbors",
            "show eigrp neighbors",
            ("as", "ip_address", "interface"),
            {"hold", "uptime", "srtt", "rto", "q_cnt", "seq_num"},
        ),
        (
            "cisco_ios_show_ip_bgp_summary",
            "show ip bgp summary",
            ("address_family", "bgp_neighbor"),
            {
                "messages_received",
                "messages_sent",
                "table_version",
                "input_queue",
                "output_queue",
                "up_down",
            },
        ),
        (
            "cisco_nxos_show_ip_bgp_summary",
            "show ip bgp summary",
            ("vrf", "bgp_neigh"),
            {"msg_rcvd", "msg_sent", "in_queue", "out_queue", "up_down"},
        ),
        (
            "cisco_xr_show_ip_bgp_summary",
            "show ip bgp summary",
            ("bgp_neigh",),
            {"msg_rcvd", "msg_sent", "in_queue", "out_queue", "up_down"},
        ),
        (
            "cisco_xr_show_bgp_vrf_all_ipv4_unicast_summary",
            "show bgp vrf all ipv4 unicast summary",
            ("vrf",),
            {
                "messages_received",
                "messages_sent",
                "table_version",
                "input_queue",
                "output_queue",
                "up_down",
            },
        ),
        (
            "cisco_ios_show_isis_neighbors",
            "show isis neighbors",
            ("system_id", "interface", "type"),
            {"hold_time"},
        ),
        (
            "cisco_xr_show_isis_neighbors",
            "show isis neighbors",
            ("system_id", "interface", "type"),
            {"hold_time"},
        ),
        # Routes: one record per path, so the next hop is in the key.
        (
            "cisco_ios_show_ip_route",
            "show ip route",
            ("vrf", "network", "prefix_length", "nexthop_ip", "nexthop_if"),
            {"uptime"},
        ),
        (
            "cisco_nxos_show_ip_route",
            "show ip route",
            ("vrf", "network", "prefix_length", "nexthop_ip", "nexthop_if"),
            {"uptime"},
        ),
        (
            "cisco_xr_show_ip_route",
            "show ip route",
            ("vrf", "network", "prefix_length", "next_hop", "interface"),
            {"uptime"},
        ),
        # ARP and MAC tables.
        ("cisco_ios_show_ip_arp", "show ip arp", ("ip_address", "interface"), {"age"}),
        ("cisco_ios_show_arp", "show arp", ("address", "interface"), {"age_min"}),
        ("cisco_nxos_show_ip_arp", "show ip arp", ("ip_address", "interface"), {"age"}),
        ("cisco_xr_show_arp", "show arp", ("ip_address", "interface", "cpu"), {"age"}),
        (
            "cisco_ios_show_mac-address-table",
            "show mac-address-table",
            ("vlan_id", "destination_address"),
            set(),
        ),
        (
            "cisco_nxos_show_mac_address-table",
            "show mac address-table",
            ("vlan_id", "mac_address"),
            {"age"},
        ),
        (
            "arista_eos_show_mac_address-table",
            "show mac address-table",
            ("vlan_id", "mac_address"),
            {"moves", "last_move"},
        ),
    ],
)
def test_profile_key_and_dropped_fields_match_the_template(
    template: str, command: str, key: tuple[str, ...], dropped: set[str]
) -> None:
    with (NTC / f"{template}.textfsm").open(encoding="utf-8") as handle:
        fields = {name.lower() for name in textfsm.TextFSM(handle).header}
    profile = PROFILES[command]

    assert choose_key(fields, profile=profile) == key
    assert fields & set(profile.volatile) == dropped


# --- neighbors stay present across views --------------------------------------


@dataclass(frozen=True)
class Neighbors:
    """Sample output: a header, then one block per neighbor.

    ``{0}`` in a block or the header stands for a timer that moves between
    views; the second block is the one taken away.
    """

    platform: str
    command: str
    head: str
    blocks: tuple[str, ...]
    gone: dict[str, str]
    tail: str = ""


def view(
    case: Neighbors, tick: int, *, reverse: bool = False, drop: int | None = None
) -> str:
    """One view of the sample: timers at ``tick``, neighbors in either order."""
    order = [index for index in range(len(case.blocks)) if index != drop]
    if reverse:
        order.reverse()
    body = "".join(case.blocks[index].format(tick) for index in order)
    return case.head.format(tick) + body + case.tail


def parse(case: Neighbors, text: str, *, raw: bool = False) -> Snapshot:
    """Parse one view with ntc-templates as device r1."""
    catalog = TemplateCatalog.load(platform=case.platform, environ={})
    return parse_text(
        text,
        catalog=catalog,
        platform=case.platform,
        command=case.command,
        host="r1",
        raw=raw,
    )


NXOS_CDP = Neighbors(
    "cisco_nxos",
    "show cdp neighbors",
    "Capability Codes: R - Router, T - Trans-Bridge, B - Source-Route-Bridge\n"
    "                  S - Switch, H - Host, I - IGMP, r - Repeater,\n"
    "                  V - VoIP-Phone, D - Remotely-Managed-Device,\n"
    "                  s - Supports-STP-Dispute\n"
    "\n"
    "Device-ID          Local Intrfce  Hldtme Capability  Platform      Port ID\n",
    (
        (
            "leaf1(FDO21120U8N)\n"
            "                    Eth1/1         1{0}8    "
            "R S I s   N9K-C93180YC- Eth1/49\n"
        ),
        (
            "leaf2(FDO21120U9P)\n"
            "                    Eth1/2         1{0}2    "
            "R S I s   N9K-C93180YC- Eth1/49\n"
        ),
        (
            "core1.example.net   mgmt0          1{0}3    "
            "R S I     WS-C3850-48P  Gi1/0/12\n"
        ),
    ),
    {"local_interface": "Ethernet1/2", "neighbor_name": "leaf2(FDO21120U9P)"},
    "\nTotal entries displayed: 3\n",
)

EOS_LLDP_BLOCK = (
    "Interface Ethernet{n} detected 1 LLDP neighbors:\n"
    "\n"
    '  Neighbor 001c.7300.000{n}/"Ethernet1", age {{0}}3 seconds\n'
    "  Discovered 6 days, 1:09:40 ago; Last changed 6 days, 1:09:40 ago\n"
    "  - Chassis ID type: MAC address (4)\n"
    "    Chassis ID     : 001c.7300.000{n}\n"
    "  - Port ID type: Interface name (5)\n"
    '    Port ID     : "Ethernet1"\n'
    "  - Time To Live: 120 seconds\n"
    '  - System Name: "spine{n}"\n'
    '  - System Description: "Arista Networks EOS version 4.31.2F running on an'
    ' Arista Networks DCS-7050SX3-48YC8"\n'
    "  - Management Address Subtype: IPv4 (1)\n"
    "    Management Address        : 192.0.2.3{n}\n"
    "\n"
)
EOS_LLDP = Neighbors(
    "arista_eos",
    "show lldp neighbors detail",
    "",
    tuple(EOS_LLDP_BLOCK.format(n=n) for n in (1, 2, 3)),
    {"local_interface": "Ethernet2", "neighbor_name": "spine2"},
)

XR_LLDP_BLOCK = (
    "------------------------------------------------\n"
    "Local Interface: GigabitEthernet0/0/0/{n}\n"
    "Chassis id: 0c75.bd61.0b0{n}\n"
    "Port id: Gi0/0/0\n"
    "Port Description: GigabitEthernet0/0/0\n"
    "System Name: rtr{n}\n"
    "\n"
    "System Description:\n"
    "Cisco IOS Software [Cupertino], ISR Software (X86_64_LINUX_IOSD-UNIVERSALK9-M),"
    " Version 17.9.4a, RELEASE SOFTWARE (fc3)\n"
    "\n"
    "Time remaining: 1{{0}}1 seconds\n"
    "Hold Time: 120 seconds\n"
    "System Capabilities: B,R\n"
    "Enabled Capabilities: R\n"
    "Management Addresses:\n"
    "  IPv4 address: 10.0.0.1{n}\n"
    "\n"
    "Peer MAC Address: 0c:75:bd:61:0b:0{n}\n"
    "\n"
)
XR_LLDP = Neighbors(
    "cisco_xr",
    "show lldp neighbors detail",
    "Wed Oct 28 22:01:20.115 EDT\n"
    "Capability codes:\n"
    "        (R) Router, (B) Bridge, (T) Telephone, (C) DOCSIS Cable Device\n"
    "        (W) WLAN Access Point, (P) Repeater, (S) Station, (O) Other\n"
    "\n",
    tuple(XR_LLDP_BLOCK.format(n=n) for n in (0, 1, 2)),
    {"local_interface": "GigabitEthernet0/0/0/1", "neighbor": "rtr1"},
    "\nTotal entries displayed: 3\n",
)

# Long device lines are split in two at a column; the text is unchanged.
XR_OSPF = Neighbors(
    "cisco_xr",
    "show ospf neighbor",
    "Wed Oct 28 22:01:20.115 EDT\n"
    "\n"
    "* Indicates MADJ interface\n"
    "# Indicates Neighbor awaiting BFD session up\n"
    "\n"
    "Neighbors for OSPF 1\n"
    "\n"
    "Neighbor ID     Pri   State           Dead Time   Address         Interface\n",
    (
        (
            "10.255.0.11     1     FULL/  -        00:00:3{0}    10.0.0.2        "
            "GigabitEthernet0/0/0/1\n"
            "    Neighbor is up for 2d0{0}h\n"
        ),
        (
            "10.255.0.12     1     FULL/  -        00:00:3{0}    10.0.0.6        "
            "GigabitEthernet0/0/0/2\n"
            "    Neighbor is up for 1d0{0}h\n"
        ),
        (
            "10.255.0.13     1     FULL/DR         00:00:3{0}    10.0.1.2        "
            "GigabitEthernet0/0/0/3\n"
            "    Neighbor is up for 5w{0}d\n"
        ),
    ),
    {"neighbor_id": "10.255.0.12", "interface": "GigabitEthernet0/0/0/2"},
    "\nTotal neighbor count: 3\n",
)

NXOS_OSPF = Neighbors(
    "cisco_nxos",
    "show ip ospf neighbor",
    " OSPF Process ID 1 VRF default\n"
    " Total number of neighbors: 3\n"
    " Neighbor ID     Pri State            Up Time  Address         Interface\n",
    (
        " 10.255.0.21       1 FULL/ -          00:1{0}:13 10.0.1.1        Eth1/1\n",
        " 10.255.0.22       1 FULL/ -          00:2{0}:41 10.0.2.1        Eth1/2\n",
        " 10.255.0.23       1 FULL/DR          3d0{0}h    10.0.3.1        Vlan30\n",
    ),
    {"vrf": "default", "neighbor_id": "10.255.0.22", "interface": "Ethernet1/2"},
)

IOS_EIGRP = Neighbors(
    "cisco_ios",
    "show ip eigrp neighbors",
    "EIGRP-IPv4 Neighbors for AS(100)\n"
    "H   Address                 Interface              "
    "Hold Uptime   SRTT   RTO  Q  Seq\n"
    "                                                   "
    "(sec)         (ms)       Cnt Num\n",
    (
        (
            "2   10.1.0.1                Gi0/0/0                  "
            "1{0} 5w2d        1   100  0  4{0}\n"
        ),
        (
            "1   10.0.0.5                Gi0/0/2                  "
            "1{0} 00:4{0}:07    2   100  0  9{0}\n"
        ),
        (
            "0   10.0.2.9                Tu100                    "
            "1{0} 2d0{0}h      12   200  0  1{0}\n"
        ),
    ),
    {"as": "100", "ip_address": "10.0.0.5", "interface": "GigabitEthernet0/0/2"},
)

IOS_BGP = Neighbors(
    "cisco_ios",
    "show ip bgp summary",
    "BGP router identifier 10.255.0.11, local AS number 65011\n"
    "BGP table version is 41{0}, main routing table version 41{0}\n"
    "12 network entries using 2976 bytes of memory\n"
    "14 path entries using 1904 bytes of memory\n"
    "\n"
    "Neighbor        V           AS MsgRcvd MsgSent   TblVer  "
    "InQ OutQ Up/Down  State/PfxRcd\n",
    (
        (
            "10.0.0.1        4        65000   4821{0}   4819{0}      41{0}    "
            "0    0 4w2d           11\n"
        ),
        (
            "10.0.0.5        4        65000    120{0}    118{0}      41{0}    "
            "0    0 1d0{0}h          11\n"
        ),
        (
            "192.0.2.254     4        64512       0       0        1    "
            "0    0 never    Active\n"
        ),
    ),
    {"address_family": "", "bgp_neighbor": "10.0.0.5"},
)

IOS_ISIS = Neighbors(
    "cisco_ios",
    "show isis neighbors",
    "Tag CORE:\n"
    "System Id       Type Interface     IP Address      State Holdtime Circuit Id\n",
    (
        (
            "core1           L2   Gi0/0/1       10.0.0.1    "
            "    UP    2{0}       core1.01\n"
        ),
        (
            "core2           L2   Gi0/0/2       10.0.0.5    "
            "    UP    2{0}       core2.01\n"
        ),
        (
            "sw1             L1   Gi0/0/0       10.1.0.1    "
            "    UP    {0}        rtr1.02\n"
        ),
    ),
    {"system_id": "core2", "interface": "GigabitEthernet0/0/2", "type": "L2"},
)

XR_ISIS = Neighbors(
    "cisco_xr",
    "show isis neighbors",
    "IS-IS CORE neighbors:\n"
    "System Id      Interface        SNPA           State Holdtime Type IETF-NSF\n",
    (
        (
            "rtr1           Gi0/0/0/1        0c75.bd61.0a02 "
            "Up    2{0}       L2   Capable\n"
        ),
        (
            "rtr2           Gi0/0/0/2        0c75.bd61.0c02 "
            "Up    2{0}       L2   Capable\n"
        ),
        (
            "rtr3           BE10             *PtoP*         "
            "Up    2{0}       L1L2 Capable\n"
        ),
    ),
    {"system_id": "rtr2", "interface": "GigabitEthernet0/0/0/2", "type": "L2"},
    "\nTotal neighbor count: 3\n",
)

CASES = [
    NXOS_CDP,
    EOS_LLDP,
    XR_LLDP,
    XR_OSPF,
    NXOS_OSPF,
    IOS_EIGRP,
    IOS_BGP,
    IOS_ISIS,
    XR_ISIS,
]
IDS = [f"{case.platform}:{case.command}" for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reordered_neighbors_with_moved_timers_are_unchanged(case: Neighbors) -> None:
    before_text = view(case, 1)
    after_text = view(case, 4, reverse=True)

    before, after = parse(case, before_text), parse(case, after_text)

    assert before.problems == ()
    assert len(before.results[0].records) == len(case.blocks)
    # As the template returns them, the two views differ...
    raw_before = parse(case, before_text, raw=True).results[0].records
    raw_after = parse(case, after_text, raw=True).results[0].records
    assert raw_before != raw_after
    # ...and once normalized they are the same neighbors.
    assert compare(before, after).differences == ()
    assert before.results[0].records == after.results[0].records


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_neighbor_that_did_not_come_back_is_reported_removed(case: Neighbors) -> None:
    before = parse(case, view(case, 1))
    after = parse(case, view(case, 4, reverse=True, drop=1))

    [command] = compare(before, after).differences

    assert [(change.kind, change.key) for change in command.changes] == [
        ("removed", case.gone)
    ]
