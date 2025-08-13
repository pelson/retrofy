import sys
import textwrap
from typing import Any, Dict

import libcst as cst
import pytest

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
    class Container:
        def __init__(self, items):
            self.items = items

    namespace = {
        "Point": Point,
        "Container": Container,
        "__builtins__": __builtins__,
    }

    exec(code, namespace)

    # Filter out built-ins, functions, imports, and our injected items
    result_locals = {
        k: v
        for k, v in namespace.items()
        if (
            k not in ("Point", "Container", "__builtins__", "collections")
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

    test_calls = textwrap.dedent("""
    result1 = http_error(400)
    result2 = http_error(404)
    result3 = http_error(500)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Bad request"
    assert converted_results["result2"] == "Not found"
    assert converted_results["result3"] == "Something's wrong"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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

    test_calls = textwrap.dedent("""
    result1 = process_point((0, 0))
    result2 = process_point((0, 5))
    result3 = process_point((3, 0))
    result4 = process_point((2, 4))
    result5 = process_point([1, 2])  # list should work too
    result6 = process_point({"x": 1, "y": 2})  # dict should NOT match sequences
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "origin"
    assert converted_results["result2"] == "y-axis: 5"
    assert converted_results["result3"] == "x-axis: 3"
    assert converted_results["result4"] == "point: 2, 4"
    assert converted_results["result5"] == "point: 1, 2"
    # Critical: dict should not match sequence patterns (result6 should be None)
    assert converted_results["result6"] is None

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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

    test_calls = textwrap.dedent("""
    result1 = categorize_number(150)
    result2 = categorize_number(50)
    result3 = categorize_number(5)
    result4 = categorize_number(-10)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "large: 150"
    assert converted_results["result2"] == "medium: 50"
    assert converted_results["result3"] == "small: 5"
    assert converted_results["result4"] == "non-positive: -10"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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

    test_calls = textwrap.dedent("""
    result1 = classify_value(2)
    result2 = classify_value("a")
    result3 = classify_value(42)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Small number"
    assert converted_results["result2"] == "Letter"
    assert converted_results["result3"] == "Other"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_or_patterns_with_variables():
    """Test OR patterns with variable bindings - expanded to separate cases."""
    test_case_source = textwrap.dedent("""
    class XAxisMarker:
        def __init__(self, x):
            self.x = x

    def axis_point(value):
        match value:
            case Point(x=x, y=0) | Point(x=0, y=x):
                return f"On axis at {x}"
            case Point(x=x, y=-1) | XAxisMarker(x=x) if x > 0:
                return f"On x-axis at x={x}"
            case _:
                return "Not on axis"
    """)

    # OR patterns with different variable bindings are expanded into separate if/elif conditions
    expected = textwrap.dedent("""
    class XAxisMarker:
        def __init__(self, x):
            self.x = x

    def axis_point(value):
        if isinstance(value, Point) and value.y == 0:
            x = value.x
            return f"On axis at {x}"
        elif isinstance(value, Point) and value.x == 0:
            x = value.y
            return f"On axis at {x}"
        elif isinstance(value, Point) and value.y == -1 and value.x > 0:
            x = value.x
            return f"On x-axis at x={x}"
        elif isinstance(value, XAxisMarker) and value.x > 0:
            x = value.x
            return f"On x-axis at x={x}"
        else:
            return "Not on axis"
    """)

    test_calls = textwrap.dedent("""
    result1 = axis_point(Point(5, 0))
    result2 = axis_point(Point(0, 3))
    result3 = axis_point(Point(2, 4))
    result4 = axis_point(XAxisMarker(5))
    result5 = axis_point(Point(6, -1))
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "On axis at 5"
    assert converted_results["result2"] == "On axis at 3"
    assert converted_results["result3"] == "Not on axis"
    assert converted_results["result4"] == "On x-axis at x=5"
    assert converted_results["result5"] == "On x-axis at x=6"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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

    test_calls = textwrap.dedent("""
    result1 = describe_point(Point(0, 0))
    result2 = describe_point(Point(0, 5))
    result3 = describe_point(Point(3, 0))
    result4 = describe_point(Point(2, 4))
    result5 = describe_point("not a point")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Origin"
    assert converted_results["result2"] == "Y-axis: 5"
    assert converted_results["result3"] == "X-axis: 3"
    assert converted_results["result4"] == "Point: 2, 4"
    assert converted_results["result5"] == "Not a point"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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

    test_calls = textwrap.dedent("""
    result1 = handle_request({"action": "get", "resource": "users"})
    result2 = handle_request({"action": "post", "resource": "posts", "data": {"title": "Hello"}})
    result3 = handle_request({"action": "delete"})
    result4 = handle_request({"invalid": "request"})
    result5 = handle_request("not a dict")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Getting users"
    assert converted_results["result2"] == "Posting to posts: {'title': 'Hello'}"
    assert converted_results["result3"] == "Unknown action: delete"
    assert converted_results["result4"] == "Invalid request"
    assert converted_results["result5"] == "Invalid request"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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
            rest = list(sequence[1:])
            return f"First: {first}, Rest: {rest}"
        elif isinstance(sequence, collections.abc.Sequence) and not isinstance(sequence, (str, collections.abc.Mapping)) and len(sequence) >= 1:
            prefix = list(sequence[0:-1])
            last = sequence[-1]
            return f"Prefix: {prefix}, Last: {last}"
        else:
            return "No match"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_sequence([1, 2, 3, 4])
    result2 = process_sequence([42])
    result3 = process_sequence([])
    result4 = process_sequence("string")  # Should not match
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "First: 1, Rest: [2, 3, 4]"
    assert converted_results["result2"] == "First: 42, Rest: []"
    assert converted_results["result3"] == "No match"
    assert converted_results["result4"] == "No match"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
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
        if isinstance(data, collections.abc.Mapping) and "users" in data and isinstance(data["users"], collections.abc.Sequence) and not isinstance(data["users"], (str, collections.abc.Mapping)) and len(data["users"]) == 1 and isinstance(data["users"][0], collections.abc.Mapping) and "name" in data["users"][0] and "active" in data["users"][0] and data["users"][0]["active"] is True:
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

    test_calls = textwrap.dedent("""
    result1 = analyze_data({"users": [{"name": "Alice", "active": True}]})
    result2 = analyze_data({"users": []})
    result3 = analyze_data({"users": [{"name": "Bob"}, {"name": "Carol"}]})
    result4 = analyze_data({"items": []})
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Active user: Alice"
    assert converted_results["result2"] == "No users"
    assert converted_results["result3"] == "Users count: 2"
    assert converted_results["result4"] == "Invalid data"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_comprehensive_pattern_execution_converted_only():
    """Test converted code execution on all Python versions (without string validation)."""
    # This test validates converted code behavior on Python versions without match statements
    test_case_source = textwrap.dedent("""
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
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def comprehensive_matcher(data):
        if isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 0:
            return "empty list"
        elif isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 1 and data[0] > 10:
            x = data[0]
            return f"single large: {x}"
        elif isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 2:
            x, y = data
            return f"pair: {x}, {y}"
        elif isinstance(data, collections.abc.Mapping) and "type" in data and data["type"] == "user" and "name" in data:
            name = data["name"]
            return f"user: {name}"
        elif isinstance(data, Point) and data.x == 0:
            y = data.y
            return f"y-axis point: {y}"
        else:
            return "other"
    """)

    test_calls = textwrap.dedent("""
    result1 = comprehensive_matcher([])
    result2 = comprehensive_matcher([15])
    result3 = comprehensive_matcher([5])  # Small number
    result4 = comprehensive_matcher([1, 2])
    result5 = comprehensive_matcher({"type": "user", "name": "Alice"})
    result6 = comprehensive_matcher(Point(0, 5))
    result7 = comprehensive_matcher({"type": "admin"})  # Dict without name
    result8 = comprehensive_matcher("string")  # Should not match sequences
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

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

    if sys.version_info >= (3, 10):
        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_as_patterns_simple():
    """Test basic as patterns for capturing matched values."""
    test_case_source = textwrap.dedent("""
    def process_value(value):
        match value:
            case (1 | 2 | 3) as num:
                return f"Small number: {num}"
            case ("hello" | "hi") as greeting:
                return f"Greeting: {greeting}"
            case _ as anything:
                return f"Other: {anything}"
    """)

    expected = textwrap.dedent("""
    def process_value(value):
        if value in (1, 2, 3):
            num = value
            return f"Small number: {num}"
        elif value in ("hello", "hi"):
            greeting = value
            return f"Greeting: {greeting}"
        else:
            anything = value
            return f"Other: {anything}"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_value(2)
    result2 = process_value("hello")
    result3 = process_value(42)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Small number: 2"
    assert converted_results["result2"] == "Greeting: hello"
    assert converted_results["result3"] == "Other: 42"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_nested_as_patterns():
    """Test as patterns nested within other patterns."""
    test_case_source = textwrap.dedent("""
    def analyze_point(data):
        match data:
            case {"point": (x, y) as coords} if x > 0 and y > 0:
                return f"Positive quadrant: {coords}"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def analyze_point(data):
        if isinstance(data, collections.abc.Mapping) and "point" in data and isinstance(data["point"], collections.abc.Sequence) and not isinstance(data["point"], (str, collections.abc.Mapping)) and len(data["point"]) == 2 and data["point"][0] > 0 and data["point"][1] > 0:
            coords = data["point"]
            x, y = data["point"]
            return f"Positive quadrant: {coords}"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    result1 = analyze_point({"point": (3, 4)})
    result3 = analyze_point({"data": "other"})
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)  # noqa: F841

    assert converted_results["result1"] == "Positive quadrant: (3, 4)"
    assert converted_results["result3"] == "Not a point"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert converted_results == original_results


def test_value_patterns_constants():
    """Test value patterns using dotted names for constants."""
    test_case_source = textwrap.dedent("""
    import math

    def classify_angle(angle):
        match angle:
            case math.pi:
                return "π radians"
            # case math.pi / 2:   # Note this would produce a syntax error. Would be good to test this separately.
            #    return "π radians"
            case 0:
                return "Zero"
            case _:
                return "Other angle"
    """)

    expected = textwrap.dedent("""
    import math

    def classify_angle(angle):
        if angle == math.pi:
            return "π radians"
        elif angle == 0:
            return "Zero"
        else:
            return "Other angle"
    """)

    test_calls = textwrap.dedent("""
    import math
    result1 = classify_angle(math.pi)
    result2 = classify_angle(math.pi / 2)
    result3 = classify_angle(0)
    result4 = classify_angle(1.5)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)  # noqa: F841

    assert converted_results["result1"] == "π radians"
    assert converted_results["result3"] == "Zero"
    assert converted_results["result4"] == "Other angle"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_group_patterns():
    """Test parenthesized group patterns."""
    test_case_source = textwrap.dedent("""
    def check_complex_condition(data):
        match data:
            case (1 | 2) if data > 1:
                return "Two"
            case (1 | 2):
                return "One"
            case ((3 | 4) | (5 | 6)):
                return "Mid range"
            case _:
                return "Other"
    """)

    expected = textwrap.dedent("""
    def check_complex_condition(data):
        if data in (1, 2) and data > 1:
            return "Two"
        elif data in (1, 2):
            return "One"
        elif data in (3, 4):
            return "Mid range"
        elif data in (5, 6):
            return "Mid range"
        else:
            return "Other"
    """)

    test_calls = textwrap.dedent("""
    result1 = check_complex_condition(2)
    result2 = check_complex_condition(1)
    result3 = check_complex_condition(4)
    result4 = check_complex_condition(10)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Two"
    assert converted_results["result2"] == "One"
    assert converted_results["result3"] == "Mid range"
    assert converted_results["result4"] == "Other"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_mapping_patterns_with_rest():
    """Test mapping patterns with **rest to capture remaining items."""
    test_case_source = textwrap.dedent("""
    def process_config(config):
        match config:
            case {"name": name, "version": version, **extras}:
                return f"App {name} v{version} with extras: {extras}"
            case {"name": name, **rest}:
                return f"App {name} with config: {rest}"
            case _:
                return "Invalid config"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_config(config):
        if isinstance(config, collections.abc.Mapping) and "name" in config and "version" in config:
            name = config["name"]
            version = config["version"]
            extras = {k: v for (k, v) in config.items() if k not in {"name", "version"}}
            return f"App {name} v{version} with extras: {extras}"
        elif isinstance(config, collections.abc.Mapping) and "name" in config:
            name = config["name"]
            rest = {k: v for (k, v) in config.items() if k not in {"name"}}
            return f"App {name} with config: {rest}"
        else:
            return "Invalid config"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_config({"name": "myapp", "version": "1.0", "debug": True, "port": 8080})
    result2 = process_config({"name": "myapp", "author": "me"})
    result3 = process_config({"invalid": True})
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert (
        converted_results["result1"]
        == "App myapp v1.0 with extras: {'debug': True, 'port': 8080}"
    )
    assert converted_results["result2"] == "App myapp with config: {'author': 'me'}"
    assert converted_results["result3"] == "Invalid config"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_mixed_sequence_patterns():
    """Test sequence patterns mixing literals and variables."""
    test_case_source = textwrap.dedent("""
    def parse_command(cmd):
        match cmd:
            case ["git", "add", *files]:
                return f"Adding files: {files}"
            case ["git", "commit", "-m", message]:
                return f"Committing: {message}"
            case ["git", action, *args]:
                return f"Git {action} with args: {args}"
            case [program, *args] if len(args) > 0:
                return f"Running {program} with {len(args)} args"
            case [program]:
                return f"Running {program} with no args"
            case _:
                return "Not a command"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def parse_command(cmd):
        if isinstance(cmd, collections.abc.Sequence) and not isinstance(cmd, (str, collections.abc.Mapping)) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "add":
            files = list(cmd[2:])
            return f"Adding files: {files}"
        elif isinstance(cmd, collections.abc.Sequence) and not isinstance(cmd, (str, collections.abc.Mapping)) and len(cmd) == 4 and cmd[0] == "git" and cmd[1] == "commit" and cmd[2] == "-m":
            message = cmd[3]
            return f"Committing: {message}"
        elif isinstance(cmd, collections.abc.Sequence) and not isinstance(cmd, (str, collections.abc.Mapping)) and len(cmd) >= 2 and cmd[0] == "git":
            action = cmd[1]
            args = list(cmd[2:])
            return f"Git {action} with args: {args}"
        elif isinstance(cmd, collections.abc.Sequence) and not isinstance(cmd, (str, collections.abc.Mapping)) and len(cmd) >= 1 and len(cmd[1:]) > 0:
            program = cmd[0]
            args = list(cmd[1:])
            return f"Running {program} with {len(args)} args"
        elif isinstance(cmd, collections.abc.Sequence) and not isinstance(cmd, (str, collections.abc.Mapping)) and len(cmd) == 1:
            program = cmd[0]
            return f"Running {program} with no args"
        else:
            return "Not a command"
    """)

    test_calls = textwrap.dedent("""
    result1 = parse_command(["git", "add", "file1.py", "file2.py"])
    result2 = parse_command(["git", "commit", "-m", "Initial commit"])
    result3 = parse_command(["git", "status"])
    result4 = parse_command(["python", "script.py", "--verbose"])
    result5 = parse_command(["ls"])
    result6 = parse_command("not a list")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Adding files: ['file1.py', 'file2.py']"
    assert converted_results["result2"] == "Committing: Initial commit"
    assert converted_results["result3"] == "Git status with args: []"
    assert converted_results["result4"] == "Running python with 2 args"
    assert converted_results["result5"] == "Running ls with no args"
    assert converted_results["result6"] == "Not a command"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_nested_class_patterns():
    """Test complex nested class pattern matching."""
    test_case_source = textwrap.dedent("""
    class Container:
        def __init__(self, items):
            self.items = items

    def analyze_container(data):
        match data:
            case Container(items=[Point(x=0, y=y), *rest]):
                return f"Container starts with y-axis point {y}, has {len(rest)} more"
            case Container(items=[Point(x=x, y=0), Point(x=x2, y=y2)]):
                return f"Container with x-axis point ({x}, 0) and point ({x2}, {y2})"
            case Container(items=[]):
                return "Empty container"
            case Container(items=items):
                return f"Container with {len(items)} items"
            case _:
                return "Not a container"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    class Container:
        def __init__(self, items):
            self.items = items

    def analyze_container(data):
        if isinstance(data, Container) and isinstance(data.items, collections.abc.Sequence) and not isinstance(data.items, (str, collections.abc.Mapping)) and len(data.items) >= 1 and isinstance(data.items[0], Point) and data.items[0].x == 0:
            y = data.items[0].y
            rest = list(data.items[1:])
            return f"Container starts with y-axis point {y}, has {len(rest)} more"
        elif isinstance(data, Container) and isinstance(data.items, collections.abc.Sequence) and not isinstance(data.items, (str, collections.abc.Mapping)) and len(data.items) == 2 and isinstance(data.items[0], Point) and data.items[0].y == 0 and isinstance(data.items[1], Point):
            x = data.items[0].x
            x2 = data.items[1].x
            y2 = data.items[1].y
            return f"Container with x-axis point ({x}, 0) and point ({x2}, {y2})"
        elif isinstance(data, Container) and isinstance(data.items, collections.abc.Sequence) and not isinstance(data.items, (str, collections.abc.Mapping)) and len(data.items) == 0:
            return "Empty container"
        elif isinstance(data, Container):
            items = data.items
            return f"Container with {len(items)} items"
        else:
            return "Not a container"
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    test_calls = textwrap.dedent("""
    result1 = analyze_container(Container([Point(0, 5), Point(1, 1)]))
    result2 = analyze_container(Container([Point(3, 0), Point(2, 4)]))
    result3 = analyze_container(Container([]))
    result4 = analyze_container(Container([1, 2, 3]))
    result5 = analyze_container("not a container")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    assert (
        converted_results["result1"]
        == "Container starts with y-axis point 5, has 1 more"
    )
    assert (
        converted_results["result2"]
        == "Container with x-axis point (3, 0) and point (2, 4)"
    )
    assert converted_results["result3"] == "Empty container"
    assert converted_results["result4"] == "Container with 3 items"
    assert converted_results["result5"] == "Not a container"

    if sys.version_info >= (3, 10):
        if sys.version_info >= (3, 10):
            # STRING VALIDATION: Test exact code generation
            module = cst.parse_module(test_case_source)
            result = _converters.convert_match_statement(module)
            assert result.code == expected

            # EQUIVALENCE VALIDATION: Compare with original
            original_source_with_calls = test_case_source + test_calls
            original_results = execute_code_with_results(original_source_with_calls)
            assert original_results == converted_results


def test_or_patterns_with_as():
    """Test OR patterns combined with as patterns."""
    test_case_source = textwrap.dedent("""
    def process_number_or_string(value):
        match value:
            case (int() | float()) as number if number > 0:
                return f"Positive number: {number}"
            case (int() | float()) as number:
                return f"Non-positive number: {number}"
            case (str() | bytes()) as text:
                return f"Text data: {text}"
            case _ as other:
                return f"Other type: {type(other).__name__}"
    """)

    expected = textwrap.dedent("""
    def process_number_or_string(value):
        if isinstance(value, (int, float)) and value > 0:
            number = value
            return f"Positive number: {number}"
        elif isinstance(value, (int, float)):
            number = value
            return f"Non-positive number: {number}"
        elif isinstance(value, (str, bytes)):
            text = value
            return f"Text data: {text}"
        else:
            other = value
            return f"Other type: {type(other).__name__}"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_number_or_string(42)
    result2 = process_number_or_string(-5)
    result3 = process_number_or_string(3.14)
    result4 = process_number_or_string("hello")
    result5 = process_number_or_string([1, 2, 3])
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Positive number: 42"
    assert converted_results["result2"] == "Non-positive number: -5"
    assert converted_results["result3"] == "Positive number: 3.14"
    assert converted_results["result4"] == "Text data: hello"
    assert converted_results["result5"] == "Other type: list"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_or_pattern_literal_and_type():
    test_case_source = textwrap.dedent("""
    def process_number_or_string(value):
        match value:
            case (int() | 0 | bool()) as res:
                return f"Zero or integer or bool {res}"
    """)

    expected = textwrap.dedent("""
    def process_number_or_string(value):
        if isinstance(value, int) or value == 0 or isinstance(value, bool):
            res = value
            return f"Zero or integer or bool {res}"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_number_or_string(42)
    result2 = process_number_or_string(0.0)
    result3 = process_number_or_string(None)
    result4 = process_number_or_string(1.0)
    result5 = process_number_or_string(True)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Zero or integer or bool 42"
    assert converted_results["result2"] == "Zero or integer or bool 0.0"
    assert converted_results["result3"] is None
    assert converted_results["result4"] is None
    assert converted_results["result5"] == "Zero or integer or bool True"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_mixed_pattern_combinations():
    """Test complex combinations of different pattern types."""
    test_case_source = textwrap.dedent("""
    def complex_matcher(data):
        match data:
            case {"type": "point", "coords": (x, y) as coords} if x == y:
                return f"Diagonal point: {coords}"
            case {"type": "point", "coords": Point(x=x, y=y)} as point_data:
                return f"Point object: ({x}, {y}) from {point_data}"
            case {"items": [*items]} if all(isinstance(i, (int, float)) for i in items):
                return f"Numeric items: {sum(items)}"
            case {"nested": {"deep": value}} | {"alt": {"deep": value}}:
                return f"Deep value: {value}"
            case _:
                return "No match"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def complex_matcher(data):
        if isinstance(data, collections.abc.Mapping) and "type" in data and data["type"] == "point" and "coords" in data and isinstance(data["coords"], collections.abc.Sequence) and not isinstance(data["coords"], (str, collections.abc.Mapping)) and len(data["coords"]) == 2 and data["coords"][0] == data["coords"][1]:
            coords = data["coords"]
            x, y = data["coords"]
            return f"Diagonal point: {coords}"
        elif isinstance(data, collections.abc.Mapping) and "type" in data and data["type"] == "point" and "coords" in data and isinstance(data["coords"], Point):
            point_data = data
            x = data["coords"].x
            y = data["coords"].y
            return f"Point object: ({x}, {y}) from {point_data}"
        elif isinstance(data, collections.abc.Mapping) and "items" in data and isinstance(data["items"], collections.abc.Sequence) and not isinstance(data["items"], (str, collections.abc.Mapping)) and len(data["items"]) >= 0 and all(isinstance(i, (int, float)) for i in data["items"][0:]):
            items = list(data["items"][0:])
            return f"Numeric items: {sum(items)}"
        elif isinstance(data, collections.abc.Mapping) and "nested" in data and isinstance(data["nested"], collections.abc.Mapping) and "deep" in data["nested"]:
            value = data["nested"]["deep"]
            return f"Deep value: {value}"
        elif isinstance(data, collections.abc.Mapping) and "alt" in data and isinstance(data["alt"], collections.abc.Mapping) and "deep" in data["alt"]:
            value = data["alt"]["deep"]
            return f"Deep value: {value}"
        else:
            return "No match"
    """)

    test_calls = textwrap.dedent("""
    result1 = complex_matcher({"type": "point", "coords": (3, 3)})
    result2 = complex_matcher({"type": "point", "coords": Point(2, 4)})
    result3 = complex_matcher({"items": [1, 2, 3, 4, 5]})
    result4 = complex_matcher({"nested": {"deep": "treasure"}})
    result5 = complex_matcher({"alt": {"deep": "treasure"}})
    result6 = complex_matcher({"nothing": "matches"})
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Diagonal point: (3, 3)"
    assert "Point object: (2, 4)" in converted_results["result2"]
    assert converted_results["result3"] == "Numeric items: 15"
    assert converted_results["result4"] == "Deep value: treasure"
    assert converted_results["result5"] == "Deep value: treasure"
    assert converted_results["result6"] == "No match"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_literal_matching_multiple_types():
    """Test literal patterns with different types including None, True, False."""
    test_case_source = textwrap.dedent("""
    def check_literal(value):
        match value:
            case True:
                return "Boolean True"
            case False:
                return "Boolean False"
            case None:
                return "None value"
            case 0:
                return "Zero integer"
            case 0.0:
                return "Zero float"
            case "":
                return "Empty string"
            case []:
                return "Empty list"
            case {}:
                return "Empty dict"
            case _:
                return f"Other: {value}"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def check_literal(value):
        if value is True:
            return "Boolean True"
        elif value is False:
            return "Boolean False"
        elif value is None:
            return "None value"
        elif value == 0:
            return "Zero integer"
        elif value == 0.0:
            return "Zero float"
        elif value == "":
            return "Empty string"
        elif isinstance(value, collections.abc.Sequence) and not isinstance(value, (str, collections.abc.Mapping)) and len(value) == 0:
            return "Empty list"
        elif isinstance(value, collections.abc.Mapping) and len(value) == 0:
            return "Empty dict"
        else:
            return f"Other: {value}"
    """)

    test_calls = textwrap.dedent("""
    result1 = check_literal(True)
    result2 = check_literal(False)
    result3 = check_literal(None)
    result4 = check_literal(0)
    result5 = check_literal(0.0)
    result6 = check_literal("")
    result7 = check_literal([])
    result8 = check_literal({})
    result9 = check_literal(42)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Boolean True"
    assert converted_results["result2"] == "Boolean False"
    assert converted_results["result3"] == "None value"
    assert converted_results["result4"] == "Zero integer"
    assert (
        converted_results["result5"] == "Zero integer"
    )  # 0.0 matches case 0: due to equality
    assert converted_results["result6"] == "Empty string"
    assert converted_results["result7"] == "Empty list"
    assert converted_results["result8"] == "Empty dict"
    assert converted_results["result9"] == "Other: 42"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_positional_class_patterns():
    """Test positional class pattern matching using __match_args__."""
    test_case_source = textwrap.dedent("""
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def describe_point_positional(point):
        match point:
            case Point(0, 0):
                return "Origin"
            case Point(0, y):
                return f"Y-axis: {y}"
            case Point(x, 0):
                return f"X-axis: {x}"
            case Point(x, y):
                return f"Point: {x}, {y}"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def describe_point_positional(point):
        if isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[0]) == 0 and getattr(point, Point.__match_args__[1]) == 0:
            return "Origin"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[0]) == 0:
            y = getattr(point, Point.__match_args__[1])
            return f"Y-axis: {y}"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[1]) == 0:
            x = getattr(point, Point.__match_args__[0])
            return f"X-axis: {x}"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point):
            x = getattr(point, Point.__match_args__[0])
            y = getattr(point, Point.__match_args__[1])
            return f"Point: {x}, {y}"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    result1 = describe_point_positional(Point(0, 0))
    result2 = describe_point_positional(Point(0, 5))
    result3 = describe_point_positional(Point(3, 0))
    result4 = describe_point_positional(Point(2, 4))
    result5 = describe_point_positional("not a point")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Origin"
    assert converted_results["result2"] == "Y-axis: 5"
    assert converted_results["result3"] == "X-axis: 3"
    assert converted_results["result4"] == "Point: 2, 4"
    assert converted_results["result5"] == "Not a point"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_mixed_positional_keyword_patterns():
    """Test mixing positional and keyword arguments in class patterns."""
    test_case_source = textwrap.dedent("""
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def analyze_point_mixed(point):
        match point:
            case Point(0, y=y):  # Mix: first positional, second keyword
                return f"Y-axis (mixed): {y}"
            case Point(x, y=0):  # Mix: first positional, second keyword
                return f"X-axis (mixed): {x}"
            case Point(x=x, y=y):  # All keywords
                return f"Point (keywords): {x}, {y}"
            case Point(x, y):  # All positional
                return f"Point (positional): {x}, {y}"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def analyze_point_mixed(point):
        if isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 1):
            raise TypeError("Point() accepts 0 positional sub-patterns (1 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[0]) == 0:  # Mix: first positional, second keyword
            y = point.y
            return f"Y-axis (mixed): {y}"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 1):
            raise TypeError("Point() accepts 0 positional sub-patterns (1 given)")
        elif isinstance(point, Point) and point.y == 0:  # Mix: first positional, second keyword
            x = getattr(point, Point.__match_args__[0])
            return f"X-axis (mixed): {x}"
        elif isinstance(point, Point):  # All keywords
            x = point.x
            y = point.y
            return f"Point (keywords): {x}, {y}"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point):  # All positional
            x = getattr(point, Point.__match_args__[0])
            y = getattr(point, Point.__match_args__[1])
            return f"Point (positional): {x}, {y}"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    result1 = analyze_point_mixed(Point(0, 5))
    result2 = analyze_point_mixed(Point(3, 0))
    result3 = analyze_point_mixed(Point(2, 4))
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Y-axis (mixed): 5"
    assert converted_results["result2"] == "X-axis (mixed): 3"
    assert converted_results["result3"] == "Point (keywords): 2, 4"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_enum_patterns():
    """Test matching enum values as patterns."""
    test_case_source = textwrap.dedent("""
    from enum import Enum

    class Color(Enum):
        RED = 0
        GREEN = 1
        BLUE = 2

    def describe_color(color):
        match color:
            case Color.RED:
                return "I see red!"
            case Color.GREEN:
                return "Grass is green"
            case Color.BLUE:
                return "I'm feeling blue"
            case _:
                return "Unknown color"
    """)

    expected = textwrap.dedent("""
    from enum import Enum

    class Color(Enum):
        RED = 0
        GREEN = 1
        BLUE = 2

    def describe_color(color):
        if color == Color.RED:
            return "I see red!"
        elif color == Color.GREEN:
            return "Grass is green"
        elif color == Color.BLUE:
            return "I'm feeling blue"
        else:
            return "Unknown color"
    """)

    test_calls = textwrap.dedent("""
    result1 = describe_color(Color.RED)
    result2 = describe_color(Color.GREEN)
    result3 = describe_color(Color.BLUE)
    result4 = describe_color("red")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "I see red!"
    assert converted_results["result2"] == "Grass is green"
    assert converted_results["result3"] == "I'm feeling blue"
    assert converted_results["result4"] == "Unknown color"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_dataclass_patterns():
    """Test pattern matching with dataclasses."""
    test_case_source = textwrap.dedent("""
    def where_is(point):
        match point:
            case Point(x=0, y=0):
                return "Origin"
            case Point(x=0, y=y):
                return f"Y={y}"
            case Point(x=x, y=0):
                return f"X={x}"
            case Point():
                return "Somewhere else"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    def where_is(point):
        if isinstance(point, Point) and point.x == 0 and point.y == 0:
            return "Origin"
        elif isinstance(point, Point) and point.x == 0:
            y = point.y
            return f"Y={y}"
        elif isinstance(point, Point) and point.y == 0:
            x = point.x
            return f"X={x}"
        elif isinstance(point, Point):
            return "Somewhere else"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    result1 = where_is(Point(0, 0))
    result2 = where_is(Point(0, 5))
    result3 = where_is(Point(3, 0))
    result4 = where_is(Point(2, 4))
    result5 = where_is("not a point")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Origin"
    assert converted_results["result2"] == "Y=5"
    assert converted_results["result3"] == "X=3"
    assert converted_results["result4"] == "Somewhere else"
    assert converted_results["result5"] == "Not a point"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_empty_class_patterns():
    """Test class patterns without any attribute constraints."""
    test_case_source = textwrap.dedent("""
    def classify_object(obj):
        match obj:
            case Point():  # Matches any Point instance
                return "It's a Point"
            case list():   # Matches any list
                return "It's a list"
            case dict():   # Matches any dict
                return "It's a dict"
            case str():    # Matches any string
                return "It's a string"
            case _:
                return "Something else"
    """)

    expected = textwrap.dedent("""
    def classify_object(obj):
        if isinstance(obj, Point):  # Matches any Point instance
            return "It's a Point"
        elif isinstance(obj, list):   # Matches any list
            return "It's a list"
        elif isinstance(obj, dict):   # Matches any dict
            return "It's a dict"
        elif isinstance(obj, str):    # Matches any string
            return "It's a string"
        else:
            return "Something else"
    """)

    test_calls = textwrap.dedent("""
    result1 = classify_object(Point(1, 2))
    result2 = classify_object([1, 2, 3])
    result3 = classify_object({"key": "value"})
    result4 = classify_object("hello")
    result5 = classify_object(42)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    assert converted_results["result1"] == "It's a Point"
    assert converted_results["result2"] == "It's a list"
    assert converted_results["result3"] == "It's a dict"
    assert converted_results["result4"] == "It's a string"
    assert converted_results["result5"] == "Something else"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_nested_sequence_point_patterns():
    """Test complex nested patterns with sequences and points from PEP 636."""
    test_case_source = textwrap.dedent("""
    from dataclasses import dataclass

    @dataclass
    class DCPoint:
        x: float
        y: float

    def analyze_points(points):
        match points:
            case []:
                return "No points"
            case [DCPoint(0, 0)]:
                return "The origin"
            case [DCPoint(x, y)]:
                return f"Single point {x}, {y}"
            case [DCPoint(0, y1), DCPoint(0, y2)]:
                return f"Two on the Y axis at {y1}, {y2}"
            case [DCPoint(x1, y1), DCPoint(x2, y2)] if x1 == x2:
                return f"Two points on vertical line x={x1}: ({x1}, {y1}), ({x2}, {y2})"
            case [DCPoint(x1, y1), DCPoint(x2, y2)]:
                return f"Two points: ({x1}, {y1}), ({x2}, {y2})"
            case _:
                return "Complex or invalid points"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    from dataclasses import dataclass

    @dataclass
    class DCPoint:
        x: float
        y: float
        __match_args__ = ('x', 'y')

    def analyze_points(points):
        if isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 0:
            return "No points"
        elif isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 1 and isinstance(points[0], DCPoint) and getattr(points[0], DCPoint.__match_args__[0]) == 0 and getattr(points[0], DCPoint.__match_args__[1]) == 0:
            return "The origin"
        elif isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 1 and isinstance(points[0], DCPoint):
            x = getattr(points[0], DCPoint.__match_args__[0])
            y = getattr(points[0], DCPoint.__match_args__[1])
            return f"Single point {x}, {y}"
        elif isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 2 and isinstance(points[0], DCPoint) and getattr(points[0], DCPoint.__match_args__[0]) == 0 and isinstance(points[1], DCPoint) and getattr(points[1], DCPoint.__match_args__[0]) == 0:
            y1 = getattr(points[0], DCPoint.__match_args__[1])
            y2 = getattr(points[1], DCPoint.__match_args__[1])
            return f"Two on the Y axis at {y1}, {y2}"
        elif isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 2 and isinstance(points[0], DCPoint) and isinstance(points[1], DCPoint) and getattr(points[0], DCPoint.__match_args__[0]) == getattr(points[1], DCPoint.__match_args__[0]):
            x1 = getattr(points[0], DCPoint.__match_args__[0])
            y1 = getattr(points[0], DCPoint.__match_args__[1])
            x2 = getattr(points[1], DCPoint.__match_args__[0])
            y2 = getattr(points[1], DCPoint.__match_args__[1])
            return f"Two points on vertical line x={x1}: ({x1}, {y1}), ({x2}, {y2})"
        elif isinstance(points, collections.abc.Sequence) and not isinstance(points, (str, collections.abc.Mapping)) and len(points) == 2 and isinstance(points[0], DCPoint) and isinstance(points[1], DCPoint):
            x1 = getattr(points[0], DCPoint.__match_args__[0])
            y1 = getattr(points[0], DCPoint.__match_args__[1])
            x2 = getattr(points[1], DCPoint.__match_args__[0])
            y2 = getattr(points[1], DCPoint.__match_args__[1])
            return f"Two points: ({x1}, {y1}), ({x2}, {y2})"
        else:
            return "Complex or invalid points"
    """)

    test_calls = textwrap.dedent("""
    result1 = analyze_points([])
    result2 = analyze_points([DCPoint(0, 0)])
    result3 = analyze_points([DCPoint(3, 4)])
    result4 = analyze_points([DCPoint(0, 2), DCPoint(0, 5)])
    result5 = analyze_points([DCPoint(3, 2), DCPoint(3, 8)])
    result6 = analyze_points([DCPoint(1, 2), DCPoint(4, 5)])
    result7 = analyze_points([DCPoint(1, 1), DCPoint(2, 2), DCPoint(3, 3)])
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    assert converted_results["result1"] == "No points"
    assert converted_results["result2"] == "The origin"
    assert converted_results["result3"] == "Single point 3, 4"
    assert converted_results["result4"] == "Two on the Y axis at 2, 5"
    assert (
        converted_results["result5"]
        == "Two points on vertical line x=3: (3, 2), (3, 8)"
    )
    assert converted_results["result6"] == "Two points: (1, 2), (4, 5)"
    assert converted_results["result7"] == "Complex or invalid points"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        # Note: We also need to convert dataclasses for this one to work in Python 3.9
        result = _converters.convert_dataclass(result)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert converted_results == original_results


def test_guard_diagonal_patterns():
    """Test guard conditions for diagonal point checking from PEP 636."""
    test_case_source = textwrap.dedent("""
    class Point:
        __match_args__ = ("y_attr", "x_attr")
        def __init__(self, x, y):
            self.x_attr = x
            self.y_attr = y

    def check_diagonal(point):
        match point:
            case Point(y, x) if x > y:
                return f"Below the y=x curve ({x}, {y})"
            case Point(y, x) if x == y:
                return f"Y=X at {x}"
            case Point(y, x):
                return f"Not on the diagonal: ({x}, {y})"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    class Point:
        __match_args__ = ("y_attr", "x_attr")
        def __init__(self, x, y):
            self.x_attr = x
            self.y_attr = y

    def check_diagonal(point):
        if isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[1]) > getattr(point, Point.__match_args__[0]):
            y = getattr(point, Point.__match_args__[0])
            x = getattr(point, Point.__match_args__[1])
            return f"Below the y=x curve ({x}, {y})"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[1]) == getattr(point, Point.__match_args__[0]):
            y = getattr(point, Point.__match_args__[0])
            x = getattr(point, Point.__match_args__[1])
            return f"Y=X at {x}"
        elif isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point):
            y = getattr(point, Point.__match_args__[0])
            x = getattr(point, Point.__match_args__[1])
            return f"Not on the diagonal: ({x}, {y})"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    result1 = check_diagonal(Point(3, 3))
    result2 = check_diagonal(Point(2, 5))
    result3 = check_diagonal("not a point")
    result4 = check_diagonal(Point(4, 3))
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)  # noqa: F841

    assert converted_results["result1"] == "Y=X at 3"
    assert converted_results["result2"] == "Not on the diagonal: (2, 5)"
    assert converted_results["result3"] == "Not a point"
    assert converted_results["result4"] == "Below the y=x curve (4, 3)"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert converted_results == original_results


def test_point_no_match_args():
    test_case_source = textwrap.dedent("""
    def check_diagonal(point):
        match point:
            case Point(x, y) if x == y:
                return f"Y=X at {x}"
            case _:
                return "Not a point"
    """)

    expected = textwrap.dedent("""
    def check_diagonal(point):
        if isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[0]) == getattr(point, Point.__match_args__[1]):
            x = getattr(point, Point.__match_args__[0])
            y = getattr(point, Point.__match_args__[1])
            return f"Y=X at {x}"
        else:
            return "Not a point"
    """)

    test_calls = textwrap.dedent("""
    check_diagonal(Point(3, 3))
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    error_msg = "Point\\(\\) accepts 0 positional sub-patterns"
    with pytest.raises(TypeError, match=error_msg):
        execute_code_with_results(converted_source_with_calls)  # noqa: F841

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        with pytest.raises(TypeError, match=error_msg):
            execute_code_with_results(original_source_with_calls)


def test_tuple_unpacking_no_parens():
    """Test tuple pattern matching without parentheses as mentioned in PEP 636."""
    test_case_source = textwrap.dedent("""
    def process_tuple_variants(data):
        match data:
            case action, obj:  # Equivalent to (action, obj)
                return f"Action: {action}, Object: {obj}"
            case single_item,:  # Single item tuple
                return f"Single: {single_item}"
            case first, *rest:  # First item and rest
                return f"First: {first}, Rest: {rest}"
            case _:
                return "No match"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_tuple_variants(data):
        if isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 2:  # Equivalent to (action, obj)
            action, obj = data
            return f"Action: {action}, Object: {obj}"
        elif isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 1:  # Single item tuple
            single_item = data[0]
            return f"Single: {single_item}"
        elif isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) >= 1:  # First item and rest
            first = data[0]
            rest = list(data[1:])
            return f"First: {first}, Rest: {rest}"
        else:
            return "No match"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_tuple_variants(("go", "north"))
    result2 = process_tuple_variants(("quit",))
    result3 = process_tuple_variants(("drop", "sword", "shield", "potion"))
    result4 = process_tuple_variants("string")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Action: go, Object: north"
    assert converted_results["result2"] == "Single: quit"
    assert (
        converted_results["result3"]
        == "First: drop, Rest: ['sword', 'shield', 'potion']"
    )
    assert converted_results["result4"] == "No match"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_builtin_type_patterns():
    """Test pattern matching against built-in types like int(), str(), list()."""
    test_case_source = textwrap.dedent("""
    def classify_builtin_type(value):
        match value:
            case int() if value > 0:
                return f"Positive integer: {value}"
            case int():
                return f"Non-positive integer: {value}"
            case str() if len(value) > 5:
                return f"Long string: {value}"
            case str():
                return f"Short string: {value}"
            case list() if len(value) == 0:
                return "Empty list"
            case list():
                return f"List with {len(value)} items"
            case dict():
                return f"Dictionary with {len(value)} keys"
            case _:
                return f"Other type: {type(value).__name__}"
    """)

    expected = textwrap.dedent("""
    def classify_builtin_type(value):
        if isinstance(value, int) and value > 0:
            return f"Positive integer: {value}"
        elif isinstance(value, int):
            return f"Non-positive integer: {value}"
        elif isinstance(value, str) and len(value) > 5:
            return f"Long string: {value}"
        elif isinstance(value, str):
            return f"Short string: {value}"
        elif isinstance(value, list) and len(value) == 0:
            return "Empty list"
        elif isinstance(value, list):
            return f"List with {len(value)} items"
        elif isinstance(value, dict):
            return f"Dictionary with {len(value)} keys"
        else:
            return f"Other type: {type(value).__name__}"
    """)

    test_calls = textwrap.dedent("""
    result1 = classify_builtin_type(42)
    result2 = classify_builtin_type(-5)
    result3 = classify_builtin_type("hello world")
    result4 = classify_builtin_type("hi")
    result5 = classify_builtin_type([])
    result6 = classify_builtin_type([1, 2, 3])
    result7 = classify_builtin_type({"a": 1, "b": 2})
    result8 = classify_builtin_type(3.14)
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Positive integer: 42"
    assert converted_results["result2"] == "Non-positive integer: -5"
    assert converted_results["result3"] == "Long string: hello world"
    assert converted_results["result4"] == "Short string: hi"
    assert converted_results["result5"] == "Empty list"
    assert converted_results["result6"] == "List with 3 items"
    assert converted_results["result7"] == "Dictionary with 2 keys"
    assert converted_results["result8"] == "Other type: float"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_deeply_nested_as_patterns():
    """Test as patterns nested within other as patterns."""
    test_case_source = textwrap.dedent("""
    def process_nested_data(data):
        match data:
            case ([x, y] as coords) as data_wrapper:
                return f"Coords {coords} in wrapper {data_wrapper}"
            case {"outer": {"inner": value} as inner_dict} as outer_dict:
                return f"Inner dict {inner_dict} in outer {outer_dict}"
            case ({"name": name} as record) as container:
                return f"Record {record} in container {container}"
            case _:
                return "No match"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_nested_data(data):
        if isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 2:
            data_wrapper = data
            coords = data
            x, y = data
            return f"Coords {coords} in wrapper {data_wrapper}"
        elif isinstance(data, collections.abc.Mapping) and "outer" in data and isinstance(data["outer"], collections.abc.Mapping) and "inner" in data["outer"]:
            outer_dict = data
            inner_dict = data["outer"]
            value = data["outer"]["inner"]
            return f"Inner dict {inner_dict} in outer {outer_dict}"
        elif isinstance(data, collections.abc.Mapping) and "name" in data:
            container = data
            record = data
            name = data["name"]
            return f"Record {record} in container {container}"
        else:
            return "No match"
    """)

    test_calls = textwrap.dedent("""
    result1 = process_nested_data([3, 4])
    result2 = process_nested_data({"outer": {"inner": "treasure"}})
    result3 = process_nested_data({"name": "Alice"})
    result4 = process_nested_data("no match")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert converted_results["result1"] == "Coords [3, 4] in wrapper [3, 4]"
    assert "Inner dict {'inner': 'treasure'} in outer" in converted_results["result2"]
    assert "Record {'name': 'Alice'} in container" in converted_results["result3"]
    assert converted_results["result4"] == "No match"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_multiple_as_patterns_different_levels():
    """Test multiple as patterns at different nesting levels."""
    test_case_source = textwrap.dedent("""
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y
        def __repr__(self):
            return f"Point({self.x}, {self.y})"

    def analyze_complex_structure(data):
        match data:
            case {"items": [item as first, *rest] as item_list} as full_data:
                return f"First: {first}, List: {item_list}, Full: {full_data}"
            case Point(x as x_coord, y as y_coord) as point:
                return f"Point({x_coord}, {y_coord}) = {point}"
            case {"metadata": {"id": id_val as identifier} as meta} as document:
                return f"ID: {identifier}, Meta: {meta}, Doc: {document}"
            case _:
                return "No match"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    class Point:
        __match_args__ = ("x", "y")
        def __init__(self, x, y):
            self.x = x
            self.y = y
        def __repr__(self):
            return f"Point({self.x}, {self.y})"

    def analyze_complex_structure(data):
        if isinstance(data, collections.abc.Mapping) and "items" in data and isinstance(data["items"], collections.abc.Sequence) and not isinstance(data["items"], (str, collections.abc.Mapping)) and len(data["items"]) >= 1:
            full_data = data
            item_list = data["items"]
            first = data["items"][0]
            item = data["items"][0]
            rest = list(data["items"][1:])
            return f"First: {first}, List: {item_list}, Full: {full_data}"
        elif isinstance(data, Point):
            point = data
            x_coord = getattr(data, Point.__match_args__[0])
            x = getattr(data, Point.__match_args__[0])
            y_coord = getattr(data, Point.__match_args__[1])
            y = getattr(data, Point.__match_args__[1])
            return f"Point({x_coord}, {y_coord}) = {point}"
        elif isinstance(data, collections.abc.Mapping) and "metadata" in data and isinstance(data["metadata"], collections.abc.Mapping) and "id" in data["metadata"]:
            document = data
            meta = data["metadata"]
            identifier = data["metadata"]["id"]
            id_val = data["metadata"]["id"]
            return f"ID: {identifier}, Meta: {meta}, Doc: {document}"
        else:
            return "No match"
    """)

    test_calls = textwrap.dedent("""
    result1 = analyze_complex_structure({"items": [1, 2, 3]})
    result2 = analyze_complex_structure(Point(5, 10))
    result3 = analyze_complex_structure({"metadata": {"id": "doc123"}})
    result4 = analyze_complex_structure("no match")
    """)

    # EXECUTION VALIDATION: Test converted code behavior (all Python versions)
    converted_source_with_calls = expected + test_calls
    converted_results = execute_code_with_results(converted_source_with_calls)

    # Verify converted code produces expected results on all Python versions
    assert (
        "First: 1, List: [1, 2, 3], Full: {'items': [1, 2, 3]}"
        in converted_results["result1"]
    )
    assert "Point(5, 10)" in converted_results["result2"]
    assert "ID: doc123, Meta: {'id': 'doc123'}" in converted_results["result3"]
    assert converted_results["result4"] == "No match"

    if sys.version_info >= (3, 10):
        # STRING VALIDATION: Test exact code generation
        module = cst.parse_module(test_case_source)
        result = _converters.convert_match_statement(module)
        assert result.code == expected

        # EQUIVALENCE VALIDATION: Compare with original
        original_source_with_calls = test_case_source + test_calls
        original_results = execute_code_with_results(original_source_with_calls)
        assert original_results == converted_results


def test_as_patterns_with_star_expressions_invalid_syntax():
    """Test that invalid syntax with as patterns and star expressions produces the same error."""
    test_case_source = textwrap.dedent("""
    def process_with_star_as(data):
        match data:
            case [first, *middle as mid_items, last]:
                return f"First: {first}, Middle: {mid_items}, Last: {last}"
            case [*prefix as pre_items, final] as full_list:
                return f"Prefix: {pre_items}, Final: {final}, Full: {full_list}"
            case {"keys": [*values as all_vals]} as data_dict:
                return f"Values: {all_vals}, Dict: {data_dict}"
            case _:
                return "No match"
    """)

    # Test that both original and converted code produce syntax errors
    # since "*middle as mid_items" is invalid Python syntax

    # Test that CST parsing fails with a syntax error
    with pytest.raises(Exception) as exc_info:
        cst.parse_module(test_case_source)

    # Verify it's a syntax error (could be ParserSyntaxError or SyntaxError depending on version)
    assert (
        "Syntax" in str(type(exc_info.value).__name__)
        or "syntax" in str(exc_info.value).lower()
    )

    if sys.version_info >= (3, 10):
        # Test that exec also fails with a syntax error for the original code
        with pytest.raises(SyntaxError):
            exec(test_case_source)


def test_complex_as_pattern_combinations_invalid_syntax():
    """Test that complex invalid as pattern combinations produce the same error."""
    test_case_source = textwrap.dedent("""
    def handle_complex_as_patterns(data):
        match data:
            case {"response": {"data": [{"value": val} as item] as items} as response} as full:
                return f"Value: {val}, Item: {item}, Items: {items}, Response: {response}, Full: {full}"
            case ({"x": x_val} | {"y": y_val}) as coord_dict as wrapper:
                x = x_val if "x" in coord_dict else None
                y = y_val if "y" in coord_dict else None
                return f"Coord dict: {coord_dict}, Wrapper: {wrapper}, X: {x}, Y: {y}"
            case [(*group as elements,) as tuple_group] as list_wrapper:
                return f"Elements: {elements}, Tuple: {tuple_group}, List: {list_wrapper}"
            case _:
                return "No match"
    """)

    # Test that both original and converted code produce syntax errors
    # since patterns like "as coord_dict as wrapper" and "*group as elements" are invalid Python syntax

    # Test that CST parsing fails with a syntax error
    with pytest.raises(Exception) as exc_info:
        cst.parse_module(test_case_source)

    # Verify it's a syntax error (could be ParserSyntaxError or SyntaxError depending on version)
    assert (
        "Syntax" in str(type(exc_info.value).__name__)
        or "syntax" in str(exc_info.value).lower()
    )

    if sys.version_info >= (3, 10):
        # Test that exec also fails with a syntax error for the original code
        with pytest.raises(SyntaxError):
            exec(test_case_source)
