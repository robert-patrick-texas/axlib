# axlib release notes

Newest release first. [CHANGELOG.md](CHANGELOG.md) lists every individual change.

## 1.0.10

### Overview

`axlib parse diff` now answers the question a network operations team asks after a maintenance window: did every neighbor come back? Built-in profiles now cover OSPF, EIGRP, BGP, and IS-IS neighbors, routes, ARP, and MAC address tables on Cisco IOS, NX-OS, and IOS XR, Arista EOS, and Junos, beside the CDP and LLDP neighbors, interfaces, and ports they already covered. Neighbors listed in another order and timers that moved are no longer reported, with no `--ignore` options. A new guide, `docs/KARVI.md`, shows how to collect show-command output with karvi and turn it into tables, JSON lines, snapshots, and before-and-after reports. No storage format changes, and installing or upgrading axlib upgrades no other package.

### Are all neighbors back?

Collect the neighbor commands before and after the work and compare. In this report from the guide's example, the switch lost its lobby access point, and the router's link to the core moved to a new port as planned:

```text
rtr1: show cdp neighbors
  - local_interface=GigabitEthernet0/0/1 neighbor_name=core1
  + local_interface=GigabitEthernet0/0/2 neighbor_name=core1
rtr1: show ip ospf neighbor
  - neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/1
  + neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/2
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby.example.net
```

Every device listed its neighbors in another order the second time, and every hold time and dead time had moved; none of that shows. A neighbor that didn't come back is listed with `-`. One that came back on another port is a `-` and a `+` with the same name. One that is still listed but in another state is a `~` line, such as an OSPF neighbor that is no longer `FULL` or a BGP peer whose prefix count turned into `Active`.

| Table | A record is | Dropped |
|---|---|---|
| CDP and LLDP neighbors | the neighbor's name on a local port | hold time, age |
| OSPF neighbors | the router ID on an interface | dead time, uptime, and the detail output's counters |
| EIGRP neighbors | the address on an interface, per AS | hold, uptime, SRTT, RTO, queue count, sequence number |
| BGP peers | the peer's address, per VRF and address family | messages, table version, queues, up/down time, flaps |
| IS-IS neighbors | the system ID on an interface, per level | hold time |
| Routes | one path: prefix and next hop, per VRF | uptime |
| ARP | the IP address on an interface | age |
| MAC addresses | the MAC address, per VLAN | age, and Arista's move count |

- **Every platform's spelling.** A profile names each field name ntc-templates uses for the same thing (IOS's `nexthop_ip` is IOS XR's `next_hop`), so one profile serves a command on every platform, including IOS XR's `show route`, `show arp`, `show ospf neighbor`, `show eigrp neighbors`, and BGP summaries, and Junos's `show bgp summary` and `show isis adjacency`. The CDP and LLDP profile now also identifies IOS XR's LLDP detail, which calls the neighbor's name `neighbor`.
- **Interface names in lists** are spelled out like the others, so a MAC address's ports read `GigabitEthernet1/0/5` and join with the interface tables. **MAC addresses sort as text** (`0017.c8d2.7707` before `00e0.4c3a.9909`).
- **Before**, these commands were compared as whole records: every timer that moved turned a row into a removed and an added row, and finding the neighbor that didn't come back meant reading past them.
- **IOS XR's `show bgp summary`** has no template of its own in ntc-templates, so it is parsed with the template for `show bgp`, which reads the BGP table. Use `show bgp instance all summary`. A BGP peer that sends a full Internet table changes its prefix count all the time; `docs/PARSING.md` explains how to leave it out and what that costs.

`docs/PARSING.md` ("How records are made comparable") lists every command and field.

### Collecting with karvi, parsing with axlib

`docs/KARVI.md` follows one change through karvi and axlib on Cisco IOS XE and IOS XR, with sample collections in `examples/karvi` so every command can be run without devices:

- **The same platform names.** karvi now uses `cisco_ios` and `cisco_xr`, the names netmiko and ntc-templates use, and still accepts `cisco_iosxe`, `cisco_xe`, and `cisco_iosxr`. Give axlib the main names.
- **karvi's collection files** (`karvi run --cd=DIR`, one file per device, each command after a `! COMMAND` line) are read with one `--marker` pattern, and the device is named after the file.
- **Every output format**: tables to read, JSON lines with `jq` recipes (ports not connected, ARP as CSV, MAC addresses per VLAN, interfaces from IOS XE and IOS XR together), snapshots to keep, and Python reports, such as the switch port each IP address is on.
- **How Cisco output is normalized**: one name per command, the same field names on every platform, one spelling for interface names, a stable order, and no timers.
- **The neighbor check**, with a recipe that lists only the neighbors that are gone from any port, then the rest of the before-and-after report: interface status, routes, ARP, and MAC tables.
- **A maintenance-window script** that collects before and after, lists any neighbor that didn't come back (`neighbors`, exit status 1 or 0), and compares everything.

### Dependencies

Every dependency is at its newest version, tested with `make check` on Python 3.11, 3.12, 3.13, and 3.14. Nothing is held.

- **Development tools:** `pytest` 9.2.0 (was 9.1.1), `ruff` 0.17.3 (was 0.17.2), and `ty` 0.0.93 (was 0.0.92). `platformdirs` was also upgraded in `uv.lock`.
- **Runtime dependencies and extras** already required their newest versions, so nothing else changes for an installation.

### For anyone working on axlib

- **`tests/test_parse_profiles.py`** checks each profile's key and dropped fields against the installed ntc templates, so a template that renames a field fails `make check` (and `make deps`) instead of quietly changing a comparison. It also parses NX-OS CDP, Arista and IOS XR LLDP, OSPF, EIGRP, BGP, and IS-IS samples twice, reordered with moved timers, and expects no change, then with one neighbor gone, and expects exactly that neighbor reported. `docs/DEVELOPMENT.md` ("Adding a comparison profile") describes the steps.
- **The release workflow** calls its own CI and documentation workflows with GitHub's self-repository syntax, `uses: $/...`. This release is the first to call the documentation workflow that way.
- **The source tarball** includes `examples/karvi`, whose collection files have no extension, so `tests/test_parse_karvi.py` runs from an unpacked tarball too.

### Compatibility

- **Comparisons of the newly covered commands change.** Records that differ only in timers are no longer reported, a record that changed is listed by its key (`- neighbor_id=10.255.0.1 interface=GigabitEthernet0/0/1`), and `--json` has the whole records as before. `--ignore age --ignore uptime` and the like still work and now change nothing for these commands.
- **Snapshots from 1.0.9** hold these commands without a key and with their timers, so comparing one with a 1.0.10 snapshot lists every row of those commands as removed and added, even when both were made from the same capture. Parse the captures again with 1.0.10, or compare the captures themselves.
- **MAC table ports are spelled out** (`GigabitEthernet1/0/5`, not `Gi1/0/5`) in tables, JSON lines, and snapshots; adjust any filter that matches the short name. `--raw` gives the records as the template returns them.
- **No storage format changes, and no installer changes.** A shared host upgrades in place with `sudo ./axlib-1.0.10/install.sh` or `sudo /opt/shared/axlib/current/install.sh update`, keeping its options, and rolling back to 1.0.9 as `docs/INSTALL.md` describes works.

### Validation

Before tagging, on the development host and in GitHub Actions on the commit before this one:

```text
make deps: make check with every dependency at its newest
  version on CPython 3.11.16, 3.12.15, 3.13.15, and 3.14.3:  570 passed on each, nothing held
make check, locally (Ruff 0.17.3, ty 0.0.93, docstring
  audit, tests):                                           570 passed
make deps-check (the release gate):                        12 of 12 dependencies newest
make docs (strict, no broken links):                       passed
CI (GitHub Actions) on the commit before this one: make
  check on CPython 3.11 to 3.14, docs, build, and the
  installer, fresh and upgrading from 1.0.9:               passed
twine check --strict of the built tarball and wheel:       passed
Source tarball contents: docs/KARVI.md, examples/karvi,
  and the new tests:                                       present
The guide's commands and outputs, rerun on examples/karvi,
  and its maintenance-window script, run end to end with
  a stand-in for karvi (a missing neighbor, every
  neighbor back, an unreachable device):                   as documented
```

The release workflow runs every CI check and `make deps-check` again on the tag before publishing.

## 1.0.9

### Overview

A shared host can now upgrade itself. `install.sh update` installs the latest release from GitHub once it matches the SHA-256 digest GitHub publishes for it, and keeps the configuration, keys, and credential stores; `update --dry-run` only reports whether there is one. Both work through a proxy, including one that inspects TLS. `install.sh --with-all` installs every optional part, now and in later releases. From this release on, each release references, and is tested with, the newest version of every dependency, so this one raises the lower bounds of `cryptography`, `redis`, `markdown-it-py`, and `mdit-py-plugins`. No storage format changes.

### Updating a shared host from GitHub

```bash
/opt/shared/axlib/current/install.sh update --dry-run   # is there a newer release? no root needed
sudo /opt/shared/axlib/current/install.sh update        # install it
```

- **`update`** compares the axlib version in the shared environment with GitHub's latest release, downloads that release's tarball, and stops unless the tarball matches the digest GitHub publishes. Then it runs the new release's `install.sh`, which starts from the options recorded in `/etc/axlib/install.env`, like any upgrade: `axlib.toml`, the keys, the credential stores, and the libraries added with `axuv` are kept, and the previous release stays in `releases/` for a rollback.
- **`--dry-run`** changes nothing and needs no root, so any member of `netops` can run it. It lists the download, the digest, the recorded options, and what is kept, with a link to the release notes. It exits with status 100 when a newer release exists and 0 when axlib is up to date, so a monitoring job can run it on a schedule.
- **`--proxy URL`** (or `--proxy=URL`) downloads through an HTTP or HTTPS proxy and passes it to the new release's installer as `HTTPS_PROXY` and `HTTP_PROXY`, so uv downloads Python and packages through it too.
- **Behind a TLS-inspecting proxy**, give curl the organization's CA certificate after `--`, which passes the rest of the command line to curl: `update --proxy=URL -- --cacert FILE --proxy-cacert FILE`. `-k` accepts any certificate instead, and warns each time it is used: the digest then arrives over the same unchecked connection as the release, so it no longer proves where the release came from. When curl does not trust a certificate, `update` says so and names these fixes.
- `update` needs `curl` and access to `api.github.com` and `github.com`. `docs/INSTALL.md` ("Upgrading axlib" and "Through a proxy") has the details.

**1.0.8 and earlier have no `update`**, so a shared host moves to 1.0.9 from the tarball, as before, and uses `update` from then on:

```bash
tar xzf axlib-1.0.9.tar.gz
sudo ./axlib-1.0.9/install.sh
```

### Every optional part: `--with-all`

`sudo ./install.sh --with-all` installs the credential manager, show-command parsing, and Markdown conversion. It is recorded as `WITH_ALL='1'`, so a later upgrade to a release that adds an optional part installs that part too. A `--no-` option for one part, given after it or in a later run, leaves that part out and turns `--with-all` off. It does not include `--with-netenv`, which puts every operator's password in every login's environment: a policy for the host owner, not a feature.

### Dependencies

Each release now references, and is tested with, the newest version of every direct dependency, even one published the same day. A dependency whose newest version fails the tests is held at the last version that passed, named in its release's notes, and tried again at the next release; the release workflow refuses to publish when a dependency is behind without such a hold.

Every dependency is at its newest version, tested with `make check` on Python 3.11, 3.12, 3.13, and 3.14. Nothing is held.

- **Raised lower bounds:** `cryptography>=50.0.2` (was `>=42`) and `redis>=8.1.0` (was `>=5`); for `axlib[markdown]`, `markdown-it-py[linkify]>=4.2.0` (was `>=4.0`) and `mdit-py-plugins>=0.6.1` (was `>=0.5`). The other extras already required their newest versions: `rich` 15.0.0, `textual` 8.2.8, `ntc-templates` 9.3.0, and `textfsm` 2.1.0.
- **Development tools:** `ruff` 0.17.2 (was 0.16.7), `ty` 0.0.92 (was 0.0.80), `pytest` 9.1.1, and `pytest-cov` 7.1.0.

### For anyone working on axlib

- **`make deps`** is the first step of a release. It raises every dependency to its newest version, upgrades `uv.lock`, runs `make check` on each supported Python, holds back only a dependency whose newest version fails (recorded in `tools/dependency-holds.toml`), and prints the Dependencies section for the release notes. A failing run is repeated once and counts only if it fails again.
- **`make deps-check`** is the new release gate in `release.yml`: it fails when a dependency is behind its newest version without a hold for exactly that version, or when the release notes do not name a held dependency. `make check` also fails on an upper bound added to `pyproject.toml` by hand.
- **ruff** stays pinned exactly, but moves to its newest release through `make deps`.
- **`make install-test`** also tests `install.sh update` against a stand-in for GitHub: a tarball that does not match its digest is refused, `--dry-run` exits 100, a self-signed certificate is refused unless `-k` or `-- --cacert` is given, and the update runs through a proxy (tinyproxy) with `--proxy`, uv's downloads included, keeping `axlib.toml`, the key, and a credential record. It also tests `--with-all`, `--no-textfsm`, and `update --dry-run` as a member of `netops`.
- Fixed a race in the TUI's idle-timeout test, which failed when the app closed before its first store load finished.

### Compatibility

- **Newer dependencies come along.** Installing or upgrading to 1.0.9 also upgrades `cryptography`, `redis`, `markdown-it-py`, and `mdit-py-plugins` where they are older than the versions above: in the shared environment when `install.sh` runs, and in an operator's own project when it adds the 1.0.9 wheel. A project that pins an older version of one of them cannot add 1.0.9 until it moves that pin.
- **No storage format changes.** A shared host upgrades in place with `sudo ./axlib-1.0.9/install.sh`, keeping its options, and rolling back to 1.0.8 as `docs/INSTALL.md` describes works. The 1.0.8 installer ignores the new `WITH_ALL` line in `install.env`.

### Validation

Before tagging, on the development host and in GitHub Actions on the commit before this one:

```text
make deps: make check with every dependency at its newest
  version on CPython 3.11.16, 3.12.15, 3.13.15, and 3.14.3:  passed on each, nothing held
make check, locally (Ruff 0.17.2, ty 0.0.92, docstring
  audit, tests):                                           509 passed
make deps-check (the release gate):                        12 of 12 dependencies newest
CI (GitHub Actions), make check on CPython 3.11 to 3.14,
  docs, build, and the installer, fresh and upgrading:     passed
twine check --strict of the built tarball and wheel:       passed
Installer in an Ubuntu 26.04 container, fresh: --with-all,
  update --dry-run up to date as root and as a netops
  member, then --no-textfsm:                               passed
Installer: the 1.0.8 release tarball with the TUI upgraded
  to this build adding parsing and Markdown (options
  kept), the documented rollback to 1.0.8, then
  install.sh update from a stand-in for GitHub (wrong
  digest refused, --dry-run, untrusted certificate, -k,
  -- --cacert, --proxy through tinyproxy), keeping
  axlib.toml, the key, and a credential record:            passed
actionlint on the workflows:                               no errors
zizmor 1.31.1 on the workflows:                            2 low-severity style
  suggestions (self-repository syntax), nothing else
```

The release workflow runs every CI check and `make deps-check` again on the tag before publishing.

## 1.0.8

### Overview

`axlib radius` now works over both IPv4 and IPv6: a server name with an IPv6 and an IPv4 address is tried at each until one answers, and `-4` or `-6` picks one. By default it sends one request per address and waits one second for it. The release also changes how axlib is built and published. GitHub Actions checks every push, and this is the first release published by a workflow: the tarball and wheel on GitHub and PyPI are the files that workflow built and tested, and PyPI received them by trusted publishing, without a PyPI token. No storage format changes.

### RADIUS over IPv4 and IPv6

```console
$ axlib radius radius1.example.net -u alice --secret-file /etc/axlib/radius.secret
Password for alice:
radius: 2001:db8::10 port 1812 did not answer: no reply after 1 attempt(s) of 1 s
Access-Accept from radius1.example.net (192.0.2.10) port 1812 for 'alice' in 4.1 ms (attempt 1)
```

- **Every address is tried:** the server's addresses are tried in the order the host's resolver prefers, usually IPv6 first. An address that reports the port closed, cannot be reached, or stays silent through every retry passes the check on to the next one. Before, only the first address was tried, so a server listening on IPv4 only was reported as not listening when its name resolved to IPv6 first. A reply that fails verification still ends the check at once, since it means the shared secrets differ.
- **`-4` and `-6`** (`ip_version=4` or `6` in `authenticate()`) use one IP version only, which skips the wait for an address the server ignores.
- **Shorter defaults:** one attempt of one second per address (`--timeout 1`, `--retries 0`), instead of three attempts of three seconds. A name whose IPv6 and IPv4 addresses are both silent gets one request at each and gives up after two seconds. `--timeout` and `--retries` (`timeout=` and `retries=` in Python) still set both.
- **What did not answer is reported:** each address passed over is noted on standard error, and the result on standard output keeps its format. `RadiusResult.failed_addresses` and `failed_addresses` in `--json` carry the same list. When no address answers, silence everywhere is still exit status 3; otherwise the exit status is 2 and the message names each address and what happened there.
- **IPv6 in replies:** `Framed-IPv6-Address`, `Framed-IPv6-Prefix`, and `Delegated-IPv6-Prefix` are shown as `2001:db8::7` and `2001:db8::/64` instead of hex.

A RADIUS server identifies its clients by source address, so an address that stays silent is often one the server does not list: a server that knows this host by its IPv4 address only ignores requests that arrive over IPv6. `docs/RADIUS.md` ("IPv4 and IPv6") explains the order and the timing.

### Continuous integration

Every push to `main` and every pull request runs:

- `make check` (Ruff, ty, the docstring audit, and the test suite) on Python 3.11, 3.12, 3.13, and 3.14.
- `make docs`, which fails on a broken link, and `uv build` with `twine check --strict`.
- `install.sh` end to end in an Ubuntu 26.04 container: a fresh install with every optional part, and an upgrade from the previous release with the rollback from `docs/INSTALL.md` and a second upgrade.

The first runs found two problems that the development host hid. GitHub's runners resolve `localhost` to `::1` first, which exposed the single-address RADIUS check above, and they have no `netops` group, which one test assumed. Dependabot proposes updates to the workflows' pinned actions once a week.

### Releases

Pushing a `v<version>` tag runs `.github/workflows/release.yml`. It repeats every check on the tagged commit, confirms that the tag matches the version in `pyproject.toml` and is on `main`, then creates the GitHub release with its notes from `RELEASE_NOTES.md`, uploads the same files to PyPI, and deploys the documentation site. Each PyPI file carries a signed attestation naming the workflow run that built it. `docs/DEVELOPMENT.md` describes the steps and the one-time setup.

For anyone working on axlib:

- **`make install-test`** runs `install.sh` in a disposable container; `FROM=<old tarball>` tests an upgrade, a rollback, and a second upgrade (`tools/installer-test.sh`).
- **`make publish` is removed.** Releases are published only by the workflow.
- **`make check` requires release notes:** it fails unless `RELEASE_NOTES.md` has a section and `CHANGELOG.md` a dated heading for the version in `pyproject.toml`.

### Compatibility

- **`axlib radius` defaults:** `--timeout` is 1 second (was 3) and `--retries` is 0 (was 2), so a silent server is reported after 1 second instead of 9, per address. A server that takes over a second to answer, or a path that loses packets, now needs `--timeout` or `--retries`; `--timeout 3 --retries 2` restores the old wait. Scripts that relied on the resends should pass `--retries` explicitly.
- **`axlib radius` with a host name** tries each of its addresses, so each silent address adds `--timeout` times the number of attempts. Use `-4` or `-6`, or an IP address, to try one address only.
- **`axlib radius` error text:** a closed port is reported as `cannot reach SERVER port PORT: the server's host answered that nothing is listening ...` instead of the same sentence without the server and port. A server name all of whose addresses fail with at least one error exits 2 (`RadiusError`), not 3. Addresses passed over are printed on standard error even when the check succeeds.
- **No storage format changes.** A shared host upgrades in place with `sudo ./axlib-1.0.8/install.sh`, keeping its options, and rolling back to 1.0.7 as `docs/INSTALL.md` describes works.
- **macOS is no longer listed** in the package classifiers, because nothing tests axlib there. Nothing in the code changed for macOS.

### Validation

Before tagging, on Python 3.14.3 locally and in GitHub Actions on the commit before this one:

```text
make check, locally (Ruff, ty, docstring audit, tests):  480 passed
Test suite as a non-root user with no netops group and
  localhost on ::1 first (Ubuntu 26.04 container):       477 passed, 3 skipped
CI (GitHub Actions), make check on CPython 3.11.17,
  3.12.3, 3.13.17, and 3.14.9:                           480 passed on each
CI: make docs, the build, and the installer, fresh and
  upgrading from 1.0.6 with the rollback:                passed
RADIUS defaults, localhost on ::1 and 127.0.0.1 with
  both silent: requests sent, time to give up:           1 per address, 2.0 s
twine check --strict of the built tarball and wheel:     passed
Installer in an Ubuntu 26.04 container: the 1.0.7
  release tarball with the TUI upgraded to this build
  adding parsing and Markdown (options kept), the
  documented rollback to 1.0.7, and the upgrade again:   passed
actionlint on the workflows:                             no errors
zizmor on the workflows:                                 no security findings
```

The release workflow ran every CI check again on the tag before publishing.

## 1.0.7

### Overview

This release adds `axlib md2html`, which turns Markdown runbooks, change plans, and notes into HTML pages, and uses it to publish the documentation as a website: <https://robert-patrick-texas.github.io/axlib/>. Every guide was checked against the code and corrected, and the bugs found along the way are fixed. One output changes on purpose: `axlib netenv-set` no longer prints `unset NETENABLE` unless asked to (see Compatibility below).

### Markdown to HTML

```bash
axlib md2html change-plan.md                  # writes change-plan.html beside it
axlib md2html -r runbooks -o site --index runbooks/README.md --site-name "NOC runbooks"
```

- **What it reads:** Markdown as GitHub reads it: tables, task-list check boxes, footnotes, strikethrough, and bare URLs as links. Headings get GitHub's anchors, so `INSTALL.md#offline-hosts` links keep working.
- **The pages:** a sidebar down the left lists every page converted together and the current page's sections, and an optional bar at the top holds the site name and links such as the project's repository (`--site-name`, `--link LABEL=URL`). Pages need no JavaScript, web server, or internet access, follow the light or dark system setting, and put the text first on a phone.
- **Links:** links between the Markdown files are rewritten to the HTML pages. Broken links, missing files, and `#section` links to headings that do not exist are reported as `file:line: message`, and `--strict` makes them fail the command. Linked images are copied next to the pages; hidden files are never copied.
- **Python:** `markdown_to_html()` for one string, `convert_paths()` for files and folders.
- **Install:** `uv add 'axlib[markdown]'`, or `install.sh --with-markdown` on a shared host (under 1 MB of pure Python).

`docs/MARKDOWN.md` is the guide.

### Documentation website

The guides are now published at <https://robert-patrick-texas.github.io/axlib/>, with the GitHub repository and the PyPI project linked at the top of every page and an index of pages and sections down the left. `make docs` builds the same site locally, and `.github/workflows/docs.yml` publishes it from the tag of every GitHub release, so the website always describes the latest release.

Every guide was checked against the code, and checked again after the corrections. Among the corrections: the recovery advice after an interrupted `rotate-key`, which could have deleted the only working key; when `ax.getkeys()` falls back to the shared account; and how `install.sh --no-store` behaves. New material includes a reference of every `axlib.toml` setting and `AXLIB_*` variable, the Redis cache, both stores' command lines, the full Python APIs, and a table of every command in the README.

### Fixes

- **Credentials:** a key file or database named by a relative path in `axlib.toml` that is a symlink is now refused, like any other symlink. `axlib credentials` reports a missing configuration in one line instead of a traceback, and a configuration file a user cannot read is reported as `cannot be read (Permission denied)` rather than `does not exist`. SQLite key-file problems are reported as themselves. `rotate-key --dry-run` and the TUI make the same checks as the rotation itself. The TUI empties password boxes whenever a form closes.
- **`install.sh`:** `--no-store` no longer turns the SQLite store on in a new `axlib.toml`. `current` moves to a new release only after it is installed, and a failed install restores the project and removes the unfinished release. A failing store check at the end is a warning, not a failed install. Every `AXLIB_*` variable in root's environment is ignored while it runs.
- **`axlib parse`:** commands typed in a configuration mode with `do` are parsed. `axlib parse diff` exits 2 when a command failed to parse, and a damaged snapshot is an error instead of an empty capture.
- **`axlib radius`:** end of input at a prompt is explained, and `--password-stdin` with no input no longer sends an empty password.
- **Text filters:** `triple-quotes` handles CRLF and CR-only text; `include` keeps an included file without a final newline on its own line and blocks a cycle back to the input file; `include` and `comments` report undecodable input with status 1; `variables` keeps the last of repeated assignments.

### Compatibility

- **`axlib netenv-set` output:** for a record without an enable secret, it no longer prints `unset NETENABLE`; its output is only `export` lines. `--unset` brings the line back. The `netenv-set` shell function in `examples/netenv/netenv.sh`, installed as `/etc/profile.d/axlib-netenv.sh` by `--with-netenv`, always passes `--unset`, so login shells behave as before. A script that runs `eval "$(axlib netenv-set)"` itself keeps an existing `NETENABLE` unless it adds `--unset`.
- **Exit statuses:** `axlib parse diff` exits 2, not 0 or 1, when a command failed to parse. `axlib credentials` exits 2 when it cannot run at all, keeping 1 for a missing field. `include` and `comments` exit 1, not 2, for undecodable input.
- **`import axlib.secrets`** no longer reads the configuration file when it is imported; settings are read at the first lookup.
- **No storage format changes.** A shared host upgrades in place with `sudo ./axlib-1.0.7/install.sh`, keeping its options; add `--with-markdown` for the converter. Rolling back to 1.0.6 as `docs/INSTALL.md` describes works; uv warns that 1.0.6 has no `markdown` extra.

### Validation

Validated on Python 3.14.3 and, from the built wheel, on Python 3.11.16:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     453 passed
Full suite from the built wheel, Python 3.11 and 3.14: 453 passed
Documentation site (make docs --strict):               no broken links
Installer in an Ubuntu 26.04 container, from the
  release tarballs: 1.0.6 with the TUI, parsing, and
  the login snippet upgraded to 1.0.7 (options,
  configuration, and records kept; login snippet
  passes --unset), netenv-set output with and without
  --unset, --with-markdown added and the release's
  docs built by an operator, the documented rollback
  to 1.0.6, and the upgrade again:                     passed
Installer failure paths (failed uv add and failed
  build restore the previous state):                   passed
shellcheck of install.sh, axuv, and netenv.sh:         passed
```

## 1.0.6

### Overview

This release adds show-command parsing. `axlib parse` turns the output of commands such as `show cdp neighbors` into records with TextFSM templates: the ntc-templates collection plus the team's own `.textfsm` files. `axlib parse diff` compares a collection taken before a maintenance window with one taken after it, record by record. Neighbors listed in a different order and hold times that ticked down are not reported. Removed neighbors, moved ports, and changed fields are. Parsing is an optional install, `uv add 'axlib[textfsm]'`, or `install.sh --with-textfsm` on a shared host. Nothing else changes.

### Before and after a change

```bash
axlib parse -p cisco_ios -r captures/before/ -o before.json
axlib parse diff -p cisco_ios before.json captures/after/
```

```text
sw1: show cdp neighbors
  - local_interface=GigabitEthernet1/0/5 neighbor_name=ap-lobby-01
  ~ local_interface=GigabitEthernet1/0/48 neighbor_name=core-rtr-01.example.net
      neighbor_interface: GigabitEthernet0/0/1 -> GigabitEthernet0/0/2
sw1: show ip pim neighbor
  ~ neighbor=10.20.0.2 interface=Vlan20
      mode: "DR S P G" -> "S P G"
...
9 commands compared: 7 changed, 0 only before, 0 only after, 2 unchanged
```

On the sample captures in `examples/parse`, `diff -u` of one switch's logs shows 110 changed lines and of the other's 6. `axlib parse diff` reports four real changes on the first switch and nothing on the second, whose neighbors were only listed in another order.

- **Captures** can be session logs with IOS, NX-OS, EOS, IOS-XR, or Junos prompts, script output with bare `show` or `display` commands, files and directory trees (`-r`), output piped from `ssh` (`--command`), or another header format described with `--marker`. Pager prompts, IOS timestamp lines, and Junos `{master:0}` lines are removed first. `axlib parse split` shows how a capture is cut into commands.
- **Abbreviations** such as `sh cdp nei` are recognized, and they pair with `show cdp neighbors` in the other collection.
- **Records are made comparable.** They are sorted by what identifies them, such as the local interface and neighbor. Interface names are spelled one way (`Gig 1/0/48`, `Gi1/0/48` → `GigabitEthernet1/0/48`). Fields that change on every run, such as hold time, are dropped. A template's `Key` fields identify its records; built-in profiles cover CDP and LLDP neighbors (brief and detail), IP interface brief, and interface status.
- **Output** is a JSON snapshot by default, which `axlib parse diff` reloads, or JSON lines, or tables. `axlib parse diff --json` prints the comparison as one object.
- **Exit status** for `axlib parse`: 0 parsed, 1 a command failed or nothing was parsed, 2 error. For `axlib parse diff`, as for `diff`: 0 no differences, 1 differences, 2 trouble.

The commands are also available as `axlib-parse` and `python -m axlib.parse`. `docs/PARSING.md` is the operator guide.

### The team's own templates

A template needs only a file name: put `<platform>_<command>.textfsm`, such as `cisco_ios_show_ip_pim_neighbor.textfsm`, in a template directory. No index file is needed; commands with arguments still need an ntc-templates style `index`. Directories are searched in this order:

1. `--template-dir DIR` (repeatable)
2. `AXLIB_TEXTFSM_TEMPLATES`
3. `template_dirs` in the new `[textfsm]` table of `axlib.toml`, or `textfsm/` beside `axlib.toml` when it has none. On a shared host this is `/etc/axlib/textfsm`.
4. ntc-templates, or `NTC_TEMPLATES_DIR`

A template with the same name as an ntc template replaces it, however the command is abbreviated in the capture. `axlib parse templates -p cisco_ios sh ip pim nei` shows which template parses a command and where it came from. `[textfsm]` can also set a default `platform`, as can `AXLIB_TEXTFSM_PLATFORM`.

### Python API

```python
from axlib.parse import parse_output

records = parse_output(
    conn.send_command("show cdp neighbors"), "show cdp neighbors", "cisco_ios"
)
```

`parse_output()` suits netmiko and scrapli scripts. `parse_text()`, `parse_paths()`, and `compare()` do what the commands do, and `TemplateCatalog`, `split_capture()`, `canonical_interface()`, and `normalize_records()` are public for scripts that need one step.

### Shared host

```bash
sudo ./axlib-1.0.6/install.sh --with-textfsm
```

- `--with-textfsm` installs the extra and creates `/etc/axlib/textfsm`, owned by `root:netops` with mode `2775`, so any member of `netops` can add templates. Run `chmod 2755 /etc/axlib/textfsm` to allow only root; later runs keep the mode. The choice is recorded as `WITH_TEXTFSM` in `/etc/axlib/install.env`, so upgrades keep it.
- `--no-textfsm` removes the packages and keeps the templates.
- Removing one optional part now keeps the other. Before, the installer recognized only the credential manager installed on its own.
- A new `axlib.toml` includes a commented `[textfsm]` table. An existing one is not changed and needs no edit: `/etc/axlib/textfsm` is found because it is beside it.

### Compatibility

- New package and commands only: `axlib.parse`, `axlib parse`, and `axlib-parse`. No existing command, API, storage format, or `ax.getkeys()` behavior changes.
- No new required dependencies. The optional `textfsm` extra adds `textfsm` and `ntc-templates`, about 5 MB of pure Python, both Apache-2.0. Without it, `axlib parse` explains how to install it and exits 2. Splitting captures and comparing two saved snapshots need only the standard library.
- Upgrading a shared host with `sudo ./axlib-1.0.6/install.sh` keeps its options and does not add parsing; add `--with-textfsm` for that.
- Rolling back to 1.0.5 as described in `docs/INSTALL.md` works. uv warns that the 1.0.5 wheel has no `textfsm` extra, and the parsing packages stay installed but unused.

### Validation

Validated on Python 3.14.3 and, from the built wheel, on Python 3.11.16:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     336 passed
Full suite from the built wheel, Python 3.11 and 3.14: 336 passed
Installer in an Ubuntu 26.04 container, from the
  release tarballs: 1.0.5 with the TUI upgraded to
  1.0.6 (options, configuration, and records kept),
  --with-textfsm added, a shared template used,
  before/after diff of the examples, the documented
  rollback to 1.0.5, and the upgrade again:            passed
shellcheck of install.sh:                              passed
```

## 1.0.5

### Overview

This release adds a RADIUS login check. `axlib radius` asks a RADIUS server whether it accepts a username and password, the same way a switch, router, or firewall does at login. Operators can use it to find out why a device login fails: whether the server rejects the account, the request never reaches the server, or the two sides have different shared secrets. The same check is a Python function, `axlib.radius.authenticate()`. Nothing else changes.

### RADIUS login check

```bash
axlib radius 192.0.2.10 -u "$NETUSER" --password-env NETPASS --secret-file /etc/axlib/radius.secret
```

```text
Access-Accept from 192.0.2.10 port 1812 for 'alice' in 4.2 ms (attempt 1)
  Reply-Message: Welcome, alice
  Vendor-Specific: vendor=9 type=1 value=shell:priv-lvl=15
  Message-Authenticator: verified
```

- It sends one PAP `Access-Request` to UDP port 1812, or the port given with `--port`. After `--timeout` seconds it resends the identical packet, up to `--retries` times.
- It prints the answer with the reply's attributes, or one JSON object with `--json`. The exit status is 0 for Access-Accept, 1 for Access-Reject or Access-Challenge, 2 for an error, and 3 when no reply arrives, so scripts and monitoring checks need no parsing.
- The password comes from an environment variable (`--password-env NETPASS` works after `netenv-set`), from standard input, or from an echo-off prompt. The shared secret comes from a file, an environment variable, or a prompt. `--password` and `--secret` also work, but the help text warns that other users can see them in `ps`.
- Errors explain the likely cause. If no reply arrives, the message lists a blocked port, this host not being configured as a client, and a wrong secret that the server drops silently. A reply that fails verification is reported as a probable shared-secret mismatch, and a closed port as "nothing is listening".

The command is also available as `axlib-radius` and `python -m axlib.radius`. `docs/RADIUS.md` covers the options, the Python API, how the protocol works, and troubleshooting.

### Python API

```python
from axlib.radius import authenticate

result = authenticate("192.0.2.10", "alice", password, secret)  # port=1812 by default
print(result.reply.code_name, result.accepted, result.reply.reply_messages)
```

A reject is a result, not an exception. `RadiusTimeoutError` and `RadiusError` mean that there is no answer that can be trusted. The result never holds the password or the secret. The packet functions `hide_password()`, `build_access_request()`, and `verify_response()` are public, so they can be studied and tested on their own.

### Security

- Every request carries a `Message-Authenticator`, the defense against the BlastRADIUS reply-forgery attack (CVE-2024-3596). Servers configured to require it accept these requests.
- Every reply's Response Authenticator is verified, and so is its Message-Authenticator when it has one. The output reports whether the server sent one, because a server that never does may still need the BlastRADIUS update.
- PAP hides the password only with MD5 and the shared secret. Run checks over a trusted management network.

### Compatibility

- New module and commands only: `axlib.radius`, `axlib radius`, and `axlib-radius`. No existing command, API, storage format, or `ax.getkeys()` behavior changes.
- No new dependencies. The module uses only the Python standard library, and its MD5 calls are marked `usedforsecurity=False` so they also work under FIPS-mode Python.
- Only PAP is supported. CHAP, MS-CHAP, EAP, accounting, and RADIUS over TLS are not, and an Access-Challenge, such as an OTP prompt, is reported but not answered.
- A shared host upgrades in place with `sudo ./axlib-1.0.5/install.sh`. The installer needs no new options.

### Validation

Validated on Python 3.14.3 and, from the built wheel, on Python 3.11.16:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     219 passed
RADIUS tests from the built wheel, Python 3.11 and
  3.14 (RFC 2865 section 7.1 vectors, forged and
  malformed replies, local UDP server):                41 passed
Interoperability with a pyrad server: request,
  hidden password, and Message-Authenticator
  accepted; Accept with Cisco vendor attribute and
  Reject verified; exit statuses 0, 1, and 2 (closed
  port) on both Python versions:                       passed
```

## 1.0.4

### Overview

This release makes a shared host easier to run after the first install. `install.sh` records its options and reuses them, so upgrades need no options. The `axuv` helper becomes an installed command, `/usr/local/sbin/axuv`. The `axlib` command and `ax.getkeys()` scripts find the host's configuration without `AXLIB_CONFIG_FILE`. Every credential record can now carry a short note, which the TUI and the CLIs show and edit. Storage formats are unchanged.

### Recorded installer options

Every run of `install.sh` writes its options to `/etc/axlib/install.env`, and the next run starts from them:

```bash
sudo ./axlib-1.0.4/install.sh               # an upgrade: no options needed
sudo ./axlib-1.0.4/install.sh --help        # shows the defaults the next run would use
```

New options turn earlier choices back off: `--no-tui`, `--no-netenv`, and `--store` (the opposite of `--no-store`). `--sbin-dir` chooses where `axuv` is installed. On a host installed by 1.0.3, which wrote no record, the first 1.0.4 run keeps the TUI and the netenv login snippet as they are unless told otherwise.

### The `axuv` command

`/usr/local/sbin/axuv` replaces the shell function that administrators added to root's `~/.bashrc`. `sudo axuv <uv arguments>` runs uv on the shared environment with `umask 022` and the shared Python, taking the paths from the install record, and it refuses to run as anyone but root:

```bash
sudo axuv add netmiko scrapli
sudo axuv lock --upgrade && sudo axuv sync
```

An existing `axuv` function in root's `~/.bashrc` takes precedence over the command in interactive root shells. It still works, but removing it lets the installed command follow future changes.

### Default configuration file

axlib now selects its TOML file from `--config`, then `AXLIB_CONFIG_FILE`, then the `CONFIG_FILE` in `/etc/axlib/install.env`. On an installed host, `sudo axlib credential-db add ...` works without `-E` or an `export`, and cron jobs and systemd units need no `AXLIB_CONFIG_FILE`. `axlib.credentials.default_config_file()` returns the selected file, and every `--config` help text states the order. Users who cannot read `/etc/axlib` see no change.

### Record notes

Every record may now have a `note`: one line of up to 120 characters for the people who manage the store, such as "NOC team account; owner J. Smith" or "lab account, expires in June".

- **In the TUI**, the Add and Edit forms have a Note box, and clearing it removes the note. The table has a NOTE column showing the first 40 characters. The line below the table shows the whole note of the highlighted record, so it is readable on an 80-column terminal. The `/` filter matches note text as well as service names. The column that explains how `ax.getkeys()` treats each record (`you`, `shared fallback`, `missing netpass`) is renamed from NOTES to **ADVICE**.
- **In the CLIs**, `--set note="..."` and `--remove note` work with `add` and `update`. `list` adds a NOTE column as the last column, and `list --json` adds `"note"` (`null` without one).
- **In Python**, `StoreAdmin.list_records()` returns each note in `record.visible["note"]`, and `AnnotatedRecord.note` gives it directly.

The note is stored inside the encrypted record like any other field, so neither store's format changes. It is not treated as secret, because listings display it. No lookup helper reads it: `ax.getkeys()` returns the same values, `axlib netenv-set` never exports a `NOTE` variable, and a note in both stores is not reported as overridden. Blank notes, notes over 120 characters, and notes with line breaks or control characters are refused, so a note cannot break a table row or send escape sequences to a terminal.

### Fixes

- `--no-tui` removes the credential manager's packages from the shared environment. In 1.0.3, leaving out `--with-tui` was documented to remove the TUI but did not, because `uv add` keeps an existing extra and never uninstalls packages.
- Rerunning the installer with a different `--config-dir` now replaces `AXLIB_CONFIG_FILE` in `/etc/environment` instead of keeping the old path.

### Compatibility

- On a host with `/etc/axlib/install.env`, a program that previously ran without any configuration file now reads the recorded `axlib.toml`. Set `AXLIB_CONFIG_FILE` or pass `--config` to choose a different file.
- A 1.0.3 host upgrades in place with `sudo ./axlib-1.0.4/install.sh`. Its configuration, key, store, records, and added libraries are kept.
- Mirror settings for offline hosts belong in `/etc/uv/uv.toml`, which every uv run reads, instead of the old `axuv` function.
- `list` output has a fifth column, NOTE, at the end, and `list --json` objects have a `note` key. Scripts that read the first four columns by position, or ignore unknown JSON keys, are unaffected. The TUI's advice column is now titled ADVICE.
- A record with a note stays usable by 1.0.3: lookups ignore the field, and the 1.0.3 TUI and `list` show `note` as an extra field name. The 1.0.3 CLIs refuse `--set note=...`.
- No storage format, dependency, or `ax.getkeys()` precedence changes.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     178 passed
shellcheck install.sh, scripts/axuv:                   passed
TUI notes (add, edit, clear, filter, 80-column note
  line) in headless Textual tests:                     passed
install.sh on Ubuntu 24.04 (fresh, rerun without
  options, --no-tui/--no-netenv, --config-dir change,
  axuv under umask 077, non-root refusal):             passed
Upgrade from a 1.0.3 install with TUI, netenv, and a
  shared library; rollback to 1.0.3 with axuv:         passed
Configuration default as root, a netops operator,
  and a user outside netops:                           passed
```

## 1.0.3

### Overview

This release makes axlib straightforward to deploy on a shared Linux host. The release tarball now contains `install.sh`, which sets up a shared Python, a locked axlib environment, and a ready credential store for the `netops` group, and `docs/INSTALL.md` explains how administrators maintain that environment and how network operations staff use it in their own scripts. The axlib package itself is unchanged from the previous release.

### Shared-host installer

The release tarball now contains `install.sh`, which installs axlib for every operator on a Linux host:

```bash
tar xzf axlib-<version>.tar.gz
sudo ./axlib-<version>/install.sh            # add --with-tui and/or --with-netenv as needed
```

| Path | Contents |
| --- | --- |
| `/opt/shared/python` | uv-managed Python, readable by every user |
| `/opt/shared/axlib` | uv project with a locked `.venv`, release wheels, and docs (`current/`) |
| `/usr/local/bin/axlib` | the `axlib` command |
| `/etc/axlib/axlib.toml`, `/etc/axlib/sqlite-aes256.key` | configuration and AES-256 key, `root:netops` |
| `/var/lib/axlib/credentials.db` | encrypted store, `root:netops 0660` |

`AXLIB_CONFIG_FILE` is set for all logins, so operators need no configuration. An existing configuration, key, or store is never overwritten, and rerunning a newer release's installer upgrades in place; previous wheels stay in `releases/` for rollback. See `docs/INSTALL.md`.

### Administrator and operator guides

`docs/INSTALL.md` now has two guides:

- **For administrators**: an `axuv` helper that runs uv on `/opt/shared/axlib` with the shared Python and `umask 022`; adding libraries such as netmiko and scrapli so every script can use them; pinning, upgrading, and removing libraries; tracking `pyproject.toml` and `uv.lock` in git for one-command undo; upgrades, rollback, backups, mirrors, and uninstalling. Libraries added this way survive axlib upgrades.
- **For network operations staff**: four ways to get credentials in a script: the shared interpreter (`#!/opt/shared/axlib/.venv/bin/python`, with netmiko and scrapli examples), a personal uv project or single-file uv script using the release wheel from `/opt/shared/axlib/releases/`, or the `NET*` variables from `netenv-set`. It also covers cron and systemd (use `ax.getkeys(getpass.getuser())` where `USER` may be unset) and rules for handling credentials.

Administrators should use `axuv` (or an equivalent `umask 022`) for every change to the shared environment. Running plain `uv` under a restrictive umask leaves new libraries unreadable for everyone except root, and the damage persists through reinstalls until the uv cache is cleaned. The guide's troubleshooting table gives the symptoms and the recovery steps.

### Compatibility

- The axlib package is unchanged from the previous release: no API, command, storage format, or dependency changes.
- Hosts that already have axlib installed can adopt the installer by running it. An existing configuration, key, or store is kept and only verified.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules, including docs code): passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     156 passed
shellcheck install.sh:                                 passed
install.sh on Ubuntu 24.04 (fresh, rerun, upgrade,
  rollback, non-root operator, login autoload):        passed
Shared libraries (netmiko, scrapli) as another user,
  kept across an axlib upgrade:                        passed
Personal uv project on the shared Python:              passed
```

## 1.0.2

### Overview

This release lets Network Operations staff load their network login into the shell when they SSH in. `axlib netenv-set` exports `NETUSER`, `NETPASS`, and `NETENABLE` from the operator's axlib record, and `axlib netenv-clear` removes them. Storage formats, `ax.getkeys()`, and the lookup precedence are unchanged.

### Session credentials

A program cannot change its parent shell's environment, so both commands print shell statements for the shell to `eval`:

```bash
eval "$(axlib netenv-set)"      # export NETUSER, NETPASS, NETENABLE
eval "$(axlib netenv-clear)"    # unset them
```

- `$USER` selects the record, as in `ax.getkeys()`. `--service NAME` loads another record.
- Existing `NET*` variables are ignored during the lookup, so a stale value never looks like a successful load.
- The shared fallback service is used only with `--allow-shared`, and always with a warning.
- A record without an enable secret unsets `NETENABLE`.
- Values are quoted with `shlex.quote`; standard output carries only shell code, and every message goes to standard error.
- On any failure nothing is printed on standard output and the environment is unchanged. Exit statuses: `0` exported, `1` no complete record, `2` configuration or store error, `3` refused because standard output is a terminal (which keeps the password off the screen).
- `--check` reports which fields would be set without showing values.

The commands are also available as `axlib-netenv-set`, `axlib-netenv-clear`, and `python -m axlib.credentials.netenv set|clear`.

### uv scripts and shell integration

- `scripts/netenv-set` and `scripts/netenv-clear` are uv inline-metadata scripts (`#!/usr/bin/env -S uv run --quiet --script`) that install axlib from the checkout they live in and run from any directory. Call them by their real path; a symlink moves the directory uv resolves the checkout from.
- `examples/netenv/netenv.sh` defines `netenv-set` and `netenv-clear` shell functions for bash and zsh and loads credentials when an interactive shell starts. Non-interactive sessions (`ssh host command`, `scp`, `sftp`, `rsync`) are left alone, `set -x` tracing is paused during the `eval`, and the functions return the command's real exit status. Install it in `~/.bashrc`, `~/.zshrc`, or `/etc/profile.d/`.

Read `docs/NETENV.md` before enabling this on a shared host. Exported passwords are visible to every program the shell starts, remain in long-running `tmux` sessions after a password change, and take precedence over the store in `ax.getkeys()`.

### Compatibility

- No storage format or schema changes, and no migrations.
- No new runtime dependencies.
- `ax.getkeys()`, `ax.getinfoblox()`, and existing commands are unchanged.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules, including scripts/):  passed
ty static type check:                                  passed
Educational docstring audit:                           passed
Main pytest suite:                                     156 passed
Measured source line coverage:                         82%
Source-distribution build:                             passed
Extracted source-distribution tests:                   156 passed
bash eval round trip, set -x, non-interactive guard:   passed
```

## 1.0.1

### Overview

This release adds a Python administration API for the encrypted credential stores and an optional full-screen credential manager (TUI) for operators who run `ax.getkeys()` scripts but do not write Python. Storage formats are unchanged: existing SQLite databases, encrypted text files, and key files work without any migration.

### Python administration API

`axlib.credentials.StoreAdmin` administers either durable store through one API: `status()`, `initialize()`, `add()`, `update()` (including removal of optional fields), `delete()`, `rotate_key()`, and `annotate()`. It normalizes service names exactly as `ax.getkeys()` looks them up, validates fields against a record profile, clears the service's Redis cache entry after every change, and never returns a secret value. Both CLIs and the TUI are thin layers over it.

`python -m axlib.credentials.admin` is a standalone health check (text or `--json`) that exits `0` only when every enabled store is ready.

### Record profiles

Profiles record which fields each lookup helper expects: `network` (`netuser`, `netpass`, optional `netenable`) for `ax.getkeys()` and `infoblox` (`ibgrid`, `ibuser`, `ibpass`) for `ax.getinfoblox()`. Infoblox records can now be provisioned from the command line or the TUI. `python -m axlib.credentials.profiles` lists them.

### Command-line changes

- `add` and `update` accept `--profile network|infoblox` (default `network`).
- `update --remove FIELD` deletes an optional field; required fields cannot be removed.
- `rotate-key` re-encrypts every record under a new AES-256 key (`--generate-key` or `--new-key-file`, with `--dry-run` and a required `--yes`).
- When a change is saved but the Redis cache cannot be cleared, the command now prints `status=...` and `cache=stale` before exiting `1`, so it no longer looks like a failed save.
- `axlib credential-db` and `axlib credential-file` now share one implementation; existing command lines and output are unchanged.

### Credential manager (TUI)

Install the optional extra and start the manager locally or over SSH:

```bash
uv add 'axlib[tui]'
axlib credential-tui --config /etc/axlib/axlib.toml
```

It lists services with notes explaining how `ax.getkeys()` will use them, and it adds, edits, and deletes records, initializes stores, switches between the SQLite and text-file stores, and rotates keys. Credential values are never displayed. The manager refuses to start when Textual keystroke logging (`TEXTUAL_LOG`, devtools, or debug mode) is enabled, blocks clipboard copies from password boxes, matches its colors to the terminal, and closes after five idle minutes. See `docs/CREDENTIAL_TUI.md`.

### Version numbering

The release number now lives only in `pyproject.toml` and is changed with `uv version --bump patch`. `axlib.__version__` reads it from the installed package metadata, and a test fails if the number is hard-coded anywhere else.

### Compatibility

- No storage format or schema changes, and no migrations.
- `ax.getkeys()`, `ax.getinfoblox()`, and the lookup precedence are unchanged.
- Runtime dependencies are unchanged (`cryptography`, `redis`). The optional `tui` extra adds `textual>=8.2.8` and `rich>=15.0.0`.
- `normalize_service_for_write` moved to `axlib.credentials.manager` and remains importable from `axlib.credentials.cli_common`.
- `RELEASE_NOTES.md` is now included in the source distribution.

### Validation

Validated on Python 3.14.3:

```text
Ruff format and lint (all rules):                    passed
ty static type check:                                passed
Educational docstring audit:                         passed
Main pytest suite:                                   137 passed
Measured source line coverage:                       82%
Source-distribution build:                           passed
Extracted source-distribution tests:                 137 passed
TUI in a pseudo-terminal (truecolor/256/16/NO_COLOR): passed
```

## 1.0.0

### Overview

This release establishes the first axlib-owned credential-storage formats and a common administration model for durable credential stores. The former `keyring` / `keyrings.cryptfile` backend has been removed. Axlib now provides its own AES-256-GCM encrypted text credential store alongside the existing AES-256-GCM SQLite store.

This is intentionally a breaking release. Credential-store migrations are not implemented: unsupported encrypted-text formats and unsupported SQLite schema versions fail clearly and must be deliberately recreated or reprovisioned by an operator.

### Backwards-compatible application lookup

Existing network automation can continue to use:

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

When no service is explicitly supplied, `ax.getkeys()` uses `$USER` for the operator lookup. `NETUSER`, `NETPASS`, and `NETENABLE` continue to override stored fields. If an individual operator is not enlisted or lacks the required login fields, the configured shared service remains the fallback.

The lookup order for missing fields is:

1. caller/environment values;
2. Redis, when enabled;
3. SQLite, when enabled;
4. the axlib AES-256-GCM encrypted text file, when enabled.

SQLite is therefore the preferred durable store when both durable stores are configured.

### Native AES-256-GCM credential file

The new `CredentialFileStore` is implemented entirely within axlib. It does not import or depend on `keyring` or `keyrings.cryptfile`, does not read legacy cryptfile files, and does not support AES-128.

The format uses:

- AES-256-GCM from `cryptography`;
- a 32-byte URL-safe Base64 encryption key;
- a fresh 12-byte nonce for each encrypted service write;
- authenticated associated data that binds ciphertext to the normalized service identity;
- an encrypted key-check value so an incorrect key is rejected before normal credential use;
- an INI-like outer container with version/cipher metadata and encrypted service payloads;
- JSON serialization inside each encrypted service payload so both field names and field values are encrypted.

Text-file writes are protected by a sibling lock file and POSIX advisory locking. Mutations write a complete temporary sibling file, flush it, atomically replace the live credential file, and apply configured ownership/mode policy. This avoids lost updates when multiple Network Operations users administer the same shared store.

### Parallel durable-store APIs

`CredentialFileStore` and `SQLiteCredentialStore` use the same operational model:

- `initialize()` — create a new current-format store, or verify an existing current-format store;
- `create()` — create a service and fail if it already exists;
- `read()` — return requested decrypted fields;
- `update()` — modify fields and fail if the service does not exist;
- `write()` — atomically create a service when absent or merge fields when present;
- `delete()` — remove selected fields;
- `delete_service()` — remove the complete service record;
- `list_records()` — return service names, field names, and safe metadata without exposing secret values.

The text store additionally provides dependency-free convenience methods `get_password()`, `set_password()`, and `delete_password()` for greenfield scripts that prefer the familiar service/field calling style.

Detailed examples for every public method are in `docs/CREDENTIAL_FILE.md` and `docs/SQLITE_CREDENTIALS.md`.

### Standardized administration commands

The two durable stores now have parallel command-line interfaces:

```text
axlib credential-file init
axlib credential-file add
axlib credential-file update
axlib credential-file delete
axlib credential-file list

axlib credential-db init
axlib credential-db add
axlib credential-db update
axlib credential-db delete
axlib credential-db list
```

Standalone entry points are also installed:

```text
axlib-credential-file
axlib-credential-db
```

Both administration CLIs support the same input patterns where applicable:

```text
--set FIELD=VALUE
--from-env FIELD=ENV_VAR
--prompt FIELD
--dry-run
```

Listings expose only service/field names and safe timestamps. Credential values are not printed. Successful durable-store modifications invalidate the affected Redis cache entry when Redis caching is enabled.

### Configuration changes

The encrypted text store uses the new `[credential_file]` configuration section. Historical `keyring_file`, `keyring_key`, `keyring_key_file`, `AXLIB_KEYRING_*`, and `axlib-set-keys` interfaces have been removed.

Shared-host example:

```toml
[credentials]
shared_service = "network-shared"

[credential_file]
enabled = true
file = "/var/lib/axlib/credentials.axc"
key_file = "/etc/axlib/credential-file.key"
lock_timeout = 5.0
file_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true

[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite-aes256.key"
timeout = 5.0
database_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true
```

For both stores, relative data/key paths in TOML are resolved relative to the selected configuration file. Key files may live independently of the database/credential-file path.

Direct key injection is also supported through the environment; direct encryption keys are not read from TOML:

```text
AXLIB_CREDENTIAL_FILE_KEY
AXLIB_SQLITE_KEY
```

The corresponding key-file variables are:

```text
AXLIB_CREDENTIAL_FILE_KEY_FILE
AXLIB_SQLITE_KEY_FILE
```

### Shared and private permission models

The shared-server defaults are intended for operators in the `netops` group:

- encrypted credential file: `0660`;
- credential-file lock file: `0660`;
- SQLite database: `0660`;
- encryption key files: `0640`;
- configured group: `netops`.

Private deployments can select `0600` modes and disable group enforcement by configuring an empty group. Existing symlinks and non-regular sensitive paths are rejected.

### Storage-version policy: no migrations

This release deliberately does not contain credential-store migration code.

For the encrypted text store, `initialize()` creates the current format when the file is absent and verifies the exact supported format/version/cipher when it exists. Legacy cryptfile data, AES-128 data, unknown versions, and alternate formats are rejected.

For SQLite, `initialize()` creates the current schema for a new database and verifies the exact current schema version for an existing database. Older or newer schema versions are rejected rather than upgraded or rewritten.

This policy keeps the initial production formats and educational code straightforward. Migration logic can be added later only if an operator explicitly requires it.

### Dependency changes

Runtime dependencies are now:

```text
cryptography>=42
redis>=5
```

Removed dependencies:

```text
keyring
keyrings.cryptfile
```

There is no optional keyring adapter and no keyring entry-point registration.

### Validation

The final release was validated on Python 3.13.5:

```text
Source compilation:                         passed
Educational docstring audit:                passed
Main pytest suite:                          78 passed
Measured source line coverage:              76%
Wheel build:                                passed
Source-distribution build:                  passed
Wheel metadata/dependency audit:            passed
Wheel entry-point audit:                    passed
Clean installed-wheel credential-file use: passed
Installed ax.getkeys() lookup:              passed
Installed shared-service fallback:          passed
Text-store convenience password methods:    passed
Extracted source-distribution tests:         78 passed
Unsupported text format/version rejection:  passed by test suite
Unsupported SQLite schema rejection:        passed by test suite
```

A live Redis service was not required for release validation. Redis behavior, precedence, and invalidation boundaries are exercised through the test suite without placing production secrets in test output.
