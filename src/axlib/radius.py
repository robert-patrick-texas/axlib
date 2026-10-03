# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Check a username and password against a RADIUS server (RFC 2865).

Network devices hand logins to a RADIUS server (FreeRADIUS, Cisco ISE, Aruba
ClearPass, Microsoft NPS, ...) and act on its answer.  When a login fails it is
useful to ask the server directly, from an automation host, whether it accepts
the account: the answer separates "the server rejects this user" from "the
device never reached the server" or "the device and server disagree on the
shared secret".  This module is that RADIUS client.  It sends one
``Access-Request`` and reports ``Access-Accept``, ``Access-Reject``, or
``Access-Challenge`` together with any attributes the server returned, such as
``Reply-Message`` or a Cisco ``shell:priv-lvl=15`` vendor attribute.

How a RADIUS exchange works (and what each part of this module does):

* One UDP datagram goes to the server (port 1812 by default) and one comes
  back.  UDP has no delivery guarantee, so the client resends the *identical*
  packet after a timeout (:func:`authenticate`, ``retries``).
* Every packet is a 20-byte header (code, identifier, length, 16-byte
  authenticator) followed by attributes encoded as type-length-value
  (:func:`build_access_request`).
* The client and server share a secret that never crosses the network.  The
  password is hidden by XOR with MD5 hashes of that secret and a random
  *Request Authenticator* (:func:`hide_password`).
* The server proves its reply is genuine with a *Response Authenticator*, an
  MD5 hash over the reply, the request's authenticator, and the secret
  (:func:`verify_response`).  A mismatch almost always means the two sides
  have different shared secrets.
* Every request also carries a ``Message-Authenticator`` attribute, an
  HMAC-MD5 of the whole packet.  The 2024 "BlastRADIUS" attack
  (CVE-2024-3596) forges replies to requests that lack it, and current servers
  can be configured to drop such requests.  When a reply carries one too, it
  is verified and reported.

Scope: PAP (a plain password) only.  CHAP, MS-CHAP, EAP, accounting, and
RADIUS over TLS are not implemented, and an ``Access-Challenge`` (for example
a one-time-password prompt) is reported rather than answered.  The hidden
password is only as strong as MD5 and the shared secret, so run checks over a
trusted management network.

Dependencies:
    Python standard library only.

CLI example:
    ``axlib radius 192.0.2.10 -u alice --secret-file /etc/axlib/radius.secret``

Python example::

    from axlib.radius import authenticate

    result = authenticate("192.0.2.10", "alice", password, secret)
    if result.accepted:
        print("accepted", result.reply.reply_messages)
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import struct
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import TextIO

DEFAULT_PORT = 1812
DEFAULT_TIMEOUT = 3.0
DEFAULT_RETRIES = 2
DEFAULT_NAS_IDENTIFIER = "axlib"

# Packet codes (RFC 2865 section 3).
ACCESS_REQUEST = 1
ACCESS_ACCEPT = 2
ACCESS_REJECT = 3
ACCESS_CHALLENGE = 11
CODE_NAMES: Mapping[int, str] = MappingProxyType(
    {
        ACCESS_REQUEST: "Access-Request",
        ACCESS_ACCEPT: "Access-Accept",
        ACCESS_REJECT: "Access-Reject",
        ACCESS_CHALLENGE: "Access-Challenge",
    }
)
REPLY_CODES = frozenset({ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE})

# Attribute types this client sends or treats specially.
USER_NAME = 1
USER_PASSWORD = 2
NAS_IP_ADDRESS = 4
REPLY_MESSAGE = 18
VENDOR_SPECIFIC = 26
NAS_IDENTIFIER = 32
MESSAGE_AUTHENTICATOR = 80
NAS_IPV6_ADDRESS = 95

# Name and value format of attributes that replies commonly carry.  Anything
# else is still returned, named "Attribute-<number>".  A full RADIUS
# dictionary has hundreds of entries; a check tool needs only these.
ATTRIBUTES: Mapping[int, tuple[str, str]] = MappingProxyType(
    {
        USER_NAME: ("User-Name", "text"),
        6: ("Service-Type", "integer"),
        7: ("Framed-Protocol", "integer"),
        8: ("Framed-IP-Address", "address"),
        11: ("Filter-Id", "text"),
        14: ("Login-IP-Host", "address"),
        15: ("Login-Service", "integer"),
        REPLY_MESSAGE: ("Reply-Message", "text"),
        24: ("State", "octets"),
        25: ("Class", "octets"),
        VENDOR_SPECIFIC: ("Vendor-Specific", "vendor"),
        27: ("Session-Timeout", "integer"),
        28: ("Idle-Timeout", "integer"),
        MESSAGE_AUTHENTICATOR: ("Message-Authenticator", "octets"),
    }
)

# "!" means network byte order (big-endian); B is one byte, H two bytes, and
# 16s a 16-byte string: code, identifier, length, authenticator.
HEADER = struct.Struct("!BBH16s")
AUTHENTICATOR_LENGTH = 16
MAX_PACKET_LENGTH = 4096
MAX_ATTRIBUTE_VALUE = 253  # the one-byte length field also counts type and length
MAX_PASSWORD_LENGTH = 128

# Exit statuses are part of the command's interface, so monitoring scripts can
# branch on them; each outcome keeps one stable number.
EXIT_ACCEPT = 0
EXIT_REJECT = 1
EXIT_ERROR = 2
EXIT_NO_REPLY = 3


class RadiusError(Exception):
    """The RADIUS check could not produce a trustworthy answer."""


class RadiusTimeoutError(RadiusError):
    """No valid reply arrived before the last retry timed out."""


@dataclass(frozen=True, slots=True)
class RadiusAttribute:
    """One type-length-value attribute from a RADIUS packet.

    Attributes:
        number: Attribute type, such as ``18`` for ``Reply-Message``.
        value: Raw value bytes, without the type and length octets.
    """

    number: int
    value: bytes

    @property
    def name(self) -> str:
        """Return the attribute's dictionary name.

        Args:
            None: The name is looked up from :attr:`number`.

        Returns:
            str: A name such as ``Reply-Message``, or ``Attribute-<number>``
                for a type this module does not list.

        Raises:
            None: Unknown types get a generated name.
        """
        return ATTRIBUTES.get(self.number, (f"Attribute-{self.number}", ""))[0]

    @property
    def text(self) -> str:
        """Return the value formatted for an operator to read.

        Args:
            None: The format is chosen from :data:`ATTRIBUTES`.

        Returns:
            str: Text, a decimal number, an IP address, a vendor summary, or
                ``0x``-prefixed hex for binary data.

        Raises:
            None: A value that does not fit its format is shown as hex.
        """
        kind = ATTRIBUTES.get(self.number, ("", "octets"))[1]
        if kind == "text":
            return self.value.decode("utf-8", errors="replace")
        if kind == "integer" and len(self.value) == 4:
            return str(int.from_bytes(self.value, "big"))
        if kind == "address" and len(self.value) == 4:
            return str(ipaddress.IPv4Address(self.value))
        if kind == "vendor":
            return _vendor_text(self.value)
        return _text_or_hex(self.value)


@dataclass(frozen=True, slots=True)
class RadiusReply:
    """A server reply whose authenticators have been verified.

    Attributes:
        code: Packet code: ``2`` Accept, ``3`` Reject, or ``11`` Challenge.
        attributes: Every attribute in the order the server sent them.
        message_authenticator: ``True`` when the reply carried a
            ``Message-Authenticator`` and it matched.
    """

    code: int
    attributes: tuple[RadiusAttribute, ...]
    message_authenticator: bool

    @property
    def code_name(self) -> str:
        """Return the RFC name of the reply code.

        Args:
            None: The name is looked up from :attr:`code`.

        Returns:
            str: ``Access-Accept``, ``Access-Reject``, or ``Access-Challenge``.

        Raises:
            None: Every code a verified reply can hold is listed.
        """
        return CODE_NAMES.get(self.code, f"Code-{self.code}")

    @property
    def accepted(self) -> bool:
        """Return whether the server accepted the credentials.

        Args:
            None: The answer comes from :attr:`code`.

        Returns:
            bool: ``True`` only for ``Access-Accept``.

        Raises:
            None: A single comparison.
        """
        return self.code == ACCESS_ACCEPT

    @property
    def reply_messages(self) -> tuple[str, ...]:
        """Return the text of every ``Reply-Message`` attribute.

        Args:
            None: The messages are read from :attr:`attributes`.

        Returns:
            tuple[str, ...]: Messages for the user, often explaining a reject.

        Raises:
            None: Text decoding replaces invalid bytes.
        """
        return tuple(a.text for a in self.attributes if a.number == REPLY_MESSAGE)


@dataclass(frozen=True, slots=True)
class RadiusResult:
    """Outcome of :func:`authenticate`.  It never holds the password or secret.

    Attributes:
        server: Server name or address as the caller gave it.
        address: IP address the request was actually sent to.
        port: UDP port the request was sent to.
        username: User name that was checked.
        attempts: Transmissions needed, ``1`` when the first one was answered.
        elapsed_ms: Milliseconds from the first transmission to the reply.
        reply: The verified server reply.
    """

    server: str
    address: str
    port: int
    username: str
    attempts: int
    elapsed_ms: float
    reply: RadiusReply

    @property
    def accepted(self) -> bool:
        """Return whether the server accepted the credentials.

        Args:
            None: Delegates to :attr:`RadiusReply.accepted`.

        Returns:
            bool: ``True`` only for ``Access-Accept``.

        Raises:
            None: A single comparison.
        """
        return self.reply.accepted

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready summary for scripts and monitoring tools.

        Args:
            None: Every value comes from this result.

        Returns:
            dict[str, object]: Plain strings, numbers, booleans, and lists.

        Raises:
            None: Only builds dictionaries and lists.
        """
        return {
            "result": self.reply.code_name,
            "accepted": self.accepted,
            "code": self.reply.code,
            "server": self.server,
            "address": self.address,
            "port": self.port,
            "username": self.username,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "message_authenticator": self.reply.message_authenticator,
            "reply_messages": list(self.reply.reply_messages),
            "attributes": [
                {"type": a.number, "name": a.name, "value": a.text}
                for a in self.reply.attributes
            ],
        }


def _text_or_hex(value: bytes) -> str:
    """Show bytes as text when they are printable, otherwise as hex.

    Args:
        value (bytes): Attribute value of unknown or binary format.

    Returns:
        str: The decoded text, or ``0x`` followed by hex digits.

    Raises:
        None: Undecodable bytes fall back to hex.
    """
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return text if text and text.isprintable() else "0x" + value.hex()


def _vendor_text(value: bytes) -> str:
    """Summarize a ``Vendor-Specific`` attribute (RFC 2865 section 5.26).

    The value starts with a 4-byte IANA enterprise number (``9`` is Cisco,
    ``14823`` Aruba) followed, by convention, by vendor sub-attributes in the
    same type-length-value layout as ordinary attributes.

    Args:
        value (bytes): Raw ``Vendor-Specific`` value.

    Returns:
        str: For example ``vendor=9 type=1 value=shell:priv-lvl=15``, or hex
            when the value does not follow the convention.

    Raises:
        None: Malformed values are shown as hex.
    """
    if len(value) < 6:
        return "0x" + value.hex()
    vendor = int.from_bytes(value[:4], "big")
    parts = []
    offset = 4
    while offset < len(value):
        sub_type, sub_length = value[offset], value[offset + 1 : offset + 2]
        if not sub_length or sub_length[0] < 2 or offset + sub_length[0] > len(value):
            return f"vendor={vendor} 0x{value[4:].hex()}"
        sub_value = value[offset + 2 : offset + sub_length[0]]
        parts.append(f"type={sub_type} value={_text_or_hex(sub_value)}")
        offset += sub_length[0]
    return f"vendor={vendor} " + "; ".join(parts)


def _md5(*parts: bytes) -> bytes:
    """Return the MD5 digest of the concatenated parts.

    RADIUS fixes MD5 as part of the protocol, so there is no stronger hash to
    choose.  ``usedforsecurity=False`` records that choice and lets Python
    running in FIPS mode allow the call.

    Args:
        *parts (bytes): Byte strings hashed in order.

    Returns:
        bytes: 16-byte digest.

    Raises:
        None: Hashing bytes cannot fail.
    """
    digest = hashlib.md5(usedforsecurity=False)
    for part in parts:
        digest.update(part)
    return digest.digest()


def _hmac_md5(key: bytes, data: bytes) -> bytes:
    """Return the HMAC-MD5 used by ``Message-Authenticator`` (RFC 3579).

    Args:
        key (bytes): Shared secret.
        data (bytes): Whole packet with the attribute's value zeroed.

    Returns:
        bytes: 16-byte message authentication code.

    Raises:
        None: Hashing bytes cannot fail.
    """
    return hmac.new(key, data, partial(hashlib.md5, usedforsecurity=False)).digest()


def _attribute(number: int, value: bytes) -> bytes:
    """Encode one attribute as type, length, value.

    Args:
        number (int): Attribute type, 1-255.
        value (bytes): Value bytes, at most 253 of them.

    Returns:
        bytes: The encoded attribute.

    Raises:
        ValueError: If ``value`` is empty or too long for one attribute.
    """
    if not value or len(value) > MAX_ATTRIBUTE_VALUE:
        name = ATTRIBUTES.get(number, (f"attribute {number}", ""))[0]
        raise ValueError(
            f"{name} must be 1-{MAX_ATTRIBUTE_VALUE} bytes, not {len(value)}."
        )
    return bytes((number, len(value) + 2)) + value


def hide_password(password: bytes, secret: bytes, authenticator: bytes) -> bytes:
    """Hide a password for the ``User-Password`` attribute (RFC 2865 5.2).

    The password is padded with zero bytes to a multiple of 16.  The first
    16-byte block is XORed with ``MD5(secret + authenticator)`` and each later
    block with ``MD5(secret + previous hidden block)``.  Only a holder of the
    secret can rebuild those hashes and reverse the XOR.

    Args:
        password (bytes): Password, at most 128 bytes.
        secret (bytes): Shared secret.
        authenticator (bytes): The request's random 16-byte authenticator.

    Returns:
        bytes: Hidden password, 16 to 128 bytes long.

    Raises:
        ValueError: If the password is longer than 128 bytes.
    """
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"RADIUS passwords are limited to {MAX_PASSWORD_LENGTH} bytes, "
            f"not {len(password)}."
        )
    # Ceiling division; an empty password still fills one block.
    blocks = max(1, (len(password) + 15) // 16)
    padded = password.ljust(blocks * 16, b"\0")
    hidden = b""
    previous = authenticator
    for start in range(0, len(padded), 16):
        key = _md5(secret, previous)
        previous = bytes(
            p ^ k for p, k in zip(padded[start : start + 16], key, strict=True)
        )
        hidden += previous
    return hidden


def build_access_request(
    *,
    identifier: int,
    authenticator: bytes,
    username: str,
    password: str,
    secret: bytes,
    nas_identifier: str = DEFAULT_NAS_IDENTIFIER,
    nas_address: str | None = None,
) -> bytes:
    """Encode an ``Access-Request`` packet for a PAP login check.

    ``Message-Authenticator`` is placed first, as the BlastRADIUS advisories
    recommend.  Its HMAC is computed over the finished packet with the field
    set to zeros, then written into place.

    Args:
        identifier (int): Request number 0-255 that the reply must repeat.
        authenticator (bytes): 16 unpredictable bytes from :mod:`secrets`.
        username (str): User name to check.
        password (str): Password to check, at most 128 bytes in UTF-8.
        secret (bytes): Shared secret configured for this client on the server.
        nas_identifier (str): Name identifying this client to the server; a
            request must carry a NAS identifier or NAS address.
        nas_address (str | None): This host's IPv4 or IPv6 address, sent as
            ``NAS-IP-Address`` or ``NAS-IPv6-Address`` when given.

    Returns:
        bytes: The complete packet, ready to send.

    Raises:
        ValueError: If an argument is out of range or a value is too long.
    """
    if not 0 <= identifier <= 255:
        raise ValueError("identifier must be 0-255.")
    if len(authenticator) != AUTHENTICATOR_LENGTH:
        raise ValueError("authenticator must be 16 bytes.")
    body = (
        _attribute(MESSAGE_AUTHENTICATOR, bytes(AUTHENTICATOR_LENGTH))
        + _attribute(USER_NAME, username.encode())
        + _attribute(
            USER_PASSWORD, hide_password(password.encode(), secret, authenticator)
        )
        + _attribute(NAS_IDENTIFIER, nas_identifier.encode())
    )
    if nas_address:
        address = ipaddress.ip_address(nas_address)
        number = NAS_IP_ADDRESS if address.version == 4 else NAS_IPV6_ADDRESS
        body += _attribute(number, address.packed)
    header = HEADER.pack(
        ACCESS_REQUEST, identifier, HEADER.size + len(body), authenticator
    )
    packet = header + body
    # The HMAC value starts after the header and the attribute's two octets.
    start = HEADER.size + 2
    mac = _hmac_md5(secret, packet)
    return packet[:start] + mac + packet[start + AUTHENTICATOR_LENGTH :]


def _parse_attributes(data: bytes) -> tuple[RadiusAttribute, ...]:
    """Split the attribute section of a packet into attributes.

    Args:
        data (bytes): Packet bytes after the 20-byte header.

    Returns:
        tuple[RadiusAttribute, ...]: Attributes in packet order.

    Raises:
        RadiusError: If an attribute's length runs past the packet.
    """
    attributes = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 2 or not 2 <= data[offset + 1] <= len(data) - offset:
            raise RadiusError(f"reply has a malformed attribute at byte {offset + 20}.")
        length = data[offset + 1]
        attributes.append(
            RadiusAttribute(data[offset], data[offset + 2 : offset + length])
        )
        offset += length
    return tuple(attributes)


def _check_message_authenticator(
    header: bytes,
    request_authenticator: bytes,
    attributes: tuple[RadiusAttribute, ...],
    secret: bytes,
) -> bool:
    """Verify a reply's ``Message-Authenticator`` when it carries one.

    For a reply, the HMAC covers the packet with the *request's* authenticator
    in the header and the attribute's own value set to zeros.

    Args:
        header (bytes): The reply's first four bytes (code, identifier, length).
        request_authenticator (bytes): Authenticator of our request.
        attributes (tuple[RadiusAttribute, ...]): Parsed reply attributes.
        secret (bytes): Shared secret.

    Returns:
        bool: ``True`` if present and correct, ``False`` if absent.

    Raises:
        RadiusError: If the attribute is repeated, malformed, or wrong.
    """
    found = [a for a in attributes if a.number == MESSAGE_AUTHENTICATOR]
    if not found:
        return False
    if len(found) > 1 or len(found[0].value) != AUTHENTICATOR_LENGTH:
        raise RadiusError("reply has a malformed Message-Authenticator.")
    zeroed = b"".join(
        _attribute(
            a.number,
            bytes(AUTHENTICATOR_LENGTH)
            if a.number == MESSAGE_AUTHENTICATOR
            else a.value,
        )
        for a in attributes
    )
    expected = _hmac_md5(secret, header + request_authenticator + zeroed)
    if not hmac.compare_digest(expected, found[0].value):
        raise RadiusError(
            "reply's Message-Authenticator is wrong; the shared secret probably "
            "differs from the server's."
        )
    return True


def verify_response(packet: bytes, *, request: bytes, secret: bytes) -> RadiusReply:
    """Check that a reply answers ``request`` and was signed with ``secret``.

    The Response Authenticator is ``MD5(code + identifier + length + request
    authenticator + attributes + secret)``.  Only a holder of the secret can
    produce it, so a match proves the reply came from the server and was not
    changed on the way.

    Args:
        packet (bytes): Datagram received from the server.
        request (bytes): The ``Access-Request`` it should answer.
        secret (bytes): Shared secret.

    Returns:
        RadiusReply: Code and attributes of the verified reply.

    Raises:
        RadiusError: If the reply is malformed, answers a different request,
            or fails either authenticator check.
    """
    if len(packet) < HEADER.size:
        raise RadiusError(f"reply is {len(packet)} bytes, shorter than a header.")
    code, identifier, length, authenticator = HEADER.unpack_from(packet)
    if identifier != request[1]:
        raise RadiusError("reply answers a different request identifier.")
    if code not in REPLY_CODES:
        raise RadiusError(f"reply has unexpected packet code {code}.")
    if not HEADER.size <= length <= len(packet):
        raise RadiusError(f"reply length field {length} does not match the datagram.")
    # RFC 2865 section 3: bytes past the length field are padding to ignore.
    packet = packet[:length]
    request_authenticator = request[4 : HEADER.size]
    expected = _md5(packet[:4], request_authenticator, packet[HEADER.size :], secret)
    if not hmac.compare_digest(expected, authenticator):
        raise RadiusError(
            "reply's Response Authenticator is wrong; the shared secret probably "
            "differs from the server's."
        )
    attributes = _parse_attributes(packet[HEADER.size :])
    signed = _check_message_authenticator(
        packet[:4], request_authenticator, attributes, secret
    )
    return RadiusReply(code=code, attributes=attributes, message_authenticator=signed)


def _await_reply(
    sock: socket.socket, request: bytes, secret: bytes, timeout: float
) -> RadiusReply | None:
    """Wait up to ``timeout`` seconds for the reply to ``request``.

    Args:
        sock (socket.socket): UDP socket connected to the server.
        request (bytes): The request that was just sent.
        secret (bytes): Shared secret.
        timeout (float): Seconds to wait.

    Returns:
        RadiusReply | None: The verified reply, or ``None`` on timeout.

    Raises:
        RadiusError: If the server's host reports the port closed, or a reply
            fails verification.
    """
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        sock.settimeout(remaining)
        try:
            data = sock.recv(MAX_PACKET_LENGTH)
        except TimeoutError:
            return None
        except ConnectionRefusedError as exc:
            # A connected UDP socket reports the ICMP "port unreachable" that
            # the server's host sends back when nothing listens on the port.
            raise RadiusError(
                "the server's host answered that nothing is listening on this "
                "UDP port (ICMP port unreachable)."
            ) from exc
        if len(data) >= 2 and data[1] != request[1]:
            continue  # a late reply to some other request: discard it
        return verify_response(data, request=request, secret=secret)
    return None


def authenticate(
    server: str,
    username: str,
    password: str,
    secret: str | bytes,
    *,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    nas_identifier: str = DEFAULT_NAS_IDENTIFIER,
) -> RadiusResult:
    """Ask a RADIUS server whether it accepts a username and password.

    A reject or challenge is a normal answer and is returned, not raised;
    check :attr:`RadiusResult.accepted`.  Exceptions mean there is no
    trustworthy answer at all.

    Args:
        server (str): Server host name, IPv4 address, or IPv6 address.
        username (str): User name to check.
        password (str): Password to check.
        secret (str | bytes): Shared secret the server has configured for
            this host.
        port (int): Server UDP port, 1812 by default.
        timeout (float): Seconds to wait for a reply to each transmission.
        retries (int): Extra transmissions after the first times out.
        nas_identifier (str): Name this client reports in ``NAS-Identifier``.

    Returns:
        RadiusResult: The verified reply plus timing and addressing details.

    Raises:
        ValueError: If an argument is empty, out of range, or too long.
        RadiusTimeoutError: If no reply arrives after every retry.
        RadiusError: If the server cannot be resolved or reached, or its reply
            fails verification.
    """
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    if not secret_bytes:
        raise ValueError("the shared secret cannot be empty.")
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be 1-65535, not {port}.")
    if timeout <= 0 or retries < 0:
        raise ValueError("timeout must be positive and retries zero or more.")
    try:
        family, kind, protocol, _, address = socket.getaddrinfo(
            server, port, type=socket.SOCK_DGRAM
        )[0]
    except socket.gaierror as exc:
        raise RadiusError(f"cannot resolve {server!r}: {exc.strerror}") from exc

    identifier = secrets.randbelow(256)
    # The Request Authenticator must be unpredictable (RFC 2865 section 3),
    # so it comes from the operating system's secure random source.
    authenticator = secrets.token_bytes(AUTHENTICATOR_LENGTH)
    try:
        with socket.socket(family, kind, protocol) as sock:
            # connect() on UDP sends nothing; it fixes the destination so the
            # kernel delivers only datagrams from the server, and it chooses
            # the local address reported as this client's NAS address.
            sock.connect(address)
            request = build_access_request(
                identifier=identifier,
                authenticator=authenticator,
                username=username,
                password=password,
                secret=secret_bytes,
                nas_identifier=nas_identifier,
                nas_address=sock.getsockname()[0],
            )
            started = time.perf_counter()
            for attempt in range(1, retries + 2):
                # Retransmissions resend the identical packet, so a late reply
                # to an earlier copy is still a valid answer.
                sock.send(request)
                reply = _await_reply(sock, request, secret_bytes, timeout)
                if reply is not None:
                    return RadiusResult(
                        server=server,
                        address=str(address[0]),
                        port=port,
                        username=username,
                        attempts=attempt,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                        reply=reply,
                    )
    except OSError as exc:
        raise RadiusError(f"cannot reach {server} port {port}: {exc}") from exc
    raise RadiusTimeoutError(
        f"no reply from {server} port {port} after {retries + 1} attempt(s) of "
        f"{timeout:g} s. Check that the server is reachable on UDP {port}, that "
        "it lists this host as a RADIUS client, and that the shared secret "
        "matches (many servers silently drop requests signed with a wrong one)."
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the ``axlib radius`` argument parser.

    Args:
        None: Options are defined by this module.

    Returns:
        argparse.ArgumentParser: Parser for the RADIUS check command.

    Raises:
        None: Parser construction performs no network access.
    """
    parser = argparse.ArgumentParser(
        prog="axlib radius",
        description=(
            "Send one RADIUS Access-Request (PAP) and report whether the server "
            "accepts the username and password."
        ),
        epilog=(
            "The password and shared secret are prompted for when no other "
            "source is given. Values passed with --password or --secret are "
            "visible to other users in ps and kept in shell history. "
            "Exit status: 0 Access-Accept, 1 Access-Reject or Access-Challenge, "
            "2 error, 3 no reply."
        ),
    )
    parser.add_argument("server", help="RADIUS server host name or IP address.")
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Server UDP port (default: {DEFAULT_PORT}).",
    )
    parser.add_argument("-u", "--username", required=True, help="User name to check.")
    password = parser.add_mutually_exclusive_group()
    password.add_argument("--password", help="Password (visible in ps; avoid).")
    password.add_argument(
        "--password-env",
        metavar="VAR",
        help="Read the password from environment variable VAR, such as NETPASS.",
    )
    password.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of standard input.",
    )
    secret = parser.add_mutually_exclusive_group()
    secret.add_argument("--secret", help="Shared secret (visible in ps; avoid).")
    secret.add_argument(
        "--secret-env",
        metavar="VAR",
        help="Read the shared secret from environment variable VAR.",
    )
    secret.add_argument(
        "--secret-file",
        metavar="PATH",
        help="Read the shared secret from the first line of PATH.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Seconds to wait for each reply (default: {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Resends after a timeout (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "--nas-identifier",
        default=DEFAULT_NAS_IDENTIFIER,
        help=f"NAS-Identifier sent to the server (default: {DEFAULT_NAS_IDENTIFIER}).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the result as one JSON object."
    )
    return parser


def _from_environment(variable: str, environ: Mapping[str, str]) -> str:
    """Return the value of a named environment variable.

    Args:
        variable (str): Variable name given on the command line.
        environ (Mapping[str, str]): Environment to read.

    Returns:
        str: The variable's exact value.

    Raises:
        ValueError: If the variable is not set.
    """
    if variable not in environ:
        raise ValueError(f"environment variable {variable!r} is not set.")
    return environ[variable]


def _read_inputs(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    stdin: TextIO,
    prompt: Callable[[str], str],
) -> tuple[str, str]:
    """Collect the password and shared secret from the chosen sources.

    Args:
        args (argparse.Namespace): Parsed command-line options.
        environ (Mapping[str, str]): Environment for ``--*-env`` options.
        stdin (TextIO): Stream for ``--password-stdin``.
        prompt (Callable[[str], str]): Reads a value with echo disabled.

    Returns:
        tuple[str, str]: The password and the shared secret.

    Raises:
        ValueError: If a variable is unset or the secret file is empty.
        OSError: If the secret file cannot be read.
        EOFError: If a prompt has no input to read.
    """
    if args.password is not None:
        password = args.password
    elif args.password_env:
        password = _from_environment(args.password_env, environ)
    elif args.password_stdin:
        password = stdin.readline().rstrip("\r\n")
    else:
        password = prompt(f"Password for {args.username}: ")

    if args.secret is not None:
        secret = args.secret
    elif args.secret_env:
        secret = _from_environment(args.secret_env, environ)
    elif args.secret_file:
        lines = Path(args.secret_file).read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0]:
            raise ValueError(f"{args.secret_file} does not start with a secret.")
        secret = lines[0]
    else:
        secret = prompt(f"Shared secret for {args.server}: ")
    return password, secret


def _render_text(result: RadiusResult) -> str:
    """Format a result as a summary line and one line per attribute.

    Args:
        result (RadiusResult): Outcome of :func:`authenticate`.

    Returns:
        str: Lines for an operator to read.

    Raises:
        None: Only string formatting.
    """
    target = result.server
    if result.address != result.server:
        target += f" ({result.address})"
    lines = [
        (
            f"{result.reply.code_name} from {target} port {result.port} for "
            f"{result.username!r} in {result.elapsed_ms:g} ms "
            f"(attempt {result.attempts})"
        )
    ]
    lines.extend(
        f"  {a.name}: {a.text}"
        for a in result.reply.attributes
        if a.number != MESSAGE_AUTHENTICATOR
    )
    signed = "verified" if result.reply.message_authenticator else "not sent by server"
    lines.append(f"  Message-Authenticator: {signed}")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run ``axlib radius``.

    Args:
        argv (Sequence[str] | None): Arguments excluding the command name;
            defaults to :data:`sys.argv`.
        environ (Mapping[str, str] | None): Environment for ``--*-env``;
            defaults to :data:`os.environ`.
        stdin (TextIO | None): Stream for ``--password-stdin``.
        stdout (TextIO | None): Destination for the result.
        stderr (TextIO | None): Destination for error messages.
        prompt (Callable[[str], str]): Reads secrets with echo disabled.

    Returns:
        int: One of the ``EXIT_*`` statuses defined in this module.

    Raises:
        SystemExit: If argparse rejects the command line.
    """
    args = build_arg_parser().parse_args(argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    try:
        password, secret = _read_inputs(
            args,
            os.environ if environ is None else environ,
            sys.stdin if stdin is None else stdin,
            prompt,
        )
        result = authenticate(
            args.server,
            args.username,
            password,
            secret,
            port=args.port,
            timeout=args.timeout,
            retries=args.retries,
            nas_identifier=args.nas_identifier,
        )
    except RadiusTimeoutError as exc:
        print(f"radius: {exc}", file=err)
        return EXIT_NO_REPLY
    except (RadiusError, ValueError, OSError, EOFError) as exc:
        print(f"radius: {exc}", file=err)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result.to_dict(), indent=2), file=out)
    else:
        print(_render_text(result), file=out)
    return EXIT_ACCEPT if result.accepted else EXIT_REJECT


if __name__ == "__main__":
    raise SystemExit(main())
