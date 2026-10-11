import itertools
import textwrap

import libcst as cst
import pytest

from retrofy import _converters


def test_if():
    test_case_source = textwrap.dedent("""
    if (a:= foo()) > 5:
        assert a is 3
    """)

    expected = textwrap.dedent("""
    a = foo()
    if a > 5:
        assert a is 3
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_if__indent():
    test_case_source = textwrap.dedent("""
    while True:
        if (a:= foo()) > 5:
            assert a is 3
    """)

    expected = textwrap.dedent("""
    while True:
        a = foo()
        if a > 5:
            assert a is 3
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_if__multiple():
    test_case_source = textwrap.dedent("""
    if (a:= foo()) > 5 or (b:= bar()) < 6:
        assert a is 3
    """)

    # bar() must only be called when the left-hand side is false.
    expected = textwrap.dedent("""
    a = foo()
    _retrofy_cond_0 = a > 5
    if not _retrofy_cond_0:
        b = bar()
        _retrofy_cond_0 = b < 6
    if _retrofy_cond_0:
        assert a is 3
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_if__nested():
    test_case_source = textwrap.dedent("""
    if (a:= foo()) > 5:
        if (b:= foo()) > 6:
            if (c:= foo()) > 7:
                assert a is 3
    """)

    expected = textwrap.dedent("""
    a = foo()
    if a > 5:
        b = foo()
        if b > 6:
            c = foo()
            if c > 7:
                assert a is 3
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_while():
    test_case_source = textwrap.dedent("""
    while (chunk := file.read(8192)):
        process(chunk)
    """)

    expected = textwrap.dedent("""
    while True:
        chunk = file.read(8192)
        if not chunk: break
        process(chunk)
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_while__multiple():
    test_case_source = textwrap.dedent("""
    while (chunk := file.read(8192)) and (alive := random.random()) > 2:
        process(chunk)
    """)

    # random.random() must only be called when chunk is truthy.
    expected = textwrap.dedent("""
    while True:
        chunk = file.read(8192)
        if not chunk: break
        alive = random.random()
        if not (alive > 2): break
        process(chunk)
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_while__with_continue():
    test_case_source = textwrap.dedent("""
    while (chunk := file.read(8192)) > 10:
        if chunk > 5:
            continue
        process(chunk)
    """)

    expected = textwrap.dedent("""
    while True:
        chunk = file.read(8192)
        if not (chunk > 10): break
        if chunk > 5:
            continue
        process(chunk)
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_assignment():
    # A case which is valid though discouraged (in PEP-572).
    case_source = "y0 = (y1 := f(x))"
    # The fact that it is on a single line is not semantically important.
    expected = "y1 = f(x); y0 = y1"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_fstring__as_expr():
    case_source = "f'{(x:=10)}'"
    expected = "x = 10; f'{x}'"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_fstring__within_expr():
    case_source = "print(f'{(x:=10)}')"
    expected = "x = 10; print(f'{x}')"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_fstring__multiple():
    case_source = "print(f'{(x:=10)}', f'{(y:=20)}')"
    expected = "x = 10; y = 20; print(f'{x}', f'{y}')"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_comp():
    case_source = "[y := f(x), (x := y**2), y**3]"
    expected = "y = f(x); x = y**2; [y, x, y**3]"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_subexpression__single():
    """One walrus, evaluated first in the filter, unused elsewhere: one line."""
    case_source = "filtered_data = [y for x in data if (y := f(x)) is not None]\n"
    expected = (
        "filtered_data = [y for x, y in ((x, f(x)) for x in data) if y is not None]\n"
    )
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_subexpression__multiple():
    """g(x + 1) is only called when y is not None (two walruses: helper)."""
    case_source = "filtered_data = [y for x in data if (y := f(x)) is not None and (z := g(x + 1)) > 2]\n"
    expected = textwrap.dedent("""\
    def _retrofy_comp_0(_retrofy_iter):
        # [y for x in data if (y := f(x)) is not None and (z := g(x + 1)) > 2]
        global y, z
        for x in _retrofy_iter:
            y = f(x)
            if y is not None:
                z = g(x + 1)
                if z > 2:
                    yield y
    filtered_data = [_retrofy_item for _retrofy_item in _retrofy_comp_0(data)]
    """)
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_set_comprehension():
    """One walrus, evaluated first in the filter, unused elsewhere: one line."""
    case_source = "{y for x in data if (y := f(x)) is not None}\n"
    expected = "{y for x, y in ((x, f(x)) for x in data) if y is not None}\n"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_dict_comprehension_short_circuit():
    """g(x, y) is only called when y is truthy (two walruses: helper)."""
    case_source = "{k: v for x in data if (y := f(x)) and (v := g(x, y))}\n"
    expected = textwrap.dedent("""\
    def _retrofy_comp_0(_retrofy_iter):
        # {k: v for x in data if (y := f(x)) and (v := g(x, y))}
        global y, v
        for x in _retrofy_iter:
            y = f(x)
            if y:
                v = g(x, y)
                if v:
                    yield (k, v)
    {_retrofy_key: _retrofy_value for _retrofy_key, _retrofy_value in _retrofy_comp_0(data)}
    """)
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_nested_comprehension():
    """Several ``for`` clauses thread all loop variables through."""
    case_source = "[z for x in data for y in items if (z := f(x, y)) > 0]\n"
    expected = (
        "[z for x, y, z in ((x, y, f(x, y)) for x in data for y in items) if z > 0]\n"
    )
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_simple_short_circuit_and():
    case_source = textwrap.dedent("""
    if (a := foo()) > 5 and (b := bar()) < 6:
        assert a is 3
    """)
    expected = textwrap.dedent("""
    a = foo()
    if a > 5:
        b = bar()
        if b < 6:
            assert a is 3
    """)
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_nested_dict_comprehension():
    """g(z) is only called when z is truthy (two walruses: helper)."""
    case_source = (
        "{k: v for x in data for y in items if (z := f(x, y)) and (v := g(z))}\n"
    )
    expected = textwrap.dedent("""\
    def _retrofy_comp_0(_retrofy_iter):
        # {k: v for x in data for y in items if (z := f(x, y)) and (v := g(z))}
        global z, v
        for x in _retrofy_iter:
            for y in items:
                z = f(x, y)
                if z:
                    v = g(z)
                    if v:
                        yield (k, v)
    {_retrofy_key: _retrofy_value for _retrofy_key, _retrofy_value in _retrofy_comp_0(data)}
    """)
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_nested_set_comprehension():
    """Several ``for`` clauses thread all loop variables through."""
    case_source = "{z for x in data for y in items if (z := f(x, y)) > threshold}\n"
    expected = "{z for x, y, z in ((x, y, f(x, y)) for x in data for y in items) if z > threshold}\n"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_if__multiline_condition_second_walrus():
    # Splitting the condition must not leave a multi-line sub-expression
    # without its enclosing parentheses (issue #65).
    case_source = textwrap.dedent("""
    if (
        (x := g())
        and k(x)
        and (y := h(x))
    ):
        assert y
    """)
    expected = textwrap.dedent("""
    x = g()
    if x and k(x):
        y = h(x)
        if y:
            assert y
    """)
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def _trace(func, values):
    """Call *func* with a recording callable per entry of *values*.

    Returns the order of calls together with the result (or exception
    type), so that two implementations can be compared on behaviour,
    including short-circuiting.
    """
    calls = []

    def recorder(name, value):
        def call(*args):
            calls.append((name, args))
            return value

        return call

    args = [recorder(name, value) for name, value in zip("ghk", values)]
    try:
        outcome = func(*args)
    except Exception as err:
        outcome = type(err)
    return calls, outcome


# Each snippet defines ``f(g, h, k)``. It is run natively and after
# conversion, for every combination of falsy/truthy return values.
SEMANTIC_CASES = {
    "issue-65-multiline": """
        def f(g, h, k):
            if (
                (x := g())
                and k(x)
                and (y := h(x))
            ):
                return y
            return "no"
        """,
    "and-second-walrus-in-left": """
        def f(g, h, k):
            if (x := g()) and (y := h(x)) and k(y):
                return y
            return "no"
        """,
    "and-three-walruses": """
        def f(g, h, k):
            if (x := g()) and (y := h(x)) and (z := k(y)):
                return z
            return "no"
        """,
    "and-with-else": """
        def f(g, h, k):
            if (x := g()) and (y := h(x)):
                return ("yes", y)
            else:
                return ("no", k())
        """,
    "leading-plain-operand": """
        def f(g, h, k):
            if g() and (y := h()):
                return y
            return "no"
        """,
    "or": """
        def f(g, h, k):
            if (x := g()) or (y := h()):
                return x
            return "no"
        """,
    "or-and-mixed": """
        def f(g, h, k):
            if (x := g()) or ((y := h()) and (z := k(y))):
                return "yes"
            return "no"
        """,
    "not": """
        def f(g, h, k):
            if not ((x := g()) and (y := h())):
                return "yes"
            return "no"
        """,
    "ifexp": """
        def f(g, h, k):
            if (x := h()) if g() else (y := k()):
                return "yes"
            return "no"
        """,
    "elif": """
        def f(g, h, k):
            if g():
                return "first"
            elif (x := h()) and (y := k(x)):
                return y
            elif (z := k()):
                return ("third", z)
            return "no"
        """,
    "return-walrus-in-if-body": """
        def f(g, h, k):
            if g():
                return (x := h())
            return "no"
        """,
    "while-and": """
        def f(g, h, k):
            n = 0
            while (x := g()) and (y := h(x)) and n < 2:
                n += 1
            return n
        """,
    "while-or": """
        def f(g, h, k):
            n = 0
            while ((x := g()) or (y := h())) and n < 2:
                n += 1
            return n
        """,
    "while-else": """
        def f(g, h, k):
            n = 0
            while (x := g()) and n < 2:
                n += 1
                if k():
                    break
            else:
                return ("else", n)
            return ("broke", n)
        """,
    "one-line-suites": """
        def f(g, h, k):
            if (x := g()) and (y := h()): return (x, y)
            n = 0
            while (z := k()) and n < 2: n += 1
            else: return ("else", n)
            return "no"
        """,
    "comprehension-walrus-in-condition": """
        def f(g, h, k):
            # leading comment
            if [y for x in [g(), h()] if (y := x)] and k():
                return "yes"
            return "no"
        """,
    "for-header-walrus-in-if-body": """
        def f(g, h, k):
            if g():
                for _ in (x := [h()]):
                    return x
            return "no"
        """,
}


@pytest.mark.parametrize("name", SEMANTIC_CASES)
def test_condition_semantics_preserved(name):
    source = textwrap.dedent(SEMANTIC_CASES[name])
    converted = _converters.convert(source)

    native_ns: dict = {}
    exec(source, native_ns)
    converted_ns: dict = {}
    exec(converted, converted_ns)

    for values in itertools.product([0, 1], repeat=3):
        native = _trace(native_ns["f"], values)
        assert _trace(converted_ns["f"], values) == native, (values, converted)


# A walrus in a comprehension or generator expression binds in the
# enclosing function (or module) scope, and is evaluated per item (#70).
# Each snippet defines ``f()``; its result must match the native one.
COMPREHENSION_CASES = {
    "element-accumulates": """
        def f():
            b = 0
            res = [b := i + b for i in range(5)]
            return res, b
        """,
    "filter-binding-visible-after": """
        def f():
            res = [y for x in range(4) if (y := x * 2) > 2]
            return res, y
        """,
    "set-comprehension": """
        def f():
            res = {(last := x % 3) for x in range(5)}
            return res, last
        """,
    "dict-key-and-value": """
        def f():
            a, b = 1, 2
            fib = {(c := a): (a := b) + (b := a + c) - b for __ in range(6)}
            return fib, a, b, c
        """,
    "genexp-short-circuit": """
        def f():
            calls = []
            def num(n):
                calls.append(n)
                return n
            found = any((last := num(n)) == 1 for n in [1, 2, 3])
            return found, last, calls
        """,
    "genexp-lazy": """
        def f():
            seen = None
            gen = ((seen := x) for x in range(3))
            before = seen
            first = next(gen)
            return before, first, seen
        """,
    "genexp-first-iterable-eager": """
        def f():
            calls = []
            def source():
                calls.append("source")
                return [1, 2]
            gen = ((y := x) for x in source())
            calls.append("created")
            return list(gen), y, calls
        """,
    "nested-comprehensions": """
        def f():
            total = 0
            res = [[(total := total + j) for j in range(i)] for i in range(4)]
            return res, total
        """,
    "inner-iterable-and-filter": """
        def f():
            res = [(p := (x, y)) for x in range(3) if x for y in range(x) if y >= 0]
            return res, p
        """,
    "global-keyword": """
        G = None
        def f():
            global G
            res = [(G := x) for x in range(3)]
            return res, G
        """,
    "nonlocal-keyword": """
        def f():
            n = None
            def inner():
                nonlocal n
                return [(n := x) for x in range(3)]
            return inner(), n
        """,
    "in-if-condition": """
        def f():
            if any((hit := x) > 1 for x in range(5)):
                return "found", hit
            return "none"
        """,
    "in-method-with-mangled-name": """
        class C:
            def m(self):
                res = [(__v := x) for x in range(3)]
                return res, __v
        def f():
            return C().m()
        """,
    "global-mangled-name": """
        class C:
            def m(self):
                global __g
                __g = 0
                [_C__g := 1 for a in [2]]
                first = __g
                [__g := 2 for a in [3]]
                return first, __g
        def f():
            return C().m()
        """,
    "one-line-filter": """
        def f():
            calls = []
            def rec(v):
                calls.append(v)
                return v
            res = [y * 10 for x in range(5) if (y := rec(x % 3)) > 0]
            return res, calls
        """,
    "one-line-earlier-filter-and-clauses": """
        def f():
            calls = []
            def rec(v):
                calls.append(v)
                return v
            res = [(x, z) for x in range(4) if rec(x) % 2 for y in range(x) if (z := x * y) > 1]
            return res, calls
        """,
    "one-line-genexp-lazy": """
        def f():
            calls = []
            def rec(v):
                calls.append(v)
                return v
            gen = (y for x in range(3) if (y := rec(x)) >= 0)
            return next(gen), calls
        """,
    "async": """
        import asyncio
        async def agen():
            for x in range(3):
                yield x
        async def double(x):
            return x * 2
        async def g():
            res = [(y := await double(x)) async for x in agen()]
            return res, y
        def f():
            return asyncio.run(g())
        """,
}


def _run_f(source: str):
    namespace: dict = {"__name__": "walrus_case"}
    exec(source, namespace)
    return namespace["f"]()


# Results to compare against instead of running the snippet natively: older
# CPythons reject a mangled walrus target declared ``global`` (a compiler bug
# that CPython's own test_named_expression_scope_mangled_names covers), while
# the converted code runs fine on them.
COMPREHENSION_RESULTS = {"global-mangled-name": (1, 2)}


@pytest.mark.parametrize("name", COMPREHENSION_CASES)
def test_comprehension_semantics_preserved(name):
    source = textwrap.dedent(COMPREHENSION_CASES[name])
    converted = _converters.convert(source)
    if name in COMPREHENSION_RESULTS:
        expected = COMPREHENSION_RESULTS[name]
    else:
        expected = _run_f(source)
    assert _run_f(converted) == expected, converted


def test_comprehension_module_level_binding():
    source = "res = [(m := x * 2) for x in range(3)]\n"
    native: dict = {}
    exec(source, native)
    converted: dict = {}
    exec(_converters.convert(source), converted)
    assert (converted["res"], converted["m"]) == (native["res"], native["m"])


@pytest.mark.xfail(
    strict=True,
    reason="A walrus in a comprehension inside a lambda binds in the lambda's "
    "scope, which a helper hoisted before the statement cannot reach.",
)
def test_comprehension_in_lambda():
    source = textwrap.dedent("""
        def f():
            g = lambda: ([(y := x) for x in range(3)], y)
            return g()
        """)
    converted = _converters.convert(source)
    assert _run_f(converted) == _run_f(source)


def _uses_helper(source: str) -> bool:
    module = cst.parse_module(textwrap.dedent(source))
    return "def _retrofy_comp_" in _converters.convert_walrus_operator(module).code


@pytest.mark.parametrize(
    ("source", "helper"),
    [
        pytest.param("r = [y for x in d if (y := f(x))]\n", False, id="simple-filter"),
        pytest.param(
            "r = [y for x in d if p(x) and (y := f(x))]\n",
            True,
            id="walrus-not-evaluated-first",
        ),
        pytest.param(
            "r = [y for x in d if p(x) if (y := f(x))]\n",
            True,
            id="walrus-not-in-first-filter",
        ),
        pytest.param(
            "r = [y for x in d if (y := f(x))]\nprint(y)\n",
            True,
            id="target-used-after",
        ),
        pytest.param(
            "def g():\n    r = [y for x in d if (y := f(x))]\n    return locals()\n",
            True,
            id="scope-uses-locals",
        ),
        pytest.param("r = [y := f(x) for x in d]\n", True, id="walrus-in-element"),
        pytest.param(
            "r = [y for x in d if (y := f(y))]\n",
            True,
            id="target-read-in-its-own-value",
        ),
    ],
)
def test_comprehension_one_line_only_when_equivalent(source, helper):
    """The one-line form keeps the comprehension in place (and its line
    numbers), but the binding stays inside it, so it's only used when that
    can't be observed."""
    assert _uses_helper(source) is helper


def test_comprehension_one_line_keeps_layout():
    source = textwrap.dedent("""\
        res = [
            y * 2
            for x in data
            if (y := f(x))
        ]
        """)
    expected = textwrap.dedent("""\
        res = [
            y * 2
            for x, y in ((x, f(x)) for x in data)
            if y
        ]
        """)
    result = _converters.convert_walrus_operator(cst.parse_module(source))
    assert result.code == expected


def test_comprehension_helper_keeps_original_as_comment():
    source = "def g():\n    t = 0\n    return [t := t + x for x in d]\n"
    result = _converters.convert_walrus_operator(cst.parse_module(source)).code
    assert "        # [t := t + x for x in d]\n" in result
