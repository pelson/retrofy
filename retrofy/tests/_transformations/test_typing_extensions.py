import textwrap
from typing import Any, Dict

import pytest


def execute_code_with_results(code: str) -> Dict[str, Any]:
    """Execute code and return the final locals() containing results."""
    namespace = {"__builtins__": __builtins__}
    exec(code, namespace)

    # Filter out built-ins, imports, and system items but keep functions and classes
    result_locals = {
        k: v
        for k, v in namespace.items()
        if (
            not k.startswith("__")
            and k not in {"sys", "typing", "typing_extensions"}
            and not (
                hasattr(v, "__module__")
                and v.__module__ in {"sys", "typing", "typing_extensions"}
            )
        )
    }
    return result_locals


def transform_typing_extensions(source_code: str) -> str:
    """Apply typing_extensions transformation to source code."""
    from retrofy._transformations.typing_extensions import (
        transform_typing_extensions as transform,
    )

    return transform(source_code)


def test_literal_from_typing():
    """Test typing.Literal transformation from typing import."""

    source = textwrap.dedent("""
    from typing import Literal

    def process_mode(mode: Literal["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal

    def process_mode(mode: Literal["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_literal_typing_dot():
    """Test typing.Literal transformation with typing.Literal syntax."""

    source = textwrap.dedent("""
    import typing

    def process_mode(mode: typing.Literal["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    def process_mode(mode: typing.Literal["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_literal_with_alias():
    """Test typing.Literal transformation with alias."""

    source = textwrap.dedent("""
    from typing import Literal as Lit

    def process_mode(mode: Lit["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal as Lit
    else:
        from typing_extensions import Literal as Lit

    def process_mode(mode: Lit["read", "write"]) -> str:
        return f"Processing in {mode} mode"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_get_args_from_typing():
    """Test typing.get_args transformation from typing import."""

    source = textwrap.dedent("""
    from typing import get_args, Union

    def check_args(tp):
        return get_args(Union[str, int])
    """)

    expected = textwrap.dedent("""
    import sys
    from typing import Union

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    def check_args(tp):
        return get_args(Union[str, int])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_get_args_typing_dot():
    """Test typing.get_args transformation with typing.get_args syntax."""

    source = textwrap.dedent("""
    import typing

    def check_args(tp):
        return typing.get_args(typing.Union[str, int])
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def check_args(tp):
        return typing.get_args(typing.Union[str, int])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_get_origin_from_typing():
    """Test typing.get_origin transformation from typing import."""

    source = textwrap.dedent("""
    from typing import get_origin, Union

    def check_origin(tp):
        return get_origin(Union[str, int])
    """)

    expected = textwrap.dedent("""
    import sys
    from typing import Union

    if sys.version_info >= (3, 10):
        from typing import get_origin
    else:
        from typing_extensions import get_origin

    def check_origin(tp):
        return get_origin(Union[str, int])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_get_origin_typing_dot():
    """Test typing.get_origin transformation with typing.get_origin syntax."""

    source = textwrap.dedent("""
    import typing

    def check_origin(tp):
        return typing.get_origin(typing.Union[str, int])
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def check_origin(tp):
        return typing.get_origin(typing.Union[str, int])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_multiple_features_combined():
    """Test multiple typing_extensions features in one file."""

    source = textwrap.dedent("""
    from typing import Literal, get_args, get_origin, Union

    def process_data(mode: Literal["read", "write"], tp: Union[str, int]):
        origin = get_origin(tp)
        args = get_args(tp)
        return f"Mode: {mode}, Origin: {origin}, Args: {args}"
    """)

    expected = textwrap.dedent("""
    import sys
    from typing import Union

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal

    if sys.version_info >= (3, 10):
        from typing import get_args, get_origin
    else:
        from typing_extensions import get_args, get_origin

    def process_data(mode: Literal["read", "write"], tp: Union[str, int]):
        origin = get_origin(tp)
        args = get_args(tp)
        return f"Mode: {mode}, Origin: {origin}, Args: {args}"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_mixed_import_styles():
    """Test mixing from imports and typing.X syntax."""

    source = textwrap.dedent("""
    import typing
    from typing import get_args

    def check_both(tp):
        lit = typing.Literal["test"]
        args = get_args(tp)
        typing.get_origin(tp)
        return args
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    def check_both(tp):
        lit = typing.Literal["test"]
        args = get_args(tp)
        typing.get_origin(tp)
        return args
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_with_future_annotations():
    """Test typing_extensions transformations with __future__ annotations."""

    source = textwrap.dedent("""
    from __future__ import annotations
    from typing import Literal, get_args

    def process(mode: Literal["read", "write"]) -> None:
        args = get_args(mode)
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:
        from typing_extensions import Literal

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    def process(mode: Literal["read", "write"]) -> None:
        args = get_args(mode)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_multiple_typing_dot_features_same_version():
    """Test that multiple typing.X features with same version get combined into one block."""

    source = textwrap.dedent("""
    import typing

    def process_data(tp):
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)
        return origin, args
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin
        typing.get_args = typing_extensions.get_args

    def process_data(tp):
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)
        return origin, args
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_from_typing():
    """Test typing.final transformation from typing import."""

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

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_typing_dot():
    """Test typing.final transformation with typing.final syntax."""

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

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_with_other_features():
    """Test final combined with other typing_extensions features."""

    source = textwrap.dedent("""
    import typing
    from typing import final

    @final
    @typing.final
    class MyClass:
        pass

    def check_type(tp):
        return typing.get_args(tp)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    @final
    @typing.final
    class MyClass:
        pass

    def check_type(tp):
        return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_with_other_features_v2():
    source = textwrap.dedent("""
    import typing

    if typing.TYPE_CHECKING:
        from typing import final

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if typing.TYPE_CHECKING:
        if sys.version_info < (3, 8):
            import typing_extensions
            typing.final = typing_extensions.final
        if sys.version_info < (3, 10):
            import typing_extensions
            typing.get_args = typing_extensions.get_args
        if sys.version_info >= (3, 8):
            from typing import final
        else:
            from typing_extensions import final

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_with_other_features_v3():
    source = textwrap.dedent("""
    if False:
        import typing
        bar = typing.final

        from typing import final

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    expected = textwrap.dedent("""
    import sys
    if False:
        import typing
        if sys.version_info < (3, 8):
            import typing_extensions
            typing.final = typing_extensions.final
        if sys.version_info < (3, 10):
            import typing_extensions
            typing.get_args = typing_extensions.get_args
        bar = typing.final
        if sys.version_info >= (3, 8):
            from typing import final
        else:
            from typing_extensions import final

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_with_other_features_v4():
    source = textwrap.dedent("""
    if False:
        import typing
        final = 1
        assert final == 1
        from typing import final
        assert final != 1

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    expected = textwrap.dedent("""
    import sys
    if False:
        import typing
        if sys.version_info < (3, 8):
            import typing_extensions
            typing.final = typing_extensions.final
        if sys.version_info < (3, 10):
            import typing_extensions
            typing.get_args = typing_extensions.get_args
        final = 1
        assert final == 1
        if sys.version_info >= (3, 8):
            from typing import final
        else:
            from typing_extensions import final
        assert final != 1

        @final
        @typing.final
        class MyClass:
            pass

        def check_type(tp):
            return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected


def test_already_in_block():
    # If we already have appropriate
    source = textwrap.dedent("""
    import sys
    import typing
    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    @typing.final
    class MyClass:
        pass

    """)

    expected = source
    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_not_quite_right_version_check():
    # If we already have appropriate checking, don't repeat ourselves.
    source = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 9):  # Note it should be 3.8.
        import typing_extensions
        typing.final = typing_extensions.final

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

    if sys.version_info < (3, 9):  # Note it should be 3.8.
        import typing_extensions
        typing.final = typing_extensions.final

    @typing.final
    class MyClass:
        pass
    """)
    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


@pytest.mark.xfail(strict=True)
def test_final_duplicated_import_in_type_checking():
    source = textwrap.dedent("""
    import typing

    from typing import final

    final = lambda x: x

    if typing.TYPE_CHECKING:
        from typing import final

    @final
    @typing.final
    class MyClass:
        pass

    def check_type(tp):
        return typing.get_args(tp)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    final = lambda x: x

    if typing.TYPE_CHECKING:
        if sys.version_info >= (3, 8):
            from typing import final
        else:
            from typing_extensions import final

    @final
    @typing.final
    class MyClass:
        pass

    def check_type(tp):
        return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_scoping_rules_0():
    source = textwrap.dedent("""
    from __future__ import annotations
    import typing

    if typing.TYPE_CHECKING:
        f = typing.get_args
    """)
    expected = textwrap.dedent("""
    from __future__ import annotations
    import sys
    import typing

    if typing.TYPE_CHECKING:
        if sys.version_info < (3, 10):
            import typing_extensions
            typing.get_args = typing_extensions.get_args
        f = typing.get_args
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_scoping_rules_1():
    source = textwrap.dedent("""
    from __future__ import annotations
    import typing

    if typing.TYPE_CHECKING:
        f = typing.get_args

    if 1 == 2:
        with typing.get_args:
            pass
    """)
    expected = textwrap.dedent("""
    from __future__ import annotations
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    if typing.TYPE_CHECKING:
        f = typing.get_args

    if 1 == 2:
        with typing.get_args:
            pass
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_scoping_rules_2():
    source = textwrap.dedent("""
    from __future__ import annotations
    import typing

    if typing.TYPE_CHECKING:
        f = typing.get_args

    bar = typing.get_args
    """)
    expected = textwrap.dedent("""
    from __future__ import annotations
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    if typing.TYPE_CHECKING:
        f = typing.get_args

    bar = typing.get_args
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_final_scoping_rules_4():
    source = textwrap.dedent("""
    import typing

    def foo(bar: typing.get_args) -> None:
        pass
    """)
    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def foo(bar: typing.get_args) -> None:
        pass
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_no_transformation_needed():
    """Test that code without typing_extensions features is not transformed."""

    source = textwrap.dedent("""
    from typing import Union, List

    def process_data(items: List[Union[str, int]]) -> None:
        for item in items:
            print(item)
    """)

    expected = textwrap.dedent("""
    from typing import Union, List

    def process_data(items: List[Union[str, int]]) -> None:
        for item in items:
            print(item)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_while_loop_context():
    """Test typing_extensions transformation in while loop context."""

    source = textwrap.dedent("""
    import typing

    def process_data():
        condition = True
        while condition:
            from typing import Literal
            mode: Literal["read"] = "read"
            args = typing.get_args(type(mode))
            condition = False
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def process_data():
        condition = True
        while condition:
            if sys.version_info >= (3, 8):
                from typing import Literal
            else:
                from typing_extensions import Literal
            mode: Literal["read"] = "read"
            args = typing.get_args(type(mode))
            condition = False
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_with_statement_context():
    """Test typing_extensions transformation in with statement context."""

    source = textwrap.dedent("""
    import typing
    from contextlib import nullcontext

    def process_data():
        with nullcontext():
            from typing import get_origin
            origin = get_origin(list[str])
            literal = typing.Literal["test"]
    """)

    expected = textwrap.dedent("""
    import sys
    import typing
    from contextlib import nullcontext

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    def process_data():
        with nullcontext():
            if sys.version_info >= (3, 10):
                from typing import get_origin
            else:
                from typing_extensions import get_origin
            origin = get_origin(list[str])
            literal = typing.Literal["test"]
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_for_loop_context():
    """Test typing_extensions transformation in for loop context."""

    source = textwrap.dedent("""
    import typing

    def process_data():
        for i in range(3):
            from typing import final
            @final
            class TempClass:
                pass
            result = typing.get_args(type(i))
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def process_data():
        for i in range(3):
            if sys.version_info >= (3, 8):
                from typing import final
            else:
                from typing_extensions import final
            @final
            class TempClass:
                pass
            result = typing.get_args(type(i))
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_try_except_context():
    """Test typing_extensions transformation in try/except context."""

    source = textwrap.dedent("""
    import typing

    def process_data():
        try:
            from typing import Literal
            mode: Literal["safe"] = "safe"
        except ImportError:
            from typing import get_origin
            origin = get_origin(list)
        finally:
            result = typing.get_args(dict)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def process_data():
        try:
            if sys.version_info >= (3, 8):
                from typing import Literal
            else:
                from typing_extensions import Literal
            mode: Literal["safe"] = "safe"
        except ImportError:
            if sys.version_info >= (3, 10):
                from typing import get_origin
            else:
                from typing_extensions import get_origin
            origin = get_origin(list)
        finally:
            result = typing.get_args(dict)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_nested_functions_context():
    """Test typing_extensions transformation in nested functions."""

    source = textwrap.dedent("""
    import typing

    def outer_function():
        from typing import Literal

        def inner_function():
            from typing import get_args
            return get_args(typing.Literal["nested"])

        def another_inner():
            return typing.get_origin(dict)

        return inner_function, another_inner
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def outer_function():
        if sys.version_info >= (3, 8):
            from typing import Literal
        else:
            from typing_extensions import Literal

        def inner_function():
            if sys.version_info >= (3, 10):
                from typing import get_args
            else:
                from typing_extensions import get_args
            return get_args(typing.Literal["nested"])

        def another_inner():
            return typing.get_origin(dict)

        return inner_function, another_inner
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_class_methods_context():
    """Test typing_extensions transformation in class methods."""

    source = textwrap.dedent("""
    import typing

    class DataProcessor:
        def process(self):
            from typing import final, Literal

            @final
            class InnerClass:
                def method(self) -> Literal["result"]:
                    return "result"

            return typing.get_args(type(InnerClass()))

        @staticmethod
        def static_method():
            from typing import get_origin
            return get_origin(list[str])

        @classmethod
        def class_method(cls):
            return typing.Literal["class"]
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    class DataProcessor:
        def process(self):
            if sys.version_info >= (3, 8):
                from typing import final, Literal
            else:
                from typing_extensions import final, Literal

            @final
            class InnerClass:
                def method(self) -> Literal["result"]:
                    return "result"

            return typing.get_args(type(InnerClass()))

        @staticmethod
        def static_method():
            if sys.version_info >= (3, 10):
                from typing import get_origin
            else:
                from typing_extensions import get_origin
            return get_origin(list[str])

        @classmethod
        def class_method(cls):
            return typing.Literal["class"]
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_lambda_functions_context():
    """Test typing_extensions transformation with lambda functions."""

    source = textwrap.dedent("""
    import typing

    def process_data():
        from typing import get_args

        # Lambda using typing_extensions features
        get_literal_args = lambda x: get_args(x)
        check_origin = lambda t: typing.get_origin(t)

        # Test with Literal types
        literal_checker = lambda mode: mode if isinstance(mode, typing.Literal["read", "write"]) else None

        return get_literal_args, check_origin, literal_checker
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def process_data():
        if sys.version_info >= (3, 10):
            from typing import get_args
        else:
            from typing_extensions import get_args

        # Lambda using typing_extensions features
        get_literal_args = lambda x: get_args(x)
        check_origin = lambda t: typing.get_origin(t)

        # Test with Literal types
        literal_checker = lambda mode: mode if isinstance(mode, typing.Literal["read", "write"]) else None

        return get_literal_args, check_origin, literal_checker
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_match_statement_context():
    """Test typing_extensions transformation in match statement context (Python 3.10+)."""

    source = textwrap.dedent("""
    import typing

    def process_value(value):
        match value:
            case "literal":
                from typing import Literal
                mode: Literal["match"] = "match"
                return typing.get_args(type(mode))
            case _:
                from typing import get_origin
                return get_origin(dict)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def process_value(value):
        match value:
            case "literal":
                if sys.version_info >= (3, 8):
                    from typing import Literal
                else:
                    from typing_extensions import Literal
                mode: Literal["match"] = "match"
                return typing.get_args(type(mode))
            case _:
                if sys.version_info >= (3, 10):
                    from typing import get_origin
                else:
                    from typing_extensions import get_origin
                return get_origin(dict)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_for_else_context():
    """Test typing_extensions transformation in for/else context."""

    source = textwrap.dedent("""
    import typing

    def search_data(items):
        for item in items:
            if item == "target":
                from typing import Literal
                result: Literal["found"] = "found"
                break
        else:
            from typing import get_origin
            result = get_origin(type(items))

        return typing.final(result)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    def search_data(items):
        for item in items:
            if item == "target":
                if sys.version_info >= (3, 8):
                    from typing import Literal
                else:
                    from typing_extensions import Literal
                result: Literal["found"] = "found"
                break
        else:
            if sys.version_info >= (3, 10):
                from typing import get_origin
            else:
                from typing_extensions import get_origin
            result = get_origin(type(items))

        return typing.final(result)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_while_else_context():
    """Test typing_extensions transformation in while/else context."""

    source = textwrap.dedent("""
    import typing

    def process_until_condition():
        counter = 0
        while counter < 5:
            from typing import Literal
            status: Literal["processing"] = "processing"
            counter += 1
            if counter == 10:  # This won't happen
                break
        else:
            from typing import get_args
            final_status = get_args(typing.Literal["completed"])

        return final_status
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    def process_until_condition():
        counter = 0
        while counter < 5:
            if sys.version_info >= (3, 8):
                from typing import Literal
            else:
                from typing_extensions import Literal
            status: Literal["processing"] = "processing"
            counter += 1
            if counter == 10:  # This won't happen
                break
        else:
            if sys.version_info >= (3, 10):
                from typing import get_args
            else:
                from typing_extensions import get_args
            final_status = get_args(typing.Literal["completed"])

        return final_status
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_try_else_context():
    """Test typing_extensions transformation in try/else context."""

    source = textwrap.dedent("""
    import typing

    def safe_operation():
        try:
            from typing import Literal
            result: Literal["attempting"] = "attempting"
            # Some operation that might fail
            value = 1 / 1
        except ZeroDivisionError:
            from typing import get_origin
            result = get_origin(type(Exception))
        else:
            from typing import get_args
            result = get_args(typing.Literal["success"])
        finally:
            final_result = typing.final(result)

        return final_result
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal
        typing.final = typing_extensions.final

    def safe_operation():
        try:
            if sys.version_info >= (3, 8):
                from typing import Literal
            else:
                from typing_extensions import Literal
            result: Literal["attempting"] = "attempting"
            # Some operation that might fail
            value = 1 / 1
        except ZeroDivisionError:
            if sys.version_info >= (3, 10):
                from typing import get_origin
            else:
                from typing_extensions import get_origin
            result = get_origin(type(Exception))
        else:
            if sys.version_info >= (3, 10):
                from typing import get_args
            else:
                from typing_extensions import get_args
            result = get_args(typing.Literal["success"])
        finally:
            final_result = typing.final(result)

        return final_result
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_comprehensions_context():
    """Test typing_extensions transformation in comprehensions."""

    source = textwrap.dedent("""
    import typing
    from typing import get_args

    def process_comprehensions():
        # List comprehension
        literal_list = [typing.Literal["item"] for i in range(3)]

        # Dict comprehension
        literal_dict = {i: typing.get_origin(list) for i in range(2)}

        # Set comprehension
        literal_set = {get_args(typing.Literal["set"]) for _ in range(1)}

        return literal_list, literal_dict, literal_set
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    def process_comprehensions():
        # List comprehension
        literal_list = [typing.Literal["item"] for i in range(3)]

        # Dict comprehension
        literal_dict = {i: typing.get_origin(list) for i in range(2)}

        # Set comprehension
        literal_set = {get_args(typing.Literal["set"]) for _ in range(1)}

        return literal_list, literal_dict, literal_set
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_async_await_context():
    """Test typing_extensions transformation in async/await context."""

    source = textwrap.dedent("""
    import typing
    import asyncio

    async def async_process():
        from typing import Literal

        async def inner_async():
            from typing import get_args
            return get_args(typing.Literal["async"])

        status: Literal["running"] = "running"
        result = await inner_async()
        return typing.get_origin(type(result))
    """)

    expected = textwrap.dedent("""
    import sys
    import typing
    import asyncio

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    async def async_process():
        if sys.version_info >= (3, 8):
            from typing import Literal
        else:
            from typing_extensions import Literal

        async def inner_async():
            if sys.version_info >= (3, 10):
                from typing import get_args
            else:
                from typing_extensions import get_args
            return get_args(typing.Literal["async"])

        status: Literal["running"] = "running"
        result = await inner_async()
        return typing.get_origin(type(result))
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_decorators_context():
    """Test typing_extensions transformation with decorators."""

    source = textwrap.dedent("""
    import typing
    from typing import final

    def my_decorator(func):
        from typing import Literal
        mode: Literal["decorated"] = "decorated"
        return func

    @my_decorator
    @final
    def decorated_function():
        from typing import get_args
        return get_args(typing.Literal["function"])

    @typing.final
    class DecoratedClass:
        def method(self):
            return typing.get_origin(dict)
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal
        typing.final = typing_extensions.final

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    def my_decorator(func):
        if sys.version_info >= (3, 8):
            from typing import Literal
        else:
            from typing_extensions import Literal
        mode: Literal["decorated"] = "decorated"
        return func

    @my_decorator
    @final
    def decorated_function():
        if sys.version_info >= (3, 10):
            from typing import get_args
        else:
            from typing_extensions import get_args
        return get_args(typing.Literal["function"])

    @typing.final
    class DecoratedClass:
        def method(self):
            return typing.get_origin(dict)
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_generator_conditions():
    """Test typing_extensions in generator conditions."""

    source = textwrap.dedent("""
    import typing

    def process_items(items):
        # Generator with condition using typing_extensions
        filtered = (x for x in items if isinstance(x, typing.Literal["valid"]))

        # List comprehension with condition
        valid_items = [x for x in items if typing.get_origin(type(x)) == str]

        return list(filtered), valid_items
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def process_items(items):
        # Generator with condition using typing_extensions
        filtered = (x for x in items if isinstance(x, typing.Literal["valid"]))

        # List comprehension with condition
        valid_items = [x for x in items if typing.get_origin(type(x)) == str]

        return list(filtered), valid_items
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_if_conditions():
    """Test typing_extensions in if/elif conditions."""

    source = textwrap.dedent("""
    import typing

    def check_value(value):
        if isinstance(value, typing.Literal["test"]):
            return "literal"
        elif typing.get_args(type(value)):
            return "has_args"
        elif hasattr(typing.get_origin(type(value)), "__name__"):
            return "has_origin"
        else:
            return "unknown"
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def check_value(value):
        if isinstance(value, typing.Literal["test"]):
            return "literal"
        elif typing.get_args(type(value)):
            return "has_args"
        elif hasattr(typing.get_origin(type(value)), "__name__"):
            return "has_origin"
        else:
            return "unknown"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_exception_types():
    """Test typing_extensions in exception handling."""

    source = textwrap.dedent("""
    import typing

    def handle_exceptions():
        try:
            value = "test"
        except typing.get_origin(Exception) as e:
            return "caught_origin"
        except (TypeError, typing.get_args(ValueError)[0] if typing.get_args(ValueError) else ValueError):
            return "caught_complex"
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin
        typing.get_args = typing_extensions.get_args

    def handle_exceptions():
        try:
            value = "test"
        except typing.get_origin(Exception) as e:
            return "caught_origin"
        except (TypeError, typing.get_args(ValueError)[0] if typing.get_args(ValueError) else ValueError):
            return "caught_complex"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_function_defaults():
    """Test typing_extensions in function default arguments."""

    source = textwrap.dedent("""
    import typing

    def process_data(
        mode: str = "default",
        config: dict = None,
        validator = typing.get_args,
        type_checker = typing.get_origin
    ):
        # Use the typing functions in the body to validate they work
        args = validator(typing.Literal["test"])
        origin = type_checker(list[str])
        return mode, config, args, origin
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def process_data(
        mode: str = "default",
        config: dict = None,
        validator = typing.get_args,
        type_checker = typing.get_origin
    ):
        # Use the typing functions in the body to validate they work
        args = validator(typing.Literal["test"])
        origin = type_checker(list[str])
        return mode, config, args, origin
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)

    # Test execution
    results = execute_code_with_results(result)
    mode, config, args, origin = results["process_data"]()
    assert mode == "default"
    assert config is None
    assert args == ("test",)  # get_args returns tuple of literal values
    assert origin is list  # get_origin returns the origin type


def test_class_inheritance():
    """Test typing_extensions in class inheritance."""

    source = textwrap.dedent("""
    import typing

    class BaseType:
        pass

    class MyClass(typing.get_origin(dict), BaseType):
        def __init__(self):
            self.mode: typing.Literal["class"] = "class"
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    class BaseType:
        pass

    class MyClass(typing.get_origin(dict), BaseType):
        def __init__(self):
            self.mode: typing.Literal["class"] = "class"
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_with_context_managers():
    """Test typing_extensions in with statement context managers."""

    source = textwrap.dedent("""
    import typing
    from contextlib import nullcontext

    def process_with_context():
        with nullcontext(typing.get_args(list)), nullcontext(typing.Literal["context"]):
            pass
    """)

    expected = textwrap.dedent("""
    import sys
    import typing
    from contextlib import nullcontext

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    def process_with_context():
        with nullcontext(typing.get_args(list)), nullcontext(typing.Literal["context"]):
            pass
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_assert_conditions():
    """Test typing_extensions in assert statements."""

    source = textwrap.dedent("""
    import typing

    def validate_data(value):
        assert isinstance(value, typing.Literal["valid"]), "Invalid value"
        assert typing.get_origin(type(value)) is not None
        assert typing.get_args(type(value))
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin
        typing.get_args = typing_extensions.get_args

    def validate_data(value):
        assert isinstance(value, typing.Literal["valid"]), "Invalid value"
        assert typing.get_origin(type(value)) is not None
        assert typing.get_args(type(value))
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_yield_expressions():
    """Test typing_extensions in yield expressions."""

    source = textwrap.dedent("""
    import typing

    def generator_function():
        yield typing.get_args(list)
        yield typing.Literal["yielded"]
        yield from typing.get_origin(dict)()
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def generator_function():
        yield typing.get_args(list)
        yield typing.Literal["yielded"]
        yield from typing.get_origin(dict)()
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_raise_statements():
    """Test typing_extensions in raise statements."""

    source = textwrap.dedent("""
    import typing

    def raise_errors():
        raise typing.get_origin(Exception)("Error message")
        raise ValueError(typing.Literal["error"])
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def raise_errors():
        raise typing.get_origin(Exception)("Error message")
        raise ValueError(typing.Literal["error"])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_ternary_expressions():
    """Test typing_extensions in ternary expressions."""

    source = textwrap.dedent("""
    import typing

    def process_ternary(value):
        result = typing.get_args(type(value)) if isinstance(value, typing.Literal["test"]) else typing.get_origin(type(value))
        return result
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def process_ternary(value):
        result = typing.get_args(type(value)) if isinstance(value, typing.Literal["test"]) else typing.get_origin(type(value))
        return result
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_slice_expressions():
    """Test typing_extensions in slice expressions."""

    source = textwrap.dedent("""
    import typing

    def process_slices(items):
        first = items[typing.get_args(type(items))[0] if typing.get_args(type(items)) else 0]
        subset = items[0:typing.get_origin(len)(typing.Literal["slice"])]
        return first, subset
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.Literal = typing_extensions.Literal

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def process_slices(items):
        first = items[typing.get_args(type(items))[0] if typing.get_args(type(items)) else 0]
        subset = items[0:typing.get_origin(len)(typing.Literal["slice"])]
        return first, subset
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_loop_targets():
    """Test typing_extensions in loop targets and iterables."""

    source = textwrap.dedent("""
    import typing

    def process_loops():
        # For loop with typing_extensions in iterable
        for item in typing.get_args(tuple):
            pass

        # While loop with typing_extensions in condition
        while typing.get_origin(type([])) == list:
            break
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args
        typing.get_origin = typing_extensions.get_origin

    def process_loops():
        # For loop with typing_extensions in iterable
        for item in typing.get_args(tuple):
            pass

        # While loop with typing_extensions in condition
        while typing.get_origin(type([])) == list:
            break
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)


def test_invalid_runtime_code():
    """What happens in dead code that doesn't have the typing import"""

    source = textwrap.dedent("""
    if False:
        foo = typing.get_origin(typing.List[int])
    """)

    expected = textwrap.dedent("""
    import sys
    if False:
        if sys.version_info < (3, 10):
            import typing_extensions
            typing.get_origin = typing_extensions.get_origin
        foo = typing.get_origin(typing.List[int])
    """)

    result = transform_typing_extensions(source)
    assert result == expected
    assert expected == transform_typing_extensions(expected)
