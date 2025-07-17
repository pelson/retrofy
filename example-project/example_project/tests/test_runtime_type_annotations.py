"""
Test runtime type annotations that fail in Python 3.7 without transformation.
"""

# from __future__ import annotations  # TODO: Enable this when the bug is fixed.
from typing import get_type_hints


def to_str(data: str | int) -> str:
    """Process data with modern type annotations."""
    return str(data)


def test_runtime_type_annotations():
    """Test that modern type annotations work at runtime in Python 3.7."""
    # This will fail in Python 3.7 unless tuple[str | int, ...] is transformed
    # because get_type_hints() evaluates the annotations at runtime

    assert to_str(123) == "123"

    # This line will fail in Python 3.7 without proper transformation
    # because it tries to evaluate tuple[str | int, ...] at runtime
    type_hints = get_type_hints(to_str)

    # Verify the hints were retrieved successfully
    assert "data" in type_hints
    assert "return" in type_hints
