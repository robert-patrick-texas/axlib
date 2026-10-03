# RADIUS login check: `axlib radius`

`axlib radius` asks a RADIUS server whether it accepts a username and
password, the same way a switch, router, or firewall does when someone logs
in. Use it to find out why a device login fails: whether the server rejects the
account, the request never reaches the server, or the device and the server
have different shared secrets. It uses only the Python standard library.

## Quick start

```console
$ axlib radius 192.0.2.10 -u alice --secret-file /etc/axlib/radius.secret
Password for alice:
Access-Accept from 192.0.2.10 port 1812 for 'alice' in 4.2 ms (attempt 1)
  Reply-Message: Welcome, alice
  Vendor-Specific: vendor=9 type=1 value=shell:priv-lvl=15
  Message-Authenticator: verified
```

The server needs this host configured as a RADIUS client with the same shared
secret, in the same way as a network device. Examples are a `client` block in
FreeRADIUS `clients.conf`, a Network Device in Cisco ISE, or a RADIUS client in
Microsoft NPS. If it isn't configured, most servers silently ignore the
request and the check ends with "no reply".

## Command line

```text
axlib radius SERVER -u USERNAME [-p PORT] [password source] [secret source]
             [--timeout SECONDS] [--retries N] [--nas-identifier NAME] [--json]
```

The same command is available as `axlib-radius` and `python -m axlib.radius`.

| Option | Meaning |
| --- | --- |
| `SERVER` | Server host name, IPv4 address, or IPv6 address. |
| `-p`, `--port` | Server UDP port (default 1812). |
| `-u`, `--username` | User name to check (required). |
| `--timeout` | Seconds to wait for each reply (default 3). |
| `--retries` | Times to resend after a timeout (default 2, so 3 attempts). |
| `--nas-identifier` | `NAS-Identifier` value sent to the server (default `axlib`). Server policies sometimes match on it. |
| `--json` | Print one JSON object instead of text. |

The password comes from the first of these that is given. When none is given,
you are prompted with echo turned off:

| Password option | Use |
| --- | --- |
| `--password-env VAR` | Read from an environment variable, for example `--password-env NETPASS` after `netenv-set` (see `docs/NETENV.md`). |
| `--password-stdin` | Read the first line of standard input: `printf '%s\n' "$pw" \| axlib radius ...`. |
| `--password TEXT` | On the command line. Avoid it on shared hosts. |

The shared secret works the same way:

| Secret option | Use |
| --- | --- |
| `--secret-file PATH` | Read the first line of a file. Protect it like a key file: `chmod 0640`, group `netops`. |
| `--secret-env VAR` | Read from an environment variable. |
| `--secret TEXT` | On the command line. Avoid it on shared hosts. |

Anything typed on the command line is visible to every user on the host
through `ps` and is kept in shell history. Use these options only in a lab.

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | `Access-Accept`: the server accepted the username and password. |
| 1 | `Access-Reject` or `Access-Challenge`: the server answered, but did not accept the login. |
| 2 | Error: bad option, unreadable secret file, name lookup failure, port closed, or a reply that failed verification. |
| 3 | No reply after every retry. |

Results go to standard output and errors to standard error. Scripts can use
the exit status without parsing the output:

```bash
for server in 192.0.2.10 192.0.2.11; do
    if axlib radius "$server" -u svc-monitor --password-env MONPASS \
            --secret-file /etc/axlib/radius.secret > /dev/null; then
        echo "$server ok"
    else
        echo "$server FAILED ($?)"
    fi
done
```

### JSON output

`--json` prints every field of the result:

```json
{
  "result": "Access-Accept",
  "accepted": true,
  "code": 2,
  "server": "radius1.example.net",
  "address": "192.0.2.10",
  "port": 1812,
  "username": "alice",
  "attempts": 1,
  "elapsed_ms": 4.2,
  "message_authenticator": true,
  "reply_messages": ["Welcome, alice"],
  "attributes": [
    {"type": 80, "name": "Message-Authenticator", "value": "0x5be1..."},
    {"type": 18, "name": "Reply-Message", "value": "Welcome, alice"},
    {"type": 26, "name": "Vendor-Specific", "value": "vendor=9 type=1 value=shell:priv-lvl=15"}
  ]
}
```

Select fields with `jq`, for example `axlib radius ... --json | jq -r .result`.

## Python API

```python
from axlib.radius import RadiusError, RadiusTimeoutError, authenticate

try:
    result = authenticate(
        "192.0.2.10",  # server
        "alice",  # username
        password,  # password
        secret,  # shared secret (str or bytes)
        port=1812,  # these keyword arguments show the defaults
        timeout=3.0,
        retries=2,
        nas_identifier="axlib",
    )
except RadiusTimeoutError:
    print("no reply: unreachable, not a configured client, or wrong secret")
except RadiusError as exc:
    print(f"no trustworthy answer: {exc}")
else:
    print(result.reply.code_name, result.reply.reply_messages)
    if result.accepted:
        ...
```

A reject is an answer, not an error. `authenticate()` returns it, and
`result.accepted` is `False`. An exception means there is no answer that can
be trusted. `ValueError` is raised for invalid arguments, such as an empty
secret, a port outside 1-65535, or a password longer than 128 bytes.

The returned `RadiusResult` never contains the password or the secret. Its
fields are `server`, `address`, `port`, `username`, `attempts`, `elapsed_ms`,
and `reply`. The `reply` (`RadiusReply`) has `code`, `code_name`, `accepted`,
`reply_messages`, `message_authenticator`, and `attributes`. Each attribute
(`RadiusAttribute`) has its `number`, raw `value` bytes, `name`, and readable
`text`. `result.to_dict()` gives the JSON form shown above.

The packet functions are public, so you can study them or test them on their
own:

- `hide_password(password, secret, authenticator)` hides the password as
  RFC 2865 section 5.2 describes.
- `build_access_request(...)` encodes a complete request.
- `verify_response(packet, request=..., secret=...)` checks a reply and parses
  it.

## How it works

1. **One UDP request, one reply.** The client sends an `Access-Request`
   datagram. UDP does not guarantee delivery, so after `--timeout` seconds the
   client resends the identical packet, up to `--retries` times.
2. **Packet layout.** A 20-byte header (code, identifier, length, and a
   16-byte authenticator) is followed by attributes. Each attribute is one
   type byte, one length byte, and the value. The request carries
   `Message-Authenticator`, `User-Name`, `User-Password`, `NAS-Identifier`,
   and `NAS-IP-Address`, or `NAS-IPv6-Address` for an IPv6 server.
3. **Password hiding.** The client picks a random 16-byte *Request
   Authenticator*. The password is XORed with `MD5(secret + authenticator)`.
   The secret itself never crosses the network.
4. **Reply verification.** The server signs its reply with a *Response
   Authenticator*: `MD5(reply header + request authenticator + reply
   attributes + secret)`. A wrong value means the reply did not come from a
   holder of the secret. In practice, that almost always means the two sides
   have different secrets.
5. **Message-Authenticator.** Every request carries an HMAC-MD5 of the whole
   packet. The 2024 BlastRADIUS attack (CVE-2024-3596) forges replies to
   requests that lack one, and patched servers can be set to drop such
   requests. When the reply carries one too, it is verified. The output
   reports whether it did, because a server that never sends one may still
   need the BlastRADIUS update.

## Troubleshooting

| Symptom | Likely causes |
| --- | --- |
| `no reply ... after 3 attempt(s)` (exit 3) | A firewall blocks UDP 1812. The server does not list this host's address as a client. The shared secret is wrong and the server drops the request without answering, which FreeRADIUS and ISE both do. Check the server's log for this host's address. |
| `nothing is listening on this UDP port` (exit 2) | The host is reachable, but no RADIUS service is running on that port. Check `--port`; some older servers use 1645. |
| `Response Authenticator is wrong` (exit 2) | The server answered, but with a different shared secret. Compare the secret on both sides; trailing spaces count. |
| `Access-Reject` (exit 1) | The server rejected the login: a wrong password, a locked account, or a policy that does not match. `Reply-Message` and the server's logs usually explain which. |
| `Access-Challenge` (exit 1) | The server wants a second factor, such as an OTP. This client does not answer challenges. |
| `cannot resolve` (exit 2) | DNS cannot resolve the server name. Use the IP address or fix the name. |

The client sends its NAS address as the local address the operating system
picks for the route to the server. Behind NAT, the server sees the translated
address instead, and that address is the one to configure as the client.

## Limits

- Only PAP (a plain password). CHAP, MS-CHAP, EAP (PEAP, EAP-TLS), accounting,
  and RADIUS over TLS (RadSec) are not implemented.
- `Access-Challenge` is reported, not answered.
- PAP hiding is only as strong as MD5 and the shared secret. Anyone who
  captures the request and knows or guesses the secret can recover the
  password. Run checks over a trusted management network, and use a long,
  random secret.
