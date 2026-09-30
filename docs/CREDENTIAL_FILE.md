# AES-256-GCM encrypted credential file

`CredentialFileStore` is axlib's native encrypted text store. The container is INI-like so operators can identify format metadata and service names, but each service payload—including field names and values—is encrypted with AES-256-GCM. The format does not read `keyrings.cryptfile` files and does not support AES-128.

Every write uses a fresh 12-byte nonce. AES-GCM associated data binds ciphertext to the service name, so moving ciphertext between service sections fails authentication. A file-level encrypted key-check marker detects a wrong key before records are used.

The store uses a persistent sibling `.lock` file with POSIX `flock`. Writers hold an exclusive lock, readers hold a shared lock, and updates are written to a temporary sibling, fsynced, and atomically replaced.

## Configure and initialize

```python
from axlib.credentials import CredentialFileStore, load_settings

settings = load_settings("/etc/axlib/axlib.toml")
store = CredentialFileStore(settings)
store.initialize()
```

To generate the configured key file and initialize from the CLI:

```bash
axlib credential-file --config /etc/axlib/axlib.toml init --generate-key
```

`initialize()` creates a missing version-1 file or verifies an existing version-1 file and key. It never upgrades another format or version.

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

A duplicate service raises `CredentialRecordExistsError`.

## read()

Read only the fields needed by the current workflow:

```python
values = store.read("operator", ("netuser", "netpass"))
username = values.get("netuser")
password = values.get("netpass")
```

A missing service or missing requested field is simply omitted from the returned dictionary. Authentication failures, malformed files, and wrong keys raise credential errors.

## update()

Use `update()` when the service must already exist:

```python
store.update(
    "operator",
    {"netpass": "rotated-password"},
)
```

Only supplied fields replace existing fields. The full record is re-encrypted with a fresh nonce. A missing service raises `CredentialRecordNotFoundError`.

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

If no fields remain, the complete service record is removed. A missing service raises `CredentialRecordNotFoundError`.

## delete_service()

Delete the complete service explicitly:

```python
deleted = store.delete_service("operator")
if not deleted:
    print("service was already absent")
```

The method returns `True` when a record was removed and `False` when none existed.

## list_records()

List safe administrative metadata without returning credential values:

```python
for record in store.list_records():
    print(record.service)
    print(record.fields)
    print(record.created_at)
    print(record.updated_at)
```

Listing decrypts each payload to determine field names, so it also detects corrupted records or the wrong encryption key. As with SQLite, `list_records(visible_fields=("note",))` also returns each record's note in `record.visible`.

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

Existing key files are never overwritten. Do not print or log the returned key text.

## rotate_key()

Re-encrypt every record and the file's key-check marker under a new AES-256 key:

```python
import os

new_key = os.urandom(32)
rotated = store.rotate_key(new_key)
```

Rotation requires `credential_file.key_file` / `AXLIB_CREDENTIAL_FILE_KEY_FILE`; axlib cannot rotate a key that is only supplied through `AXLIB_CREDENTIAL_FILE_KEY` because it cannot update the calling shell's environment. The key file is replaced only after the credential file has already been atomically replaced with the new ciphertext. If the process is interrupted between those two steps, the new key remains recoverable at `<key_file>.rotating` beside the configured key file — move it into place manually to finish the rotation, or remove it to retry from scratch. `rotate_key()` refuses to start when that staging file already exists, so an interrupted rotation cannot be silently retried over an unresolved one.

From the CLI:

```bash
axlib credential-file --config /etc/axlib/axlib.toml rotate-key --generate-key --yes
```

Use `--new-key-file PATH` instead of `--generate-key` to rotate to a key produced by another process (for example a KMS export). `--dry-run` prints the record count without changing anything, and `--yes` is required to proceed because every record is re-encrypted.

## Familiar password-method facade

Greenfield scripts that previously used a simple keyring-style API can use axlib directly without installing `keyring` or `keyrings.cryptfile`:

```python
store.set_password("operator", "netpass", "rotated-password")
password = store.get_password("operator", "netpass")
store.delete_password("operator", "netpass")
```

These are convenience methods over `write()`, `read()`, and `delete()`. The standardized CRUD methods remain preferred when working with multiple network credential fields.

## CLI symmetry with SQLite

`credential-file` and `credential-db` intentionally offer the same lifecycle verbs (`init/add/update/delete/list/rotate-key`) and secure value-input options. This lets training examples move from one durable store to the other without changing the operational workflow.
