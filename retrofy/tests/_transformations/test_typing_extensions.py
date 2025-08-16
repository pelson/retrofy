import textwrap
from typing import Any, Dict

import pytest


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

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_origin = typing_extensions.get_origin

    def check_both(tp):
        lit = typing.Literal["test"]
        args = get_args(tp)
        typing.get_origin(tp)
        return args
    """)

    result = transform_typing_extensions(source)
    assert result == expected


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

    if sys.version_info >= (3, 8):
        from typing import final
    else:
        from typing_extensions import final

    if sys.version_info < (3, 8):
        import typing_extensions
        typing.final = typing_extensions.final

    if sys.version_info < (3, 10):
        import typing_extensions
        typing.get_args = typing_extensions.get_args

    @final
    @typing.final
    class MyClass:
        pass

    def check_type(tp):
        return typing.get_args(tp)
    """)

    result = transform_typing_extensions(source)
    assert result == expected


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
    # TODO: Fix me - I have to do this because we are generating whitespace on empty lines.
    #  Either this is a good thing, in which case we should stop using dedent in our
    #  tests, or we don't produce such lines.
    result = textwrap.dedent(result)
    assert result == expected


@pytest.mark.xfail(strict=True)
def test_already_in_block():
    source = textwrap.dedent("""
    import sys
    import typing
    if sys.version_info < (3, 8):
        if sys.version_info < (3, 8):
            import typing_extensions
            typing.final = typing_extensions.final

    @typing.final
    class MyClass:
        pass

    """)

    expected = textwrap.dedent("""

    """)

    result = transform_typing_extensions(source)
    assert result == expected


@pytest.mark.xfail(strict=True)
def test_final_duplicated_import_in_type_checking():
    source = textwrap.dedent("""
    import typing

    from typing import final

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
