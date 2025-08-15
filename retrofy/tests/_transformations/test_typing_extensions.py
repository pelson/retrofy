import textwrap
from typing import Any, Dict

import libcst as cst

from retrofy._transformations.typing_extensions import TypingExtensionsTransformer


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
    module = cst.parse_module(source_code)
    transformer = TypingExtensionsTransformer()
    transformed_module = module.visit(transformer)
    return transformed_module.code


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

    if sys.version_info >= (3, 8):
        from typing import Literal as __typing_Literal
    else:
        from typing_extensions import Literal as __typing_Literal

    def process_mode(mode: __typing_Literal["read", "write"]) -> str:
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

    if sys.version_info >= (3, 10):
        from typing import get_args as __typing_get_args
    else:
        from typing_extensions import get_args as __typing_get_args

    def check_args(tp):
        return __typing_get_args(typing.Union[str, int])
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

    if sys.version_info >= (3, 10):
        from typing import get_origin as __typing_get_origin
    else:
        from typing_extensions import get_origin as __typing_get_origin

    def check_origin(tp):
        return __typing_get_origin(typing.Union[str, int])
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
        return args
    """)

    expected = textwrap.dedent("""
    import sys
    import typing

    if sys.version_info >= (3, 8):
        from typing import Literal as __typing_Literal
    else:
        from typing_extensions import Literal as __typing_Literal

    if sys.version_info >= (3, 10):
        from typing import get_args
    else:
        from typing_extensions import get_args

    def check_both(tp):
        lit = __typing_Literal["test"]
        args = get_args(tp)
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
