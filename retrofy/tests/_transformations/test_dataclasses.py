import sys
import textwrap
from typing import Any, Dict

import libcst as cst

from retrofy._transformations.dataclass import DataclassTransformer


def execute_code_with_results(code: str) -> Dict[str, Any]:
    """Execute code and return the final locals() containing results."""
    namespace = {"__builtins__": __builtins__}
    exec(code, namespace)

    # Filter out built-ins, functions, imports, and other non-result items
    result_locals = {
        k: v
        for k, v in namespace.items()
        if (not k.startswith("__") and not callable(v) and not hasattr(v, "__name__"))
    }
    return result_locals


def transform_dataclass(source_code: str) -> str:
    """Apply dataclass transformation to source code."""
    module = cst.parse_module(source_code)
    transformer = DataclassTransformer()
    transformed_module = module.visit(transformer)
    return transformed_module.code


def test_dataclass_simple():
    """Test simple dataclass transformation that adds __match_args__."""

    # Simple dataclass without match_args=False
    source = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int
    """)

    expected = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int
        __match_args__ = ('x', 'y')
    """)

    # Test calls to verify the dataclass works
    test_calls = textwrap.dedent("""
    p = Point(1, 2)
    has_match_args = hasattr(Point, '__match_args__')
    match_args_value = getattr(Point, '__match_args__', None)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify the __match_args__ attribute was added
    assert converted_results["has_match_args"] is True
    assert converted_results["match_args_value"] == ("x", "y")

    # STRING VALIDATION: Test exact code generation
    result = transform_dataclass(source)
    assert result == expected

    if sys.version_info >= (3, 10):
        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        # In Python 3.10+, dataclasses automatically have __match_args__
        assert original_results["has_match_args"] is True
        assert original_results["match_args_value"] == ("x", "y")


def test_dataclass_with_match_args_false():
    """Test dataclass with match_args=False should not get __match_args__."""

    source = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass(match_args=False)
    class Point:
        x: int
        y: int
    """)

    expected = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int

    try:
        del Point.__match_args__
    except AttributeError:
        pass
    """)

    # Test calls to verify match_args=False is respected
    test_calls = textwrap.dedent("""
    p = Point(1, 2)
    has_match_args = hasattr(Point, '__match_args__')
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify __match_args__ was NOT added due to match_args=False
    assert converted_results["has_match_args"] is False

    # STRING VALIDATION: Test exact code generation
    result = transform_dataclass(source)
    assert result == expected

    if sys.version_info >= (3, 10):
        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        # In Python 3.10+ with match_args=False, no __match_args__ should exist
        assert original_results["has_match_args"] is False


def test_dataclass_with_explicit_match_args():
    """Test dataclass with explicitly defined __match_args__ should not be modified."""

    source = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int
        __match_args__ = ('y', 'x')  # Reverse order on purpose
    """)

    expected = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int
        __match_args__ = ('y', 'x')  # Reverse order on purpose
    """)

    # Test calls to verify explicit __match_args__ is preserved
    test_calls = textwrap.dedent("""
    p = Point(1, 2)
    has_match_args = hasattr(Point, '__match_args__')
    match_args_value = getattr(Point, '__match_args__', None)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify the explicit __match_args__ value is preserved
    assert converted_results["has_match_args"] is True
    assert converted_results["match_args_value"] == ("y", "x")

    # STRING VALIDATION: Test exact code generation (no changes expected)
    result = transform_dataclass(source)
    assert result == expected

    if sys.version_info >= (3, 10):
        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        # The explicit __match_args__ should override the auto-generated one
        assert original_results["has_match_args"] is True
        assert original_results["match_args_value"] == ("y", "x")


def test_dataclass_with_match_args_false_and_explicit():
    """Test dataclass with match_args=False and explicit __match_args__ should preserve explicit value."""

    source = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass(match_args=False)
    class Point:
        x: int
        y: int
        __match_args__ = ('y', 'x')  # Explicitly defined
    """)

    expected = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int
        __match_args__ = ('y', 'x')  # Explicitly defined
    """)

    # Test calls to verify explicit __match_args__ is preserved
    test_calls = textwrap.dedent("""
    p = Point(1, 2)
    has_match_args = hasattr(Point, '__match_args__')
    match_args_value = getattr(Point, '__match_args__', None)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify the explicit __match_args__ value is preserved
    assert converted_results["has_match_args"] is True
    assert converted_results["match_args_value"] == ("y", "x")

    # STRING VALIDATION: Test exact code generation
    result = transform_dataclass(source)
    assert result == expected

    if sys.version_info >= (3, 10):
        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        # The explicit __match_args__ should override match_args=False
        assert original_results["has_match_args"] is True
        assert original_results["match_args_value"] == ("y", "x")
