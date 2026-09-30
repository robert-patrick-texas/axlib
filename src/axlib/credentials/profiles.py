# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Credential record profiles: which fields a stored service should contain.

axlib stores credentials as named *services* (``jsmith``, ``networkshared``,
``infoblox``), each holding a few named *fields*.  The lookup helpers expect
particular field names:

* ``ax.getkeys()`` reads ``netuser``, ``netpass``, and ``netenable``.
* ``ax.getinfoblox()`` reads ``ibgrid``, ``ibuser``, and ``ibpass``.

Every profile also allows an optional ``note``: free text for people, such as
"lab account, expires in June".  No lookup helper reads it, so a note never
changes what ``ax.getkeys()`` returns.  It is stored inside the encrypted
record like every other field, and listings show it because it is not secret.

A *profile* writes those expectations down once.  The Python admin API
(:mod:`axlib.credentials.admin`), both administration CLIs, and the optional TUI
all validate against the same profile objects, so an operator can never create
a record that one tool accepts but ``ax.getkeys()`` cannot use.

Run this module on its own to see what each profile expects.  The JSON form is
convenient in pipelines::

    python -m axlib.credentials.profiles
    python -m axlib.credentials.profiles --json | jq -r '.[].fields[].name'

Dependencies:
    Python standard library only.

Example:
    >>> from axlib.credentials.profiles import NETWORK_PROFILE, validate_values
    >>> values = {"netuser": "ops", "netpass": "example"}
    >>> sorted(validate_values(values, NETWORK_PROFILE, creating=True))
    ['netpass', 'netuser']
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import TextIO


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Describe one credential field within a profile.

    ``frozen=True`` makes instances immutable, so a shared profile cannot be
    changed by accident at runtime; ``slots=True`` keeps each instance small.

    Attributes:
        name: Key stored inside the encrypted record, such as ``netpass``.
        label: Friendly description shown in listings and the TUI.
        secret: ``True`` when the value must never be displayed.
        required: ``True`` when a new record must include this field.
        lookup: ``True`` when the profile's lookup helper reads the field.
            ``False`` for the note, which is only for people.
    """

    name: str
    label: str
    secret: bool
    required: bool
    lookup: bool = True


@dataclass(frozen=True, slots=True)
class RecordProfile:
    """Describe the set of fields that one lookup helper expects.

    Attributes:
        name: Short identifier used by ``--profile`` and JSON output.
        label: Friendly description shown in messages and the TUI.
        fields: Field specifications in display order.
    """

    name: str
    label: str
    fields: tuple[FieldSpec, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        """Return every field name in display order.

        Args:
            None: Names come from this profile's ``fields``.

        Returns:
            tuple[str, ...]: Field names such as ``("netuser", "netpass")``.

        Raises:
            None: Reading attributes of frozen dataclasses cannot fail.
        """
        return tuple(spec.name for spec in self.fields)

    @property
    def required_names(self) -> tuple[str, ...]:
        """Return the fields that a new record must include.

        Args:
            None: Names come from this profile's ``fields``.

        Returns:
            tuple[str, ...]: Required field names in display order.

        Raises:
            None: Reading attributes of frozen dataclasses cannot fail.
        """
        return tuple(spec.name for spec in self.fields if spec.required)

    @property
    def lookup_names(self) -> tuple[str, ...]:
        """Return the fields that the profile's lookup helper reads.

        Args:
            None: Names come from this profile's ``fields``.

        Returns:
            tuple[str, ...]: Field names such as ``("netuser", "netpass",
                "netenable")``; never the note.

        Raises:
            None: Reading attributes of frozen dataclasses cannot fail.
        """
        return tuple(spec.name for spec in self.fields if spec.lookup)


# The one field every profile shares.  Listings, the CLIs, and the TUI refer to
# it by this name, so it is defined once here.
NOTE_FIELD = "note"
# Long enough for a sentence, short enough to stay readable in a table row.
NOTE_MAX_LENGTH = 120
NOTE_SPEC = FieldSpec(NOTE_FIELD, "Note", secret=False, required=False, lookup=False)


NETWORK_PROFILE = RecordProfile(
    name="network",
    label="Network device login",
    fields=(
        FieldSpec("netuser", "Username", secret=False, required=True),
        FieldSpec("netpass", "Password", secret=True, required=True),
        # An enable secret is optional because privilege-15 accounts log in
        # directly to enable mode; ax.getkeys() returns None for it.
        FieldSpec("netenable", "Enable secret", secret=True, required=False),
        NOTE_SPEC,
    ),
)

INFOBLOX_PROFILE = RecordProfile(
    name="infoblox",
    label="Infoblox grid API",
    fields=(
        FieldSpec("ibgrid", "Grid master host", secret=False, required=True),
        FieldSpec("ibuser", "API username", secret=False, required=True),
        FieldSpec("ibpass", "API password", secret=True, required=True),
        NOTE_SPEC,
    ),
)

# MappingProxyType is a read-only view of a dictionary.  Callers can look up
# profiles by name but cannot add or replace one by mistake.
PROFILES: Mapping[str, RecordProfile] = MappingProxyType(
    {profile.name: profile for profile in (NETWORK_PROFILE, INFOBLOX_PROFILE)}
)

DEFAULT_PROFILE = NETWORK_PROFILE


def get_profile(name: str) -> RecordProfile:
    """Return the profile registered under ``name``.

    Args:
        name (str): Profile identifier such as ``network`` or ``infoblox``.

    Returns:
        RecordProfile: The matching immutable profile.

    Raises:
        ValueError: If no profile has that name.
    """
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown credential profile {name!r}; choose from: {', '.join(PROFILES)}."
        ) from exc


def profile_for_fields(fields: Iterable[str]) -> RecordProfile:
    """Guess which profile an existing record was created with.

    Stored records do not remember their profile, so the best match is the
    profile sharing the most field names with the record.

    Args:
        fields (Iterable[str]): Field names present in a stored record.

    Returns:
        RecordProfile: Best-matching profile; the network profile when nothing
            matches, because ``ax.getkeys()`` is the most common consumer.

    Raises:
        None: Set arithmetic on strings cannot fail.
    """
    present = set(fields)
    # max() returns the *first* of several equal scores, and PROFILES lists the
    # network profile first, so it wins ties and the "no match" case.
    return max(
        PROFILES.values(),
        key=lambda profile: len(present.intersection(profile.field_names)),
    )


def missing_required(fields: Iterable[str], profile: RecordProfile) -> tuple[str, ...]:
    """List required fields of ``profile`` that are absent from ``fields``.

    Args:
        fields (Iterable[str]): Field names that are present.
        profile (RecordProfile): Profile whose requirements are checked.

    Returns:
        tuple[str, ...]: Missing required names in display order; empty when
            the record is complete.

    Raises:
        None: Set membership checks on strings cannot fail.
    """
    present = set(fields)
    return tuple(name for name in profile.required_names if name not in present)


def validate_values(
    values: Mapping[str, str],
    profile: RecordProfile,
    *,
    creating: bool,
) -> dict[str, str]:
    """Check a field mapping against a profile before it is stored.

    Args:
        values (Mapping[str, str]): Field names mapped to plaintext values.
        profile (RecordProfile): Profile that defines the allowed fields.
        creating (bool): ``True`` for a brand-new record, which must include
            every required field; ``False`` for a partial update.

    Returns:
        dict[str, str]: A plain-dictionary copy of ``values``, with the note
            stripped of surrounding spaces.

    Raises:
        ValueError: If ``values`` is empty, contains a field outside the
            profile, has an unsuitable note (see :func:`validate_note`), or
            (when ``creating``) lacks a required field.
    """
    prepared = dict(values)
    if not prepared:
        raise ValueError("Specify at least one credential field.")
    if NOTE_FIELD in prepared:
        prepared[NOTE_FIELD] = validate_note(prepared[NOTE_FIELD])

    unsupported = sorted(set(prepared).difference(profile.field_names))
    if unsupported:
        raise ValueError(
            f"{profile.label} records support only "
            f"{', '.join(profile.field_names)}; received: {', '.join(unsupported)}"
        )
    if creating:
        missing = missing_required(prepared, profile)
        if missing:
            raise ValueError(
                f"A new {profile.label} record requires "
                f"{', '.join(profile.required_names)}; missing: {', '.join(missing)}"
            )
    return prepared


def validate_note(note: str) -> str:
    """Check that a note is one short line of readable text.

    Notes appear in one-line table rows and in ``key=value`` output, so a
    newline or a terminal control character would break the layout (or, for
    escape sequences, change the operator's terminal).

    Args:
        note (str): Note text as typed.

    Returns:
        str: The note without leading or trailing spaces.

    Raises:
        ValueError: If the note is blank, longer than
            :data:`NOTE_MAX_LENGTH`, or contains a control character.
    """
    text = note.strip()
    if not text:
        raise ValueError(f"A note cannot be blank; remove the {NOTE_FIELD} field.")
    if len(text) > NOTE_MAX_LENGTH:
        raise ValueError(
            f"A note may have at most {NOTE_MAX_LENGTH} characters; "
            f"this one has {len(text)}."
        )
    if not text.isprintable():
        raise ValueError("A note must be one line without control characters.")
    return text


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for the standalone profile listing.

    Args:
        None: Options are fixed by this module.

    Returns:
        argparse.ArgumentParser: Parser accepting an optional ``--json`` flag.

    Raises:
        None: Constructing argparse objects has no side effects.
    """
    parser = argparse.ArgumentParser(
        prog="python -m axlib.credentials.profiles",
        description="Show which fields each axlib credential profile expects.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write a JSON array instead of a text table.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, stream: TextIO | None = None) -> int:
    """Print every profile as a text table or JSON.

    Args:
        argv (Sequence[str] | None): Arguments excluding the program name.
        stream (TextIO | None): Destination; defaults to standard output.

    Returns:
        int: Always ``0``; listing static data cannot fail.

    Raises:
        SystemExit: If :mod:`argparse` rejects the arguments.
    """
    args = build_arg_parser().parse_args(argv)
    output = sys.stdout if stream is None else stream
    if args.json:
        # asdict() converts nested dataclasses into plain dicts and lists,
        # which is exactly the shape json.dumps() understands.
        payload = [asdict(profile) for profile in PROFILES.values()]
        print(json.dumps(payload, indent=2), file=output)
        return 0

    # Imported here so that importing this data module stays dependency-light.
    from .cli_common import render_table

    rows = [
        (
            profile.name,
            spec.name,
            "yes" if spec.required else "no",
            "yes" if spec.secret else "no",
            "yes" if spec.lookup else "no",
            spec.label,
        )
        for profile in PROFILES.values()
        for spec in profile.fields
    ]
    render_table(
        ("PROFILE", "FIELD", "REQUIRED", "SECRET", "LOOKUP", "DESCRIPTION"),
        rows,
        stream=output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
