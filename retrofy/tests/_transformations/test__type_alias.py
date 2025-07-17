import textwrap

import libcst as cst

from retrofy import _converters


def test_simple_type_alias():
    """Test simple type alias transformation."""
    test_case_source = textwrap.dedent("""
    type Point = tuple[float, float]
    """)

    expected = textwrap.dedent("""
    Point = tuple[float, float]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_generic_type_alias():
    """Test generic type alias transformation."""
    test_case_source = textwrap.dedent("""
    type GenericPoint[T] = tuple[T, T]
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    GenericPoint: typing.TypeAlias = tuple[T, T]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_generic_type_alias_with_bound():
    """Test generic type alias with bound transformation."""
    test_case_source = textwrap.dedent("""
    type NumberPoint[T: int] = tuple[T, T]
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T", bound=int)
    NumberPoint: typing.TypeAlias = tuple[T, T]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_multiple_type_params():
    """Test type alias with multiple type parameters."""
    test_case_source = textwrap.dedent("""
    type Pair[T, U] = tuple[T, U]
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    U = typing.TypeVar("U")
    Pair: typing.TypeAlias = tuple[T, U]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_complex_type_alias():
    """Test complex type alias with union and optional."""
    test_case_source = textwrap.dedent("""
    type StringOrInt = str | int
    """)

    expected = textwrap.dedent("""
    StringOrInt = str | int
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_type_alias_with_existing_typing_import():
    """Test that existing typing import is preserved."""
    test_case_source = textwrap.dedent("""
    import typing
    type GenericList[T] = list[T]
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    GenericList: typing.TypeAlias = list[T]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_type_alias_with_from_typing_import():
    """Test that from typing import is detected."""
    test_case_source = textwrap.dedent("""
    from typing import List
    type GenericList[T] = List[T]
    """)

    expected = textwrap.dedent("""
    import typing
    from typing import List
    T = typing.TypeVar("T")
    GenericList: typing.TypeAlias = List[T]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_type_alias_with_future_import():
    """Test that typing import is placed after __future__ imports."""
    test_case_source = textwrap.dedent("""
    from __future__ import annotations
    type GenericList[T] = list[T]
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import typing
    T = typing.TypeVar("T")
    GenericList: typing.TypeAlias = list[T]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_multiple_type_aliases():
    """Test multiple type aliases in same module."""
    test_case_source = textwrap.dedent("""
    type Point = tuple[float, float]
    type GenericPoint[T] = tuple[T, T]
    type StringPoint = tuple[str, str]
    """)

    expected = textwrap.dedent("""
    import typing
    Point = tuple[float, float]
    T = typing.TypeVar("T")
    GenericPoint: typing.TypeAlias = tuple[T, T]
    StringPoint = tuple[str, str]
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_type_alias_with_class_and_function():
    """Test type alias mixed with other code."""
    test_case_source = textwrap.dedent("""
    def foo() -> int:
        return 42

    type Point = tuple[float, float]

    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    def foo() -> int:
        return 42

    Point = tuple[float, float]

    class MyClass:
        pass
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_integration_with_converters():
    """Test type alias works with the full converter pipeline."""
    test_case_source = textwrap.dedent("""
    type Point = tuple[float, float]
    """)

    expected = textwrap.dedent("""
    Point = tuple[float, float]
    """)

    result = _converters.convert(test_case_source)
    assert result == expected


def test_integration_generic_with_converters():
    """Test generic type alias works with the full converter pipeline."""
    test_case_source = textwrap.dedent("""
    type GenericPoint[T] = tuple[T, T]
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    GenericPoint: typing.TypeAlias = tuple[T, T]
    """)

    result = _converters.convert(test_case_source)
    assert result == expected


def test_generic_class_simple():
    """Test simple generic class transformation."""
    test_case_source = textwrap.dedent("""
    class ClassA[T]:
        def method1(self) -> T:
            pass
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    class ClassA(typing.Generic[T]):
        def method1(self) -> T:
            pass
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_generic_class_with_bound():
    """Test generic class with bound transformation."""
    test_case_source = textwrap.dedent("""
    class ClassA[T: str]:
        def method1(self) -> T:
            pass
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T", bound=str)
    class ClassA(typing.Generic[T]):
        def method1(self) -> T:
            pass
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_generic_function_simple():
    """Test simple generic function transformation."""
    test_case_source = textwrap.dedent("""
    def func[T](a: T) -> T:
        return a
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T")
    def func(a: T) -> T:
        return a
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_type_alias(module)
    assert result.code == expected


def test_integration_generic_class_with_converters():
    """Test generic class works with the full converter pipeline."""
    test_case_source = textwrap.dedent("""
    class ClassA[T: str]:
        def method1(self) -> T:
            pass
    """)

    expected = textwrap.dedent("""
    import typing
    T = typing.TypeVar("T", bound=str)
    class ClassA(typing.Generic[T]):
        def method1(self) -> T:
            pass
    """)

    result = _converters.convert(test_case_source)
    assert result == expected
