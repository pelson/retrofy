import textwrap

import libcst as cst

from retrofy._transformations.dataclass import DataclassTransformer


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

    result = transform_dataclass(source)
    assert result == expected


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

    @dataclass(match_args=False)
    class Point:
        x: int
        y: int
    """)

    result = transform_dataclass(source)
    assert result == expected


if __name__ == "__main__":
    test_dataclass_simple()
    test_dataclass_with_match_args_false()
