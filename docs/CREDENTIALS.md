# axlib credentials

Axlib gives Network Operations scripts one lookup API while allowing two AES-256-GCM durable stores. Existing operator scripts may continue to use:

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

When no service is passed, `getkeys()` uses `$USER` (when `$USER` is unset, the shared service, else the service `default`). `NETUSER`, `NETPASS`, and `NETENABLE` override stored values; a variable that is empty or only spaces counts as unset. If the lookup still lacks `netuser` or `netpass`, a shared service is configured (`credentials.shared_service` or `AXLIB_SHARED_SERVICE`), and the service looked up was not already the shared service, `getkeys()` prints a warning to standard error and returns the shared service's values instead. A missing `netenable` alone never triggers the fallback. Without a shared service, `getkeys()` warns and returns what it found, with `None` for missing fields.

## Lookup precedence

For each missing field, axlib uses this order:

1. Caller/environment value.
2. Redis, when enabled.
3. SQLite AES-256-GCM store, when enabled.
4. AES-256-GCM encrypted text-file store, when enabled.

Values found in a durable store are written back to Redis when it is enabled. Backend failures are reported on standard error, prefixed `***AX:`, without printing credential values, and the next configured durable store may still satisfy the lookup.

Service names are normalized before every lookup, and before every write through the administration CLIs, the Python administration API, and the TUI: dots, dashes, and underscores are removed, so `first.last`, `first-last`, and `firstlast` all name the record `firstlast`. Choose service names that stay distinct after this rule. (The store classes themselves write a name exactly as given.) The administration tools also refuse a blank name, a name containing whitespace, and a name made only of `.`, `-`, and `_`.

## Lookup helpers

The package root offers these read-only helpers. Missing fields come back as `None`.

| Function | Returns | Lookup |
| --- | --- | --- |
| `ax.getkeys(service=None)` | `(netuser, netpass, netenable)` | Service defaults to `$USER`; `NETUSER`, `NETPASS`, `NETENABLE` override; shared-service fallback as described above. |
| `ax.getnetkeys(service=None)` | `(netuser, netpass, netenable)` | The same lookup for one service, with no fallback. Service defaults to the shared service, else `default`. |
| `ax.getinfoblox(service=None)` | `(ibgrid, ibuser, ibpass)` | Service defaults to `infoblox`; `IBGRID`, `IBUSER`, `IBPASS` override; no fallback. |
| `ax.readkeyring(**fields)` | `dict` of the requested fields | Keyword arguments only: `service=` names the service (default `default`). Fields passed with a value other than `None` are kept; the rest are filled from Redis and the stores. |

`ax.updatedict(dict1, dict2)` fills the `None` values in `dict1` from `dict2` and returns `dict1`. Two more names remain in `axlib.secrets` for old scripts: `servicenamefilter(service)` returns the normalized service name (`first.last` becomes `firstlast`), and `printkv(mapping)` prints every key and value, so never pass it credentials.

These helpers load their settings through `axlib.config`, once, when they are first used, from `AXLIB_CONFIG_FILE` or the install record (see [Configuration](#configuration)); they take no configuration-file argument. To use another file in a script, set `AXLIB_CONFIG_FILE` before the first credential call, or call `axlib.config.refresh("/path/to/axlib.toml")`. An invalid configuration, such as a selected TOML file that does not exist or cannot be read, raises `axlib.credentials.CredentialConfigurationError`. Backend failures (an unreachable Redis server, a wrong key, an unreadable store) are only reported, as described above.

## Checking a lookup (`axlib credentials`)

`axlib credentials` runs the same lookup as `ax.getkeys()` or `ax.getinfoblox()` and prints only whether each field was found:

```console
$ axlib credentials network --service first.last
netuser=set
netpass=set
netenable=missing
$ axlib credentials network --service lab.user
***AX: WARNING: individual network credentials are incomplete for service 'lab.user'; trying configured shared service 'network-shared'.
netuser=set
netpass=set
netenable=set
```

`network` checks `netuser`, `netpass`, and `netenable`, including the shared-service fallback; `--service` defaults to `$USER`. `infoblox` checks `ibgrid`, `ibuser`, and `ibpass`; `--service` defaults to `infoblox`. The exit status is `0` when `netuser` and `netpass` (for `infoblox`, all three fields) were found, and `1` when one is missing. When the check cannot run, for example because the configuration file does not exist or cannot be read, the command prints one line to standard error, starting with `axlib credentials:`, and exits with status `2`. Warnings, such as the switch to the shared service, also go to standard error. The command has no `--config` option: it uses `AXLIB_CONFIG_FILE`, else the install record. `python -m axlib.secrets` runs the same command.

## Configuration

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

[redis]
enabled = false
host = "localhost"
port = 6379
db = 15
tls = false
connect_timeout = 2.0
socket_timeout = 2.0
cache_ttl = 900
key_prefix = "axlib"
```

[examples/axlib.toml.example](../examples/axlib.toml.example) is the complete sample to copy. Its `[textfsm]` table belongs to `axlib parse` (see [PARSING.md](PARSING.md)). Every credential setting is listed in the [Settings reference](#settings-reference) below.

axlib reads one TOML file and never searches directories for it. It uses the first of:

1. the file passed to `load_settings(path)` or to a command's `--config`,
2. the file named by `AXLIB_CONFIG_FILE`,
3. `CONFIG_FILE` in `/etc/axlib/install.env`, which `install.sh` writes on a shared host (see [INSTALL.md](INSTALL.md)).

`axlib.credentials.default_config_file()` returns the file that steps 2 and 3 select. With no file, settings come from `AXLIB_*` variables and built-in defaults alone. A file that is selected but does not exist is an error (`Credential configuration file does not exist: ...`), whichever step selected it. So is a file that exists but cannot be read, with the reason in parentheses: for example, `AXLIB_CONFIG_FILE=/etc/axlib/axlib.toml` for a user outside the group of `/etc/axlib` (mode `2750`) gives `Credential configuration file cannot be read: /etc/axlib/axlib.toml (Permission denied)`. An install record that the user cannot read is treated as absent.

Among the credential tools, `--config` is accepted by `axlib credential-db`, `axlib credential-file`, `axlib credential-tui`, `axlib netenv-set`, and `python -m axlib.credentials.admin`. `axlib credentials` and the lookup helpers have no such option.

Relative `credential_file.file`, `credential_file.key_file`, `sqlite.database`, and `sqlite.key_file` values are resolved relative to the directory of the selected TOML file. The same paths given through environment variables are relative to the current directory, and `~` is expanded in every path. The Redis `ca_certs`, `certfile`, and `keyfile` paths are not resolved against the TOML file, so give them as absolute paths. Direct AES keys and the Redis password are never loaded from TOML.

The direct key variables must contain URL-safe Base64 that decodes to exactly 32 bytes. Key files use the same representation. When both a direct key variable and a key file are configured, the variable wins.

### Settings reference

Every TOML key has an environment variable that replaces it. A variable that is set, even to an empty string, replaces the TOML value, so unset a variable rather than emptying it to fall back to the file. Booleans accept `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0`, and `enable`/`disable` (or `enabled`/`disabled`); any other value is a configuration error. "none" in the tables below means the setting is unset.

`[credentials]`

| Key | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `shared_service` | `AXLIB_SHARED_SERVICE` | none | Service that `ax.getkeys()` falls back to. |

`[credential_file]`: the encrypted text store.

| Key | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `AXLIB_CREDENTIAL_FILE_ENABLE` | `false` | Include this store in lookups. Requires `file` and a key source. |
| `file` | `AXLIB_CREDENTIAL_FILE` | none | Encrypted text file. A lock file, `<file>.lock`, is kept beside it. |
| `key_file` | `AXLIB_CREDENTIAL_FILE_KEY_FILE` | none | File holding the AES-256 key. Must not be the same path as `file`. |
| (environment only) | `AXLIB_CREDENTIAL_FILE_KEY` | none | The key itself, used instead of `key_file`. |
| `lock_timeout` | `AXLIB_CREDENTIAL_FILE_LOCK_TIMEOUT` | `5.0` | Seconds to wait for the file lock; must be greater than 0. |
| `file_mode` | `AXLIB_CREDENTIAL_FILE_MODE` | `"0660"` | Mode of the text file and its lock file. |
| `key_file_mode` | `AXLIB_CREDENTIAL_FILE_KEY_FILE_MODE` | `"0640"` | Mode of the key file. |
| `owner` | `AXLIB_CREDENTIAL_FILE_OWNER` | none | Owner account enforced on these files. |
| `group` | `AXLIB_CREDENTIAL_FILE_GROUP` | `"netops"` | Group enforced on these files; `""` for none. |
| `enforce_permissions` | `AXLIB_CREDENTIAL_FILE_ENFORCE_PERMISSIONS` | `true` | Check and correct existing files before use (see below). |

`[sqlite]`: the encrypted SQLite store, checked before the text store.

| Key | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `AXLIB_SQLITE_ENABLE` | `false` | Include this store in lookups. Requires `database` and a key source. |
| `database` | `AXLIB_SQLITE_DATABASE` | none | SQLite database file. |
| `key_file` | `AXLIB_SQLITE_KEY_FILE` | none | File holding the AES-256 key. Must not be the same path as `database`. |
| (environment only) | `AXLIB_SQLITE_KEY` | none | The key itself, used instead of `key_file`. |
| `timeout` | `AXLIB_SQLITE_TIMEOUT` | `5.0` | Seconds to wait for another writer's lock; must be greater than 0. |
| `database_mode` | `AXLIB_SQLITE_DATABASE_MODE` | `"0660"` | Mode of the database. |
| `key_file_mode` | `AXLIB_SQLITE_KEY_FILE_MODE` | `"0640"` | Mode of the key file. |
| `owner` | `AXLIB_SQLITE_OWNER` | none | Owner account enforced on these files. |
| `group` | `AXLIB_SQLITE_GROUP` | `"netops"` | Group enforced on these files; `""` for none. |
| `enforce_permissions` | `AXLIB_SQLITE_ENFORCE_PERMISSIONS` | `true` | Check and correct existing files before use (see below). |

`[redis]`: the optional plaintext cache.

| Key | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `AXLIB_REDIS_ENABLE` | `false` | Use Redis as a cache in front of the stores. |
| `host` | `AXLIB_REDIS_HOST` | `"localhost"` | Redis host name or address. |
| `port` | `AXLIB_REDIS_PORT` | `6379` | TCP port, 1 to 65535. |
| `db` | `AXLIB_REDIS_DB` | `15` | Logical database number. |
| `username` | `AXLIB_REDIS_USERNAME` | none | ACL user name. |
| (environment only) | `AXLIB_REDIS_PASSWORD` | none | ACL password. |
| `tls` | `AXLIB_REDIS_TLS` | `false` | Connect with TLS. The next four settings apply only with TLS. |
| `ca_certs` | `AXLIB_REDIS_CA_CERTS` | none | CA bundle that verifies the server certificate. |
| `certfile` | `AXLIB_REDIS_CERTFILE` | none | Client certificate for mutual TLS. |
| `keyfile` | `AXLIB_REDIS_KEYFILE` | none | Private key for `certfile`. |
| `cert_reqs` | `AXLIB_REDIS_CERT_REQS` | `"required"` | Server certificate check: `required`, `optional`, or `none`. |
| `connect_timeout` | `AXLIB_REDIS_CONNECT_TIMEOUT` | `2.0` | Seconds allowed to connect; must be greater than 0. |
| `socket_timeout` | `AXLIB_REDIS_SOCKET_TIMEOUT` | `2.0` | Seconds allowed per Redis command; must be greater than 0. |
| `cache_ttl` | `AXLIB_REDIS_CACHE_TTL` | `900` | Seconds a cached value lives; `0` sets no expiry. |
| `key_prefix` | `AXLIB_REDIS_KEY_PREFIX` | `""` | Namespace: each service is cached in the hash `<prefix>:<service>`, or `<service>` without a prefix. Leading and trailing colons are removed. |

### Redis cache

Redis holds plaintext values, so it is off by default and is never the source of truth. Each service is one Redis hash whose fields are the credential fields. Lookups read missing fields from it first and write back fields found in a durable store. While the cache is enabled, every `add`, `update`, and `delete` through the CLIs, the Python administration API, or the TUI deletes that service's hash so a changed password is not served from the cache. If Redis cannot be reached, lookups report it and continue with the durable stores. Use TLS (`tls = true`) and an ACL user for a Redis server on another host.

Both stores need the `cryptography` package, and the cache needs `redis`; installing axlib installs both. The `redis` package is imported only when the cache is enabled.

### The `axlib.config` module

Older scripts read settings as attributes of `axlib.config`. There is one attribute per setting, named after its table and key (`sqlite_timeout`, `redis_host`, `credential_file_lock_timeout`). The exceptions are `shared_service` for `credentials.shared_service`; `credential_file` and `credential_file_mode` for `credential_file.file` and `credential_file.file_mode`; `credential_file_enable`, `sqlite_enable`, and `redis_enable` for `enabled`; and `credential_file_key`, `sqlite_key`, and `redis_password` for the environment-only values. Paths are strings and modes are integers.

The module loads the settings when it is first imported, which happens the first time a script uses a lookup helper. `axlib.config.refresh(config_file=None, *, environ=None)` loads them again (after `AXLIB_CONFIG_FILE` changes, for example) and returns the new `CredentialSettings`. `axlib.config.as_settings()` builds a validated `CredentialSettings` from the current attributes; the lookup helpers call it on every lookup, so a test can assign an attribute and the next lookup uses it. New code should call `axlib.credentials.load_settings()` instead.

## Shared Linux host protection

The default policy for both stores is designed for operators in group `netops`:

- durable data file/database: `0660`
- encryption key file: `0640`
- group: `netops`

A private deployment can use `0600` for both and set `group = ""`.

Modes are octal strings such as `"0660"`. A TOML integer is read as the mode number itself, so write `0o660`, not `660`. A mode may not grant any access to other users or any execute bit, and it must let the owner read. The text file and the database must also let the owner write; a key file may be read-only, such as `0440`. A mode that breaks these rules is a configuration error.

With `enforce_permissions = true` (the default), axlib checks the text file and its lock file, or the database, and the key file before each use, and changes a mode, owner, or group that differs from the settings. Only a file's owner (or root) can change it, so a shared store should already have the right protection; otherwise the command fails with an error. With `enforce_permissions = false`, existing files are used as they are, but files axlib creates still get the configured protection. Symlinks and anything other than a regular file are refused either way.

A typical shared directory setup is:

```bash
sudo install -d -o root -g netops -m 2770 /var/lib/axlib
sudo install -d -o root -g netops -m 2750 /etc/axlib
```

`install.sh` creates both directories this way, with the group from its `--group` option. Group members need write access to the data directory because SQLite creates its journal beside the database and the text store replaces its file there. The setgid bit (`2xxx`) gives new files the directory's group. When `init` has to create a missing parent directory itself, it uses `2770` for a group-writable store, `2750` for a group-readable store or a key file, and `0700` otherwise (no group, or a mode without group access); an existing directory is never changed.

## Standardized administration CLI

The text and SQLite stores intentionally use the same verbs:

```bash
axlib credential-file init --generate-key
axlib credential-file add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable
axlib credential-file update --service "$USER" --prompt netpass
axlib credential-file list
axlib credential-file delete --service "$USER" --yes
axlib credential-file rotate-key --generate-key --yes

axlib credential-db init --generate-key
axlib credential-db add --service "$USER" --set netuser="$USER" --prompt netpass --prompt netenable
axlib credential-db update --service "$USER" --prompt netpass
axlib credential-db list
axlib credential-db delete --service "$USER" --yes
axlib credential-db rotate-key --generate-key --yes
```

Each store's own page covers its command in more detail: [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md#command-line) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md#command-line).

`--config FILE` belongs to `credential-file` and `credential-db` themselves, so it goes before the verb: `axlib credential-db --config /etc/axlib/axlib.toml list`.

Both CLIs also support `--from-env FIELD=ENV_VAR`, `--dry-run` where applicable, and `list --json`. Commands print field names but never credential values; the only value a listing shows is the record's non-secret note (see [Notes](#notes)).

`add` and `update` take values from `--set FIELD=VALUE`, `--from-env FIELD=ENV_VAR`, and `--prompt FIELD` (typed with echo off). Each may be repeated. When one field is given more than once, `--prompt` wins over `--from-env`, which wins over `--set`. Prefer `--prompt` or `--from-env` for passwords, because shell history often keeps `--set` values. Output is `key=value` lines:

```console
$ axlib credential-db add --service first.last --set netuser=first.last --from-env netpass=NETPASS_NEW
action=add
service=firstlast
fields=netpass,netuser
status=added
$ axlib credential-db list
SERVICE        FIELDS                          CREATED_UTC           UPDATED_UTC           NOTE
firstlast      netpass,netuser                 2026-10-20T07:13:42Z  2026-10-20T07:13:42Z
networkshared  netenable,netpass,netuser,note  2026-10-20T07:13:42Z  2026-10-20T07:13:42Z  NOC team account; owner J. Smith
```

`--dry-run` (on `add`, `update`, `delete`, and `rotate-key`) validates the request and prints the plan followed by `dry_run=true` without changing the store. `delete` and `rotate-key` refuse to run without `--yes`.

`update --remove FIELD` deletes an optional field such as `netenable`; required fields cannot be removed (delete the whole service instead). If a change is saved but the Redis cache cannot be cleared, the command prints `status=...` and `cache=stale`, explains on standard error how long the old cached value may be used, and exits with status 1.

`init` without `--generate-key` creates a missing store with the configured key, or verifies an existing one (format, key, and file protection); it never migrates or overwrites anything. `init --generate-key` first creates the configured key file with a random key. It refuses to replace an existing key file, and it is refused while `AXLIB_SQLITE_KEY` (for `credential-db`) or `AXLIB_CREDENTIAL_FILE_KEY` (for `credential-file`) is set, because that variable would take precedence over the new file.

`rotate-key` re-encrypts every record and the store's key-check marker under a new AES-256 key, replacing the configured key file only after the durable store has already been updated. `--generate-key` makes a random key; `--new-key-file PATH` uses a key produced elsewhere (the file is read and checked even with `--dry-run`). Rotation requires a key file and is refused while the store's direct key variable is set, because axlib cannot update the calling shell's environment. It is also refused while a `<key_file>.rotating` file from an interrupted rotation exists. `--dry-run` makes the same checks and fails with the same message and exit status as a real run. See [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md#rotate_key) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md#rotate_key) for the interrupted-rotation recovery path.

### Output and exit statuses

Every verb except `list` prints `key=value` lines on standard output:

| Verb | Lines |
| --- | --- |
| `init` | `status=initialized` |
| `add`, `update` | `action=`, `service=` (normalized), `fields=` (names set), `removed=` (names removed, `update` only), then `status=added` or `status=updated` |
| `delete` | `action=delete`, `service=`, then `status=deleted` or `status=not-found` |
| `rotate-key` | `action=rotate-key`, `records=` (count), then `status=rotated` and `rotated=` (count re-encrypted) |

A line with an empty value is left out. A dry run ends with `dry_run=true` instead of the `status=` line, and a change whose Redis cache could not be cleared adds `cache=stale`.

`list` prints the columns `SERVICE`, `FIELDS` (comma-separated names), `CREATED_UTC`, `UPDATED_UTC`, and `NOTE`, sorted by service. `list --json` prints an array of objects with the keys `service`, `fields` (a list), `created_at`, `updated_at`, and `note` (`null` without a note). Timestamps are UTC, such as `2026-10-20T07:13:42Z`.

Exit statuses:

| Status | Meaning |
| --- | --- |
| `0` | Success, including a dry run. |
| `1` | The store could not be opened or changed (configuration, key, permissions, or format); `add` of a service that already exists; `update` of a service that does not exist; `delete` of a service that does not exist (`status=not-found`); or a change saved with `cache=stale`. The message goes to standard error, prefixed `axlib-credential-db:` or `axlib-credential-file:`. |
| `2` | The command line was wrong: an unknown option, an invalid service or field name, a field the profile does not allow, a missing required field, removing a required field, a refused note, a missing `--yes`, an unset `--from-env` variable, an empty prompted value, or input that ends before a prompted value is entered. Usage and the message go to standard error. |

## Record profiles

A profile lists the fields one lookup helper expects. `add` and `update` accept `--profile` (default `network`):

| Profile | Fields (required in bold) | Read by |
| --- | --- | --- |
| `network` | **netuser**, **netpass**, netenable, note | `ax.getkeys()` |
| `infoblox` | **ibgrid**, **ibuser**, **ibpass**, note | `ax.getinfoblox()` |

```bash
axlib credential-db add --service infoblox --profile infoblox \
    --set ibgrid=gm.example.net --set ibuser=api --prompt ibpass
python -m axlib.credentials.profiles --json
```

### Notes

Every record may have a `note`: one line of up to 120 characters for the
people who manage the store, such as who owns a shared account or when a lab
account expires. No lookup helper reads it, so a note never changes what
`ax.getkeys()` returns, and `axlib netenv-set` never exports it. It is
encrypted with the other fields but is not secret, so listings show it:

```bash
axlib credential-db update --service network-shared --set note="NOC team account; owner J. Smith"
axlib credential-db update --service network-shared --remove note
axlib credential-db list               # NOTE is the last column
axlib credential-db list --json        # "note": "..." or null
```

A note is stripped of leading and trailing spaces. A blank note, a note over
120 characters, and a note with a line break, tab, or other non-printable
character are refused. `python -m axlib.credentials.profiles` marks the note `LOOKUP no`.

## Python administration API

`axlib.credentials.StoreAdmin` is the programming interface behind both CLIs and the TUI. It normalizes service names exactly as `ax.getkeys()` looks them up, validates fields against a profile, clears the Redis cache after every change, and never returns a secret value.

```python
from axlib.credentials import INFOBLOX_PROFILE, StoreAdmin, StoreKind, load_settings

admin = StoreAdmin(load_settings("/etc/axlib/axlib.toml"), StoreKind.SQLITE)

status = admin.status()  # safe summary; errors are reported, not raised
if status.can_initialize:
    admin.initialize(generate_key=status.can_generate_key)

result = admin.add("first.last", {"netuser": "first.last", "netpass": password})
result.service  # 'firstlast'
result.cache_error  # None, or why Redis could not be cleared

admin.update("firstlast", {"netpass": new_password}, remove=["netenable"])
admin.add(
    "infoblox",
    {"ibgrid": "gm", "ibuser": "api", "ibpass": api_password},
    profile=INFOBLOX_PROFILE,
)

for item in admin.annotate(admin.list_records(), operator="first.last"):
    print(item.record.service, [note.text for note in item.notes])

admin.delete("firstlast")
admin.rotate_key()  # new random key; returns the record count
```

`StoreKind.SQLITE` (`"db"`) and `StoreKind.FILE` (`"file"`) select the store. `StoreAdmin` methods:

| Method | Result |
| --- | --- |
| `status()` | `StoreStatus`; problems are reported in its `error`, not raised. |
| `initialize(*, generate_key=False)` | Creates or verifies the store, as `init` does; `generate_key=True` first creates the configured key file. |
| `list_records()` | Records sorted by service, each with `service`, `fields`, `created_at`, `updated_at`, and `visible` (holding the `note`, if any). |
| `annotate(records, *, operator=None)` | One `AnnotatedRecord` per record, in the same order. |
| `read_non_secret(service, profile)` | `dict` of the fields the profile does not mark secret, such as `netuser` or `ibgrid`. |
| `add(service, values, *, profile=NETWORK_PROFILE)` | `ChangeResult`; the record must not exist yet. |
| `update(service, values=None, *, remove=(), profile=NETWORK_PROFILE)` | `ChangeResult`; sets `values`, then removes the `remove` fields. |
| `delete(service)` | `ChangeResult` for the whole record. |
| `rotate_key(new_key=None)` | Number of records re-encrypted; `None` generates a random key, or pass your own 32 bytes. |
| `check_key_rotation()` | Raises the `CredentialConfigurationError` that `rotate_key()` would raise for a set direct key variable, a missing key file, or a leftover `<key_file>.rotating`, and changes nothing. `rotate-key --dry-run` uses it. |
| `load_key_file(path)` | The 32-byte key read and checked from a key file, for `rotate_key()`. |

`ChangeResult` has `service` (normalized), `action` (`"added"`, `"updated"`, or `"deleted"`), `fields` and `removed` (field names), and `cache_error` (`None`, or why Redis could not be cleared; the change is saved either way).

`StoreStatus` has `kind`, `enabled`, `data_path`, `data_exists`, `key_source` (`"environment"`, `"file"`, or `"none"`), `key_file`, `key_file_exists`, `record_count` (`None` when the store could not be read), and `error`. Its properties are `ready` (the store opened and decrypted), `state` (`"ready"`, `"not-configured"`, `"not-initialized"`, or `"error"`), `can_initialize` (a path is configured and the store is not ready), and `can_generate_key` (a key file is configured, does not exist yet, and no direct key variable is set). `to_dict()` returns the JSON form described below.

`AnnotatedRecord` has `record`, `profile` (the profile whose fields best match the record), `notes`, and `note` (the record's own note text, or `None`). Each entry of `notes` is a `RecordNote` with a `kind` (`NoteKind`) and a `text`:

| `NoteKind` | `text` example | Meaning |
| --- | --- | --- |
| `OPERATOR` | `you` | The record belongs to `operator`. |
| `SHARED` | `shared fallback` | The record is the configured shared service. |
| `INCOMPLETE` | `missing netpass` | A required field of its profile is absent. |
| `OVERRIDDEN` | `SQLite overrides netpass` | A text-store record whose fields an enabled SQLite store also holds for the same service, so lookups use the SQLite values. |

Invalid names, fields, or notes raise `ValueError`; `add` of an existing service raises `CredentialRecordExistsError`; `update` or `delete` of a missing one raises `CredentialRecordNotFoundError`; other store problems raise `CredentialError`. All of these exception classes are in `axlib.credentials` (see [Python API reference](#python-api-reference)).

Run the module directly for a health check that suits cron or monitoring:

```bash
python -m axlib.credentials.admin --config /etc/axlib/axlib.toml
python -m axlib.credentials.admin --store db
python -m axlib.credentials.admin --json | jq '.[] | select(.ready | not)'
```

It prints a table with the columns `STORE` (`db` or `file`), `ENABLED`, `STATE` (`ready`, `not-configured`, `not-initialized`, or `error`), `RECORDS`, `KEY` (`environment`, `file`, or `none`), and `PATH`, and writes each store's error to standard error. `--json` prints an array with one object per store, with the keys `store`, `label` (`SQLite` or `text file`), `enabled`, `state`, `ready`, `records`, `data_path`, `key_source`, `key_file`, `key_file_exists`, and `error`. It exits `0` only when every enabled store is ready. With `--store db` or `--store file`, only that store is checked, even when it is disabled. When no store is enabled, or the configuration cannot be loaded (the message goes to standard error), the exit status is `1`.

## Python API reference

`axlib.credentials` exports the names below. Importing it opens no store, file, or network connection.

Settings:

- `CredentialSettings`: the immutable settings object. Its attributes follow the [`axlib.config` names](#the-axlibconfig-module), except that the `enabled` settings end in `_enabled` (`sqlite_enabled`) and paths are `pathlib.Path` objects.
- `load_settings(config_file=None, *, environ=None)`: read the TOML file and the environment as described in [Configuration](#configuration) and return a validated `CredentialSettings`.
- `default_config_file(environ=None)`: the file named by `AXLIB_CONFIG_FILE`, else the install record's `CONFIG_FILE`, else `None`.
- `validate_settings(settings)`: check the range and consistency rules (an enabled store has a path and a key source, timeouts are greater than 0, the Redis port and TLS policy are valid) and return the same object, or raise `CredentialConfigurationError`.
- `parse_file_mode(value, *, name, default, require_owner_write)`: apply the mode rules in [Shared Linux host protection](#shared-linux-host-protection) and return the mode as an integer.

Lookup:

- `lookup_values(service="default", values=None, *, settings, cache=None, store=None, reporter=...)`: fill the `None` values of `values` (field names mapped to a value or `None`) from Redis and the enabled stores, and return them as a new `dict`. `store` replaces the store chain with one store of your own, `cache` replaces the Redis cache (used only when Redis is enabled), and `reporter` receives each warning (by default printed to standard error).
- `merge_missing(destination, source)`: fill the `None` values of `destination` from `source`, in place, and return `destination`.
- `normalize_legacy_service_name(service)`: the lookup rule for service names (remove `.`, `-`, and `_`).
- `normalize_service_for_write(service)`: the same rule plus the checks for writes; raises `ValueError`.

Stores, keys, and cache (examples for every store method are in [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md)):

- `SQLiteCredentialStore(settings)` and `CredentialFileStore(settings)`, whose listings return `SQLiteCredentialRecord` and `CredentialFileRecord` objects (`service`, `fields`, `created_at`, `updated_at`, `visible`).
- `generate_sqlite_key_file(path, ...)` and `generate_credential_file_key_file(path, ...)`: write a new random key file to `path`.
- `read_sqlite_key(path)` and `read_credential_file_key(path)`: read and check a key file and return the 32-byte key.
- `RedisCredentialCache(settings)`: the cache, used in a `with` statement, with `read(service, fields)`, `write(service, values)`, and `delete(service, fields=None)`.

Administration: `StoreAdmin`, `StoreKind`, `StoreStatus`, `ChangeResult`, `AnnotatedRecord`, `RecordNote`, and `NoteKind` (see [Python administration API](#python-administration-api)); `configured_kinds(settings)` returns the stores that have a path configured, SQLite first; `prepare_update(values, remove, profile)` validates an update without touching a store and returns the values and the sorted names to remove, or raises `ValueError`.

Profiles:

- `FieldSpec(name, label, secret, required, lookup=True)`: one field; `lookup` is `False` only for the note.
- `RecordProfile(name, label, fields)`: one profile, with the properties `field_names`, `required_names`, and `lookup_names`.
- `NETWORK_PROFILE`, `INFOBLOX_PROFILE`, and `PROFILES`, a read-only mapping from profile name to profile.
- `get_profile(name)`: the profile with that name; raises `ValueError` for an unknown name.
- `validate_values(values, profile, *, creating)`: check a field mapping against a profile (with `creating=True`, every required field must be present) and return a copy with the note stripped, or raise `ValueError`.
- `validate_note(note)` and `NOTE_MAX_LENGTH` (`120`) are in `axlib.credentials.profiles`.

Exceptions:

```text
CredentialError (a RuntimeError)
├── CredentialConfigurationError    missing or invalid settings
├── CredentialDependencyError       a needed package (cryptography, redis) is missing
└── CredentialBackendError          Redis or a store could not complete the work
    ├── CredentialRecordExistsError     add of a service that already exists
    └── CredentialRecordNotFoundError   update or delete of a service that does not exist
```

## Credential manager (TUI)

`axlib credential-tui` is an optional full-screen manager for the same stores, intended for operators who use `ax.getkeys()` scripts but do not write Python. Install it with `uv add 'axlib[tui]'`; see [CREDENTIAL_TUI.md](CREDENTIAL_TUI.md).

## Session environment (`netenv-set`)

`axlib netenv-set` exports an operator's `netuser`, `netpass`, and `netenable` as `NETUSER`, `NETPASS`, and `NETENABLE` in the calling shell (via `eval`), and `axlib netenv-clear` unsets them. Once they are exported, `ax.getkeys()` returns them before consulting any store. See [NETENV.md](NETENV.md).

## Storage format policy

Axlib intentionally contains no storage migration framework. The encrypted text file accepts only axlib text-format version 1 with AES-256-GCM. SQLite accepts only its current axlib application ID and schema version. An unsupported version fails with an operator-facing error; recreate or reprovision the store deliberately rather than relying on application startup to modify it. Key rotation is a separate, supported operation: it re-encrypts current-format data under a new key rather than changing the format or schema, so it does not conflict with this no-migration policy.

See [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md) for full Python API examples and each store's command line.
