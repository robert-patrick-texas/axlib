# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Allow ``python -m axlib.credentials.tui`` to start the credential manager.

Python runs a package's ``__main__`` module when the package is executed with
``-m``.  Keeping this file to a single call means the command-line behavior is
defined in exactly one place, :func:`axlib.credentials.tui.main`.

Dependencies:
    The optional ``tui`` extra (``uv add 'axlib[tui]'``).

Example:
    ``python -m axlib.credentials.tui --config /etc/axlib/axlib.toml``
"""

from . import main

if __name__ == "__main__":
    raise SystemExit(main())
