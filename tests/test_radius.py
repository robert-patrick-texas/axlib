# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for the RADIUS login check, using RFC vectors and a local UDP server.

The fake server is written independently of :mod:`axlib.radius`: it recovers
the hidden password, checks the request's Message-Authenticator, and signs its
replies with its own code, so the client and the server check each other.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from axlib.__main__ import main as axlib_main
from axlib.radius import (
    ACCESS_ACCEPT,
    EXIT_ACCEPT,
    EXIT_ERROR,
    EXIT_NO_REPLY,
    EXIT_REJECT,
    RadiusAttribute,
    RadiusError,
    RadiusTimeoutError,
    authenticate,
    build_access_request,
    hide_password,
    main,
    verify_response,
)

SECRET = b"testing123"
PASSWORD = "c0rrect horse"  # noqa: S105 - test credential

# RFC 2865 section 7.1: user "nemo", password "arctangent".
RFC_SECRET = b"xyzzy5461"
RFC_AUTHENTICATOR = bytes.fromhex("0f403f9473978057bd83d5cb98f4227a")
RFC_HIDDEN_PASSWORD = bytes.fromhex("0dbe708d93d413ce3196e43f782a0aee")
RFC_ACCEPT = bytes.fromhex(
    "02 00 0026 86fe220e7624ba2a1005f6bf9b55e0b2"  # code, id, length, authenticator
    "06 06 00000001"  # Service-Type = Login
    "0f 06 00000000"  # Login-Service = Telnet
    "0e 06 c0a80103"  # Login-IP-Host = 192.168.1.3
)


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


def _hmac(data: bytes, secret: bytes = SECRET) -> bytes:
    return hmac.new(secret, data, lambda: hashlib.md5(usedforsecurity=False)).digest()


def _attributes(data: bytes) -> list[tuple[int, bytes]]:
    found = []
    while data:
        found.append((data[0], data[2 : data[1]]))
        data = data[data[1] :]
    return found


def _encode(attributes: list[tuple[int, bytes]]) -> bytes:
    return b"".join(bytes((t, len(v) + 2)) + v for t, v in attributes)


def _unhide(hidden: bytes, secret: bytes, authenticator: bytes) -> str:
    plain, previous = b"", authenticator
    for start in range(0, len(hidden), 16):
        block = hidden[start : start + 16]
        plain += bytes(
            a ^ b for a, b in zip(block, _md5(secret + previous), strict=True)
        )
        previous = block
    return plain.rstrip(b"\0").decode()


def _request_mac_ok(request: bytes) -> bool:
    attributes = [
        (t, bytes(16) if t == 80 else v) for t, v in _attributes(request[20:])
    ]
    mac = dict(_attributes(request[20:]))[80]
    return _hmac(request[:20] + _encode(attributes)) == mac


def _sign_reply(
    request: bytes,
    code: int,
    attributes: list[tuple[int, bytes]],
    *,
    secret: bytes = SECRET,
    with_mac: bool = True,
) -> bytes:
    if with_mac:
        attributes = [(80, bytes(16)), *attributes]
    body = _encode(attributes)
    header = bytes((code, request[1])) + (20 + len(body)).to_bytes(2, "big")
    if with_mac:
        mac = _hmac(header + request[4:20] + body, secret)
        body = body[:2] + mac + body[18:]
    return header + _md5(header + request[4:20] + body + secret) + body


@dataclass
class FakeServer:
    """A one-thread RADIUS server answering on 127.0.0.1."""

    port: int
    requests: list[bytes] = field(default_factory=list)
    drop_first: int = 0
    reply_secret: bytes = SECRET
    with_mac: bool = True


@contextmanager
def fake_server(**options: Any) -> Iterator[FakeServer]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.05)
    server = FakeServer(port=sock.getsockname()[1], **options)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                request, peer = sock.recvfrom(4096)
            except TimeoutError:
                continue
            server.requests.append(request)
            if len(server.requests) <= server.drop_first:
                continue
            fields = dict(_attributes(request[20:]))
            password = _unhide(fields[2], SECRET, request[4:20])
            if password == PASSWORD and _request_mac_ok(request):
                reply = [(18, b"Welcome"), (26, b"\0\0\0\x09\x01\x13shell:priv-lvl=15")]
                code = 2
            else:
                reply, code = [(18, b"Bad password")], 3
            sock.sendto(
                _sign_reply(
                    request,
                    code,
                    reply,
                    secret=server.reply_secret,
                    with_mac=server.with_mac,
                ),
                peer,
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        stop.set()
        thread.join()
        sock.close()


def _closed_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --- packet encoding, checked against RFC 2865 section 7.1 -----------------


def test_hide_password_matches_rfc_example() -> None:
    hidden = hide_password(b"arctangent", RFC_SECRET, RFC_AUTHENTICATOR)
    assert hidden == RFC_HIDDEN_PASSWORD


def test_hide_password_chains_blocks_and_pads_empty() -> None:
    long_password = "x" * 40
    hidden = hide_password(long_password.encode(), SECRET, RFC_AUTHENTICATOR)
    assert len(hidden) == 48
    assert _unhide(hidden, SECRET, RFC_AUTHENTICATOR) == long_password
    assert len(hide_password(b"", SECRET, RFC_AUTHENTICATOR)) == 16
    with pytest.raises(ValueError, match="128 bytes"):
        hide_password(b"x" * 129, SECRET, RFC_AUTHENTICATOR)


def test_verify_response_accepts_rfc_example() -> None:
    request = bytes((1, 0, 0, 20)) + RFC_AUTHENTICATOR
    reply = verify_response(RFC_ACCEPT, request=request, secret=RFC_SECRET)

    assert reply.code == ACCESS_ACCEPT
    assert reply.accepted
    assert reply.code_name == "Access-Accept"
    assert not reply.message_authenticator
    assert [(a.name, a.text) for a in reply.attributes] == [
        ("Service-Type", "1"),
        ("Login-Service", "0"),
        ("Login-IP-Host", "192.168.1.3"),
    ]


def test_verify_response_ignores_padding_after_length() -> None:
    request = bytes((1, 0, 0, 20)) + RFC_AUTHENTICATOR
    reply = verify_response(RFC_ACCEPT + b"\0\0", request=request, secret=RFC_SECRET)
    assert reply.accepted


@pytest.mark.parametrize(
    ("packet", "message"),
    [
        (RFC_ACCEPT[:10], "shorter than a header"),
        (RFC_ACCEPT[:1] + b"\x07" + RFC_ACCEPT[2:], "different request"),
        (b"\x05" + RFC_ACCEPT[1:], "unexpected packet code"),
        (RFC_ACCEPT[:2] + b"\x00\x40" + RFC_ACCEPT[4:], "length field"),
        (RFC_ACCEPT[:-1] + b"\x04", "Response Authenticator"),
    ],
)
def test_verify_response_rejects_bad_replies(packet: bytes, message: str) -> None:
    request = bytes((1, 0, 0, 20)) + RFC_AUTHENTICATOR
    with pytest.raises(RadiusError, match=message):
        verify_response(packet, request=request, secret=RFC_SECRET)


def test_verify_response_rejects_wrong_secret() -> None:
    request = bytes((1, 0, 0, 20)) + RFC_AUTHENTICATOR
    with pytest.raises(RadiusError, match="shared secret probably differs"):
        verify_response(RFC_ACCEPT, request=request, secret=b"not-the-secret")


def _sample_request(**overrides: Any) -> bytes:
    options: dict[str, Any] = {
        "identifier": 42,
        "authenticator": RFC_AUTHENTICATOR,
        "username": "alice",
        "password": PASSWORD,
        "secret": SECRET,
        "nas_address": "192.0.2.50",
    }
    options.update(overrides)
    return build_access_request(**options)


def test_build_access_request_layout() -> None:
    request = _sample_request()
    fields = _attributes(request[20:])

    assert request[:2] == bytes((1, 42))
    assert int.from_bytes(request[2:4], "big") == len(request)
    assert request[4:20] == RFC_AUTHENTICATOR
    assert fields[0][0] == 80  # Message-Authenticator first
    assert dict(fields)[1] == b"alice"
    assert _unhide(dict(fields)[2], SECRET, RFC_AUTHENTICATOR) == PASSWORD
    assert dict(fields)[32] == b"axlib"
    assert dict(fields)[4] == bytes((192, 0, 2, 50))
    assert _request_mac_ok(request)


def test_build_access_request_ipv6_nas_address() -> None:
    fields = dict(_attributes(_sample_request(nas_address="2001:db8::5")[20:]))
    assert 4 not in fields
    assert len(fields[95]) == 16


def test_message_authenticator_detects_tampering() -> None:
    request = bytearray(_sample_request())
    request[-1] ^= 1
    assert not _request_mac_ok(bytes(request))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"identifier": 256}, "identifier"),
        ({"authenticator": b"short"}, "16 bytes"),
        ({"username": ""}, "User-Name"),
        ({"username": "u" * 254}, "User-Name"),
    ],
)
def test_build_access_request_validates(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _sample_request(**overrides)


def test_attribute_text_formats() -> None:
    assert RadiusAttribute(18, b"hi").text == "hi"
    assert RadiusAttribute(27, (3600).to_bytes(4, "big")).text == "3600"
    assert RadiusAttribute(25, b"\x00\xff").text == "0x00ff"
    assert RadiusAttribute(25, b"CACS:abc").text == "CACS:abc"
    assert RadiusAttribute(200, b"x").name == "Attribute-200"
    vsa = b"\0\0\0\x09\x01\x13shell:priv-lvl=15"
    assert RadiusAttribute(26, vsa).text == "vendor=9 type=1 value=shell:priv-lvl=15"
    assert RadiusAttribute(26, b"\0\0\0\x09\x01\x40ab").text == "vendor=9 0x0140" + (
        b"ab".hex()
    )
    assert RadiusAttribute(26, b"\0\x09").text == "0x0009"


# --- network exchange with a local server --------------------------------


def test_authenticate_accept() -> None:
    with fake_server() as server:
        result = authenticate(
            "127.0.0.1", "alice", PASSWORD, SECRET.decode(), port=server.port
        )

    assert result.accepted
    assert result.attempts == 1
    assert result.address == "127.0.0.1"
    assert result.reply.message_authenticator
    assert result.reply.reply_messages == ("Welcome",)
    assert "c0rrect" not in repr(result)
    assert dict(_attributes(server.requests[0][20:]))[4] == bytes((127, 0, 0, 1))


def test_authenticate_reject_is_a_result_not_an_error() -> None:
    with fake_server() as server:
        result = authenticate("localhost", "alice", "wrong", SECRET, port=server.port)
    assert not result.accepted
    assert result.reply.code_name == "Access-Reject"
    assert result.reply.reply_messages == ("Bad password",)


def test_authenticate_retransmits_identical_packet() -> None:
    with fake_server(drop_first=1) as server:
        result = authenticate(
            "127.0.0.1", "alice", PASSWORD, SECRET, port=server.port, timeout=0.2
        )
    assert result.attempts == 2
    assert server.requests[0] == server.requests[1]


def test_authenticate_reply_without_message_authenticator() -> None:
    with fake_server(with_mac=False) as server:
        result = authenticate("127.0.0.1", "alice", PASSWORD, SECRET, port=server.port)
    assert result.accepted
    assert not result.reply.message_authenticator


def test_authenticate_detects_secret_mismatch() -> None:
    with (
        fake_server(reply_secret=b"other-secret") as server,
        pytest.raises(RadiusError, match="shared secret"),
    ):
        authenticate("127.0.0.1", "alice", PASSWORD, SECRET, port=server.port)


def test_authenticate_times_out() -> None:
    with (
        fake_server(drop_first=99) as server,
        pytest.raises(RadiusTimeoutError, match="after 2 attempt"),
    ):
        authenticate(
            "127.0.0.1",
            "alice",
            PASSWORD,
            SECRET,
            port=server.port,
            timeout=0.1,
            retries=1,
        )
    assert len(server.requests) == 2


def test_authenticate_reports_closed_port() -> None:
    with pytest.raises(RadiusError, match="nothing is listening"):
        authenticate(
            "127.0.0.1", "alice", PASSWORD, SECRET, port=_closed_udp_port(), timeout=1
        )


def test_authenticate_reports_unresolvable_server() -> None:
    with pytest.raises(RadiusError, match="cannot resolve"):
        authenticate("no-such-host.invalid", "alice", PASSWORD, SECRET)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"secret": ""}, "secret"),
        ({"port": 0}, "port"),
        ({"timeout": 0}, "timeout"),
        ({"retries": -1}, "retries"),
    ],
)
def test_authenticate_validates_arguments(options: dict, message: str) -> None:
    arguments: dict = {"secret": SECRET, **options}
    with pytest.raises(ValueError, match=message):
        authenticate("127.0.0.1", "alice", PASSWORD, **arguments)


# --- command line ----------------------------------------------------------


def _run(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = main(argv, stdout=out, stderr=err, **kwargs)
    return status, out.getvalue(), err.getvalue()


def test_cli_accept_json_with_env_and_secret_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "radius.secret"
    secret_file.write_text(SECRET.decode() + "\n", encoding="utf-8")
    with fake_server() as server:
        status, out, err = _run(
            [
                "127.0.0.1",
                "-p",
                str(server.port),
                "-u",
                "alice",
                "--password-env",
                "NETPASS",
                "--secret-file",
                str(secret_file),
                "--json",
            ],
            environ={"NETPASS": PASSWORD},
        )

    assert (status, err) == (EXIT_ACCEPT, "")
    document = json.loads(out)
    assert document["result"] == "Access-Accept"
    assert document["accepted"] is True
    assert document["message_authenticator"] is True
    assert document["reply_messages"] == ["Welcome"]
    assert {
        "type": 26,
        "name": "Vendor-Specific",
        "value": ("vendor=9 type=1 value=shell:priv-lvl=15"),
    } in document["attributes"]
    assert PASSWORD not in out


def test_cli_reject_text_with_stdin_and_prompt() -> None:
    prompts: list[str] = []

    def prompt(text: str) -> str:
        prompts.append(text)
        return SECRET.decode()

    with fake_server() as server:
        status, out, _ = _run(
            ["localhost", "-p", str(server.port), "-u", "bob", "--password-stdin"],
            stdin=io.StringIO("wrong\n"),
            prompt=prompt,
        )

    assert status == EXIT_REJECT
    assert prompts == ["Shared secret for localhost: "]
    assert out.splitlines()[0].startswith(
        "Access-Reject from localhost (127.0.0.1) port"
    )
    assert "  Reply-Message: Bad password" in out
    assert out.splitlines()[-1] == "  Message-Authenticator: verified"


def test_cli_prompts_for_password_and_accepts_direct_secret() -> None:
    with fake_server() as server:
        status, out, _ = _run(
            [
                "127.0.0.1",
                "-p",
                str(server.port),
                "-u",
                "alice",
                "--secret",
                SECRET.decode(),
            ],
            prompt=lambda _text: PASSWORD,
        )
    assert status == EXIT_ACCEPT
    assert out.startswith("Access-Accept from 127.0.0.1 port")


def test_cli_no_reply_exit_status() -> None:
    with fake_server(drop_first=99) as server:
        status, out, err = _run(
            [
                "127.0.0.1",
                "-p",
                str(server.port),
                "-u",
                "alice",
                "--password",
                "x",
                "--secret-env",
                "S",
                "--timeout",
                "0.05",
                "--retries",
                "0",
            ],
            environ={"S": "s"},
        )
    assert (status, out) == (EXIT_NO_REPLY, "")
    assert err.startswith("radius: no reply")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--password-env", "UNSET", "--secret", "s"], "'UNSET' is not set"),
        (["--password", "x", "--secret-file", "/nonexistent/secret"], "No such"),
    ],
)
def test_cli_input_errors(extra: list[str], message: str) -> None:
    status, out, err = _run(["127.0.0.1", "-u", "alice", *extra], environ={})
    assert (status, out) == (EXIT_ERROR, "")
    assert message in err


def test_cli_empty_secret_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "empty"
    secret_file.write_text("\n", encoding="utf-8")
    status, _, err = _run(
        [
            "127.0.0.1",
            "-u",
            "alice",
            "--password",
            "x",
            "--secret-file",
            str(secret_file),
        ]
    )
    assert status == EXIT_ERROR
    assert "does not start with a secret" in err


def test_axlib_dispatcher_runs_radius(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        axlib_main(["radius", "--help"])
    assert stopped.value.code == 0
    assert "Access-Request" in capsys.readouterr().out


def _resign(reply: bytes, request: bytes, body: bytes) -> bytes:
    """Rebuild a reply around ``body`` with a correct Response Authenticator."""
    header = reply[:2] + (20 + len(body)).to_bytes(2, "big")
    return header + _md5(header + request[4:20] + body + SECRET) + body


def test_verify_response_rejects_forged_message_authenticator() -> None:
    # A forger who can fix the Response Authenticator (the BlastRADIUS attack)
    # still cannot produce the HMAC, so the reply must be refused.
    request = _sample_request()
    reply = _sign_reply(request, ACCESS_ACCEPT, [(18, b"hi")])
    body = bytearray(reply[20:])
    body[2] ^= 0xFF
    with pytest.raises(RadiusError, match="Message-Authenticator is wrong"):
        verify_response(
            _resign(reply, request, bytes(body)), request=request, secret=SECRET
        )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"\x12\x09hi", "malformed attribute"),
        (b"\x50\x05abc", "malformed Message-Authenticator"),
    ],
)
def test_verify_response_rejects_malformed_attributes(
    body: bytes, message: str
) -> None:
    request = _sample_request()
    reply = _resign(bytes((ACCESS_ACCEPT, request[1], 0, 0)), request, body)
    with pytest.raises(RadiusError, match=message):
        verify_response(reply, request=request, secret=SECRET)
