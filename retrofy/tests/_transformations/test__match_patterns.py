import sys
import textwrap
from typing import Any, Dict

import libcst as cst

from retrofy import _converters


class Point:
    """Test class for pattern matching."""

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"


def execute_code_with_results(
    code: str,
) -> Dict[str, Any]:
    """Execute code and return the final locals() containing results."""
    # Create a clean namespace with our test classes
    namespace = {
        "Point": Point,
        "__builtins__": __builtins__,
    }

    exec(code, namespace)

    # Filter out built-ins, functions, imports, and our injected items
    result_locals = {
        k: v
        for k, v in namespace.items()
        if (
            k not in ("Point", "__builtins__", "collections")
            and not k.startswith("__")
            and not callable(v)
            and not hasattr(v, "__name__")
        )  # exclude modules
    }
    return result_locals


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

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = http_error(400)
    result2 = http_error(404)
    result3 = http_error(500)
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Bad request"
    assert converted_results["result2"] == "Not found"
    assert converted_results["result3"] == "Something's wrong"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_sequence_matching_tuple():
    """Test sequence matching with tuples."""
    test_case_source = textwrap.dedent("""
    def process_point(point):
        match point:
            case (0, 0):
                return "origin"
            case (0, y):
                return f"y-axis: {y}"
            case (x, 0):
                return f"x-axis: {x}"
            case (x, y):
                return f"point: {x}, {y}"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_point(point):
        if point == (0, 0):
            return "origin"
        elif isinstance(point, collections.abc.Sequence) and not isinstance(point, (str, collections.abc.Mapping)) and len(point) == 2 and point[0] == 0:
            y = point[1]
            return f"y-axis: {y}"
        elif isinstance(point, collections.abc.Sequence) and not isinstance(point, (str, collections.abc.Mapping)) and len(point) == 2 and point[1] == 0:
            x = point[0]
            return f"x-axis: {x}"
        elif isinstance(point, collections.abc.Sequence) and not isinstance(point, (str, collections.abc.Mapping)) and len(point) == 2:
            x, y = point
            return f"point: {x}, {y}"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = process_point((0, 0))
    result2 = process_point((0, 5))
    result3 = process_point((3, 0))
    result4 = process_point((2, 4))
    result5 = process_point([1, 2])  # list should work too
    result6 = process_point({"x": 1, "y": 2})  # dict should NOT match sequences
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "origin"
    assert converted_results["result2"] == "y-axis: 5"
    assert converted_results["result3"] == "x-axis: 3"
    assert converted_results["result4"] == "point: 2, 4"
    assert converted_results["result5"] == "point: 1, 2"
    # Critical: dict should not match sequence patterns (result6 should be None)
    assert converted_results["result6"] is None

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_guard_clauses():
    """Test guard clauses with if conditions."""
    test_case_source = textwrap.dedent("""
    def categorize_number(x):
        match x:
            case n if n > 100:
                return f"large: {n}"
            case n if n > 10:
                return f"medium: {n}"
            case n if n > 0:
                return f"small: {n}"
            case n:
                return f"non-positive: {n}"
    """)

    expected = textwrap.dedent("""
    def categorize_number(x):
        if x > 100:
            n = x
            return f"large: {n}"
        elif x > 10:
            n = x
            return f"medium: {n}"
        elif x > 0:
            n = x
            return f"small: {n}"
        else:
            n = x
            return f"non-positive: {n}"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = categorize_number(150)
    result2 = categorize_number(50)
    result3 = categorize_number(5)
    result4 = categorize_number(-10)
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "large: 150"
    assert converted_results["result2"] == "medium: 50"
    assert converted_results["result3"] == "small: 5"
    assert converted_results["result4"] == "non-positive: -10"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_or_patterns_simple():
    """Test OR patterns with consistent variable bindings."""
    test_case_source = textwrap.dedent("""
    def classify_value(value):
        match value:
            case 1 | 2 | 3:
                return "Small number"
            case "a" | "b":
                return "Letter"
            case _:
                return "Other"
    """)

    expected = textwrap.dedent("""
    def classify_value(value):
        if value in (1, 2, 3):
            return "Small number"
        elif value in ("a", "b"):
            return "Letter"
        else:
            return "Other"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = classify_value(2)
    result2 = classify_value("a")
    result3 = classify_value(42)
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Small number"
    assert converted_results["result2"] == "Letter"
    assert converted_results["result3"] == "Other"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_or_patterns_with_variables():
    """Test OR patterns with variable bindings - expanded to separate cases."""
    test_case_source = textwrap.dedent("""
    def axis_point(value):
        match value:
            case Point(x=x, y=0) | Point(x=0, y=x):
                return f"On axis at {x}"
            case _:
                return "Not on axis"
    """)

    # OR patterns with different variable bindings are expanded into separate if/elif conditions
    expected = textwrap.dedent("""
    def axis_point(value):
        if isinstance(value, Point) and value.y == 0:
            x = value.x
            return f"On axis at {x}"
        elif isinstance(value, Point) and value.x == 0:
            x = value.y
            return f"On axis at {x}"
        else:
            return "Not on axis"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = axis_point(Point(5, 0))
    result2 = axis_point(Point(0, 3))
    result3 = axis_point(Point(2, 4))
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "On axis at 5"
    assert converted_results["result2"] == "On axis at 3"
    assert converted_results["result3"] == "Not on axis"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_class_pattern_matching():
    """Test class pattern matching with attributes."""
    test_case_source = textwrap.dedent("""
    def describe_point(point):
        match point:
            case Point(x=0, y=0):
                return "Origin"
            case Point(x=0, y=y):
                return f"Y-axis: {y}"
            case Point(x=x, y=0):
                return f"X-axis: {x}"
            case Point(x=x, y=y):
                return f"Point: {x}, {y}"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    def describe_point(point):
        if isinstance(point, Point) and point.x == 0 and point.y == 0:
            return "Origin"
        elif isinstance(point, Point) and point.x == 0:
            y = point.y
            return f"Y-axis: {y}"
        elif isinstance(point, Point) and point.y == 0:
            x = point.x
            return f"X-axis: {x}"
        elif isinstance(point, Point):
            x = point.x
            y = point.y
            return f"Point: {x}, {y}"
        else:
            return "Not a point"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = describe_point(Point(0, 0))
    result2 = describe_point(Point(0, 5))
    result3 = describe_point(Point(3, 0))
    result4 = describe_point(Point(2, 4))
    result5 = describe_point("not a point")
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Origin"
    assert converted_results["result2"] == "Y-axis: 5"
    assert converted_results["result3"] == "X-axis: 3"
    assert converted_results["result4"] == "Point: 2, 4"
    assert converted_results["result5"] == "Not a point"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_mapping_patterns():
    """Test dictionary/mapping patterns."""
    test_case_source = textwrap.dedent("""
    def handle_request(request):
        match request:
            case {"action": "get", "resource": resource}:
                return f"Getting {resource}"
            case {"action": "post", "resource": resource, "data": data}:
                return f"Posting to {resource}: {data}"
            case {"action": action}:
                return f"Unknown action: {action}"
            case _:
                return "Invalid request"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def handle_request(request):
        if isinstance(request, collections.abc.Mapping) and "action" in request and request["action"] == "get" and "resource" in request:
            resource = request["resource"]
            return f"Getting {resource}"
        elif isinstance(request, collections.abc.Mapping) and "action" in request and request["action"] == "post" and "resource" in request and "data" in request:
            resource = request["resource"]
            data = request["data"]
            return f"Posting to {resource}: {data}"
        elif isinstance(request, collections.abc.Mapping) and "action" in request:
            action = request["action"]
            return f"Unknown action: {action}"
        else:
            return "Invalid request"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = handle_request({"action": "get", "resource": "users"})
    result2 = handle_request({"action": "post", "resource": "posts", "data": {"title": "Hello"}})
    result3 = handle_request({"action": "delete"})
    result4 = handle_request({"invalid": "request"})
    result5 = handle_request("not a dict")
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Getting users"
    assert converted_results["result2"] == "Posting to posts: {'title': 'Hello'}"
    assert converted_results["result3"] == "Unknown action: delete"
    assert converted_results["result4"] == "Invalid request"
    assert converted_results["result5"] == "Invalid request"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_star_patterns():
    """Test star patterns with slicing logic."""
    test_case_source = textwrap.dedent("""
    def process_sequence(sequence):
        match sequence:
            case [first, *rest]:
                return f"First: {first}, Rest: {rest}"
            case [*prefix, last]:
                return f"Prefix: {prefix}, Last: {last}"
            case _:
                return "No match"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_sequence(sequence):
        if isinstance(sequence, collections.abc.Sequence) and not isinstance(sequence, (str, collections.abc.Mapping)) and len(sequence) >= 1:
            first = sequence[0]
            rest = sequence[1:]
            return f"First: {first}, Rest: {rest}"
        elif isinstance(sequence, collections.abc.Sequence) and not isinstance(sequence, (str, collections.abc.Mapping)) and len(sequence) >= 1:
            prefix = sequence[0:-1]
            last = sequence[-1]
            return f"Prefix: {prefix}, Last: {last}"
        else:
            return "No match"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = process_sequence([1, 2, 3, 4])
    result2 = process_sequence([42])
    result3 = process_sequence([])
    result4 = process_sequence("string")  # Should not match
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "First: 1, Rest: [2, 3, 4]"
    assert converted_results["result2"] == "First: 42, Rest: []"
    assert converted_results["result3"] == "No match"
    assert converted_results["result4"] == "No match"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_nested_patterns():
    """Test nested pattern destructuring."""
    test_case_source = textwrap.dedent("""
    def analyze_data(data):
        match data:
            case {"users": [{"name": name, "active": True}]}:
                return f"Active user: {name}"
            case {"users": []}:
                return "No users"
            case {"users": users}:
                return f"Users count: {len(users)}"
            case _:
                return "Invalid data"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def analyze_data(data):
        if isinstance(data, collections.abc.Mapping) and "users" in data and isinstance(data["users"], collections.abc.Sequence) and not isinstance(data["users"], (str, collections.abc.Mapping)) and len(data["users"]) == 1 and isinstance(data["users"][0], collections.abc.Mapping) and "name" in data["users"][0] and "active" in data["users"][0] and data["users"][0]["active"] == True:
            name = data["users"][0]["name"]
            return f"Active user: {name}"
        elif isinstance(data, collections.abc.Mapping) and "users" in data and isinstance(data["users"], collections.abc.Sequence) and not isinstance(data["users"], (str, collections.abc.Mapping)) and len(data["users"]) == 0:
            return "No users"
        elif isinstance(data, collections.abc.Mapping) and "users" in data:
            users = data["users"]
            return f"Users count: {len(users)}"
        else:
            return "Invalid data"
    """)

    # STRING VALIDATION: Test exact code generation
    module = cst.parse_module(test_case_source)
    result = _converters.convert_match_statement(module)
    assert result.code == expected

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_source_with_calls = test_case_source + textwrap.dedent("""
    result1 = analyze_data({"users": [{"name": "Alice", "active": True}]})
    result2 = analyze_data({"users": []})
    result3 = analyze_data({"users": [{"name": "Bob"}, {"name": "Carol"}]})
    result4 = analyze_data({"items": []})
    """)

    converted_code = _converters.convert(test_source_with_calls)
    converted_results = execute_code_with_results(converted_code)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Active user: Alice"
    assert converted_results["result2"] == "No users"
    assert converted_results["result3"] == "Users count: 2"
    assert converted_results["result4"] == "Invalid data"

    # EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(test_source_with_calls)
        assert original_results == converted_results


def test_comprehensive_pattern_execution_converted_only():
    """Test converted code execution on all Python versions (without string validation)."""
    # This test validates converted code behavior on Python versions without match statements
    original_code = textwrap.dedent("""
    def comprehensive_matcher(data):
        match data:
            case []:
                return "empty list"
            case [x] if x > 10:
                return f"single large: {x}"
            case [x, y]:
                return f"pair: {x}, {y}"
            case {"type": "user", "name": name}:
                return f"user: {name}"
            case Point(x=0, y=y):
                return f"y-axis point: {y}"
            case _:
                return "other"

    result1 = comprehensive_matcher([])
    result2 = comprehensive_matcher([15])
    result3 = comprehensive_matcher([5])  # Small number
    result4 = comprehensive_matcher([1, 2])
    result5 = comprehensive_matcher({"type": "user", "name": "Alice"})
    result6 = comprehensive_matcher(Point(0, 5))
    result7 = comprehensive_matcher({"type": "admin"})  # Dict without name
    result8 = comprehensive_matcher("string")  # Should not match sequences
    """)

    converted_code = _converters.convert(original_code)

    # Execute the converted code (works on all Python versions)
    converted_results = execute_code_with_results(converted_code)

    # Verify comprehensive pattern matching behavior
    assert converted_results["result1"] == "empty list"
    assert converted_results["result2"] == "single large: 15"
    assert converted_results["result3"] == "other"  # Doesn't match guard
    assert converted_results["result4"] == "pair: 1, 2"
    assert (
        converted_results["result5"] == "user: Alice"
    )  # Dict should not match sequences
    assert converted_results["result6"] == "y-axis point: 5"
    assert converted_results["result7"] == "other"  # No name key
    assert converted_results["result8"] == "other"  # String should not match sequences

    # ADDITIONAL EXECUTION VALIDATION on Python 3.10+ (if available)
    if sys.version_info >= (3, 10):
        original_results = execute_code_with_results(original_code)
        assert original_results == converted_results
