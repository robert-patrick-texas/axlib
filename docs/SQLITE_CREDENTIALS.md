# AES-256-GCM SQLite credential store

`SQLiteCredentialStore` stores one encrypted JSON payload per service. The service name and timestamps remain visible for administration; field names and values are inside the AES-256-GCM ciphertext. The current schema version is accepted exactly. Axlib does not migrate older SQLite credential schemas.

## Configure and initialize

```python
from axlib.credentials import SQLiteCredentialStore, load_settings

settings = load_settings("/etc/axlib/axlib.toml")
store = SQLiteCredentialStore(settings)
store.initialize()
```

CLI initialization can generate the configured 256-bit key file:

```bash
axlib credential-db --config /etc/axlib/axlib.toml init --generate-key
```

`initialize()` creates a missing current-schema database. For an existing database it verifies the axlib application ID, exact schema version, metadata marker, AES key, and configured file protection. It does not perform migration.

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

`create()` raises `CredentialRecordExistsError` when the service already exists.

## read()

```python
values = store.read("operator", ("netuser", "netpass"))
```

Only requested fields are returned. A missing service returns an empty dictionary.

## update()

```python
store.update("operator", {"netpass": "rotated-password"})
```

`update()` merges supplied fields into an existing service and raises `CredentialRecordNotFoundError` if the service is absent.

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

Selected fields are removed and the row is deleted when no fields remain.

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

Only safe metadata objects are returned; credential values are not exposed by the listing API. `visible_fields` names non-secret fields whose values each record should carry in `record.visible`. The store cannot tell which fields are secret, so name only fields that are safe to display; `StoreAdmin.list_records()` asks for the `note` only.

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

The helper generates a URL-safe Base64 encoding of a random 32-byte key and refuses to overwrite an existing key file.

## rotate_key()

Re-encrypt every record and the database's key-check marker under a new AES-256 key:

```python
import os

new_key = os.urandom(32)
rotated = store.rotate_key(new_key)
```

Every record is decrypted with the currently configured key and re-encrypted with a fresh nonce in one SQLite transaction. Rotation requires `sqlite.key_file` / `AXLIB_SQLITE_KEY_FILE`; axlib cannot rotate a key that is only supplied through `AXLIB_SQLITE_KEY` because it cannot update the calling shell's environment. The key file is replaced only after that transaction commits. If the process is interrupted between those two steps, the new key remains recoverable at `<key_file>.rotating` beside the configured key file — move it into place manually to finish the rotation, or remove it to retry from scratch. `rotate_key()` refuses to start when that staging file already exists, so an interrupted rotation cannot be silently retried over an unresolved one.

From the CLI:

```bash
axlib credential-db --config /etc/axlib/axlib.toml rotate-key --generate-key --yes
```

Use `--new-key-file PATH` instead of `--generate-key` to rotate to a key produced by another process (for example a KMS export). `--dry-run` prints the record count without changing anything, and `--yes` is required to proceed because every record is re-encrypted.

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

The CLI uses the same `init/add/update/delete/list/rotate-key` workflow as `axlib credential-file`.
