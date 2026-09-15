# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Small arithmetic examples used to teach functions, typing, and tests.

These functions are intentionally simple and are not specific to a network
protocol.  They provide a low-risk starting point for engineers learning how
Python arguments, return annotations, exceptions, and pytest assertions work
before they modify code that connects to production devices.

This module has no third-party dependencies.

Example:
    >>> from axlib.calc import divide
    >>> divide(10, 4)
    2.5
"""


def add(a: int, b: int) -> int:
    """Add two integer values.

    Args:
        a (int): First value, such as a discovered device count.
        b (int): Second value, such as an additional device count.

    Returns:
        int: Sum of ``a`` and ``b``.

    Raises:
        None: Integer addition is the only operation performed.
    """
    return a + b


def subtract(a: int, b: int) -> int:
    """Subtract one integer from another.

    Args:
        a (int): Starting value, such as total inventory entries.
        b (int): Value to remove, such as unreachable devices.

    Returns:
        int: Difference ``a - b``.

    Raises:
        None: Integer subtraction is the only operation performed.
    """
    return a - b


def multiply(a: int, b: int) -> int:
    """Multiply two integer values.

    Args:
        a (int): First factor, such as devices per site.
        b (int): Second factor, such as number of sites.

    Returns:
        int: Product of ``a`` and ``b``.

    Raises:
        None: Integer multiplication is the only operation performed.
    """
    return a * b


def divide(a: int, b: int) -> float:
    """Divide one integer by another and return a floating-point result.

    Args:
        a (int): Numerator, such as successful configuration backups.
        b (int): Denominator, such as total attempted backups.

    Returns:
        float: Quotient ``a / b``.

    Raises:
        ValueError: If ``b`` is zero, because a success ratio cannot be computed
            without attempted items.
    """
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
