# Parsing show commands: `axlib parse`

`axlib parse` turns the output of show commands into records with
[TextFSM](https://github.com/google/textfsm) templates, and `axlib parse diff`
compares two collections of them record by record.

The usual reason is a maintenance window. You collect `show cdp neighbors`,
`show lldp neighbors detail`, `show ip interface brief`, and so on before the
work and again after it, then compare. Comparing the text doesn't work well:
devices list CDP and LLDP neighbors in the order they heard them, not in
interface order, and hold times count down between runs. A plain `diff` of an
unchanged switch is full of lines that moved or ticked. `axlib parse` sorts
each command's records by what identifies them (local interface and neighbor,
for example), spells interface names one way, and drops fields that change on
every run. After that, only real changes show up.

Templates come from the [ntc-templates](https://github.com/networktocode/ntc-templates)
collection (about 1,000 templates for Cisco, Arista, Juniper, and many other
vendors) and from directories of your own `.textfsm` files, which are searched
first.

## Install

```bash
uv add 'axlib[textfsm]'
```

The `textfsm` extra installs TextFSM and ntc-templates. Both are pure Python
and have no other dependencies. On a shared server, `sudo ./install.sh
--with-textfsm` installs them for everyone and creates `/etc/axlib/textfsm` for
the team's own templates (see [Shared templates on a server](#shared-templates-on-a-server)).

## Quick start

`examples/parse` has a "before" and an "after" collection from two switches,
plus one template of our own:

```console
$ cd examples/parse
$ axlib parse diff -p cisco_ios -t templates before after
axlib parse diff (before): skipped 2 command(s) with no template (-v lists them)
axlib parse diff (after): skipped 1 command(s) with no template (-v lists them)
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby-01
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.net
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
sw1: show cdp neighbors detail
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.net
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
sw1: show interfaces status
  ~ port=GigabitEthernet1/0/5
      status: connected -> notconnect
      duplex: a-full -> auto
      speed: a-1000 -> auto
sw1: show ip interface brief
  ~ interface=GigabitEthernet1/0/5
      status: up -> down
      proto: up -> down
sw1: show ip pim neighbor
  ~ neighbor=10.20.0.2 interface=Vlan20
      mode: "DR S P G" -> "S P G"
sw1: show lldp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby-01
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
sw1: show lldp neighbors detail
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.net
      neighbor_port_id: Gi0/0/1 -> Gi0/0/2
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
9 commands compared: 7 changed, 0 only before, 0 only after, 2 unchanged
```

A `diff -u` of the two `sw1` logs shows 110 changed lines. The report above
shows what actually changed: the access point on Gi1/0/5 went away, the router
uplink moved to another port, and a PIM neighbor stopped being the designated
router. `sw2` was only reordered and its hold times changed, so it isn't
listed. In the "after" log the commands are abbreviated (`sh cdp nei`,
`sh ip pim nei`), and they are still paired with the full commands from
"before".

For a real maintenance window, save a snapshot before you start and compare
with it afterwards:

```bash
axlib parse -p cisco_ios -r captures/before/ -o before.json
# ... the maintenance work ...
axlib parse diff -p cisco_ios -r before.json captures/after/
```

## Feeding it output

### Session logs and capture files

A capture is any text where each command is followed by its output. A line
starts a new command when it is one of these:

| Line | Example | Host name |
|---|---|---|
| A prompt and a command | `sw1#show cdp neighbors`, `sw1>show ver`, `sw1(config)#do show ip int br` | `sw1` |
| A Junos prompt | `admin@mx1> show lldp neighbors` | `mx1` |
| An IOS-XR prompt | `RP/0/RSP0/CPU0:xr1#show cdp neighbors detail` | `xr1` |
| A bare command in column 1 | `show cdp neighbors`, `sh ip int br`, `display lldp neighbor` | the last prompt's, if any |
| Your own `--marker` pattern | `===== sw1: show cdp neighbors =====` | the `host` group, if any |

A prompt with nothing after it (`sw1#`) ends the current command. That keeps
the prompt at the end of a session log out of the last command's output.
Text before the first command, such as a login banner, is ignored.

These are removed from the output, because each one makes a strict template
stop with an error:

- pager prompts (`--More--`, Junos `---(more 45%)---`) and the backspaces
  that erase them,
- carriage returns,
- the `Load for five secs` and `Time source is` lines that IOS prints with
  `terminal exec prompt timestamp`,
- the `{master:0}` and `[edit]` lines that Junos prints above a prompt.

A site format with its own header lines needs a `--marker` regular
expression. It has a `command` group and, optionally, a `host` group:

```bash
axlib parse -p cisco_ios --marker '=+ (?P<host>\S+): (?P<command>.+?) =+$' collected.txt
```

To see how axlib will cut up a capture before parsing it, run `axlib parse
split`:

```console
$ axlib parse split examples/parse/before/sw1.log
sw1.log:1  sw1  terminal length 0  (0 lines)
sw1.log:2  sw1  show cdp neighbors  (13 lines)
sw1.log:16  sw1  show cdp neighbors detail  (42 lines)
...
```

`--json` prints each block with its output.

### Piped output

Output with no command line in it, such as the output of
`ssh sw1 'show cdp neighbors'` or a netmiko `send_command()`, needs
`--command` to say which command produced it:

```bash
ssh sw1 'show cdp neighbors' | axlib parse -p cisco_ios --command 'show cdp neighbors' --host sw1
```

A script that prints prompts, or a session log piped through `cat`, needs no
`--command`. `-` means standard input, and it is also the default.

### Files and directory trees

```bash
axlib parse -p cisco_ios captures/sw1.log captures/sw2.log
axlib parse -p cisco_ios captures/            # the files directly in captures/
axlib parse -p cisco_ios -r captures/         # every file below captures/
axlib parse -p cisco_ios -r --glob '*.txt' captures/
```

Files are read in sorted order. Hidden files and directories (`.git`) and
binary files are skipped. Bytes that aren't valid text (stray terminal codes
in a session log) become the replacement character `�` instead of stopping
the run. `--encoding` changes the encoding from UTF-8.

`axlib parse diff` pairs commands by **device** and command. The device is the
host name from the prompt. When a capture has no prompts and no `--host`, the
device is the capture's file name, or its path below the directory you gave.
For example, `before/site1/sw2.txt` pairs with `after/site1/sw2.txt`. With
prompts, files can have any names, such as `sw1-2026-10-19.log`.

### Commands that are not parsed

A command is **skipped** when no template parses it (`terminal length 0`,
`show running-config`) or when its output was filtered with `|`
(`| include`, `| begin`, ...). A template can't tell which lines are missing
from filtered output. A trailing `| no-more` (Junos) doesn't change the output
and is ignored. `axlib parse` prints how many commands it skipped, and `-v`
lists them.

A command **fails** when its template rejects the output: the template reached
an `Error` state on a line it didn't expect. Failures are always printed, with
the template's message, and they make the exit status 1. See
[Troubleshooting](#troubleshooting).

## Output

```bash
axlib parse -p cisco_ios -r captures/ -o before.json       # snapshot (JSON, default)
axlib parse -p cisco_ios -r captures/ -f jsonl | jq ...    # one record per line
axlib parse -p cisco_ios -r captures/ -f table             # aligned text
```

- **`json`** writes a snapshot that `axlib parse diff` reads. It lists each
  parsed command with its records, key, template, and where it came from, and
  then the commands that were skipped or failed. Snapshots are sorted by device
  and command, so two snapshots of an unchanged network are identical.
- **`jsonl`** writes one record per line as
  `{"device", "command", "source", "line", "record"}`, for `jq` and `grep`.
- **`table`** writes one aligned table per command under a
  `## device: command` heading. It contains no file names or line numbers, so
  `diff -u before.txt after.txt` of two tables shows only real changes.

`--ignore FIELD` drops a field from every record. `--raw` turns
normalization off: you get every field, interface names as the device printed
them, and rows in the device's order, which is exactly what the template
returns.

## Comparing collections: `axlib parse diff`

```text
axlib parse diff [options] BEFORE AFTER
```

`BEFORE` and `AFTER` can each be a snapshot written by `axlib parse`, a capture
file, a directory (with `-r` for a tree), or `-` for standard input on one
side. Comparing two snapshots needs neither templates nor `--platform`. The
capture options (`-r`, `--glob`, `--command`, `--host`, `--marker`) and the
template options (`-p`, `-t`, `--config`) apply to whichever side is captures.

Within each command, records are paired by their key:

| Mark | Meaning |
|---|---|
| `~` | Changed: the key is on both sides and other fields differ; the fields are listed as `before -> after`. |
| `-` | Removed: the key is only in BEFORE. |
| `+` | Added: the key is only in AFTER. |
| `only before` / `only after` | The whole command is missing on one side. |

When a command has no key, or several records share one, whole records are
compared, so you see removals and additions but no `~` lines. The key is
explained under [How records are made comparable](#how-records-are-made-comparable).

`--ignore FIELD` leaves a field out of the comparison, for example
`--ignore name` when port descriptions were rewritten on purpose. `--json`
prints every change with both records and a summary. If a capture holds the
same command twice for one device, the later one is compared and a note says
so.

## Adding your own templates

### Where axlib looks

Template directories are searched in this order, and the first template that
matches a command wins:

| Order | Source | Typical use |
|---|---|---|
| 1 | `--template-dir DIR` (`-t`), repeatable | trying a template you are writing |
| 2 | `AXLIB_TEXTFSM_TEMPLATES`, directories separated by `:` | your personal templates, set in `~/.bashrc` |
| 3 | `template_dirs` in `[textfsm]` of `axlib.toml`; when absent, the `textfsm` directory beside `axlib.toml` if it exists | the team's templates (`/etc/axlib/textfsm` on a shared server) |
| 4 | ntc-templates, or `NTC_TEMPLATES_DIR` as for netmiko | everything else |

The sources add up rather than replace each other, so a personal directory
never hides the team's. `axlib parse templates` shows the search path, and
with a command it shows which template parses that command:

```console
$ axlib parse templates -p cisco_ios -t examples/parse/templates sh ip pim nei
'sh ip pim nei' on cisco_ios is an abbreviation of 'show ip pim neighbor'
  template: examples/parse/templates/cisco_ios_show_ip_pim_neighbor.textfsm
  from:     --template-dir
$ axlib parse templates -p cisco_ios        # every command on the platform
```

### A template by file name

The easiest way to add a template is to put a `.textfsm` file in a template
directory and give it the ntc-templates name: the platform, an underscore, and
the command with underscores for spaces. No index file is needed.

```text
cisco_ios_show_ip_pim_neighbor.textfsm      parses "show ip pim neighbor" on cisco_ios
arista_eos_show_vrrp.textfsm                parses "show vrrp" on arista_eos
```

`examples/parse/templates/cisco_ios_show_ip_pim_neighbor.textfsm` is a
complete, commented example:

```text
Value Key NEIGHBOR (\d+\.\d+\.\d+\.\d+)
Value Key INTERFACE (\S+)
Value VERSION (v\d)
Value DR_PRIORITY (\d+)
Value MODE (\S.*?)

Start
  ^PIM\s+Neighbor\s+Table
  ^Mode:
  ^\s+[A-Z]+\s+-\s+
  ^Neighbor\s+Interface
  ^Address\s+Prio/Mode
  ^${NEIGHBOR}\s+${INTERFACE}\s+\S+/\S+\s+${VERSION}\s+${DR_PRIORITY}\s*/\s*${MODE}\s*$$ -> Record
  ^${NEIGHBOR}\s+${INTERFACE}\s+\S+/\S+\s+${VERSION}\s+${DR_PRIORITY}\s*/?\s*$$ -> Record
  ^\s*$$
  ^. -> Error
```

Two template habits make comparisons work well:

- **Mark the identifying fields `Key`.** `Value Key NEIGHBOR ...` tells
  axlib (and TextFSM) that neighbor and interface together identify a row.
  Then `axlib parse diff` reports "this neighbor's mode changed" instead of
  "one row removed, one added".
- **Don't capture fields that change on every run.** The example matches the
  uptime/expiry column with `\S+/\S+` but doesn't store it. A value that is
  never recorded can never show up as a difference.

A file-name template matches its command with no arguments, in full or
abbreviated word by word (`sh ip pim nei`). Copying an ntc template into your
directory under the same name and fixing it overrides the original, however
the command is abbreviated in the capture. Field names are lower-cased in the
output, as in ntc-templates.

To try a template on some output, run `axlib parse` with `--raw`, which shows
exactly what the template returns:

```bash
axlib parse -p cisco_ios -t ~/textfsm --command 'show ip pim neighbor' --raw -f table pim.txt
```

TextFSM's own `python -m textfsm.parser TEMPLATE OUTPUT` prints the template
and the rows it produces, with the original upper-case field names.

### Templates by index

For commands with arguments, or a template that only some hosts need, give the
directory an `index` file in the ntc-templates format. A directory with an
`index` uses only the rows in it; its other `.textfsm` files are ignored.

```text
# Comment lines start with #.  The header row names the columns.
Template, Hostname, Platform, Command

cisco_ios_show_ip_route_vrf.textfsm, .*, cisco_ios, sh[[ow]] ip ro[[ute]] vrf \S+
core_bgp_summary.textfsm, core-.*, cisco_ios, sh[[ow]] ip bgp sum[[mary]]
cisco_ios_show_module.textfsm:cisco_ios_show_module_status.textfsm, .*, cisco_ios, sh[[ow]] mod[[ule]]
```

- `Platform`, `Hostname`, and `Command` are regular expressions. In
  `Command`, `sh[[ow]]` accepts `sh`, `sho`, or `show`.
- As in TextFSM's `clitable` (which ntc-templates and netmiko use), a row
  matches the **start** of the command, and the **first** matching row wins.
  Put longer commands first, or end a pattern with `$`.
- `Hostname` is checked against the host name from the prompt. The
  `core_bgp_summary` row above applies only to hosts whose names start with
  `core-`.
- Several templates separated by `:` are run on the same output, and their
  rows are merged on their `Key` fields.
- A row with the wrong number of fields or an invalid regular expression is
  an error that names the line. (`clitable` silently drops such rows.)

### Shared templates on a server

`sudo ./install.sh --with-textfsm` creates `/etc/axlib/textfsm`, owned by
`root:netops` with mode `2775`. Members of the `netops` group can add
templates, new files inherit the group, and every operator's `axlib parse`
searches the directory without any options because it sits beside
`/etc/axlib/axlib.toml`. If only root should change shared templates, run
`chmod 2755 /etc/axlib/textfsm`; the installer keeps the mode you set. Keep
the directory in git, as `docs/INSTALL.md` suggests for the shared
environment, so you can see who changed a template and when.

## How records are made comparable

Unless you use `--raw`, each command's records go through three steps:

1. **Volatile fields are dropped**, as are any you `--ignore`.
2. **Interface names are spelled one way.** CDP's `Gig 1/0/48`, LLDP's
   `Gi1/0/48`, and `GigabitEthernet1/0/48` all become
   `GigabitEthernet1/0/48`. A name is expanded only when its letters are an
   unambiguous abbreviation of a known interface type. Names such as
   `ge-0/0/0`, `mgmt0`, `Management1`, and a phone's `Port 1` are left
   alone.
3. **Records are sorted by their key** in natural order (`Gi1/0/2` before
   `Gi1/0/10`), then by all their fields, so the result never depends on the
   device's order.

The key comes from the template's `Key` fields. If the template has none, it
comes from a built-in profile:

| Command | Key | Interface fields | Dropped |
|---|---|---|---|
| `show cdp neighbors`, `show cdp neighbors detail`, `show lldp neighbors`, `show lldp neighbors detail` | `local_interface`, `neighbor_name` | `local_interface`, `neighbor_interface` | `holdtime`, `hold_time`, `age` |
| `show ip interface brief`, `show ipv6 interface brief` | `vrf`, `interface` | `interface` | |
| `show interfaces status`, `show interface status` | `port` | `port` | |

ntc-templates uses the same field names for these commands on most
platforms, so one profile serves Cisco, Arista, and Juniper alike. Only the
key fields a template actually produces are used. IOS has no
`vrf` column, for example, so its `show ip interface brief` key is just
`interface`. A command with neither `Key` fields nor a profile is compared as
whole records.

Prefer the `detail` commands for comparisons where you can. The brief LLDP
table cuts device names to 20 characters (`core-rtr-01.example.`), while the
detail output has the full name.

## Settings

| Setting | Command line | Environment | `axlib.toml` |
|---|---|---|---|
| Platform | `-p`, `--platform` | `AXLIB_TEXTFSM_PLATFORM` | `platform` in `[textfsm]` |
| Template directories | `-t`, `--template-dir` | `AXLIB_TEXTFSM_TEMPLATES` | `template_dirs` in `[textfsm]` |
| ntc-templates location | | `NTC_TEMPLATES_DIR` | |

```toml
[textfsm]
platform = "cisco_ios"
template_dirs = ["textfsm", "/opt/netops/textfsm"]   # relative to this file
```

Platform names are netmiko's device types: `cisco_ios` (which ntc-templates
also uses for IOS-XE), `cisco_nxos`, `cisco_xr`, `arista_eos`,
`juniper_junos`, `hp_comware`, `huawei_vrp`, and so on. An unknown name gets
a suggestion (`cisco_xe` → `cisco_ios`).

`axlib.toml` is found as for every axlib command: `--config`, then
`AXLIB_CONFIG_FILE`, then the file recorded by `install.sh`. Parsing needs no
secrets, so if the configuration file it finds can't be read (for example by
an operator outside the `netops` group), parsing goes on without it. A file
named with `--config` must exist.

## Python API

```python
from axlib.parse import compare, parse_output, parse_paths

# One command's output, from netmiko or anywhere else:
records = parse_output(
    conn.send_command("show cdp neighbors"), "show cdp neighbors", "cisco_ios"
)

# Whole collections, compared:
before = parse_paths(["captures/before"], platform="cisco_ios", recursive=True)
after = parse_paths(["captures/after"], platform="cisco_ios", recursive=True)
for command in compare(before, after).differences:
    print(command.device, command.command, command.status)
    for change in command.changes:
        print(" ", change.kind, change.key, change.fields)
```

- `parse_output(output, command, platform)` returns a list of dictionaries,
  like ntc-templates' `parse_output()`. It also searches your template
  directories first, accepts abbreviations, and normalizes records
  (`raw=True` turns that off). It raises `TemplateNotFoundError`,
  `AmbiguousCommandError`, or `TemplateParseError`, all subclasses of
  `axlib.parse.ParseError`.
- `parse_text(text, ...)` and `parse_paths(paths, ...)` return a `Snapshot`.
  Its `results` are `ParsedCommand`s (records, key, host, source, template)
  and its `problems` list what was skipped or failed.
  `Snapshot.to_dict()` and `load_snapshot()` write and read the JSON form.
- `compare(before, after, ignore=...)` returns a `SnapshotDiff`.
- `TemplateCatalog.default(template_dirs=[...], platform=...)` loads the
  template search path once, to pass as `catalog=` to many calls.
  `catalog.resolve(command, platform)` shows which template a command uses.
- `split_capture(text)`, `canonical_interface(name)`, and
  `normalize_records(records, command=...)` are the individual steps.

Each module also runs on its own: `python -m axlib.parse.capture`,
`python -m axlib.parse.templates`, `python -m axlib.parse.snapshot`, and
`python -m axlib.parse.diff`.

## Exit status

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `axlib parse` | output parsed (skipped commands are fine) | a command failed, or nothing was parsed | error: unreadable file, no platform, TextFSM missing, bad settings |
| `axlib parse diff` | no differences | differences | the comparison could not be made |
| `axlib parse templates` | template found | no template, or ambiguous | error |

## Troubleshooting

**`failed: ... State Error raised. Rule Line: 27. Input Line: sw1#`**
The template met a line it didn't expect and stopped. ntc templates end with
`^. -> Error` on purpose, so that changed output is noticed rather than parsed
wrongly. Look at the input line it names:

- A prompt, a pager remnant, or an echoed command means the capture has
  something axlib didn't remove. Check with `axlib parse split --json`, or
  give a `--marker` for your capture format.
- Output from a newer software release means the template needs updating.
  Copy it into your template directory under the same name and add a rule.

**`skipped ... no template for 'show foo' on cisco_ios`**
Run `axlib parse templates -p cisco_ios show foo`. Check the platform first.
Then check whether ntc-templates names the command differently (`show
interface status` on NX-OS, `show interfaces status` on IOS). If no template
exists, write one.

**`no templates for platform 'cisco_xe'`**
ntc-templates uses `cisco_ios` for IOS-XE. The message suggests the right
name.

**`ambiguous command 'sh ip b' ...`**
The abbreviation fits more than one command in one template directory. A
device would refuse it too. Spell out more of the command.

**`no commands found`**
No line looked like a command. Use `--command` for output without command
lines, or `--marker` for a header format of your own.

**A difference you don't care about**
Use `--ignore FIELD` on `axlib parse diff`. If the field changes on every
run, leave it out of your template instead.

## Limits

- Parsing is only as good as the templates. ntc-templates covers common
  commands well, but rarer commands and new software releases may need a
  template of your own.
- The built-in comparison keys cover the CDP, LLDP, IP-interface, and
  interface-status commands above. Other commands use their templates' `Key`
  fields or are compared as whole records.
- A file-name template parses a command without arguments. Commands with
  arguments need an index row.
- Interface-name normalization knows Cisco-style names (`GigabitEthernet`,
  `TenGigabitEthernet`, `Ethernet`, `Port-channel`, ...). Other vendors'
  names are left as printed, which is still consistent between collections.
