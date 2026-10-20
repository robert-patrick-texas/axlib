# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Exceptions raised while finding templates and parsing show-command output.

Every exception derives from :class:`ParseError`, so a script can catch one
type around a whole parsing job.  The subclasses let the command line treat
them differently: a command with no template is *skipped* (a capture often
holds ``terminal length 0`` or ``show running-config``, which nobody expects
to parse), while a template that rejects the output is a *failure* the
operator needs to see.

Dependencies:
    Python standard library only.

Example:
    >>> from axlib.parse.errors import ParseError, TemplateNotFoundError
    >>> issubclass(TemplateNotFoundError, ParseError)
    True
"""

from __future__ import annotations

from collections.abc import Sequence

# One spelling of the install hint, used by every message that needs it.
INSTALL_HINT = "uv add 'axlib[textfsm]'"


class ParseError(Exception):
    """Base class for every :mod:`axlib.parse` error."""


class ParseDependencyError(ParseError):
    """TextFSM or the ntc-templates collection is not installed."""


class ParseConfigurationError(ParseError):
    """A setting, template directory, or template index file is invalid."""


class TemplateNotFoundError(ParseError):
    """No template matches the command on the platform."""


class AmbiguousCommandError(ParseError):
    """An abbreviated command matches more than one template.

    This mirrors a device's ``% Ambiguous command`` reply: ``sh ip b`` could
    mean ``show ip bgp`` or ``show ip bfd``, so guessing would be wrong.
    """

    def __init__(self, command: str, platform: str, candidates: Sequence[str]) -> None:
        """Record the abbreviation and the commands it could mean.

        Args:
            command (str): The command as it appeared in the capture.
            platform (str): Platform name, such as ``cisco_ios``.
            candidates (Sequence[str]): Every spelled-out command it matches.

        Returns:
            None: The exception is initialized in place.

        Raises:
            None: Only string formatting is performed.
        """
        self.command = command
        self.platform = platform
        self.candidates = tuple(candidates)
        super().__init__(
            f"ambiguous command {command!r} on {platform}: it could be "
            + " or ".join(repr(candidate) for candidate in self.candidates)
        )


class TemplateParseError(ParseError):
    """A template is broken, or it rejected the output it was given."""


class SnapshotFormatError(ParseError):
    """A JSON document is not an ``axlib parse`` snapshot."""
