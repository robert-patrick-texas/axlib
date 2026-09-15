# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Fixture-based tests demonstrating shared pytest inputs."""

import pytest

from axlib.calc import add, divide, multiply, subtract


@pytest.fixture
def sample_numbers() -> tuple[int, int]:
    """Provide a pair of numbers for testing."""
    return (10, 5)


def test_add_with_fixture(sample_numbers: tuple[int, int]) -> None:
    a, b = sample_numbers
    assert add(a, b) == 15


def test_subtract_with_fixture(sample_numbers: tuple[int, int]) -> None:
    a, b = sample_numbers
    assert subtract(a, b) == 5


def test_multiply_with_fixture(sample_numbers: tuple[int, int]) -> None:
    a, b = sample_numbers
    assert multiply(a, b) == 50


def test_divide_with_fixture(sample_numbers: tuple[int, int]) -> None:
    a, b = sample_numbers
    assert divide(a, b) == 2.0
