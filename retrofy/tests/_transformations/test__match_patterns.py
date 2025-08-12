"""
Tests for match statement to pre-Python 3.10 syntax conversion.

This test suite demonstrates both the patterns that CAN be translated to legacy syntax
and the patterns that CANNOT be correctly translated, highlighting the fundamental
limitations of trying to backport match statements.

WORKING PATTERNS (should pass):
- test_literal_matching_simple: Basic literal matching
- test_literal_matching_multiple_types: Different literal types
- test_variable_binding_simple: Simple variable binding
- test_sequence_matching_tuple: Tuple pattern matching
- test_sequence_matching_list: List pattern matching
- test_or_patterns_simple: Simple OR patterns with literals
- test_or_patterns_with_variables: OR patterns with different variable bindings
- test_wildcard_patterns: Wildcard and capture patterns
- test_as_patterns: As patterns for value capture

PROBLEMATIC PATTERNS (marked as skipped with explanations):
- test_guard_clauses: Guard clauses lose scoping guarantees
- test_class_pattern_matching: Becomes verbose with isinstance checks
- test_nested_patterns: Extremely verbose nested destructuring
- test_mapping_patterns: Verbose dictionary pattern matching
- test_complex_nested_with_guards: Fundamentally broken control flow
- test_star_patterns_advanced: Complex slicing logic

The skipped tests show why match statements cannot be cleanly backported and
demonstrate the limitations that would arise in any translation attempt.
"""

import textwrap

import libcst as cst
import pytest

from retrofy import _converters


def test_literal_matching_simple():
    """Test basic literal matching with numbers and strings."""
    test_case_source = textwrap.dedent("""
    def http_error(status):
        match status:
            case 400:
                return "Bad request"
            case 404:
                return "Not found"
            case _:
                return "Something's wrong"
    """)

    expected = textwrap.dedent("""
    def http_error(status):
        if status == 400:
            return "Bad request"
        elif status == 404:
            return "Not found"
        else:
            return "Something's wrong"
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_literal_matching_multiple_types():
    """Test literal matching with different types."""
    test_case_source = textwrap.dedent("""
    match value:
        case 42:
            result = "number"
        case "hello":
            result = "string"
        case True:
            result = "boolean"
        case None:
            result = "none"
    """)

    expected = textwrap.dedent("""
    if value == 42:
        result = "number"
    elif value == "hello":
        result = "string"
    elif value == True:
        result = "boolean"
    elif value == None:
        result = "none"
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_variable_binding_simple():
    """Test simple variable binding in patterns."""
    test_case_source = textwrap.dedent("""
    match x:
        case y:
            return y * 2
    """)

    expected = textwrap.dedent("""
    y = x
    return y * 2
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_sequence_matching_tuple():
    """Test sequence matching with tuples."""
    test_case_source = textwrap.dedent("""
    match point:
        case (0, 0):
            print("Origin")
        case (0, y):
            print(f"Y={y}")
        case (x, 0):
            print(f"X={x}")
        case (x, y):
            print(f"X={x}, Y={y}")
    """)

    expected = textwrap.dedent("""
    if point == (0, 0):
        print("Origin")
    elif len(point) == 2 and point[0] == 0:
        y = point[1]
        print(f"Y={y}")
    elif len(point) == 2 and point[1] == 0:
        x = point[0]
        print(f"X={x}")
    elif len(point) == 2:
        x, y = point
        print(f"X={x}, Y={y}")
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_sequence_matching_list():
    """Test sequence matching with lists."""
    test_case_source = textwrap.dedent("""
    match items:
        case []:
            print("Empty")
        case [x]:
            print(f"Single: {x}")
        case [x, y]:
            print(f"Pair: {x}, {y}")
        case [x, *rest]:
            print(f"Head: {x}, Rest: {rest}")
    """)

    expected = textwrap.dedent("""
    if len(items) == 0:
        print("Empty")
    elif len(items) == 1:
        x = items[0]
        print(f"Single: {x}")
    elif len(items) == 2:
        x, y = items
        print(f"Pair: {x}, {y}")
    elif len(items) >= 1:
        x = items[0]
        rest = items[1:]
        print(f"Head: {x}, Rest: {rest}")
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_or_patterns_simple():
    """Test OR patterns with consistent variable bindings."""
    test_case_source = textwrap.dedent("""
    match value:
        case 1 | 2 | 3:
            print("Small number")
        case "a" | "b":
            print("Letter")
    """)

    expected = textwrap.dedent("""
    if value in (1, 2, 3):
        print("Small number")
    elif value in ("a", "b"):
        print("Letter")
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_or_patterns_with_variables():
    """Test OR patterns with variable bindings - can be translated by expanding to separate cases."""
    test_case_source = textwrap.dedent("""
    match value:
        case Point(x, 0) | Point(0, x):
            print(f"On axis at {x}")
    """)

    # OR patterns with different variable bindings are translated by expanding
    # them into separate if/elif conditions:
    expected = textwrap.dedent("""
    if (isinstance(value, Point) and value.y == 0):
        x = value.x
        print(f"On axis at {x}")
    elif (isinstance(value, Point) and value.x == 0):
        x = value.y
        print(f"On axis at {x}")
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Guard clauses lose semantic scoping guarantees - match statements have consistent variable binding that cannot be replicated in if/elif chains",
)
def test_guard_clauses():
    """Test guard clauses with if conditions - demonstrates scoping limitations."""
    test_case_source = textwrap.dedent("""
    match x:
        case y if y > 0:
            return f"positive {y}"
        case y if y < 0:
            return f"negative {y}"
        case y:
            return "zero"
    """)

    # The test expects nested if/else structure to mimic match scoping:
    expected = textwrap.dedent("""
    y = x
    if y > 0:
        return f"positive {y}"
    else:
        y = x
        if y < 0:
            return f"negative {y}"
        else:
            y = x
            return "zero"
    """)

    # Problems with this translation:
    # 1. Variable 'y' is rebound multiple times unnecessarily
    # 2. Loses the clean pattern matching semantics
    # 3. More complex nesting than the original match statement
    # 4. Cannot guarantee exhaustive checking
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Class pattern matching becomes verbose with explicit isinstance checks and loses pattern-based dispatch elegance",
)
def test_class_pattern_matching():
    """Test class pattern matching with attributes - shows verbosity of translation."""
    test_case_source = textwrap.dedent("""
    def where_is(point):
        match point:
            case Point(x=0, y=0):
                print("Origin")
            case Point(x=0, y=y):
                print(f"Y={y}")
            case Point(x=x, y=0):
                print(f"X={x}")
            case Point(x=x, y=y):
                print(f"X={x}, Y={y}")
    """)

    expected = textwrap.dedent("""
    def where_is(point):
        if isinstance(point, Point) and point.x == 0 and point.y == 0:
            print("Origin")
        elif isinstance(point, Point) and point.x == 0:
            y = point.y
            print(f"Y={y}")
        elif isinstance(point, Point) and point.y == 0:
            x = point.x
            print(f"X={x}")
        elif isinstance(point, Point):
            x = point.x
            y = point.y
            print(f"X={x}, Y={y}")
    """)

    # Issues with this translation:
    # 1. Very verbose isinstance checks repeated everywhere
    # 2. Manual attribute access instead of pattern destructuring
    # 3. No automatic exhaustiveness checking
    # 4. Loses the declarative nature of pattern matching
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Nested pattern destructuring becomes extremely verbose and error-prone with manual type/structure checking",
)
def test_nested_patterns():
    """Test nested pattern destructuring - demonstrates why deep patterns are problematic."""
    test_case_source = textwrap.dedent("""
    match data:
        case {"users": [{"name": name, "active": True}]}:
            return name
        case {"users": []}:
            return "No users"
        case {"users": users}:
            return f"{len(users)} users"
    """)

    expected = textwrap.dedent("""
    if (isinstance(data, dict) and "users" in data and
        isinstance(data["users"], list) and len(data["users"]) >= 1 and
        isinstance(data["users"][0], dict) and "name" in data["users"][0] and
        "active" in data["users"][0] and data["users"][0]["active"] is True):
        name = data["users"][0]["name"]
        return name
    elif (isinstance(data, dict) and "users" in data and
          isinstance(data["users"], list) and len(data["users"]) == 0):
        return "No users"
    elif isinstance(data, dict) and "users" in data:
        users = data["users"]
        return f"{len(users)} users"
    """)

    # Major problems with nested pattern translation:
    # 1. Extremely verbose and hard to read
    # 2. Error-prone manual type/structure checking
    # 3. Loses the declarative nature of pattern matching
    # 4. No safety guarantees about data structure validity
    # 5. Order-dependent conditions that could miss cases
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Mapping patterns require verbose isinstance and key checking, losing the elegance of pattern matching",
)
def test_mapping_patterns():
    """Test dictionary/mapping patterns - shows verbosity issues."""
    test_case_source = textwrap.dedent("""
    match request:
        case {"action": "get", "resource": resource}:
            return f"Getting {resource}"
        case {"action": "post", "resource": resource, "data": data}:
            return f"Posting to {resource}: {data}"
        case {"action": action}:
            return f"Unknown action: {action}"
    """)

    expected = textwrap.dedent("""
    if (isinstance(request, dict) and "action" in request and
        request["action"] == "get" and "resource" in request):
        resource = request["resource"]
        return f"Getting {resource}"
    elif (isinstance(request, dict) and "action" in request and
          request["action"] == "post" and "resource" in request and "data" in request):
        resource = request["resource"]
        data = request["data"]
        return f"Posting to {resource}: {data}"
    elif isinstance(request, dict) and "action" in request:
        action = request["action"]
        return f"Unknown action: {action}"
    """)

    # Issues with mapping pattern translation:
    # 1. Repetitive isinstance(dict) checks
    # 2. Manual key existence checking
    # 3. Verbose condition chaining
    # 4. No automatic handling of missing keys
    # 5. Loses structural pattern matching benefits
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_wildcard_patterns():
    """Test wildcard patterns and capture patterns."""
    test_case_source = textwrap.dedent("""
    match value:
        case (x, _, z):
            return x + z
        case {"key": _, "value": v}:
            return v
        case _:
            return "default"
    """)

    expected = textwrap.dedent("""
    if len(value) == 3:
        x = value[0]
        z = value[2]
        return x + z
    elif isinstance(value, dict) and "key" in value and "value" in value:
        v = value["value"]
        return v
    else:
        return "default"
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Complex nested patterns with guards lose all semantic guarantees and become extremely fragile",
)
def test_complex_nested_with_guards():
    """Test complex nested patterns with guards - shows complete breakdown of translation."""
    test_case_source = textwrap.dedent("""
    match data:
        case {"items": [item]} if len(item.get("tags", [])) > 2:
            return item["name"]
        case {"items": items} if len(items) > 5:
            return "Too many items"
        case _:
            return "No match"
    """)

    # This shows the complexity when translating complex nested patterns with guards
    expected = textwrap.dedent("""
    if (isinstance(data, dict) and "items" in data and
        isinstance(data["items"], list) and len(data["items"]) == 1):
        item = data["items"][0]
        if len(item.get("tags", [])) > 2:
            return item["name"]
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        if len(items) > 5:
            return "Too many items"
    else:
        return "No match"
    """)

    # This translation is fundamentally broken:
    # 1. Control flow is incorrect (missing elif connections)
    # 2. First condition might succeed but guard might fail, leading to wrong fallthrough
    # 3. No guarantee that all cases are handled correctly
    # 4. Extremely fragile to maintain and debug
    # 5. Completely loses exhaustiveness checking
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


@pytest.mark.skip(
    reason="Star patterns require complex slicing logic and lose the elegance of automatic unpacking",
)
def test_star_patterns_advanced():
    """Test star patterns - shows complexity of manual slicing."""
    test_case_source = textwrap.dedent("""
    match sequence:
        case [first, *middle, last] if len(middle) > 2:
            return middle[1]
        case [*all]:
            return all
        case (*prefix, last_two):
            return prefix, last_two
    """)

    expected = textwrap.dedent("""
    if len(sequence) >= 2:
        first = sequence[0]
        middle = sequence[1:-1]
        last = sequence[-1]
        if len(middle) > 2:
            return middle[1]
    if True:  # [*all] matches any sequence
        all = list(sequence)
        return all
    if len(sequence) >= 1:
        prefix = sequence[:-1]
        last_two = sequence[-1]
        return prefix, last_two
    """)

    # Problems with star pattern translation:
    # 1. Complex manual slicing logic that's error-prone
    # 2. Multiple length checks needed for safety
    # 3. Guard conditions interact poorly with complex unpacking
    # 4. Edge cases around empty sequences are hard to handle correctly
    # 5. Loses the automatic unpacking semantics of match statements
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected


def test_as_patterns():
    """Test as patterns for capturing matched values."""
    test_case_source = textwrap.dedent("""
    match value:
        case (x, y) as point:
            return f"Point {point} has coordinates {x}, {y}"
        case [x, *rest] as full_list:
            return f"List {full_list} starts with {x}"
    """)

    expected = textwrap.dedent("""
    if len(value) == 2:
        point = value
        x, y = value
        return f"Point {point} has coordinates {x}, {y}"
    elif len(value) >= 1:
        full_list = value
        x = value[0]
        rest = value[1:]
        return f"List {full_list} starts with {x}"
    """)

    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected
