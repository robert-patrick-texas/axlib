"""Shared command-line helpers for axlib credential-store administration.

Both ``axlib credential-file`` and ``axlib credential-db`` intentionally expose
the same operator workflow.  This module holds their common input-validation and
secret-collection code so the two CLIs cannot drift into subtly different rules.
That consistency is useful on Network Operations hosts where staff may switch
between the text and SQLite stores without relearning command syntax.

Secret values can be supplied directly, read from an existing environment
variable, or entered with terminal echo disabled.  Direct command-line secrets
are supported for scripting convenience but secure prompts or injected
environment variables are preferable because shell history is often retained on
shared jump hosts.

Dependencies:
    Only the Python standard library and :mod:`axlib.credentials.manager`.

Example:
    >>> normalize_service_for_write("first.last")
    'firstlast'
    >>> parse_assignment("netuser=operator")
    ('netuser', 'operator')
"""

from __future__ import annotations

import getpass
import os
import re
from collections.abc import Sequence

from .manager import normalize_legacy_service_name

FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def normalize_service_for_write(service: str) -> str:
    """Validate and normalize a service before changing credential storage.

    Args:
        service (str): Operator name or logical service supplied with
            ``--service``.

    Returns:
        str: Lookup-compatible service key with dots, dashes, and underscores
            removed.

    Raises:
        ValueError: If the service is blank, contains whitespace, or becomes
            blank after normalization.
    """
    candidate = service.strip()
    if not candidate:
        raise ValueError("Service name cannot be blank.")
    if any(character.isspace() for character in candidate):
        raise ValueError("Service name cannot contain whitespace.")

    # Keeping one normalization rule across Redis, SQLite, and encrypted-file
    # storage prevents a script from looking up a different record simply
    # because the durable backend changed.
    normalized = normalize_legacy_service_name(candidate)
    if not normalized:
        raise ValueError(
            "Service name must contain characters other than '.', '-', or '_'."
        )
    return normalized


def validate_field_name(field: str) -> str:
    """Validate a credential field before using it as a backend key.

    Args:
        field (str): Field such as ``netuser``, ``netpass``, or ``ibgrid``.

    Returns:
        str: Validated field unchanged.

    Raises:
        ValueError: If the field contains unsupported characters or does not
            start with a letter.
    """
    candidate = field.strip()
    if not FIELD_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"Invalid field name {field!r}; use letters, digits, '_' or '-', "
            "starting with a letter."
        )
    return candidate


def parse_assignment(assignment: str) -> tuple[str, str]:
    """Parse one ``FIELD=VALUE`` command-line assignment.

    Args:
        assignment (str): Text supplied with ``--set``.

    Returns:
        tuple[str, str]: Validated field name and its exact value.

    Raises:
        ValueError: If the assignment lacks ``=`` or has an invalid field name.
    """
    if "=" not in assignment:
        raise ValueError(
            f"Invalid assignment {assignment!r}; expected FIELD=VALUE."
        )
    field, value = assignment.split("=", 1)
    return validate_field_name(field), value


def parse_environment_assignment(assignment: str) -> tuple[str, str]:
    """Read one credential value from a named environment variable.

    Args:
        assignment (str): ``FIELD=ENVIRONMENT_VARIABLE`` text supplied with
            ``--from-env``.

    Returns:
        tuple[str, str]: Validated field and exact environment value.

    Raises:
        ValueError: If syntax, field, variable name, or variable value is
            missing.
    """
    if "=" not in assignment:
        raise ValueError(
            f"Invalid environment assignment {assignment!r}; expected FIELD=ENV_VAR."
        )
    field, env_name = assignment.split("=", 1)
    field = validate_field_name(field)
    env_name = env_name.strip()
    if not env_name:
        raise ValueError("Environment variable name cannot be blank.")
    if env_name not in os.environ:
        raise ValueError(f"Environment variable {env_name!r} is not set.")
    return field, os.environ[env_name]


def collect_values(
    direct_assignments: Sequence[str],
    environment_assignments: Sequence[str],
    prompt_fields: Sequence[str],
) -> dict[str, str]:
    """Collect fields from CLI text, environment variables, and secure prompts.

    Later sources replace earlier values for the same field.  This lets an
    operator provide a general value with ``--set`` and safely override a secret
    with ``--prompt``.

    Args:
        direct_assignments (Sequence[str]): Repeated ``FIELD=VALUE`` entries.
        environment_assignments (Sequence[str]): Repeated ``FIELD=ENV_VAR``
            entries.
        prompt_fields (Sequence[str]): Fields read with terminal echo disabled.

    Returns:
        dict[str, str]: Field names and exact plaintext values ready for an
            encryption backend.

    Raises:
        ValueError: If an assignment is invalid or a prompted value is empty.
        EOFError: If a non-interactive input source cannot satisfy a prompt.
    """
    values: dict[str, str] = {}
    for assignment in direct_assignments:
        field, value = parse_assignment(assignment)
        values[field] = value
    for assignment in environment_assignments:
        field, value = parse_environment_assignment(assignment)
        values[field] = value
    for raw_field in prompt_fields:
        field = validate_field_name(raw_field)
        # getpass disables terminal echo, reducing accidental disclosure during
        # shared-screen troubleshooting or recorded terminal sessions.
        value = getpass.getpass(f"{field}: ")
        if not value:
            raise ValueError(f"Prompted value for {field!r} cannot be empty.")
        values[field] = value
    return values
