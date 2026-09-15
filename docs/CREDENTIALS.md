# axlib credentials

Axlib 1.0.0 gives Network Operations scripts one lookup API while allowing two AES-256-GCM durable stores. Existing operator scripts may continue to use:

```python
import axlib as ax

netuser, netpass, netenable = ax.getkeys()
```

When no service is passed, `getkeys()` uses `$USER`. `NETUSER`, `NETPASS`, and `NETENABLE` override stored values. If the operator record is incomplete, the configured `credentials.shared_service` remains the fallback.

## Lookup precedence

For each missing field, axlib uses this order:

1. Caller/environment value.
2. Redis, when enabled.
3. SQLite AES-256-GCM store, when enabled.
4. AES-256-GCM encrypted text-file store, when enabled.

A durable-store value may be cached back into Redis. Backend failures are reported without printing credential values, and the next configured durable store may still satisfy the lookup.

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
```

Relative `credential_file.file`, `credential_file.key_file`, `sqlite.database`, and `sqlite.key_file` values are resolved relative to the selected TOML file. Direct AES keys are never loaded from TOML.

Important environment variables include `AXLIB_CONFIG_FILE`, `AXLIB_SHARED_SERVICE`, `AXLIB_CREDENTIAL_FILE_ENABLE`, `AXLIB_CREDENTIAL_FILE`, `AXLIB_CREDENTIAL_FILE_KEY_FILE`, `AXLIB_CREDENTIAL_FILE_KEY`, `AXLIB_SQLITE_ENABLE`, `AXLIB_SQLITE_DATABASE`, `AXLIB_SQLITE_KEY_FILE`, and `AXLIB_SQLITE_KEY`.

The direct key variables must contain URL-safe Base64 that decodes to exactly 32 bytes. Key files use the same representation.

## Shared Linux host protection

The default policy for both stores is designed for operators in group `netops`:

- durable data file/database: `0660`
- encryption key file: `0640`
- group: `netops`

A private deployment can use `0600` for both and set `group = ""`.

A typical shared directory setup is:

```bash
sudo install -d -o root -g netops -m 2770 /var/lib/axlib
sudo install -d -o root -g netops -m 2750 /etc/axlib
```

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

Both CLIs also support `--from-env FIELD=ENV_VAR`, `--dry-run` where applicable, and `list --json`. Commands print field names but never credential values.

`rotate-key` re-encrypts every record and the store's key-check marker under a new AES-256 key, replacing the configured key file only after the durable store has already been updated. It requires a key file (not an environment-only key) because axlib cannot update the calling shell's environment; see [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md#rotate_key) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md#rotate_key) for the interrupted-rotation recovery path.

## Storage format policy

Axlib 1.0.0 intentionally contains no storage migration framework. The encrypted text file accepts only axlib text-format version 1 with AES-256-GCM. SQLite accepts only its current axlib application ID and schema version. An unsupported version fails with an operator-facing error; recreate or reprovision the store deliberately rather than relying on application startup to modify it. Key rotation is a separate, supported operation: it re-encrypts current-format data under a new key rather than changing the format or schema, so it does not conflict with this no-migration policy.

See [CREDENTIAL_FILE.md](CREDENTIAL_FILE.md) and [SQLITE_CREDENTIALS.md](SQLITE_CREDENTIALS.md) for full Python API examples.
