import sys
import textwrap
from typing import Any, Dict

import libcst as cst

from retrofy._transformations.typing_extensions import transform_typing_extensions


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


def transform_typing_final(source_code: str) -> str:
    """Apply typing.final transformation to source code."""
    module = cst.parse_module(source_code)
    return transform_typing_extensions(module.code)
    # transformer = TypingFinalTransformer()
    # transformed_module = module.visit(transformer)
    # return transformed_module.code


def test_typing_final_simple():
    """Test simple typing.final transformation."""

    source = textwrap.dedent("""
    from typing import final

    @final
    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    class MyClass:
        pass
    """)

    # Test calls to verify the decorator behavior
    test_calls = textwrap.dedent("""
    instance = MyClass()
    has_final_attr = hasattr(MyClass, '__final__')
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify the class was created successfully
    assert "instance" in converted_results

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_from_typing():
    """Test typing.final imported directly from typing module."""

    source = textwrap.dedent("""
    import typing

    @typing.final
    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    @typing.final
    class MyClass:
        pass
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_multiple_classes():
    """Test typing.final with multiple classes."""

    source = textwrap.dedent("""
    from typing import final

    @final
    class ClassA:
        pass

    class ClassB:
        pass

    @final
    class ClassC:
        pass
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    class ClassA:
        pass

    class ClassB:
        pass

    @final
    class ClassC:
        pass
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_with_existing_sys_import():
    """Test that existing sys import is preserved."""

    source = textwrap.dedent("""
    import sys
    from typing import final, Union

    @final
    class MyClass:
        a: typing.Union[str, int]
    """)

    expected = textwrap.dedent("""
    import sys
    from typing import Union

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    class MyClass:
        a: typing.Union[str, int]
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_with_future_import():
    """Test that sys import is placed after __future__ imports."""

    source = textwrap.dedent("""
    from __future__ import annotations
    from typing import final

    @final
    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    class MyClass:
        pass
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_manual_alias():
    source = textwrap.dedent("""
    from typing import final

    foo = final

    @foo
    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    foo = final

    @foo
    class MyClass:
        pass
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_no_transformation_needed():
    """Test that code without typing.final is not transformed."""

    source = textwrap.dedent("""
    class MyClass:
        pass

    def my_function():
        pass
    """)

    expected = textwrap.dedent("""
    class MyClass:
        pass

    def my_function():
        pass
    """)

    # STRING VALIDATION: Test exact code generation (no changes expected)
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_alias():
    """Test that code without typing.final is not transformed."""

    source = textwrap.dedent("""
    from typing import final as fi_na_l, Union as foo, Optional

    @fi_na_l
    class MyClass:
        pass
    """)

    expected = textwrap.dedent("""
    import sys
    from typing import Union as foo, Optional

    if sys.version_info >= (3, 8):
        from typing import final as fi_na_l
    else:
        from typing_extensions import final as fi_na_l

    @fi_na_l
    class MyClass:
        pass
    """)

    # STRING VALIDATION: Test exact code generation (no changes expected)
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_with_existing_imports_and_sys():
    """Test typing.final transformation with existing imports including sys."""

    source = textwrap.dedent("""
    from typing import final

    @final
    class MyClass:
        pass

    import sys

    print(sys.path)
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    class MyClass:
        pass

    import sys

    print(sys.path)
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


def test_typing_final_mixed_decorators():
    """Test typing.final with other decorators."""

    source = textwrap.dedent("""
    from typing import final

    @final
    @property
    def my_method(self):
        pass

    @final
    class MyClass:
        @final
        def my_method(self):
            pass
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    @property
    def my_method(self):
        pass

    @final
    class MyClass:
        @final
        def my_method(self):
            pass
    """)

    # STRING VALIDATION: Test exact code generation
    result = transform_typing_final(source)
    assert result == expected


if sys.version_info >= (3, 8):

    def test_typing_final_equivalence():
        """Test that transformed code behaves equivalently to original in Python 3.8+."""

        source = textwrap.dedent("""
        from typing import final

        @final
        class MyClass:
            pass
        """)

        expected = textwrap.dedent("""
        import sys
        if sys.version_info >= (3, 8):
            from typing import final
        else:
            from typing_extensions import final

        @final
        class MyClass:
            pass
        """)

        # Test calls to verify the decorator behavior
        test_calls = textwrap.dedent("""
        instance = MyClass()
        class_name = MyClass.__name__
        """)

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)

        converted_source_with_calls = expected + test_calls
        converted_results = execute_code_with_results(converted_source_with_calls)

        # Both should create the same class
        assert original_results["class_name"] == converted_results["class_name"]
