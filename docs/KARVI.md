# Collecting with karvi, parsing with axlib

karvi logs in to network devices, runs commands on each one, and saves what
they print. `axlib parse` turns that text into records with TextFSM templates,
and `axlib parse diff` compares two collections record by record. Together
they cover the checks of a maintenance window: collect before the work,
collect after it, and read only what changed.

| Step | Tool | Result |
|---|---|---|
| Run the check commands on every device | `karvi run --cd=DIR` | One text file per device |
| Turn the text into records | `axlib parse` | A table, JSON lines, or a JSON snapshot |
| Compare before with after | `axlib parse diff` | Only the records that changed |

This guide follows one change from start to finish on Cisco IOS XE and IOS
XR. The first check is the one a network operations team cares about most:
did every CDP, LLDP, and routing-protocol neighbor come back? After that come
interface status, routes, ARP, and MAC address tables. Every output below
comes from the sample files in `examples/karvi`, so you can run the same
commands without any devices. [Parsing show commands](PARSING.md) explains
every `axlib parse` option.

## Platform names

karvi and axlib use the same platform names: the ones netmiko and
ntc-templates use. Give axlib's `-p` option the name in the second column:

| Devices | karvi and axlib name | karvi also accepts | Neighbor commands that parse | Table commands that parse |
|---|---|---|---|---|
| Cisco IOS and IOS XE (Catalyst, ISR, ASR 1000) | `cisco_ios` | `cisco_iosxe`, `cisco_xe` | `show cdp neighbors [detail]`, `show lldp neighbors [detail]`, `show ip ospf neighbor`, `show ip eigrp neighbors`, `show ip bgp summary`, `show isis neighbors` | `show interfaces status`, `show ip interface brief`, `show ip route`, `show ip arp`, `show mac address-table` |
| Cisco IOS XR | `cisco_xr` | `cisco_iosxr` | `show cdp neighbors detail`, `show lldp neighbors [detail]`, `show ospf neighbor`, `show eigrp neighbors`, `show bgp instance all summary`, `show isis neighbors` | `show ipv4 interface brief`, `show route`, `show arp` |
| Cisco NX-OS | `cisco_nxos` | | `show cdp neighbors [detail]`, `show lldp neighbors [detail]`, `show ip ospf neighbors`, `show ip eigrp neighbors`, `show ip bgp summary` | `show interface status`, `show ip route`, `show ip arp`, `show mac address-table` |

The other names, such as `arista_eos` and `juniper_junos`, are the same in
both tools too. Use the main names in karvi's inventory as well, so the name
on each device's row is the one you pass to axlib.

Don't give axlib karvi's older names. axlib, like TextFSM's own lookup,
accepts a template whose platform matches the beginning of the name you give.
`-p cisco_iosxr` therefore picks the `cisco_ios` templates and parses IOS XR
output with them, giving wrong records without an error. Commands work the
same way: a template matches the beginning of the command. ntc-templates has
no template for IOS XR's `show bgp summary`, so that command is parsed with the
template for `show bgp`, which reads the BGP table, not the summary. Use
`show bgp instance all summary` on IOS XR. `axlib parse templates -p NAME
COMMAND` shows which template a platform and command pick.

## Install

karvi is installed on its own; see its documentation. axlib needs its
`textfsm` extra:

```bash
uv add 'axlib[textfsm]'
```

On a shared server, `sudo ./install.sh --with-textfsm` installs it for everyone
(see [Install](INSTALL.md)).

## Try it without devices

`examples/karvi` holds what karvi writes for three devices before and after a
change, and the command lists used to collect them:

```text
examples/karvi/
  checks/switches.txt      show interfaces status, show mac address-table, show ip arp, show cdp neighbors
  checks/routers.txt       show ip interface brief, show ip ospf neighbor, show ip route, show ip arp, show cdp neighbors
  checks/core-xr.txt       show ipv4 interface brief, show lldp neighbors
  before/cisco_ios/sw1     Catalyst 9200 access switch, gateway for VLANs 20 and 30
  before/cisco_ios/rtr1    ISR 4331 edge router
  before/cisco_xr/core1    ASR 9000 core router
  after/...                the same devices after the change
```

The change moves rtr1's link to core1 from rtr1's GigabitEthernet0/0/1 and
core1's GigabitEthernet0/0/0/1 to GigabitEthernet0/0/2 and 0/0/0/2, with a new
/30 subnet. While the cables were moved, two more things happened on sw1. Port
GigabitEthernet1/0/12 (`conf-room-c`, a conference phone) was moved from VLAN
20 to VLAN 30, which was planned. The lobby access point on
GigabitEthernet1/0/5 came unplugged, which wasn't.

From `examples/karvi`:

```bash
M='^! (?P<command>(?:sh|sho|show)\s.+)$'
axlib parse -p cisco_ios --marker "$M" after/cisco_ios -f table
axlib parse diff -p cisco_ios --marker "$M" before/cisco_ios after/cisco_ios
axlib parse diff -p cisco_xr --marker "$M" before/cisco_xr after/cisco_xr
```

The device names, addresses, and MAC addresses are invented. The addresses
come from private and documentation ranges.

## Step 1: collect with karvi

### One command list per device role

A router answers `show mac address-table` with `% Invalid input`, so give each
kind of device its own list, one command per line. Put the neighbor commands
in every list:

```text
$ cat checks/switches.txt
show interfaces status
show mac address-table
show ip arp
show cdp neighbors
$ cat checks/routers.txt
show ip interface brief
show ip ospf neighbor
show ip route
show ip arp
show cdp neighbors
```

`axlib parse` takes one platform per run, so collect each platform into its
own directory, named after the platform.

### Collect

```bash
CHG=chg-4411
mkdir -p $CHG/before/cisco_ios $CHG/before/cisco_xr

karvi run --tf switches.txt --cf checks/switches.txt \
  --continue-device-on-error --cd=$CHG/before/cisco_ios
karvi run --tf routers.txt --cf checks/routers.txt \
  --continue-device-on-error --cd=$CHG/before/cisco_ios
karvi run --tf core.txt --cf checks/core-xr.txt \
  --continue-device-on-error --cd=$CHG/before/cisco_xr
```

`--tf` names a file of devices, one per line. karvi's inventory selectors
(`--site`, `--device-group`, `--select-platform`) choose devices the same way.
`--cd=DIR` writes one file per device into `DIR` beside karvi's usual job
folder. For a single device, `karvi command --cd=DIR sw1 show ip arp` does the
same.

### What karvi writes

Each file is named after the device: the first label of its name, lowercased
(`sw1.example.net` becomes `sw1`). Each command's output follows a
`! COMMAND` line, with a blank line before every marker but the first. There
are no prompts, headers, or timestamps:

```text
! show interfaces status

Port         Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1      desk-101           connected    20         a-full a-1000 10/100/1000BaseTX
...

! show mac address-table
          Mac Address Table
-------------------------------------------
...
```

axlib takes the device name from the file name, because there is no prompt to
take it from. Leave out karvi's `--fs=SUFFIX` option; it would add the suffix
to every device name.

### Keep the collections trustworthy

- **Use a new, empty directory for each collection.** karvi replaces a
  device's file only when the device answered. A device that couldn't be
  reached keeps whatever file was already there, so a reused directory can
  hold yesterday's output next to today's.
- **Check karvi's exit status.** It isn't 0 when a device failed. The job
  folder named in karvi's last lines has `failed-devices.txt`, which `--tf`
  reads for a rerun.
- **Use `--continue-device-on-error`.** Without it, a command a device rejects
  stops that device, and with a fresh directory it gets no file at all. With
  it, the file is written and the rejected command's block holds the device's
  error text. axlib then reports that one command as failed to parse, and
  parses the rest.
- **Use `run` or `command` for a change window, not `crun`.** `karvi crun`, the
  scheduled collection, drops lines that change on every run. On IOS XE it
  drops the ` uptime is ` line of `show version`, so the uptime fields come
  back empty. `run --cd` keeps every line. `crun` collections still parse, for
  tracking a network over time.

## Step 2: parse with axlib

axlib finds commands in a capture by their prompts (`sw1#show ip arp`) or by
bare `show` lines. karvi's files have neither, so tell axlib what karvi's
command lines look like with `--marker`:

```bash
M='^! (?P<command>(?:sh|sho|show)\s.+)$'
axlib parse split --marker "$M" after/cisco_ios/sw1
```

```text
sw1:1  -  show interfaces status  (18 lines)
sw1:21  -  show mac address-table  (16 lines)
sw1:39  -  show ip arp  (14 lines)
sw1:55  -  show cdp neighbors  (9 lines)
```

The pattern accepts only `show` commands. Output that has its own `!` lines,
such as `! Last configuration change at ...` in `show running-config`, isn't
mistaken for a command. Without `--marker`, axlib finds nothing in karvi's
files and says `no commands found`.

Every example below uses this `$M`. Each output format answers a different
question.

### A table, to read

```bash
axlib parse -p cisco_ios --marker "$M" after/cisco_ios -f table
```

```text
## rtr1: show ip interface brief
interface             ip_address   status                 proto
GigabitEthernet0      192.0.2.11   up                     up
GigabitEthernet0/0/0  10.1.0.2     up                     up
GigabitEthernet0/0/1  unassigned   administratively down  down
GigabitEthernet0/0/2  10.0.0.6     up                     up
Loopback0             10.255.0.11  up                     up

## sw1: show mac-address-table
destination_address  type     vlan_id  destination_port
0050.56a1.1001       DYNAMIC  20       GigabitEthernet1/0/1
0050.56a1.1002       DYNAMIC  20       GigabitEthernet1/0/2
0050.56a1.1004       DYNAMIC  20       GigabitEthernet1/0/4
0050.56a1.1011       DYNAMIC  20       GigabitEthernet1/0/11
0017.c8d2.7707       DYNAMIC  30       GigabitEthernet1/0/7
0050.56a1.1012       DYNAMIC  30       GigabitEthernet1/0/12
00e0.4c3a.9909       DYNAMIC  30       GigabitEthernet1/0/9
...
```

Each table's columns are the template's fields, and each heading is the device
and the command. The table leaves out where each command came from, so
`diff -u before.txt after.txt` of two tables shows only changed rows.

### JSON lines, for jq and spreadsheets

`-f jsonl` writes one record per line:

```json
{"device": "sw1", "command": "show interfaces status", "source": "sw1", "line": 1, "record": {"port": "GigabitEthernet1/0/1", "name": "desk-101", "status": "connected", "vlan_id": "20", "duplex": "a-full", "speed": "a-1000", "type": "10/100/1000BaseTX", "fc_mode": ""}}
```

Collect both platforms into one file, then ask questions with `jq`:

```bash
for p in cisco_ios cisco_xr; do
  axlib parse -p $p --marker "$M" after/$p -f jsonl
done > after.jsonl
```

Ports that aren't connected, with their descriptions:

```bash
jq -r 'select(.command == "show interfaces status" and .record.status != "connected")
       | [.device, .record.port, .record.status, .record.name] | @tsv' after.jsonl
```

```text
sw1	GigabitEthernet1/0/3	notconnect	desk-103
sw1	GigabitEthernet1/0/5	notconnect	ap-lobby
sw1	GigabitEthernet1/0/6	notconnect
...
```

The ARP tables as CSV for a spreadsheet:

```bash
jq -r 'select(.command == "show ip arp")
       | [.device, .record.ip_address, .record.mac_address, .record.interface] | @csv' after.jsonl
```

Learned MAC addresses per VLAN:

```bash
jq -r 'select(.command == "show mac-address-table" and .record.type == "DYNAMIC")
       | "\(.device) vlan \(.record.vlan_id)"' after.jsonl | sort | uniq -c
```

```text
      4 sw1 vlan 20
      3 sw1 vlan 30
```

Every addressed interface on IOS XE and IOS XR in one list:

```bash
jq -r 'select(.command == "show ip interface brief" and .record.ip_address != "unassigned")
       | [.device, .record.interface, .record.ip_address, (.record.status | ascii_downcase)]
       | @tsv' after.jsonl | column -t
```

```text
rtr1   GigabitEthernet0        192.0.2.11   up
rtr1   GigabitEthernet0/0/0    10.1.0.2     up
rtr1   GigabitEthernet0/0/2    10.0.0.6     up
rtr1   Loopback0               10.255.0.11  up
core1  GigabitEthernet0/0/0/0  10.0.0.9     up
core1  GigabitEthernet0/0/0/2  10.0.0.5     up
core1  Loopback0               10.255.0.1   up
core1  MgmtEth0/RSP0/CPU0/0    192.0.2.1    up
```

core1 ran `show ipv4 interface brief`, IOS XR's own spelling, yet it's
selected as `show ip interface brief`. The next section explains why, and why
`ascii_downcase` is there.

### A JSON snapshot, to keep

The default format, `-f json`, is a snapshot: every command's records, its
key, the template, and the file and line it came from. Keep one with the change
record. `axlib parse diff` compares two snapshots later without the captures,
the templates, or `-p`:

```bash
axlib parse -p cisco_ios --marker "$M" $CHG/before/cisco_ios -o $CHG/before-cisco_ios.json
axlib parse diff $CHG/before-cisco_ios.json $CHG/after-cisco_ios.json
```

### Python, for reports of your own

The same records are available from Python. This script answers a question
every operator asks: which switch port is each address on? ARP gives each
address's MAC address, and the MAC table gives the port it was learned on:

```python
from axlib.parse import parse_paths

MARKER = r"^! (?P<command>(?:sh|sho|show)\s.+)$"

snapshot = parse_paths(
    ["examples/karvi/after/cisco_ios"], platform="cisco_ios", marker=MARKER
)

# (device, VLAN, MAC address) -> the switch ports that MAC was learned on
ports = {}
for result in snapshot.results:
    if result.template_command == "show mac-address-table":
        for record in result.records:
            key = (result.device, record["vlan_id"], record["destination_address"])
            ports[key] = record["destination_port"]

for result in snapshot.results:
    if result.template_command == "show ip arp":
        for record in result.records:
            vlan = record["interface"].removeprefix("Vlan")
            where = ports.get((result.device, vlan, record["mac_address"]))
            if where:
                print(
                    result.device, record["ip_address"], record["mac_address"], *where
                )
```

```text
sw1 10.20.0.101 0050.56a1.1001 GigabitEthernet1/0/1
sw1 10.20.0.102 0050.56a1.1002 GigabitEthernet1/0/2
sw1 10.20.0.104 0050.56a1.1004 GigabitEthernet1/0/4
sw1 10.20.0.111 0050.56a1.1011 GigabitEthernet1/0/11
sw1 10.30.0.7 0017.c8d2.7707 GigabitEthernet1/0/7
sw1 10.30.0.9 00e0.4c3a.9909 GigabitEthernet1/0/9
sw1 10.30.0.112 0050.56a1.1012 GigabitEthernet1/0/12
```

The join matches the VLAN as well as the MAC address. The ARP table still holds
10.20.0.112, conf-room-c's address before it moved to VLAN 30. Matched on
the MAC address alone, that old entry would be reported on
GigabitEthernet1/0/12 too.

`result.command` is the command as karvi sent it and `result.template_command`
is the template's spelling, the one the table and JSON lines show. Use
`template_command` to select commands.

## How axlib normalizes Cisco output

The same fact comes out of different Cisco devices in different forms.
Normalization means getting one form, so records from many devices and
platforms can be filtered, joined, and compared. axlib does this in five ways:

1. **One name per command.** Each result is named by the command its template
   parses, spelled out: `sh ip int br` and `show ip interface brief` are the
   same command, IOS XR's `show ipv4 interface brief` is reported as
   `show ip interface brief`, and `show mac address-table` is reported as
   `show mac-address-table`. Filter on these names. They follow
   ntc-templates, which isn't always consistent between platforms: the MAC
   table is `show mac-address-table` on IOS and `show mac address-table` on
   NX-OS. `axlib parse templates -p PLATFORM COMMAND` shows the name.
2. **The same field names on every platform.** ntc-templates names fields
   alike across platforms. `show ip interface brief` gives `interface`,
   `ip_address`, `status`, and `proto` on IOS XE and IOS XR, and IOS XR adds
   `vrf`. Values keep the device's own spelling, though: IOS says `up` and
   `administratively down` where IOS XR says `Up` and `Shutdown`. Fold case
   (`ascii_downcase` in jq, `.lower()` in Python) when you combine platforms.
3. **One spelling for interface names.** CDP's `Gig 1/0/5`, the MAC table's
   `Gi1/0/5`, and `GigabitEthernet1/0/5` all become `GigabitEthernet1/0/5`,
   in neighbor tables, interface tables, routes, ARP, and MAC tables alike, so
   records from different commands join on the interface.
4. **A stable order.** Records are sorted by their key in natural order
   (`Gi1/0/2` before `Gi1/0/10`, `10.20.0.9` before `10.20.0.10`), and MAC
   addresses as text. The order never depends on the order the device
   printed, so neighbors heard in another order aren't a change.
5. **No fields that change on every view.** Hold times, ages, dead times,
   uptimes, sequence numbers, and message counters are dropped, so a timer
   that moved isn't a change either.

axlib has a built-in profile, a key and the fields to drop, for each kind of
table below. [How records are made
comparable](PARSING.md#how-records-are-made-comparable) lists every command and
field:

| Table | A record is | Dropped |
|---|---|---|
| CDP and LLDP neighbors | the neighbor's name on a local port | hold time, age |
| OSPF neighbors | the router ID on an interface | dead time, uptime |
| EIGRP neighbors | the address on an interface, per AS | hold, uptime, SRTT, RTO, queue count, sequence number |
| BGP peers | the peer's address, per VRF and address family | messages, table version, queues, up/down time |
| IS-IS neighbors | the system ID on an interface, per level | hold time |
| Routes | one path: prefix and next hop, per VRF | uptime |
| ARP | the IP address on an interface | age |
| MAC addresses | the MAC address, per VLAN | age |
| Interfaces and ports | the interface or port | |

`--raw` turns normalization off and gives the records exactly as the template
returns them. `--ignore FIELD` drops a field of your own choosing, such as
`--ignore name` when port descriptions were rewritten on purpose.

## Step 3: compare before and after

```bash
axlib parse diff -p cisco_ios --marker "$M" before/cisco_ios after/cisco_ios
axlib parse diff -p cisco_xr --marker "$M" before/cisco_xr after/cisco_xr
```

### Are all neighbors back?

These are the neighbor commands from the two reports:

```text
rtr1: show cdp neighbors
  - local_interface=GigabitEthernet0/0/1 neighbor_name=core1
  + local_interface=GigabitEthernet0/0/2 neighbor_name=core1
rtr1: show ip ospf neighbor
  - neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/1
  + neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/2
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby.example.net
core1: show lldp neighbors
  - local_interface=GigabitEthernet0/0/0/1 neighbor_name=rtr1.example.net
  + local_interface=GigabitEthernet0/0/0/2 neighbor_name=rtr1.example.net
```

Each device listed its neighbors in another order after the change, and every
hold time had moved, yet none of that shows. What's left:

- **sw1 lost `ap-lobby.example.net`.** It is listed with `-` and nothing
  replaces it. This is the neighbor that didn't come back.
- **core1 is a CDP and OSPF neighbor of rtr1 again**, and rtr1 an LLDP
  neighbor of core1, on the new ports. A neighbor that comes back on another
  port is listed as one `-` and one `+` with the same name. That is the
  planned move.
- **The neighbors that came back on the same port aren't listed**: sw1's
  `rtr1.example.net` and its conference phone, rtr1's `sw1.example.net` and
  OSPF neighbor 10.255.0.21, and core1's `edge2`.

A neighbor that is still listed but in another state is a `~` line instead,
such as an OSPF neighbor that is no longer `FULL`, or a BGP peer whose prefix
count turned into `Active`:

```text
rtr1: show ip bgp summary
  ~ address_family="" bgp_neighbor=10.0.0.1
      state_or_prefixes_received: 11 -> Active
```

To list only the neighbors that are gone, whatever port they were on, compare
neighbor names. This works for CDP, LLDP, OSPF, EIGRP, IS-IS, and BGP:

```bash
neighbors() {  # neighbors DIR PLATFORM: device, command, and neighbor, one per line
  axlib parse -p "$2" --marker "$M" "$1" -f jsonl | jq -r '
    select(.command | test("cdp|lldp|ospf|eigrp|isis|bgp"))
    | .record as $r
    | [.device, .command, ($r.neighbor_name // $r.neighbor // $r.neighbor_id // $r.system_id
        // $r.bgp_neighbor // $r.bgp_neigh // $r.peer_ip // $r.ip_address)]
    | @tsv' | sort -u
}
for p in cisco_ios cisco_xr; do
  comm -23 <(neighbors before/$p $p) <(neighbors after/$p $p)
done
```

```text
sw1	show cdp neighbors	ap-lobby.example.net
```

Every neighbor of a device that karvi couldn't reach after the change is
listed too, since its file is missing. An empty result means every neighbor is
back.

### The whole report

```text
rtr1: show cdp neighbors
  - local_interface=GigabitEthernet0/0/1 neighbor_name=core1
  + local_interface=GigabitEthernet0/0/2 neighbor_name=core1
rtr1: show ip arp
  - ip_address=10.0.0.1 interface=GigabitEthernet0/0/1
  - ip_address=10.0.0.2 interface=GigabitEthernet0/0/1
  + ip_address=10.0.0.5 interface=GigabitEthernet0/0/2
  + ip_address=10.0.0.6 interface=GigabitEthernet0/0/2
rtr1: show ip interface brief
  ~ interface=GigabitEthernet0/0/1
      ip_address: 10.0.0.2 -> unassigned
      status: up -> "administratively down"
      proto: up -> down
  ~ interface=GigabitEthernet0/0/2
      ip_address: unassigned -> 10.0.0.6
      status: "administratively down" -> up
      proto: down -> up
rtr1: show ip ospf neighbor
  - neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/1
  + neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/2
rtr1: show ip route
  - vrf="" network=0.0.0.0 prefix_length=0 nexthop_ip=10.0.0.1 nexthop_if=GigabitEthernet0/0/1
  + vrf="" network=0.0.0.0 prefix_length=0 nexthop_ip=10.0.0.5 nexthop_if=GigabitEthernet0/0/2
  - vrf="" network=10.0.0.0 prefix_length=30 nexthop_ip="" nexthop_if=GigabitEthernet0/0/1
  - vrf="" network=10.0.0.2 prefix_length=32 nexthop_ip="" nexthop_if=GigabitEthernet0/0/1
  + vrf="" network=10.0.0.4 prefix_length=30 nexthop_ip="" nexthop_if=GigabitEthernet0/0/2
  + vrf="" network=10.0.0.6 prefix_length=32 nexthop_ip="" nexthop_if=GigabitEthernet0/0/2
  - vrf="" network=10.100.0.0 prefix_length=16 nexthop_ip=10.0.0.1 nexthop_if=GigabitEthernet0/0/1
  + vrf="" network=10.100.0.0 prefix_length=16 nexthop_ip=10.0.0.5 nexthop_if=GigabitEthernet0/0/2
  - vrf="" network=10.255.0.1 prefix_length=32 nexthop_ip=10.0.0.1 nexthop_if=GigabitEthernet0/0/1
  + vrf="" network=10.255.0.1 prefix_length=32 nexthop_ip=10.0.0.5 nexthop_if=GigabitEthernet0/0/2
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby.example.net
sw1: show interfaces status
  ~ port=GigabitEthernet1/0/5
      status: connected -> notconnect
      duplex: a-full -> auto
      speed: a-1000 -> auto
  ~ port=GigabitEthernet1/0/12
      vlan_id: 20 -> 30
sw1: show ip arp
  + ip_address=10.30.0.112 interface=Vlan30
sw1: show mac-address-table
  - vlan_id=20 destination_address=0050.56a1.1012
  - vlan_id=30 destination_address=00a2.eeb0.5505
  + vlan_id=30 destination_address=0050.56a1.1012
9 commands compared: 9 changed, 0 only before, 0 only after, 0 unchanged
```

`~` is a record whose key is on both sides with other fields changed: the
fields are listed as `before -> after`. `-` is a record only before and `+` a
record only after, named by its key; `--json` (below) has the whole records.
Read the rest top to bottom:

- **rtr1, interfaces and routes:** GigabitEthernet0/0/1 is shut and
  GigabitEthernet0/0/2 is up with 10.0.0.6. The default route, the core
  loopback, and the inter-area summary moved from next hop 10.0.0.1 to
  10.0.0.5; a path that moved is one `-` and one `+`. The connected /30
  changed. The routes learned from sw1 aren't listed, so they didn't change.
  This is the planned work.
- **rtr1, ARP:** the entries on GigabitEthernet0/0/1 went away when it was
  shut, and core1's new address 10.0.0.5 was learned. An address that answers
  from a new MAC address would be a `~` line.
- **sw1, GigabitEthernet1/0/12:** VLAN 20 to 30, as planned. Its phone's MAC
  address left VLAN 20 and was learned in VLAN 30, and the phone has a new
  address, 10.30.0.112.
- **sw1, GigabitEthernet1/0/5:** `ap-lobby` went from `connected` to
  `notconnect`, its MAC address is gone, and so is its CDP neighbor. This
  wasn't part of the change. It's the line to act on before the window closes.

core1's report shows the other end of the moved link:

```text
core1: show ip interface brief
  ~ vrf=default interface=GigabitEthernet0/0/0/1
      ip_address: 10.0.0.1 -> unassigned
      status: Up -> Shutdown
      proto: Up -> Down
  ~ vrf=default interface=GigabitEthernet0/0/0/2
      ip_address: unassigned -> 10.0.0.5
      status: Shutdown -> Up
      proto: Down -> Up
core1: show lldp neighbors
  - local_interface=GigabitEthernet0/0/0/1 neighbor_name=rtr1.example.net
  + local_interface=GigabitEthernet0/0/0/2 neighbor_name=rtr1.example.net
2 commands compared: 2 changed, 0 only before, 0 only after, 0 unchanged
```

A device that karvi couldn't reach in one collection has no file there, so
each of its commands is listed as missing on that side, with a count of its
records:

```text
core1: show ip interface brief
  only before (5 records)
```

When nothing changed, the report is one line, such as
`No differences in 9 commands.`

### In scripts: exit status and JSON

`axlib parse diff` exits with 0 when nothing differs, 1 when something does,
and 2 when the comparison couldn't be made or a command failed to parse (a
rejected command, for example). A script can stop on 2 and ask for review on 1.

`--json` prints every compared command with its changes, whole records
included, for a ticket system or a dashboard:

```bash
axlib parse diff --json -p cisco_ios --marker "$M" before/cisco_ios after/cisco_ios \
  | jq -c '.commands[] | select(.status == "changed") | .changes[]
           | select(.kind == "changed") | {key, fields}'
```

```text
{"key":{"interface":"GigabitEthernet0/0/1"},"fields":{"ip_address":["10.0.0.2","unassigned"],"status":["up","administratively down"],"proto":["up","down"]}}
{"key":{"interface":"GigabitEthernet0/0/2"},"fields":{"ip_address":["unassigned","10.0.0.6"],"status":["administratively down","up"],"proto":["down","up"]}}
{"key":{"port":"GigabitEthernet1/0/5"},"fields":{"status":["connected","notconnect"],"duplex":["a-full","auto"],"speed":["a-1000","auto"]}}
{"key":{"port":"GigabitEthernet1/0/12"},"fields":{"vlan_id":["20","30"]}}
```

From Python, `compare()` returns the same comparison:

```python
from axlib.parse import compare, parse_paths

MARKER = r"^! (?P<command>(?:sh|sho|show)\s.+)$"


def collection(phase):
    return parse_paths(
        [f"examples/karvi/{phase}/cisco_ios"], platform="cisco_ios", marker=MARKER
    )


diff = compare(collection("before"), collection("after"))
for command in diff.differences:
    if command.command == "show interfaces status":
        for change in command.changes:
            if change.kind == "changed" and "status" in change.fields:
                old, new = change.fields["status"]
                print(f"{command.device} {change.key['port']}: {old} -> {new}")
```

```text
sw1 GigabitEthernet1/0/5: connected -> notconnect
```

## What to look for in each table

| Table | Commands | Look for |
|---|---|---|
| CDP and LLDP neighbors | `show cdp neighbors [detail]`, `show lldp neighbors [detail]` | Neighbors only before (gone), or on another port. In the detail output, a `~` on the software version after an upgrade |
| OSPF neighbors | `show ip ospf neighbor`, `show ospf neighbor` (IOS XR) | Neighbors only before, or a state that is no longer `FULL` |
| EIGRP neighbors | `show ip eigrp neighbors`, `show eigrp neighbors` (IOS XR) | Neighbors only before |
| BGP peers | `show ip bgp summary`, `show bgp instance all summary` (IOS XR) | Peers only before, or a state (`Idle`, `Active`) where a prefix count was |
| IS-IS neighbors | `show isis neighbors` | Neighbors only before, or a state that is no longer `Up` |
| Interface status | `show interfaces status` (IOS), `show interface status` (NX-OS), `show ip interface brief` | Ports that went `notconnect` or `err-disabled`, VLAN changes, a speed that dropped (`a-1000` to `a-100` is often a bad cable), addresses that moved |
| Routes | `show ip route`, `show route` (IOS XR) | Paths whose next hop moved, routes only before (lost), routes only after (new). Add `show ip route vrf NAME` for each VRF; the template fills in `vrf`. |
| ARP | `show ip arp`, `show arp` (IOS XR) | An address whose MAC address changed (replaced hardware, or a duplicate address), addresses that vanished |
| MAC table | `show mac address-table` | MAC addresses that moved port or VLAN, ports with nothing learned |

A BGP peer that sends a full Internet table changes its prefix count all the
time; [How records are made
comparable](PARSING.md#how-records-are-made-comparable) explains how to leave
the count out and what that costs.

Some timing matters when you read ARP and MAC tables:

- **ARP entries outlive the host.** IOS keeps an entry for four hours by
  default, so a host that left during the window can still be listed after
  it. In the example, sw1 still lists 10.20.0.112 and the unplugged access
  point's 10.30.0.5. An interface that goes down clears its own entries, as
  rtr1's GigabitEthernet0/0/1 did.
- **MAC entries age out after five minutes of silence** by default. A printer
  or camera that hasn't sent anything lately can be missing from the after
  collection without having moved. Ping it, or collect again, before you call
  it lost.

## A maintenance-window script

Collect with one command, check the neighbors with another, and compare
everything with a third. This script does all three for every platform,
using the role lists from Step 1:

```bash
#!/usr/bin/env bash
# Usage: ./window.sh CHANGE before|after|neighbors|diff
set -uo pipefail
change=$1 phase=$2
M='^! (?P<command>(?:sh|sho|show)\s.+)$'
platforms=(cisco_ios cisco_xr)
status=0

collect() {  # collect TARGETS COMMANDS PLATFORM
    karvi run --tf "$1" --cf "$2" --continue-device-on-error \
        --cd="$change/$phase/$3" || status=1
}

neighbors() {  # neighbors DIR PLATFORM: device, command, and neighbor, one per line
    axlib parse -p "$2" --marker "$M" "$1" -f jsonl | jq -r '
        select(.command | test("cdp|lldp|ospf|eigrp|isis|bgp"))
        | .record as $r
        | [.device, .command, ($r.neighbor_name // $r.neighbor // $r.neighbor_id // $r.system_id
            // $r.bgp_neighbor // $r.bgp_neigh // $r.peer_ip // $r.ip_address)]
        | @tsv' | sort -u
}

case $phase in
before | after)
    if [[ -e $change/$phase ]]; then
        echo "$change/$phase already exists; use a new change name" >&2
        exit 2
    fi
    for platform in "${platforms[@]}"; do mkdir -p "$change/$phase/$platform"; done
    collect switches.txt checks/switches.txt cisco_ios
    collect routers.txt checks/routers.txt cisco_ios
    collect core.txt checks/core-xr.txt cisco_xr
    ((status)) && echo "some devices failed; see failed-devices.txt in karvi's job folder" >&2
    ;;
neighbors)
    for platform in "${platforms[@]}"; do
        missing=$(comm -23 <(neighbors "$change/before/$platform" "$platform") \
            <(neighbors "$change/after/$platform" "$platform"))
        if [[ -n $missing ]]; then
            echo "$missing"
            status=1
        fi
    done
    ((status)) || echo "Every neighbor is back."
    ;;
diff)
    for platform in "${platforms[@]}"; do
        axlib parse diff -p "$platform" --marker "$M" \
            "$change/before/$platform" "$change/after/$platform"
        ((status = $? > status ? $? : status))
    done
    ;;
esac
exit "$status"
```

`./window.sh chg-4411 before` before the work and `./window.sh chg-4411 after`
when it's done. `./window.sh chg-4411 neighbors` then lists every neighbor
that didn't come back, or says that every neighbor is back, and exits with 1
or 0. `./window.sh chg-4411 diff` shows everything that changed. Its exit
status is the highest of the platforms', so 1 means "review the changes" and
2 means "a comparison is incomplete".

## Troubleshooting

**`no commands found; use --command when the input is output only`**
The `--marker` option is missing, or its pattern doesn't match karvi's
`! show ...` lines. `axlib parse split --marker "$M" FILE` lists what it
finds.

**The device names end in `.txt` or another suffix**
karvi was given `--fs`. Rename the files, or collect again without it.

**A device's records are from an earlier collection**
The collection went into a directory that already had files, and that device
failed this time. Collect into a new directory.

**A command failed to parse**
Either the device rejected the command and karvi wrote the device's error text
in its place (see `--continue-device-on-error` above), or the output has a
layout the template doesn't know. `-v` lists each skipped and failed command
and why, and [Troubleshooting](PARSING.md#troubleshooting) explains the
messages. `axlib parse templates -p PLATFORM COMMAND` shows which template was
used.

**Records look wrong, or fields are empty**
Check the platform. The directory's name should match `-p`, and `-p` should be
a main name such as `cisco_xr`, not an alias such as `cisco_iosxr`. Then check
the command with `axlib parse templates`: IOS XR's `show bgp summary`, for
example, is parsed with the `show bgp` template (see [Platform
names](#platform-names)). For `show version` from `karvi crun`, the uptime
line was dropped on purpose (see [Keep the collections
trustworthy](#keep-the-collections-trustworthy)).

**No template parses a command**
Write your own template and put it in a template directory. See [Adding your
own templates](PARSING.md#adding-your-own-templates).

## See also

- [Parsing show commands](PARSING.md): capture formats, every option, settings,
  your own templates, the profile of each command, and the exit status of each
  command.
- karvi's `docs/COLLECTION.md`: collection files, `crun`, and its drop lists
  and masks.
