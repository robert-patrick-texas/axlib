# AES-256-GCM SQLite credential store

`SQLiteCredentialStore` stores one encrypted JSON payload per service. The service name and timestamps remain visible for administration; field names and values are inside the AES-256-GCM ciphertext. The current schema version is accepted exactly. Axlib does not migrate older SQLite credential schemas.

Every write uses a fresh 12-byte nonce, and AES-GCM associated data binds each ciphertext to its service name. The database has two tables: `credential_records` (`service`, `nonce`, `ciphertext`, `created_at`, `updated_at`) and `axlib_metadata`, one row holding the schema version and an encrypted key-check marker that detects a wrong key before records are used. `PRAGMA application_id` is `0x41584C49` and `PRAGMA user_version` is the schema version, `1`. Each change runs in its own `BEGIN IMMEDIATE` transaction, and reads open the database read-only.

## Configure and initialize

```python
from axlib.credentials import SQLiteCredentialStore, load_settings

settings = load_settings("/etc/axlib/axlib.toml")
store = SQLiteCredentialStore(settings)
store.initialize()
```

Each setting has an `AXLIB_SQLITE_*` environment variable that overrides it. The full list with defaults, and the rules for modes, are in the [settings reference](CREDENTIALS.md#settings-reference); [Shared and private permissions](#shared-and-private-permissions) below has two complete examples. For this store:

- `database` is the database file. `key_file` holds the key as URL-safe Base64 text and must be a different path from `database`. Relative paths in TOML are resolved against the directory of the TOML file.
- `AXLIB_SQLITE_KEY` supplies the key itself. It is accepted only from the environment and is used instead of `key_file` when set.
- `timeout` (default `5.0`) is how many seconds a connection waits for another writer.
- `database_mode` (default `"0660"`) applies to the database, `key_file_mode` (default `"0640"`) to the key file, and `owner` and `group` (default `"netops"`) to both. With `enforce_permissions = true` (the default), every operation corrects an existing file whose mode, owner, or group differs; see [Shared Linux host protection](CREDENTIALS.md#shared-linux-host-protection).
- `enabled` only decides whether `ax.getkeys()` reads the store. The class and the CLI work either way.

Creating the object opens no files; it raises `CredentialConfigurationError` for an invalid mode. `store.settings` holds the settings it was given.

CLI initialization can generate the configured 256-bit key file:

```bash
axlib credential-db --config /etc/axlib/axlib.toml init --generate-key
```

`initialize()` creates a missing current-schema database. For an existing database it verifies the axlib application ID, exact schema version, metadata marker, AES key, and configured file protection. It does not perform migration. The key must already exist. When the parent directory is missing, it is created with mode `2770` (a group is set and `database_mode` is group-writable), `2750` (a group is set and `database_mode` is group-readable), or `0700`. An existing directory is left unchanged. Every other method needs an initialized database and raises `CredentialBackendError` until `initialize()` has run.

## Service names, fields, and errors

The store methods use the service name exactly as given. `ax.getkeys()` looks a service up with dots, dashes, and underscores removed, so a record written as `first.last` would never be found. Normalize the name before writing:

```python
from axlib.credentials import normalize_service_for_write

service = normalize_service_for_write("first.last")  # 'firstlast'
```

Field names must be non-empty strings and values must be strings; the store does not check them against a record profile. `StoreAdmin` and the CLI normalize names and check fields for you (see [CREDENTIALS.md](CREDENTIALS.md)).

Every error is a `CredentialError`:

- `CredentialConfigurationError`: a setting, key, service name, or value mapping is missing or invalid.
- `CredentialBackendError`: the database is missing, unsafe, not an axlib database, or has another schema version, the key is wrong, a record fails authentication, or SQLite reports an error such as a lock timeout.
- `CredentialRecordExistsError` and `CredentialRecordNotFoundError`: subclasses of `CredentialBackendError` raised by the methods below.
- `CredentialDependencyError`: the `cryptography` package is unavailable.

## create()

```python
store.create(
    "operator",
    {
        "netuser": "operator-login",
        "netpass": "secret-from-approved-input",
        "netenable": "enable-secret-from-approved-input",
    },
)
```

`create()` raises `CredentialRecordExistsError` when the service already exists. An empty mapping raises `CredentialConfigurationError`.

## read()

```python
values = store.read("operator", ("netuser", "netpass"))
```

Only requested fields are returned; a requested field the record does not have is left out. A missing service returns an empty dictionary. A wrong key or a modified record raises `CredentialBackendError`.

## update()

```python
store.update("operator", {"netpass": "rotated-password"})
```

`update()` merges supplied fields into an existing service and raises `CredentialRecordNotFoundError` if the service is absent. The record is re-encrypted with a fresh nonce; `created_at` is kept and `updated_at` is set to the current time.

## write()

```python
store.write(
    "operator",
    {
        "netpass": "rotated-password",
        "netenable": "new-enable-secret",
    },
)
```

`write()` atomically creates the service when absent or merges supplied fields when present. It is the most convenient method for idempotent provisioning logic.

## delete()

```python
store.delete("operator", ("netenable",))
```

Selected fields are removed and the row is deleted when no fields remain. Field names the record does not have are ignored, and an empty `fields` sequence does nothing. A missing service raises `CredentialRecordNotFoundError`.

## delete_service()

```python
deleted = store.delete_service("operator")
```

The return value is `True` if a row was removed and `False` if the service was already absent.

## list_records()

```python
for record in store.list_records():
    print(record.service, record.fields, record.updated_at)

for record in store.list_records(visible_fields=("note",)):
    print(record.service, record.visible.get("note", ""))
```

Only safe metadata objects are returned; credential values are not exposed by the listing API. Each item is a frozen `SQLiteCredentialRecord` with `service`, `fields` (sorted field names), `created_at` and `updated_at` (UTC text such as `2026-10-20T07:10:57Z`), and `visible`. Records are sorted by service name, ignoring case. Listing decrypts every record, so it also detects a wrong key or a damaged record.

`visible_fields` names non-secret fields whose values each record should carry in `record.visible`; without it, `visible` is empty. The store cannot tell which fields are secret, so name only fields that are safe to display; `StoreAdmin.list_records()` asks for the `note` only.

## generate_sqlite_key_file()

```python
from pathlib import Path
from axlib.credentials import generate_sqlite_key_file

generate_sqlite_key_file(
    Path("/etc/axlib/sqlite-aes256.key"),
    mode=0o640,
    group="netops",
)
```

The helper generates a URL-safe Base64 encoding of a random 32-byte key, writes it to the file, and returns the same text; do not print or log it. The keyword arguments are `overwrite` (default `False`), `mode` (default `0o640`), `owner` (default `None`, keep the current user), and `group` (default `"netops"`; `None` keeps the current group). Use `mode=0o600, group=None` for a private key. A missing parent directory is created with mode `2750`, or `0700` when `group` is `None`.

By default the helper refuses to overwrite an existing key file and raises `CredentialConfigurationError`. `overwrite=True` replaces the key, which makes every record encrypted with the old key unreadable; use [rotate_key()](#rotate_key) to change the key of a database that holds records. An invalid mode or an unknown owner or group also raises `CredentialConfigurationError`, before any file is created.

## read_sqlite_key()

Read and check a key that another process wrote, for example before passing it to `rotate_key()`:

```python
from pathlib import Path
from axlib.credentials import read_sqlite_key

new_key = read_sqlite_key(Path("/etc/axlib/next-sqlite-aes256.key"))
```

It returns the 32 key bytes. A file that is unreadable, empty, not URL-safe Base64, or not exactly 32 bytes once decoded raises `CredentialConfigurationError`. `rotate-key --new-key-file` uses this function.

## rotate_key()

Re-encrypt every record and the database's key-check marker under a new AES-256 key:

```python
import os

new_key = os.urandom(32)
rotated = store.rotate_key(new_key)
```

`new_key` must be exactly 32 bytes. The return value is the number of records re-encrypted. Record timestamps do not change.

Every record is decrypted with the currently configured key and re-encrypted with a fresh nonce in one SQLite transaction. Rotation requires `sqlite.key_file` / `AXLIB_SQLITE_KEY_FILE`; axlib cannot rotate a key that is only supplied through `AXLIB_SQLITE_KEY` because it cannot update the calling shell's environment. Inside the transaction, the new key is first written to `<key_file>.rotating` beside the configured key file, with the key file's mode and group. The transaction then commits, and only after that is the staging file renamed over the key file. If the process is interrupted, `<key_file>.rotating` stays behind. Check which step was reached before touching it:

- If `axlib credential-db list` still works, the transaction did not commit and the database is unchanged. Remove the staging file and rotate again.
- If it fails with "key validation failed", the database already needs the new key. Move the staging file over the key file to finish the rotation.

`rotate_key()` refuses to start when that staging file already exists, so an interrupted rotation cannot be silently retried over an unresolved one. A wrong-length key, a set `AXLIB_SQLITE_KEY` (even when `key_file` is also configured), a missing `key_file`, or a leftover staging file raises `CredentialConfigurationError`. `store.check_key_rotation()` makes the last three checks without changing anything and returns the key file and staging file paths.

From the CLI:

```bash
axlib credential-db --config /etc/axlib/axlib.toml rotate-key --generate-key --yes
```

```console
$ axlib credential-db rotate-key --generate-key --dry-run
action=rotate-key
records=1
dry_run=true
$ axlib credential-db rotate-key --generate-key --yes
action=rotate-key
records=1
status=rotated
rotated=1
```

Use `--new-key-file PATH` instead of `--generate-key` to rotate to a key produced by another process (for example a KMS export). The file must hold the key as URL-safe Base64 text. The key it holds is written to the configured key file (as URL-safe Base64, so the text may not match byte for byte) and the file itself is left in place, so remove it once the rotation is verified. `--dry-run` makes the same refusal checks as a real rotation (a set `AXLIB_SQLITE_KEY`, no `key_file`, or a leftover staging file) and fails with the same message and exit status. It also checks the `--new-key-file`, then prints the record count without changing anything. `--yes` is required to proceed because every record is re-encrypted; without it the command exits with status 2.

## Shared and private permissions

Shared host example:

```toml
[sqlite]
enabled = true
database = "/var/lib/axlib/credentials.db"
key_file = "/etc/axlib/sqlite-aes256.key"
database_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true
```

Private account example:

```toml
[sqlite]
enabled = true
database = "/home/automation/.local/share/axlib/credentials.db"
key_file = "/home/automation/.config/axlib/sqlite-aes256.key"
database_mode = "0600"
key_file_mode = "0600"
group = ""
```

`group = ""` turns group enforcement off, and directories that axlib creates for a private store get mode `0700`.

## Command line

`axlib credential-db` is a thin layer over `StoreAdmin`. It normalizes service names, checks fields against a record profile, clears the service's Redis cache entry after each change when Redis is enabled, and never prints a credential value. `--config FILE` goes before the sub-command; without it, axlib uses `$AXLIB_CONFIG_FILE`, else the configuration file recorded by `install.sh`. The same command is installed as `axlib-credential-db`.

| Sub-command | Options | Effect |
| --- | --- | --- |
| `init` | `--generate-key` | Create or verify the database. `--generate-key` first creates the configured `key_file`; it fails if that file already exists, if `AXLIB_SQLITE_KEY` is set, or if no `key_file` is configured. |
| `add` | `--service NAME` (required), `--profile {network,infoblox}`, `--set FIELD=VALUE`, `--from-env FIELD=ENV_VAR`, `--prompt FIELD`, `--dry-run` | Create a record. The `network` profile (default) requires `netuser` and `netpass`. |
| `update` | the `add` options plus `--remove FIELD` | Change fields or remove optional ones in an existing record. |
| `delete` | `--service NAME` (required), `--yes`, `--dry-run` | Delete a complete record. `--yes` is required. |
| `list` | `--json` | Show service names, field names, timestamps, and notes. |
| `rotate-key` | `--generate-key` or `--new-key-file PATH` (one is required), `--yes`, `--dry-run` | Re-encrypt every record under a new key; see [rotate_key()](#rotate_key). |

`--set`, `--from-env`, `--prompt`, and `--remove` may be repeated. `--prompt` reads a value with terminal echo disabled. Prefer `--prompt` or `--from-env` for secrets because shell history is often kept. When a field is given more than once, `--prompt` wins over `--from-env`, which wins over `--set`. For `add`, `update`, and `delete`, `--dry-run` validates the request and prints the same lines, with `dry_run=true` in place of the `status=` line. Profiles and notes are described in [CREDENTIALS.md](CREDENTIALS.md#record-profiles).

```console
$ axlib credential-db init --generate-key
status=initialized
$ axlib credential-db add --service first.last --set netuser=first.last --from-env netpass=OPS_NETPASS --from-env netenable=OPS_NETENABLE
action=add
service=firstlast
fields=netenable,netpass,netuser
status=added
$ axlib credential-db update --service first.last --set note="lab account; expires June" --remove netenable
action=update
service=firstlast
fields=note
removed=netenable
status=updated
$ axlib credential-db list
SERVICE    FIELDS                CREATED_UTC           UPDATED_UTC           NOTE
firstlast  netpass,netuser,note  2026-10-20T07:13:29Z  2026-10-20T07:13:29Z  lab account; expires June
$ axlib credential-db list --json
[
  {
    "created_at": "2026-10-20T07:13:29Z",
    "fields": [
      "netpass",
      "netuser",
      "note"
    ],
    "note": "lab account; expires June",
    "service": "firstlast",
    "updated_at": "2026-10-20T07:13:29Z"
  }
]
$ axlib credential-db delete --service first.last --yes
action=delete
service=firstlast
status=deleted
```

In `list --json`, `note` is `null` for a record without a note. An empty store lists only the header line, or `[]` with `--json`.

The exit status is:

- `0` on success, including a dry run.
- `1` when the store reports an error (the message goes to standard error, prefixed `axlib-credential-db:`), when `delete` finds no such service (`status=not-found`), or when a change was saved but the Redis cache could not be cleared (`cache=stale`).
- `2` for a command-line error, such as an unknown option, an invalid service or field name, a field the profile does not allow, a missing required field, a missing `--yes`, or input that ends before a `--prompt` value is entered.

The CLI uses the same `init/add/update/delete/list/rotate-key` workflow as `axlib credential-file`. Unlike `CredentialFileStore`, the SQLite store has no `get_password()`, `set_password()`, or `delete_password()` methods; use `read()`, `write()`, and `delete()`.
