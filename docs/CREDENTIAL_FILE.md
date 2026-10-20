# AES-256-GCM encrypted credential file

`CredentialFileStore` is axlib's native encrypted text store. The container is INI-like so operators can identify format metadata, service names, and timestamps, but each service payload—including field names and values—is encrypted with AES-256-GCM. The format does not read `keyrings.cryptfile` files and does not support AES-128.

Every write uses a fresh 12-byte nonce. AES-GCM associated data binds ciphertext to the service name, so moving ciphertext between service sections fails authentication. A file-level encrypted key-check marker detects a wrong key before records are used.

The store uses a persistent sibling `.lock` file with POSIX `flock`. Writers hold an exclusive lock, readers hold a shared lock, and updates are written to a temporary sibling, fsynced, and atomically replaced.

A file holding one record looks like this:

```text
[axlib]
format = axlib-credential-file
version = 1
cipher = AES-256-GCM
created_at = 2026-10-20T07:10:57Z
key_check_nonce = APmC2Oz2uyn5Nqc3
key_check_ciphertext = NxYnqFn2aYF3QR2tx_kwGNtgP7z8RnRQqifllm80RbMPa4y-Khwz5_D_hs_5Mjl2mWg=

[credential:b3BlcmF0b3I]
service = operator
nonce = z2J5fqk34gqC57qg
ciphertext = WskonGqYew4Z0c9R2zGe7LkjkQUWVVLGkXBHG8L5bEhIjc6u2pXIePDT-CiuKI5LuMNAVi4KbNTUvNZotsISOXg1ZYj_noX2w7tVHIad8If9CLM=
created_at = 2026-10-20T07:10:57Z
updated_at = 2026-10-20T07:10:57Z
```

Each record section is named `credential:` followed by the service name in URL-safe Base64 without padding.

## Configure and initialize

```toml
[credential_file]
enabled = true
file = "/var/lib/axlib/credentials.axc"
key_file = "/etc/axlib/credential-file.key"
lock_timeout = 5.0
file_mode = "0660"
key_file_mode = "0640"
group = "netops"
enforce_permissions = true
```

Each setting has an `AXLIB_CREDENTIAL_FILE_*` environment variable that overrides it. The full list with defaults, and the rules for modes, are in the [settings reference](CREDENTIALS.md#settings-reference). For this store:

- `file` is the encrypted file; its lock file is the same path plus `.lock`. `key_file` holds the key as URL-safe Base64 text and must be a different path from `file`. Relative paths in TOML are resolved against the directory of the TOML file.
- `AXLIB_CREDENTIAL_FILE_KEY` supplies the key itself. It is accepted only from the environment and is used instead of `key_file` when set.
- `lock_timeout` (default `5.0`) is how many seconds an operation waits for the lock before raising `CredentialBackendError`.
- `file_mode` (default `"0660"`) applies to the credential and lock files, `key_file_mode` (default `"0640"`) to the key file, and `owner` and `group` (default `"netops"`) to all of them. With `enforce_permissions = true` (the default), every operation corrects an existing file whose mode, owner, or group differs; see [Shared Linux host protection](CREDENTIALS.md#shared-linux-host-protection). A private store uses `"0600"` for both modes and `group = ""`.
- `enabled` only decides whether `ax.getkeys()` reads the store. The class and the CLI work either way.

```python
from axlib.credentials import CredentialFileStore, load_settings

settings = load_settings("/etc/axlib/axlib.toml")
store = CredentialFileStore(settings)
store.initialize()
```

Creating the object opens no files; it raises `CredentialConfigurationError` for an invalid mode or a `lock_timeout` that is not greater than 0. `store.settings` holds the settings it was given.

To generate the configured key file and initialize from the CLI:

```bash
axlib credential-file --config /etc/axlib/axlib.toml init --generate-key
```

`initialize()` creates a missing version-1 file or verifies an existing version-1 file and key. It never upgrades another format or version. The key must already exist. It also creates the `.lock` file and, when the parent directory is missing, creates it with mode `2770` (a group is set and `file_mode` is group-writable), `2750` (a group is set and `file_mode` is group-readable), or `0700`. An existing directory is left unchanged.

## Service names, fields, and errors

The store methods use the service name exactly as given. `ax.getkeys()` looks a service up with dots, dashes, and underscores removed, so a record written as `first.last` would never be found. Normalize the name before writing:

```python
from axlib.credentials import normalize_service_for_write

service = normalize_service_for_write("first.last")  # 'firstlast'
```

Field names must be non-empty strings and values must be strings; the store does not check them against a record profile. `StoreAdmin` and the CLI normalize names and check fields for you (see [CREDENTIALS.md](CREDENTIALS.md)).

Every error is a `CredentialError`:

- `CredentialConfigurationError`: a setting, key, service name, or value mapping is missing or invalid.
- `CredentialBackendError`: the file or lock is missing, unsafe, or malformed, the key is wrong, a record fails authentication, or the lock wait times out.
- `CredentialRecordExistsError` and `CredentialRecordNotFoundError`: subclasses of `CredentialBackendError` raised by the methods below.
- `CredentialDependencyError`: the `cryptography` package or POSIX `fcntl` is unavailable.

## create()

Use `create()` when the service must not already exist:

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

A duplicate service raises `CredentialRecordExistsError`. An empty mapping raises `CredentialConfigurationError`.

## read()

Read only the fields needed by the current workflow:

```python
values = store.read("operator", ("netuser", "netpass"))
username = values.get("netuser")
password = values.get("netpass")
```

A requested field the record does not have is left out of the returned dictionary, and a missing service returns an empty dictionary. Authentication failures, malformed files, and wrong keys raise `CredentialBackendError`.

## update()

Use `update()` when the service must already exist:

```python
store.update(
    "operator",
    {"netpass": "rotated-password"},
)
```

Only supplied fields replace existing fields. The full record is re-encrypted with a fresh nonce; `created_at` is kept and `updated_at` is set to the current time. A missing service raises `CredentialRecordNotFoundError`.

## write()

`write()` is the create-or-merge operation. It is useful in provisioning code where either state is acceptable:

```python
store.write(
    "operator",
    {
        "netpass": "rotated-password",
        "netenable": "new-enable-secret",
    },
)
```

If the service is absent it is created. If present, supplied fields replace the matching fields while all other fields remain.

## delete()

Delete selected fields while retaining the service:

```python
store.delete("operator", ("netenable",))
```

If no fields remain, the complete service record is removed. Field names the record does not have are ignored, and an empty `fields` sequence does nothing. A missing service raises `CredentialRecordNotFoundError`.

## delete_service()

Delete the complete service explicitly:

```python
deleted = store.delete_service("operator")
if not deleted:
    print("service was already absent")
```

The method returns `True` when a record was removed and `False` when none existed. The record is decrypted first, so a wrong key or a damaged record raises `CredentialBackendError` instead of being deleted.

## list_records()

List safe administrative metadata without returning credential values:

```python
for record in store.list_records():
    print(record.service)
    print(record.fields)
    print(record.created_at)
    print(record.updated_at)
```

Each item is a frozen `CredentialFileRecord` with `service`, `fields` (sorted field names), `created_at` and `updated_at` (UTC text such as `2026-10-20T07:10:57Z`), and `visible`. Records are sorted by service name, ignoring case.

Listing decrypts each payload to determine field names, so it also detects corrupted records or the wrong encryption key. As with SQLite, `list_records(visible_fields=("note",))` also returns each record's note in `record.visible`; without `visible_fields`, `visible` is empty. Name only fields that are safe to display.

## generate_credential_file_key_file()

Provision a random 256-bit key file programmatically:

```python
from pathlib import Path
from axlib.credentials import generate_credential_file_key_file

generate_credential_file_key_file(
    Path("/etc/axlib/credential-file.key"),
    mode=0o640,
    group="netops",
)
```

The keyword arguments are `mode` (default `0o640`), `owner` (default `None`, keep the current user), and `group` (default `"netops"`; `None` keeps the current group). Use `mode=0o600, group=None` for a private key. A missing parent directory is created with mode `2750`, or `0700` when `group` is `None`.

Existing key files are never overwritten: the function raises `CredentialBackendError` instead. An invalid mode or an unknown owner or group raises `CredentialConfigurationError` and leaves no key file behind. The function returns the key as URL-safe Base64 text; do not print or log it.

## read_credential_file_key()

Read and check a key that another process wrote, for example before passing it to `rotate_key()`:

```python
from pathlib import Path
from axlib.credentials import read_credential_file_key

new_key = read_credential_file_key(Path("/etc/axlib/next-credential-file.key"))
```

It returns the 32 key bytes. A file that is unreadable, empty, not URL-safe Base64, or not exactly 32 bytes once decoded raises `CredentialConfigurationError`. `rotate-key --new-key-file` uses this function.

## rotate_key()

Re-encrypt every record and the file's key-check marker under a new AES-256 key:

```python
import os

new_key = os.urandom(32)
rotated = store.rotate_key(new_key)
```

`new_key` must be exactly 32 bytes. The return value is the number of records re-encrypted. Record timestamps do not change.

Rotation requires `credential_file.key_file` / `AXLIB_CREDENTIAL_FILE_KEY_FILE`; axlib cannot rotate a key that is only supplied through `AXLIB_CREDENTIAL_FILE_KEY` because it cannot update the calling shell's environment. The new key is first written to `<key_file>.rotating` beside the configured key file, with the key file's mode and group. The credential file is then atomically replaced with the new ciphertext, and only after that is the staging file renamed over the key file. If the process is interrupted, `<key_file>.rotating` stays behind. Check which step was reached before touching it:

- If `axlib credential-file list` still works, the credential file was not changed. Remove the staging file and rotate again.
- If it fails with "key verification failed", the credential file already needs the new key. Move the staging file over the key file to finish the rotation.

`rotate_key()` refuses to start when that staging file already exists, so an interrupted rotation cannot be silently retried over an unresolved one. A wrong-length key, a set `AXLIB_CREDENTIAL_FILE_KEY` (even when `key_file` is also configured), a missing `key_file`, or a leftover staging file raises `CredentialConfigurationError`.

From the CLI:

```bash
axlib credential-file --config /etc/axlib/axlib.toml rotate-key --generate-key --yes
```

```console
$ axlib credential-file rotate-key --generate-key --dry-run
action=rotate-key
records=1
dry_run=true
$ axlib credential-file rotate-key --generate-key --yes
action=rotate-key
records=1
status=rotated
rotated=1
```

Use `--new-key-file PATH` instead of `--generate-key` to rotate to a key produced by another process (for example a KMS export). The file must hold the key as URL-safe Base64 text. The key it holds is written to the configured key file (as URL-safe Base64, so the text may not match byte for byte) and the file itself is left in place, so remove it once the rotation is verified. `--dry-run` checks the `--new-key-file` and prints the record count without changing anything; it does not check for a set `AXLIB_CREDENTIAL_FILE_KEY` or a leftover staging file. `--yes` is required to proceed because every record is re-encrypted; without it the command exits with status 2.

## Familiar password-method facade

Greenfield scripts that previously used a simple keyring-style API can use axlib directly without installing `keyring` or `keyrings.cryptfile`:

```python
store.set_password("operator", "netpass", "rotated-password")
password = store.get_password("operator", "netpass")
store.delete_password("operator", "netpass")
```

These are convenience methods over `write()`, `read()`, and `delete()`. `get_password()` returns `None` when the service or field is missing, `set_password()` creates the service when needed, and `delete_password()` raises `CredentialRecordNotFoundError` for a missing service and removes the record when its last field is deleted. The standardized CRUD methods remain preferred when working with multiple network credential fields. The SQLite store does not have these methods.

## Command line

`axlib credential-file` is a thin layer over `StoreAdmin`. It normalizes service names, checks fields against a record profile, clears the service's Redis cache entry after each change when Redis is enabled, and never prints a credential value. `--config FILE` goes before the sub-command; without it, axlib uses `$AXLIB_CONFIG_FILE`, else the configuration file recorded by `install.sh`. The same command is installed as `axlib-credential-file`.

| Sub-command | Options | Effect |
| --- | --- | --- |
| `init` | `--generate-key` | Create or verify the store. `--generate-key` first creates the configured `key_file`; it fails if that file already exists, if `AXLIB_CREDENTIAL_FILE_KEY` is set, or if no `key_file` is configured. |
| `add` | `--service NAME` (required), `--profile {network,infoblox}`, `--set FIELD=VALUE`, `--from-env FIELD=ENV_VAR`, `--prompt FIELD`, `--dry-run` | Create a record. The `network` profile (default) requires `netuser` and `netpass`. |
| `update` | the `add` options plus `--remove FIELD` | Change fields or remove optional ones in an existing record. |
| `delete` | `--service NAME` (required), `--yes`, `--dry-run` | Delete a complete record. `--yes` is required. |
| `list` | `--json` | Show service names, field names, timestamps, and notes. |
| `rotate-key` | `--generate-key` or `--new-key-file PATH` (one is required), `--yes`, `--dry-run` | Re-encrypt every record under a new key; see [rotate_key()](#rotate_key). |

`--set`, `--from-env`, `--prompt`, and `--remove` may be repeated. `--prompt` reads a value with terminal echo disabled. Prefer `--prompt` or `--from-env` for secrets because shell history is often kept. When a field is given more than once, `--prompt` wins over `--from-env`, which wins over `--set`. For `add`, `update`, and `delete`, `--dry-run` validates the request and prints the same lines, with `dry_run=true` in place of the `status=` line. Profiles and notes are described in [CREDENTIALS.md](CREDENTIALS.md#record-profiles).

```console
$ axlib credential-file init --generate-key
status=initialized
$ axlib credential-file add --service first.last --set netuser=first.last --from-env netpass=OPS_NETPASS --from-env netenable=OPS_NETENABLE
action=add
service=firstlast
fields=netenable,netpass,netuser
status=added
$ axlib credential-file update --service first.last --set note="lab account; expires June" --remove netenable
action=update
service=firstlast
fields=note
removed=netenable
status=updated
$ axlib credential-file list
SERVICE    FIELDS                CREATED_UTC           UPDATED_UTC           NOTE
firstlast  netpass,netuser,note  2026-10-20T07:13:27Z  2026-10-20T07:13:28Z  lab account; expires June
$ axlib credential-file list --json
[
  {
    "created_at": "2026-10-20T07:13:27Z",
    "fields": [
      "netpass",
      "netuser",
      "note"
    ],
    "note": "lab account; expires June",
    "service": "firstlast",
    "updated_at": "2026-10-20T07:13:28Z"
  }
]
$ axlib credential-file delete --service first.last --yes
action=delete
service=firstlast
status=deleted
```

In `list --json`, `note` is `null` for a record without a note. An empty store lists only the header line, or `[]` with `--json`.

The exit status is:

- `0` on success, including a dry run.
- `1` when the store reports an error (the message goes to standard error, prefixed `axlib-credential-file:`), when `delete` finds no such service (`status=not-found`), or when a change was saved but the Redis cache could not be cleared (`cache=stale`).
- `2` for a command-line error, such as an unknown option, an invalid service or field name, a field the profile does not allow, a missing required field, or a missing `--yes`.

## CLI symmetry with SQLite

`credential-file` and `credential-db` intentionally offer the same lifecycle verbs (`init/add/update/delete/list/rotate-key`) and secure value-input options. This lets training examples move from one durable store to the other without changing the operational workflow.
