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
import ipaddress
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from axlib.__main__ import main as axlib_main
from axlib.radius import (
    ACCESS_ACCEPT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    EXIT_ACCEPT,
    EXIT_ERROR,
    EXIT_NO_REPLY,
    EXIT_REJECT,
    RadiusAttribute,
    RadiusError,
    RadiusTimeoutError,
    authenticate,
    build_access_request,
    build_arg_parser,
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
    """A one-thread RADIUS server answering on one loopback address."""

    port: int
    requests: list[bytes] = field(default_factory=list)
    drop_first: int = 0
    reply_secret: bytes = SECRET
    with_mac: bool = True


@contextmanager
def fake_server(host: str = "127.0.0.1", **options: Any) -> Iterator[FakeServer]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.bind((host, 0))
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


def _resolve_to(
    monkeypatch: pytest.MonkeyPatch, *addresses: tuple[str, int]
) -> list[int]:
    """Make every name resolve to ``addresses``, in that order.

    Returns the list that collects the address family of each lookup, so a
    test can check what ``ip_version`` asked the resolver for.
    """
    families: list[int] = []

    def getaddrinfo(host: str, port: int, **options: Any) -> list[tuple]:
        families.append(options.get("family", socket.AF_UNSPEC))
        return [
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", (address, port, 0, 0))
            if ":" in address
            else (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (address, port))
            for address, port in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return families


def _has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


NEEDS_IPV6 = pytest.mark.skipif(
    not _has_ipv6_loopback(), reason="this host has no IPv6 loopback address"
)
CLOSED_PORT = (
    "the server's host answered that nothing is listening on this UDP port "
    "(ICMP port unreachable)"
)


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


def test_attribute_text_ipv6_address_and_prefix() -> None:
    address = ipaddress.IPv6Address("2001:db8::7").packed
    assert RadiusAttribute(168, address).text == "2001:db8::7"
    assert RadiusAttribute(168, b"\x01\x02").text == "0x0102"
    # RFC 3162: reserved byte, prefix length, then only the bytes it needs.
    prefix = b"\0\x40" + bytes.fromhex("20010db800000001")
    assert RadiusAttribute(97, prefix).text == "2001:db8:0:1::/64"
    assert RadiusAttribute(123, b"\0\x00").text == "::/0"
    assert RadiusAttribute(97, b"\0\x81" + bytes(16)).text == "0x0081" + "00" * 16
    assert RadiusAttribute(97, b"\0").text == "0x00"


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
            "127.0.0.1",
            "alice",
            PASSWORD,
            SECRET,
            port=server.port,
            timeout=0.2,
            retries=1,
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
        ({"ip_version": 5}, "ip_version"),
    ],
)
def test_authenticate_validates_arguments(options: dict, message: str) -> None:
    arguments: dict = {"secret": SECRET, **options}
    with pytest.raises(ValueError, match=message):
        authenticate("127.0.0.1", "alice", PASSWORD, **arguments)


# --- IPv6, and names with several addresses ---------------------------------


@NEEDS_IPV6
def test_authenticate_over_ipv6() -> None:
    with fake_server(host="::1") as server:
        result = authenticate("::1", "alice", PASSWORD, SECRET, port=server.port)

    assert result.accepted
    assert result.address == "::1"
    assert result.failed_addresses == ()
    sent = dict(_attributes(server.requests[0][20:]))
    assert sent[95] == ipaddress.IPv6Address("::1").packed  # NAS-IPv6-Address
    assert 4 not in sent  # no NAS-IP-Address


def test_authenticate_tries_the_next_address_when_the_port_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_server() as server:
        # The server listens on 127.0.0.1 only, so 127.0.0.2 reports the
        # port closed, as ::1 does when a server listens on IPv4 only.
        _resolve_to(monkeypatch, ("127.0.0.2", server.port), ("127.0.0.1", server.port))
        result = authenticate(
            "radius.example", "alice", PASSWORD, SECRET, port=server.port
        )

    assert result.accepted
    assert result.address == "127.0.0.1"
    assert result.failed_addresses == (("127.0.0.2", CLOSED_PORT),)
    assert result.to_dict()["failed_addresses"] == [
        {"address": "127.0.0.2", "error": CLOSED_PORT}
    ]


def test_authenticate_tries_the_next_address_it_cannot_send_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Linux refuses to send to the broadcast address without SO_BROADCAST at
    # once, standing in for an address this host has no route to.
    with fake_server() as server:
        _resolve_to(
            monkeypatch, ("255.255.255.255", server.port), ("127.0.0.1", server.port)
        )
        result = authenticate(
            "radius.example", "alice", PASSWORD, SECRET, port=server.port
        )

    assert result.address == "127.0.0.1"
    [(address, error)] = result.failed_addresses
    assert address == "255.255.255.255"
    assert "Permission denied" in error


def test_authenticate_tries_the_next_address_after_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        fake_server(host="127.0.0.2", drop_first=99) as silent,
        fake_server() as server,
    ):
        _resolve_to(monkeypatch, ("127.0.0.2", silent.port), ("127.0.0.1", server.port))
        result = authenticate(
            "radius.example",
            "alice",
            PASSWORD,
            SECRET,
            port=server.port,
            timeout=0.1,
            retries=1,
        )

    assert result.address == "127.0.0.1"
    assert result.attempts == 1
    assert len(silent.requests) == 2
    assert result.failed_addresses == (
        ("127.0.0.2", "no reply after 2 attempt(s) of 0.1 s"),
    )


def test_authenticate_stops_at_a_reply_that_fails_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reply signed with another secret is an answer, not a missing server:
    # trying the next address would hide the real problem.
    with (
        fake_server(host="127.0.0.2", reply_secret=b"other-secret") as wrong,
        fake_server() as server,
    ):
        _resolve_to(monkeypatch, ("127.0.0.2", wrong.port), ("127.0.0.1", server.port))
        with pytest.raises(RadiusError, match="shared secret"):
            authenticate("radius.example", "alice", PASSWORD, SECRET, port=server.port)
    assert server.requests == []


def test_authenticate_names_every_address_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_server(host="127.0.0.3", drop_first=99) as silent:
        _resolve_to(monkeypatch, ("127.0.0.2", silent.port), ("127.0.0.3", silent.port))
        with pytest.raises(RadiusError) as caught:
            authenticate(
                "radius.example",
                "alice",
                PASSWORD,
                SECRET,
                port=silent.port,
                timeout=0.1,
                retries=0,
            )

    # One address answered with an error, so this is not "no reply".
    assert not isinstance(caught.value, RadiusTimeoutError)
    assert str(caught.value) == (
        f"cannot reach radius.example port {silent.port} at any of its addresses: "
        f"127.0.0.2: {CLOSED_PORT}; 127.0.0.3: no reply after 1 attempt(s) of 0.1 s."
    )


def test_authenticate_times_out_at_every_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        fake_server(host="127.0.0.2", drop_first=99) as first,
        fake_server(host="127.0.0.3", drop_first=99) as second,
    ):
        _resolve_to(monkeypatch, ("127.0.0.2", first.port), ("127.0.0.3", second.port))
        with pytest.raises(RadiusTimeoutError) as caught:
            authenticate(
                "radius.example",
                "alice",
                PASSWORD,
                SECRET,
                port=first.port,
                timeout=0.1,
                retries=0,
            )

    message = str(caught.value)
    assert message.startswith(
        f"no reply from radius.example port {first.port} at any of its addresses "
        "(127.0.0.2, 127.0.0.3) after 1 attempt(s) of 0.1 s each."
    )
    assert "over IPv6" in message
    assert (len(first.requests), len(second.requests)) == (1, 1)


@NEEDS_IPV6
def test_authenticate_defaults_send_one_request_of_one_second_per_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # localhost resolving to ::1 and 127.0.0.1, with a silent server on each:
    # the defaults send one request to each address and wait one second for it.
    with (
        fake_server(host="::1", drop_first=99) as ipv6,
        fake_server(drop_first=99) as ipv4,
    ):
        _resolve_to(monkeypatch, ("::1", ipv6.port), ("127.0.0.1", ipv4.port))
        started = time.monotonic()
        with pytest.raises(
            RadiusTimeoutError, match=r"after 1 attempt\(s\) of 1 s each"
        ):
            authenticate("localhost", "alice", PASSWORD, SECRET, port=ipv4.port)
        elapsed = time.monotonic() - started

    assert (len(ipv6.requests), len(ipv4.requests)) == (1, 1)
    assert 2.0 <= elapsed < 2.9


def test_cli_defaults_match_the_api() -> None:
    args = build_arg_parser().parse_args(["radius.example", "-u", "alice"])
    assert (args.timeout, args.retries) == (DEFAULT_TIMEOUT, DEFAULT_RETRIES)
    assert (DEFAULT_TIMEOUT, DEFAULT_RETRIES) == (1.0, 0)


@pytest.mark.parametrize(
    ("ip_version", "family"),
    [(None, socket.AF_UNSPEC), (4, socket.AF_INET), (6, socket.AF_INET6)],
)
def test_authenticate_ip_version_selects_the_address_family(
    monkeypatch: pytest.MonkeyPatch, ip_version: int | None, family: int
) -> None:
    with fake_server() as server:
        families = _resolve_to(monkeypatch, ("127.0.0.1", server.port))
        authenticate(
            "radius.example",
            "alice",
            PASSWORD,
            SECRET,
            port=server.port,
            ip_version=ip_version,
        )
    assert families == [family]


def test_authenticate_ip_version_without_such_an_address() -> None:
    with pytest.raises(
        RadiusError, match=r"cannot resolve '127\.0\.0\.1' to an IPv6 address"
    ):
        authenticate("127.0.0.1", "alice", PASSWORD, SECRET, ip_version=6)


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
    assert document["failed_addresses"] == []
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


def test_cli_notes_addresses_that_did_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_server() as server:
        _resolve_to(monkeypatch, ("127.0.0.2", server.port), ("127.0.0.1", server.port))
        argv = ["radius.example", "-p", str(server.port), "-u", "alice"]
        argv += ["--password", PASSWORD, "--secret", SECRET.decode()]
        status, out, err = _run(argv)

    assert status == EXIT_ACCEPT
    # The result keeps its format; the address passed over is noted on stderr.
    assert out.startswith(
        f"Access-Accept from radius.example (127.0.0.1) port {server.port}"
    )
    assert (
        err == f"radius: 127.0.0.2 port {server.port} did not answer: {CLOSED_PORT}\n"
    )


@pytest.mark.parametrize(
    ("option", "family"), [("-4", socket.AF_INET), ("--ipv6", socket.AF_INET6)]
)
def test_cli_ip_version_options(
    monkeypatch: pytest.MonkeyPatch, option: str, family: int
) -> None:
    with fake_server() as server:
        families = _resolve_to(monkeypatch, ("127.0.0.1", server.port))
        argv = ["radius.example", option, "-p", str(server.port), "-u", "alice"]
        status, _, _ = _run([*argv, "--password", PASSWORD, "--secret", "testing123"])
    assert (status, families) == (EXIT_ACCEPT, [family])


def test_cli_rejects_both_ip_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["radius.example", "-4", "-6", "-u", "alice"])
    assert stopped.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


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


def _end_of_input(_text: str) -> str:
    """Behave like getpass when input ends (Ctrl-D, or no terminal)."""
    raise EOFError


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (
            ["--secret", "s"],
            (
                "radius: input ended at the password prompt; in a script, use "
                "--password-env or --password-stdin.\n"
            ),
        ),
        (
            ["--password", "x"],
            (
                "radius: input ended at the shared secret prompt; in a script, use "
                "--secret-file or --secret-env.\n"
            ),
        ),
    ],
)
def test_cli_prompt_at_end_of_input(extra: list[str], message: str) -> None:
    status, out, err = _run(
        ["127.0.0.1", "-u", "alice", *extra], environ={}, prompt=_end_of_input
    )

    assert (status, out, err) == (EXIT_ERROR, "", message)


def test_cli_password_stdin_at_end_of_input() -> None:
    with fake_server() as server:
        argv = ["127.0.0.1", "-p", str(server.port), "-u", "alice"]
        argv += ["--password-stdin", "--secret", SECRET.decode()]
        status, out, err = _run(argv, stdin=io.StringIO(""))
        # An empty line is an empty password, which is sent and rejected.
        empty_status, _, _ = _run(argv, stdin=io.StringIO("\n"))

    # No line at all is an error, not an empty password the server rejects.
    assert (status, out) == (EXIT_ERROR, "")
    assert err == (
        "radius: input ended before the password was read from standard input.\n"
    )
    assert empty_status == EXIT_REJECT
    assert len(server.requests) == 1


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
