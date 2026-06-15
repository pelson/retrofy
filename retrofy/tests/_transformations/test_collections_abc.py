import sys
import textwrap
import types
from typing import Any, Dict

from retrofy._transformations.collections_abc import transform_collections_abc


def t(src: str) -> str:
    return transform_collections_abc(textwrap.dedent(src))


def _exec_ns(code: str, fake_sys: types.ModuleType | None = None) -> Dict[str, Any]:
    """Execute *code* in a fresh namespace and return it.

    If *fake_sys* is given it shadows the real ``sys`` module so we can drive
    the ``if sys.version_info >= (3, 9):`` branch the other way and exercise
    the typing fallback even when running on Python 3.9+.
    """
    ns: Dict[str, Any] = {"__builtins__": __builtins__}
    if fake_sys is not None:
        ns["sys"] = fake_sys
        # Strip the ``import sys`` line so our injected ``sys`` is the one used.
        code = "\n".join(ln for ln in code.splitlines() if ln.strip() != "import sys")
    exec(compile(code, "<test>", "exec"), ns)
    return ns


def _fake_sys(version: tuple) -> types.ModuleType:
    mod = types.ModuleType("sys")
    mod.version_info = version  # type: ignore[attr-defined]
    return mod


def _check_both_branches(original: str, converted: str, names: list[str]) -> None:
    """Verify behavior on both Python 3.9+ (real) and the simulated <3.9 fallback.

    - Original source must compile and run on the current (>=3.9) interpreter.
    - Converted source must compile and run on the current interpreter
      (real ``sys.version_info`` branch).
    - Converted source must also run with a fake ``sys.version_info = (3, 8)``,
      exercising the typing fallback branch — verifying its syntax and that
      every advertised binding resolves to a usable type.
    """
    # 1. original runs on real interpreter
    ns_orig = _exec_ns(original)
    for name in names:
        assert name in ns_orig, f"original failed to bind {name}"

    # 2. converted runs on real interpreter (>=3.9 branch)
    ns_new = _exec_ns(converted)
    for name in names:
        assert name in ns_new, f"converted (>=3.9 branch) failed to bind {name}"

    # 3. converted runs with simulated old interpreter (typing fallback branch)
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    for name in names:
        assert name in ns_old, f"converted (<3.9 branch) failed to bind {name}"


def test_exec_single_name():
    src = textwrap.dedent("""
    from collections.abc import Mapping

    def f(m: Mapping):
        return m
    """)
    _check_both_branches(src, t(src), ["Mapping", "f"])


def test_exec_set_rename():
    """Verify Set->AbstractSet rename actually resolves on the fallback branch."""
    src = textwrap.dedent("""
    from collections.abc import Set as AbstractSet

    x = AbstractSet
    """)
    _check_both_branches(src, t(src), ["AbstractSet", "x"])


def test_exec_set_unaliased_binds_set():
    src = textwrap.dedent("""
    from collections.abc import Set

    x = Set
    """)
    converted = t(src)
    # On real >=3.9 interpreter Set is collections.abc.Set.
    ns_new = _exec_ns(converted)
    import collections.abc

    assert ns_new["Set"] is collections.abc.Set

    # On simulated <3.9 the local binding ``Set`` should resolve to typing.AbstractSet.
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    import typing

    assert ns_old["Set"] is typing.AbstractSet


def test_exec_issue_5_full_list():
    src = textwrap.dedent("""
    from collections.abc import (
        Awaitable, Callable, Collection, Coroutine, Generator,
        ItemsView, Iterator, KeysView, Mapping, MutableMapping,
        Sequence, ValuesView,
    )
    from collections.abc import Set as AbstractSet
    """)
    names = [
        "Awaitable",
        "Callable",
        "Collection",
        "Coroutine",
        "Generator",
        "ItemsView",
        "Iterator",
        "KeysView",
        "Mapping",
        "MutableMapping",
        "Sequence",
        "ValuesView",
        "AbstractSet",
    ]
    _check_both_branches(src, t(src), names)


def test_exec_attribute_access():
    src = textwrap.dedent("""
    import collections.abc

    M = collections.abc.Mapping
    """)
    converted = t(src)
    # Real interpreter: M is the real collections.abc.Mapping.
    ns_new = _exec_ns(converted)
    import collections.abc

    assert ns_new["M"] is collections.abc.Mapping

    # Simulated <3.9: the assignment patches collections.abc.Mapping to
    # typing.Mapping. Restore afterwards so we don't pollute the real module.
    real = collections.abc.Mapping
    try:
        ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
        import typing

        assert ns_old["M"] is typing.Mapping
        # And the module attribute was patched too:
        assert collections.abc.Mapping is typing.Mapping
    finally:
        collections.abc.Mapping = real


def test_runtime_version_check_used_real_sys():
    """Sanity: on a real >=3.9 interpreter the converted code uses collections.abc."""
    assert sys.version_info >= (3, 9), "test assumes Python >= 3.9"
    src = "from collections.abc import Mapping\nx: Mapping\n"
    converted = t(src)
    ns = _exec_ns(converted)
    import collections.abc

    assert ns["Mapping"] is collections.abc.Mapping


def test_single_name_from_import():
    src = """
    from collections.abc import Mapping

    def f(m: Mapping[str, int]) -> int:
        return len(m)
    """
    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 9):
        from collections.abc import Mapping
    else:
        from typing import Mapping

    def f(m: Mapping[str, int]) -> int:
        return len(m)
    """)
    assert t(src) == expected
    assert t(expected) == expected  # idempotent


def test_multi_name_from_import():
    src = """
    from collections.abc import Mapping, Callable, Sequence

    x: Mapping[str, int]
    y: Callable[[int], int]
    z: Sequence[int]
    """
    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 9):
        from collections.abc import Mapping, Callable, Sequence
    else:
        from typing import Mapping, Callable, Sequence

    x: Mapping[str, int]
    y: Callable[[int], int]
    z: Sequence[int]
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_set_renamed_to_abstractset():
    """collections.abc.Set has no same-named equivalent in typing; falls back to AbstractSet."""
    src = """
    from collections.abc import Set as AbstractSet

    s: AbstractSet[int]
    """
    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 9):
        from collections.abc import Set as AbstractSet
    else:
        from typing import AbstractSet

    s: AbstractSet[int]
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_set_unaliased_keeps_binding():
    """``from collections.abc import Set`` must still bind ``Set`` on Py<3.9."""
    src = """
    from collections.abc import Set

    s: Set[int]
    """
    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 9):
        from collections.abc import Set
    else:
        from typing import AbstractSet as Set

    s: Set[int]
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_user_alias_preserved():
    src = """
    from collections.abc import Mapping as M

    x: M[str, int]
    """
    expected = textwrap.dedent("""
    import sys

    if sys.version_info >= (3, 9):
        from collections.abc import Mapping as M
    else:
        from typing import Mapping as M

    x: M[str, int]
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_attribute_access():
    src = """
    import collections.abc

    def f(m: collections.abc.Mapping[str, int]) -> int:
        return len(m)
    """
    expected = textwrap.dedent("""
    import sys
    import collections.abc

    if sys.version_info < (3, 9):
        import typing
        collections.abc.Mapping = typing.Mapping

    def f(m: collections.abc.Mapping[str, int]) -> int:
        return len(m)
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_type_checking_block():
    src = """
    import typing

    if typing.TYPE_CHECKING:
        from collections.abc import Mapping
    """
    expected = textwrap.dedent("""
    import sys
    import typing

    if typing.TYPE_CHECKING:
        if sys.version_info >= (3, 9):
            from collections.abc import Mapping
        else:
            from typing import Mapping
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_function_scope_import():
    src = """

    def f():
        from collections.abc import Mapping
        return Mapping
    """
    expected = textwrap.dedent("""

    import sys
    def f():
        if sys.version_info >= (3, 9):
            from collections.abc import Mapping
        else:
            from typing import Mapping
        return Mapping
    """)
    assert t(src) == expected
    assert t(expected) == expected


def test_issue_5_example():
    """The exact pattern from issue #5."""
    src = """
    from collections.abc import (
        Awaitable,
        Callable,
        Collection,
        Coroutine,
        Generator,
        ItemsView,
        Iterator,
        KeysView,
        Mapping,
        MutableMapping,
        Sequence,
    )
    from collections.abc import Set as AbstractSet
    from collections.abc import ValuesView
    """
    out = t(src)
    # The output must:
    #   - keep all the original names as bindings
    #   - emit a typing fallback that resolves Set -> AbstractSet
    assert "if sys.version_info >= (3, 9):" in out
    assert "else:" in out
    # Each name must appear in both branches (with Set->AbstractSet for the rename).
    for name in [
        "Awaitable",
        "Callable",
        "Collection",
        "Coroutine",
        "Generator",
        "ItemsView",
        "Iterator",
        "KeysView",
        "Mapping",
        "MutableMapping",
        "Sequence",
        "ValuesView",
    ]:
        assert out.count(name) >= 2, f"{name} missing from a branch in:\n{out}"
    # Set -> AbstractSet rename: source branch keeps the user-facing alias,
    # fallback branch uses the typing name without an alias.
    assert "Set as AbstractSet" in out
    typing_branch = [
        ln for ln in out.splitlines() if ln.strip().startswith("from typing import")
    ]
    assert any("AbstractSet" in ln and "Set as" not in ln for ln in typing_branch)
    assert t(out) == out  # idempotent


def test_unrelated_imports_untouched():
    src = """
    from collections.abc import Mapping
    from os.path import join
    from typing import Optional

    x: Optional[Mapping[str, int]] = None
    y = join("a", "b")
    """
    out = t(src)
    assert "from os.path import join" in out
    assert "from typing import Optional" in out
    assert "if sys.version_info >= (3, 9):" in out
    assert t(out) == out


def test_no_collections_abc_no_change():
    src = """
    from typing import Mapping

    x: Mapping[str, int] = {}
    """
    out = t(src)
    # No transformation should occur — input round-trips unchanged (modulo a
    # leading newline produced by libcst's serializer on the input).
    assert "if sys.version_info" not in out
    assert "from typing import Mapping" in out


def test_full_pipeline_integration():
    """Run through the public convert() entry to catch interaction with other passes."""
    from retrofy._converters import convert

    src = textwrap.dedent("""
    from collections.abc import Mapping

    def f(m: Mapping[str, int]) -> int:
        return len(m)
    """)
    out = convert(src)
    assert "if sys.version_info >= (3, 9):" in out
    assert "from collections.abc import Mapping" in out
    assert "from typing import Mapping" in out
