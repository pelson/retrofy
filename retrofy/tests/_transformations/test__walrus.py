import textwrap

import libcst as cst

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

    expected = textwrap.dedent("""
    a = foo(); b = bar()
    if a > 5 or b < 6:
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

    expected = textwrap.dedent("""
    while True:
        chunk = file.read(8192); alive = random.random()
        if not (chunk and alive > 2): break
        process(chunk)
    """)

    # In actual fact, the short-circuiting nature of logical
    # operators means that perhaps this should really be (but perhaps not for or):
    expected_idealistc = textwrap.dedent("""
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
    case_source = 'y0 = (y1 := f(x))'
    # The fact that it is on a single line is not semantically important.
    expected = 'y1 = f(x); y0 = y1'
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
    case_source = '[y := f(x), (x := y**2), y**3]'
    expected = "y = f(x); x = y**2; [y, x, y**3]"
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_subexpression__single():
    case_source = 'filtered_data = [y for x in data if (y := f(x)) is not None]'
    expected = 'filtered_data = [y for x, y in ([x, f(x)] for x in data) if y is not None]'
    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected


def test_subexpression__multiple():
    case_source = 'filtered_data = [y for x in data if (y := f(x)) is not None and (z := g(x + 1)) > 2]'
    expected = 'filtered_data = [y for x, y, z in ([x, f(x), g(x + 1)] for x in data) if y is not None and z > 2]'

    # If we special case for short-circuitry of and vs or, the following would be needed for and...
    ideal = '''
    [for y in (for x, y, z in ([x, y, g(x + 1] for x, y in ([x, y] for x, y in ([x, f(x)] for x in data) if y is not None)) if z > 2)]
    '''

    module = cst.parse_module(case_source)
    result = _converters.convert_walrus_operator(module)
    assert result.code == expected
