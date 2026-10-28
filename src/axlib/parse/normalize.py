# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Make parsed records comparable: stable order, stable names, no churn.

Two collections of the same command rarely list rows in the same order: CDP
and LLDP tables follow the order neighbors were heard, not interface order.
Some fields also change on every run (hold time, age) without meaning
anything changed.  Comparing raw text or raw parser output therefore shows
differences where there are none.  This module turns a template's records
into a form where only real changes remain:

* Fields that change on every run are dropped (a :class:`Profile` lists them
  per command; callers can add more with ``ignore``).
* Interface names are spelled one way: CDP's ``Gig 1/0/48``, LLDP's
  ``Gi1/0/48``, and ``GigabitEthernet1/0/48`` all become
  ``GigabitEthernet1/0/48`` (see :func:`canonical_interface`).
* Records are sorted by their *key* (the fields that identify a row, such as
  local interface plus neighbor name) in natural order, so ``Gi1/0/2`` sorts
  before ``Gi1/0/10``.

The key comes from the template itself when it marks fields with TextFSM's
``Key`` option (``Value Key NEIGHBOR (\\S+)``), which is how an operator's own
template says what identifies a row.  Otherwise a built-in :data:`PROFILES`
entry supplies it.  With neither, the whole record is its own identity, and a
comparison reports rows added and removed but never "changed".

Dependencies:
    Python standard library only.

Example:
    >>> canonical_interface("Gig 1/0/48")
    'GigabitEthernet1/0/48'
    >>> records, key = normalize_records(
    ...     [{"local_interface": "Gi1/0/10", "neighbor_name": "b", "holdtime": "150"},
    ...      {"local_interface": "Gi1/0/2", "neighbor_name": "a", "holdtime": "120"}],
    ...     command="show cdp neighbors",
    ... )
    >>> key
    ('local_interface', 'neighbor_name')
    >>> [record["local_interface"] for record in records]
    ['GigabitEthernet1/0/2', 'GigabitEthernet1/0/10']
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

Record = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Profile:
    """How to make one command's records comparable.

    Attributes:
        key: Fields that identify a record.  Only the ones a template actually
            produces are used, so one profile serves every platform.
        interfaces: Fields holding interface names to spell one way.
        volatile: Fields that change on every collection, dropped.
    """

    key: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    volatile: tuple[str, ...] = ()


# One profile serves a command on every platform: a profile lists each
# spelling ntc-templates uses for a field (IOS's "nexthop_ip" is IOS XR's
# "next_hop"), and only the fields a template actually produces are used.
#
# The question operators ask of CDP and LLDP before and after a change is
# "is every neighbor still there?", so a neighbor is identified by the local
# port and the neighbor's name.  The hold time counts down and the age counts
# up between any two views, so both are dropped; the order neighbors were
# heard in is undone by sorting.  IOS XR's LLDP detail calls the name
# "neighbor".
_NEIGHBORS = Profile(
    key=("local_interface", "neighbor_name", "neighbor"),
    interfaces=("local_interface", "neighbor_interface", "neighbor_port_id"),
    volatile=("holdtime", "hold_time", "age"),
)
_INTERFACES = Profile(key=("vrf", "interface"), interfaces=("interface",))
_PORTS = Profile(key=("port",), interfaces=("port",))
# A route is one path to a prefix.  The next hop is part of the key because
# equal-cost paths list one prefix several times, and a key shared by several
# records would leave the whole table compared as whole records.  A path that
# moves to another next hop is therefore reported as removed and added.
_ROUTES = Profile(
    key=(
        "vrf",
        "network",
        "prefix_length",
        "nexthop_ip",
        "next_hop",
        "nexthop_if",
        "interface",
    ),
    interfaces=("nexthop_if", "interface"),
    volatile=("uptime",),
)
# An ARP entry is an address on an interface; a new MAC address for the same
# address is the change worth seeing.  IOS XR lists the table once per line
# card ("cpu"), and IOS's "show arp" names the address field "address".
_ARP = Profile(
    key=("vrf", "ip_address", "address", "interface", "cpu"),
    interfaces=("interface", "physical_interface"),
    volatile=("age", "age_min"),
)
# A MAC address is learned once per VLAN; the port it is on is what changes.
# Arista counts moves and stamps the last one, both of which tick by.
_MAC_TABLE = Profile(
    key=("vlan_id", "mac_address", "destination_address"),
    interfaces=("destination_port", "ports"),
    volatile=("age", "moves", "last_move"),
)
# Routing-protocol neighbors: one router can be a neighbor over several
# links, so the interface is part of the key.  Timers, uptimes, sequence
# numbers, queue depths, and message counters change on every view; the
# state (FULL, Established or a prefix count, UP) is what matters.
_OSPF = Profile(
    key=("vrf", "neighbor_id", "interface"),
    interfaces=("interface",),
    volatile=(
        "dead_time",
        "uptime",
        "up_time",
        "neighbor_uptime",
        "state_changes",
        "last_oob_resync",
        "rq_index",
        "rq_length",
        "retransmission_number",
        "first_packet",
        "next_packet",
        "last_retransmission_scan_length",
        "max_retransmission_scan_length",
        "last_retransmission_scan_time",
        "max_retransmission_scan_time",
    ),
)
_EIGRP = Profile(
    key=("vrf", "as", "ip_address", "interface"),
    interfaces=("interface",),
    volatile=(
        "h",
        "hold",
        "uptime",
        "srtt",
        "rto",
        "q_cnt",
        "seq_num",
        "retrans",
        "retries",
    ),
)
_BGP_COUNTERS = (
    "messages_received",
    "messages_sent",
    "msg_rcvd",
    "msg_sent",
    "table_version",
    "tblver",
    "input_queue",
    "output_queue",
    "in_queue",
    "out_queue",
    "up_down",
    "in_pkt",
    "out_pkt",
    "out_q",
    "flaps",
    "last_flap",
)
_BGP = Profile(
    key=(
        "vrf",
        "instance_name",
        "address_family",
        "bgp_neighbor",
        "bgp_neigh",
        "peer_ip",
    ),
    volatile=_BGP_COUNTERS,
)
# IOS XR's "show bgp vrf all ipv4 unicast summary" makes one record per VRF
# with its neighbors in lists, so the VRF alone identifies a record.
_BGP_PER_VRF = Profile(key=("vrf",), volatile=_BGP_COUNTERS)
# IOS lists a level-1 and a level-2 adjacency to one router on one link as
# two rows, so the level ("type") is part of the key.
_ISIS = Profile(
    key=("vrf", "instance", "system_id", "interface", "type"),
    interfaces=("interface",),
    volatile=("hold_time", "holdtime"),
)

# Keyed by the command each template parses, as axlib spells it from the
# template's file name: IOS XR's "show route" is parsed by its
# "show ip route" template, so it is listed under that name.
PROFILES: Mapping[str, Profile] = MappingProxyType(
    {
        "show cdp neighbors": _NEIGHBORS,
        "show cdp neighbors detail": _NEIGHBORS,
        "show lldp neighbors": _NEIGHBORS,
        "show lldp neighbors detail": _NEIGHBORS,
        "show ip interface brief": _INTERFACES,
        "show ipv6 interface brief": _INTERFACES,
        "show interfaces status": _PORTS,
        "show interface status": _PORTS,
        "show ip route": _ROUTES,
        "show ipv6 route": _ROUTES,
        "show ip arp": _ARP,
        "show ip arp detail": _ARP,
        "show ip arp detail vrf all": _ARP,
        "show arp": _ARP,
        "show arp no-resolve": _ARP,
        "show mac-address-table": _MAC_TABLE,
        "show mac address-table": _MAC_TABLE,
        "show ip ospf neighbor": _OSPF,
        "show ip ospf neighbor detail": _OSPF,
        "show ospf neighbor": _OSPF,
        "show ospf vrf all neighbor": _OSPF,
        "show ip eigrp neighbors": _EIGRP,
        "show ip eigrp neighbors detail": _EIGRP,
        "show eigrp neighbors": _EIGRP,
        "show ip bgp summary": _BGP,
        "show ip bgp summary vrf": _BGP,
        "show ipv6 bgp summary": _BGP,
        "show bgp summary": _BGP,
        "show bgp instance all summary": _BGP,
        "show bgp vrf all ipv4 unicast summary": _BGP_PER_VRF,
        "show isis neighbors": _ISIS,
        "show isis adjacency": _ISIS,
    }
)

# Long interface names, used to expand abbreviations by unique prefix:
# "Gi" and "Gig" can only mean GigabitEthernet.
_LONG_NAMES = (
    "GigabitEthernet",
    "FastEthernet",
    "TenGigabitEthernet",
    "TwoGigabitEthernet",
    "FiveGigabitEthernet",
    "TwentyFiveGigE",
    "FortyGigabitEthernet",
    "HundredGigE",
    "Ethernet",
    "Port-channel",
    "Loopback",
    "Vlan",
    "Tunnel",
)
# Prefixes shared by two long names, resolved the way Cisco abbreviates them
# in show output ("Tw1/0/1" is 2.5 Gb/s, "Twe1/1/1" is 25 Gb/s).
_SHORT_NAMES = {"tw": "TwoGigabitEthernet"}
# Letters (the kind), optional spaces, then a number: "Gig 1/0/48", "Po1".
_INTERFACE = re.compile(
    r"^(?P<kind>[A-Za-z][A-Za-z\-]*?)(?P<space>\s*)(?P<number>\d\S*)$"
)
_DIGITS = re.compile(r"(\d+)")
# MAC addresses in Cisco (0050.56a1.1001) or colon (00:50:56:a1:10:01) form.
# Their digits are hexadecimal and fixed-width, so text order is their order.
_MAC_ADDRESS = re.compile(
    r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}|[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}",
    re.IGNORECASE,
)


def canonical_interface(name: str) -> str:
    """Spell an interface name one way, whatever abbreviation a command used.

    A name is expanded only when its letters are a unique abbreviation of a
    known long name, so ``ge-0/0/0`` (Junos), ``mgmt0``, and ``ens192`` are
    left alone.  A space before the number is accepted only after three
    letters, the style of ``show cdp neighbors`` (``Gig 1/0/48``, ``Ten 1/1/1``,
    ``Gig 0``), so a phone's port ``Port 1`` is not taken for ``Port-channel1``.

    Args:
        name (str): Interface name from command output.

    Returns:
        str: The long form, such as ``GigabitEthernet1/0/48``, or ``name``
            stripped of surrounding spaces when it is not recognized.

    Raises:
        None: Only string operations are performed.
    """
    text = name.strip()
    match = _INTERFACE.match(text)
    if not match:
        return text
    kind = match["kind"].lower()
    if len(kind) < 2 or (match["space"] and len(kind) != 3):
        return text
    long_name = _SHORT_NAMES.get(kind)
    if long_name is None:
        candidates = [item for item in _LONG_NAMES if item.lower().startswith(kind)]
        if len(candidates) != 1:
            return text
        long_name = candidates[0]
    return f"{long_name}{match['number']}"


def natural_key(value: object) -> tuple[Any, ...]:
    """Return a sort key that orders embedded numbers by value.

    Splitting ``"Gi1/0/10"`` into ``("gi", 1, "/", 0, "/", 10, "")`` makes it
    sort after ``"Gi1/0/2"``, as a person reads it.  Text and numbers always
    alternate, so keys of different values compare without type errors.  A
    MAC address is the exception: read as numbers, ``00e0.4c3a.9909`` would
    sort before ``0017.c8d2.7707``, so it is compared as lower-case text.

    Args:
        value (object): A field value; lists and other non-strings are
            compared by their JSON text.

    Returns:
        tuple[Any, ...]: The sort key.

    Raises:
        TypeError: If ``value`` cannot be converted to JSON.
    """
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if _MAC_ADDRESS.fullmatch(text):
        return (text.lower(),)
    return tuple(
        int(part) if index % 2 else part.lower()
        for index, part in enumerate(_DIGITS.split(text))
    )


def _canonical_interfaces(value: Any) -> Any:
    """Spell an interface name, or each name in a list, one way.

    Some templates collect several interfaces into one field, such as the
    ports a MAC address was learned on (TextFSM's ``List`` values).

    Args:
        value (Any): A field value: one name, or a list of names.

    Returns:
        Any: The value with every name passed through
            :func:`canonical_interface`; other types are returned unchanged.

    Raises:
        None: Only string operations are performed.
    """
    if isinstance(value, str):
        return canonical_interface(value)
    if isinstance(value, list):
        return [_canonical_interfaces(item) for item in value]
    return value


def _clean_value(value: Any) -> Any:
    """Strip surrounding whitespace from a value or each item of a list.

    Args:
        value (Any): A TextFSM value: text, or a list of text for ``List``
            values.

    Returns:
        Any: The value with whitespace stripped.

    Raises:
        None: Other types are returned unchanged.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def choose_key(
    fields: Collection[str],
    *,
    template_keys: Sequence[str] = (),
    profile: Profile | None = None,
) -> tuple[str, ...]:
    """Choose the fields that identify a record.

    Args:
        fields (Collection[str]): Fields the records have.
        template_keys (Sequence[str]): Fields the template marks ``Key``.
        profile (Profile | None): Built-in profile for the command.

    Returns:
        tuple[str, ...]: The template's present keys, else the profile's
            present keys, else ``()`` for "the whole record".

    Raises:
        None: Only membership tests are performed.
    """
    present = tuple(name for name in template_keys if name in fields)
    if not present and profile is not None:
        present = tuple(name for name in profile.key if name in fields)
    return present


def sort_records(records: Iterable[Record], key: Sequence[str]) -> list[Record]:
    """Sort records by their key fields, then by every field.

    Args:
        records (Iterable[Record]): Records to sort.
        key (Sequence[str]): Identifying fields; may be empty.

    Returns:
        list[Record]: Records in natural order of their key, with ties broken
            by the whole record so the order never depends on input order.

    Raises:
        TypeError: If a value cannot be converted to JSON.
    """
    return sorted(
        records,
        key=lambda record: (
            tuple(natural_key(record.get(name, "")) for name in key),
            natural_key(record),
        ),
    )


def normalize_records(
    records: Iterable[Mapping[str, Any]],
    *,
    command: str,
    template_keys: Sequence[str] = (),
    ignore: Collection[str] = (),
) -> tuple[list[Record], tuple[str, ...]]:
    """Drop volatile fields, spell interfaces one way, and sort.

    Args:
        records (Iterable[Mapping[str, Any]]): Records from a template, with
            lower-case field names.
        command (str): The command spelled out, such as
            ``show cdp neighbors``, used to find its :data:`PROFILES` entry.
        template_keys (Sequence[str]): Fields the template marks ``Key``.
        ignore (Collection[str]): More fields to drop.

    Returns:
        tuple[list[Record], tuple[str, ...]]: The sorted records and the key
            fields that identify them (``()`` means the whole record).

    Raises:
        TypeError: If a value cannot be converted to JSON for sorting.
    """
    profile = PROFILES.get(command)
    dropped = set(ignore) | set(profile.volatile if profile else ())
    interfaces = set(profile.interfaces if profile else ())
    cleaned: list[Record] = []
    for record in records:
        item = {
            name: _clean_value(value)
            for name, value in record.items()
            if name not in dropped
        }
        for name in interfaces & item.keys():
            item[name] = _canonical_interfaces(item[name])
        cleaned.append(item)

    fields = set().union(*(record.keys() for record in cleaned))
    key = choose_key(fields, template_keys=template_keys, profile=profile)
    return sort_records(cleaned, key), key
