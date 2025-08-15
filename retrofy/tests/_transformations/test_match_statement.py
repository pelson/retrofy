from __future__ import annotations

from dataclasses import dataclass
import re
import sys
import textwrap
import typing

import libcst
import libcst as cst
import pytest

from retrofy import _converters

if typing.TYPE_CHECKING:
    from _pytest.mark import MarkDecorator, ParameterSet


@dataclass
class MatchTestCase:
    name: str
    description: str = ""
    source: str = ""  # Original match statement code
    expected: str = ""  # Converted match statement code
    syntax_error: str | None = (
        None  # Set to a matching string if a syntax error will be raised for the given source.
    )
    test_calls: tuple[str, str] | ParameterSet = ()  # Runtime validation cases.
    failing_calls: tuple[str, str] | ParameterSet = ()  # Runtime validation cases.
    conversion_markers: typing.Collection[MarkDecorator] = ()
    conversions: typing.Sequence[typing.Callable[[cst.Module], cst.Module]] = (
        _converters.convert_match_statement,
    )


match_statement_cases = [
    MatchTestCase(
        name="literal_matching_simple",
        description="Test literal patterns with different types including None, True, False",
        source=textwrap.dedent("""
        def check_literal(value):
            match value:
                case True:
                    return "Boolean True"
                case False:
                    return "Boolean False"
                case None:
                    return "None value"
                case 42:
                    return "The answer"
                case "hello":
                    return "Greeting"
                case _:
                    return "Something else"
        """),
        expected=textwrap.dedent("""
        def check_literal(value):
            if value is True:
                return "Boolean True"
            elif value is False:
                return "Boolean False"
            elif value is None:
                return "None value"
            elif value == 42:
                return "The answer"
            elif value == "hello":
                return "Greeting"
            else:
                return "Something else"
        """),
        # syntax_error='invalid syntax',
        test_calls=[
            ("check_literal(True)", "Boolean True"),
            ("check_literal(False)", "Boolean False"),
            ("check_literal(None)", "None value"),
            ("check_literal(42)", "The answer"),
            ("check_literal('hello')", "Greeting"),
            ("check_literal('world')", "Something else"),
        ],
    ),
    MatchTestCase(
        name="test_literal_matching_simple",
        description="Test basic literal matching with numbers and strings.",
        source=textwrap.dedent("""
    def http_error(status):
        match status:
            case 400:
                return "Bad request"
            case 404:
                return "Not found"
            case _:
                return "Something's wrong"
    """),
        expected=textwrap.dedent("""
    def http_error(status):
        if status == 400:
            return "Bad request"
        elif status == 404:
            return "Not found"
        else:
            return "Something's wrong"
    """),
        test_calls=[
            ("http_error(400)", "Bad request"),
            ("http_error(404)", "Not found"),
            ("http_error(500)", "Something's wrong"),
        ],
    ),
    MatchTestCase(
        name="test_sequence_matching_tuple",
        description="Test sequence matching with tuples.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_point((0, 0))", "origin"),
            ("process_point((0, 5))", "y-axis: 5"),
            ("process_point((3, 0))", "x-axis: 3"),
            ("process_point((2, 4))", "point: 2, 4"),
            ("process_point([1, 2])", "point: 1, 2"),
            ("process_point({'x': 1, 'y': 2})", None),
        ],
    ),
    MatchTestCase(
        name="test_guard_clauses",
        description="Test guard clauses with if conditions.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("categorize_number(150)", "large: 150"),
            ("categorize_number(50)", "medium: 50"),
            ("categorize_number(5)", "small: 5"),
            ("categorize_number(-10)", "non-positive: -10"),
        ],
    ),
    MatchTestCase(
        name="test_or_patterns_simple",
        description="Test OR patterns with consistent variable bindings.",
        source=textwrap.dedent("""
    def classify_value(value):
        match value:
            case 1 | 2 | 3:
                return "Small number"
            case "a" | "b":
                return "Letter"
            case _:
                return "Other"
    """),
        expected=textwrap.dedent("""
    def classify_value(value):
        if value in (1, 2, 3):
            return "Small number"
        elif value in ("a", "b"):
            return "Letter"
        else:
            return "Other"
    """),
        test_calls=[
            ("classify_value(2)", "Small number"),
            ("classify_value('a')", "Letter"),
            ("classify_value(42)", "Other"),
        ],
    ),
    MatchTestCase(
        name="test_or_patterns_with_variables",
        description="Test OR patterns with variable bindings - expanded to separate cases.",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        expected=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        test_calls=[
            ("axis_point(Point(5, 0))", "On axis at 5"),
            ("axis_point(Point(0, 3))", "On axis at 3"),
            ("axis_point(Point(2, 4))", "Not on axis"),
            ("axis_point(XAxisMarker(5))", "On x-axis at x=5"),
            ("axis_point(Point(6, -1))", "On x-axis at x=6"),
        ],
    ),
    MatchTestCase(
        name="test_class_pattern_matching",
        description="Test class pattern matching with attributes.",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        expected=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        test_calls=[
            ("describe_point(Point(0, 0))", "Origin"),
            ("describe_point(Point(0, 5))", "Y-axis: 5"),
            ("describe_point(Point(3, 0))", "X-axis: 3"),
            ("describe_point(Point(2, 4))", "Point: 2, 4"),
            ("describe_point('not a point')", "Not a point"),
        ],
    ),
    MatchTestCase(
        name="test_mapping_patterns",
        description="Test dictionary/mapping patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("handle_request({'action': 'get', 'resource': 'users'})", "Getting users"),
            (
                "handle_request({'action': 'post', 'resource': 'posts', 'data': {'title': 'Hello'}})",
                "Posting to posts: {'title': 'Hello'}",
            ),
            ("handle_request({'action': 'delete'})", "Unknown action: delete"),
            ("handle_request({'invalid': 'request'})", "Invalid request"),
            ("handle_request('not a dict')", "Invalid request"),
        ],
    ),
    MatchTestCase(
        name="test_star_patterns",
        description="Test star patterns with slicing logic.",
        source=textwrap.dedent("""
    def process_sequence(sequence):
        match sequence:
            case [first, *rest]:
                return f"First: {first}, Rest: {rest}"
            case [*prefix, last]:
                return f"Prefix: {prefix}, Last: {last}"
            case _:
                return "No match"
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_sequence([1, 2, 3, 4])", "First: 1, Rest: [2, 3, 4]"),
            ("process_sequence([42])", "First: 42, Rest: []"),
            ("process_sequence([])", "No match"),
            ("process_sequence('string')", "No match"),
        ],
    ),
    MatchTestCase(
        name="test_nested_patterns",
        description="Test nested pattern destructuring.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            (
                "analyze_data({'users': [{'name': 'Alice', 'active': True}]})",
                "Active user: Alice",
            ),
            ("analyze_data({'users': []})", "No users"),
            (
                "analyze_data({'users': [{'name': 'Bob'}, {'name': 'Carol'}]})",
                "Users count: 2",
            ),
            ("analyze_data({'items': []})", "Invalid data"),
        ],
    ),
    MatchTestCase(
        name="test_value_patterns_constants",
        description="Test value patterns using dotted names for constants.",
        source=textwrap.dedent("""
    import math

    def classify_angle(angle):
        match angle:
            case math.pi:
                return "π radians"
            case 0:
                return "Zero"
            case _:
                return "Other angle"
    """),
        expected=textwrap.dedent("""
    import math

    def classify_angle(angle):
        if angle == math.pi:
            return "π radians"
        elif angle == 0:
            return "Zero"
        else:
            return "Other angle"
    """),
        test_calls=[
            ("classify_angle(math.pi)", "π radians"),
            ("classify_angle(0)", "Zero"),
            ("classify_angle(1.5)", "Other angle"),
        ],
    ),
    MatchTestCase(
        name="test_as_patterns_simple",
        description="Test basic as patterns for capturing matched values.",
        source=textwrap.dedent("""
    def process_value(value):
        match value:
            case (1 | 2 | 3) as num:
                return f"Small number: {num}"
            case ("hello" | "hi") as greeting:
                return f"Greeting: {greeting}"
            case _ as anything:
                return f"Other: {anything}"
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_value(2)", "Small number: 2"),
            ("process_value('hello')", "Greeting: hello"),
            ("process_value(42)", "Other: 42"),
        ],
    ),
    MatchTestCase(
        name="test_group_patterns",
        description="Test parenthesized group patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("check_complex_condition(2)", "Two"),
            ("check_complex_condition(1)", "One"),
            ("check_complex_condition(4)", "Mid range"),
            ("check_complex_condition(10)", "Other"),
        ],
    ),
    MatchTestCase(
        name="test_enum_patterns",
        description="Test matching enum values as patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("describe_color(Color.RED)", "I see red!"),
            ("describe_color(Color.GREEN)", "Grass is green"),
            ("describe_color(Color.BLUE)", "I'm feeling blue"),
            ("describe_color('red')", "Unknown color"),
        ],
    ),
    MatchTestCase(
        name="test_mapping_patterns_with_rest",
        description="Test mapping patterns with **rest to capture remaining items.",
        source=textwrap.dedent("""
    def process_config(config):
        match config:
            case {"name": name, "version": version, **extras}:
                return f"App {name} v{version} with extras: {extras}"
            case {"name": name, **rest}:
                return f"App {name} with config: {rest}"
            case _:
                return "Invalid config"
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            (
                "process_config({'name': 'myapp', 'version': '1.0', 'debug': True, 'port': 8080})",
                "App myapp v1.0 with extras: {'debug': True, 'port': 8080}",
            ),
            (
                "process_config({'name': 'myapp', 'author': 'me'})",
                "App myapp with config: {'author': 'me'}",
            ),
            ("process_config({'invalid': True})", "Invalid config"),
        ],
    ),
    MatchTestCase(
        name="test_mixed_sequence_patterns",
        description="Test sequence patterns mixing literals and variables.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            (
                "parse_command(['git', 'add', 'file1.py', 'file2.py'])",
                "Adding files: ['file1.py', 'file2.py']",
            ),
            (
                "parse_command(['git', 'commit', '-m', 'Initial commit'])",
                "Committing: Initial commit",
            ),
            ("parse_command(['git', 'status'])", "Git status with args: []"),
            (
                "parse_command(['python', 'script.py', '--verbose'])",
                "Running python with 2 args",
            ),
            ("parse_command(['ls'])", "Running ls with no args"),
            ("parse_command('not a list')", "Not a command"),
        ],
    ),
    MatchTestCase(
        name="test_nested_class_patterns",
        description="Test complex nested class pattern matching.",
        source=textwrap.dedent("""
    class Container:
        def __init__(self, items):
            self.items = items

    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        expected=textwrap.dedent("""
    import collections.abc
    class Container:
        def __init__(self, items):
            self.items = items

    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        test_calls=[
            (
                "analyze_container(Container([Point(0, 5), Point(1, 1)]))",
                "Container starts with y-axis point 5, has 1 more",
            ),
            (
                "analyze_container(Container([Point(3, 0), Point(2, 4)]))",
                "Container with x-axis point (3, 0) and point (2, 4)",
            ),
            ("analyze_container(Container([]))", "Empty container"),
            ("analyze_container(Container([1, 2, 3]))", "Container with 3 items"),
            ("analyze_container('not a container')", "Not a container"),
        ],
    ),
    MatchTestCase(
        name="test_or_patterns_with_as",
        description="Test OR patterns combined with as patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_number_or_string(42)", "Positive number: 42"),
            ("process_number_or_string(-5)", "Non-positive number: -5"),
            ("process_number_or_string(3.14)", "Positive number: 3.14"),
            ("process_number_or_string('hello')", "Text data: hello"),
            ("process_number_or_string([1, 2, 3])", "Other type: list"),
        ],
    ),
    MatchTestCase(
        name="test_or_pattern_literal_and_type",
        description="Test OR patterns mixing literals and type patterns.",
        source=textwrap.dedent("""
    def process_number_or_string(value):
        match value:
            case (int() | 0 | bool()) as res:
                return f"Zero or integer or bool {res}"
    """),
        expected=textwrap.dedent("""
    def process_number_or_string(value):
        if isinstance(value, int) or value == 0 or isinstance(value, bool):
            res = value
            return f"Zero or integer or bool {res}"
    """),
        test_calls=[
            ("process_number_or_string(42)", "Zero or integer or bool 42"),
            ("process_number_or_string(0.0)", "Zero or integer or bool 0.0"),
            ("process_number_or_string(None)", None),
            ("process_number_or_string(1.0)", None),
            ("process_number_or_string(True)", "Zero or integer or bool True"),
        ],
    ),
    MatchTestCase(
        name="test_mixed_pattern_combinations",
        description="Test complex combinations of different pattern types.",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

        def __repr__(self):
            return f'Point({self.x}, {self.y})'

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
    """),
        expected=textwrap.dedent("""
    import collections.abc
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

        def __repr__(self):
            return f'Point({self.x}, {self.y})'

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
    """),
        test_calls=[
            (
                "complex_matcher({'type': 'point', 'coords': (3, 3)})",
                "Diagonal point: (3, 3)",
            ),
            (
                "complex_matcher({'type': 'point', 'coords': Point(2, 4)})",
                "Point object: (2, 4) from {'type': 'point', 'coords': Point(2, 4)}",
            ),
            ("complex_matcher({'items': [1, 2, 3, 4, 5]})", "Numeric items: 15"),
            (
                "complex_matcher({'nested': {'deep': 'treasure'}})",
                "Deep value: treasure",
            ),
            ("complex_matcher({'alt': {'deep': 'treasure'}})", "Deep value: treasure"),
            ("complex_matcher({'nothing': 'matches'})", "No match"),
        ],
    ),
    MatchTestCase(
        name="test_literal_matching_multiple_types",
        description="Test literal patterns with different types including None, True, False.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("check_literal(True)", "Boolean True"),
            ("check_literal(False)", "Boolean False"),
            ("check_literal(None)", "None value"),
            ("check_literal(0)", "Zero integer"),
            (
                "check_literal(0.0)",
                "Zero integer",
            ),  # 0.0 matches case 0: due to equality
            ("check_literal('')", "Empty string"),
            ("check_literal([])", "Empty list"),
            ("check_literal({})", "Empty dict"),
            ("check_literal(42)", "Other: 42"),
        ],
    ),
    MatchTestCase(
        name="test_positional_class_patterns",
        description="Test positional class pattern matching using __match_args__.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("describe_point_positional(Point(0, 0))", "Origin"),
            ("describe_point_positional(Point(0, 5))", "Y-axis: 5"),
            ("describe_point_positional(Point(3, 0))", "X-axis: 3"),
            ("describe_point_positional(Point(2, 4))", "Point: 2, 4"),
            ("describe_point_positional('not a point')", "Not a point"),
        ],
    ),
    MatchTestCase(
        name="test_mixed_positional_keyword_patterns",
        description="Test mixing positional and keyword arguments in class patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("analyze_point_mixed(Point(0, 5))", "Y-axis (mixed): 5"),
            ("analyze_point_mixed(Point(3, 0))", "X-axis (mixed): 3"),
            ("analyze_point_mixed(Point(2, 4))", "Point (keywords): 2, 4"),
        ],
    ),
    MatchTestCase(
        name="test_comprehensive_pattern_execution_converted_only",
        description="Test converted code execution on all Python versions (without string validation).",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        expected=textwrap.dedent("""
    import collections.abc
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        test_calls=[
            ("comprehensive_matcher([])", "empty list"),
            ("comprehensive_matcher([15])", "single large: 15"),
            ("comprehensive_matcher([5])", "other"),  # Small number doesn't match guard
            ("comprehensive_matcher([1, 2])", "pair: 1, 2"),
            ("comprehensive_matcher({'type': 'user', 'name': 'Alice'})", "user: Alice"),
            ("comprehensive_matcher(Point(0, 5))", "y-axis point: 5"),
            ("comprehensive_matcher({'type': 'admin'})", "other"),  # Dict without name
            (
                "comprehensive_matcher('string')",
                "other",
            ),  # String should not match sequences
        ],
    ),
    MatchTestCase(
        name="test_nested_as_patterns",
        description="Test as patterns nested within other patterns.",
        source=textwrap.dedent("""
    def analyze_point(data):
        match data:
            case {"point": (x, y) as coords} if x > 0 and y > 0:
                return f"Positive quadrant: {coords}"
            case _:
                return "Not a point"
    """),
        expected=textwrap.dedent("""
    import collections.abc
    def analyze_point(data):
        if isinstance(data, collections.abc.Mapping) and "point" in data and isinstance(data["point"], collections.abc.Sequence) and not isinstance(data["point"], (str, collections.abc.Mapping)) and len(data["point"]) == 2 and data["point"][0] > 0 and data["point"][1] > 0:
            coords = data["point"]
            x, y = data["point"]
            return f"Positive quadrant: {coords}"
        else:
            return "Not a point"
    """),
        test_calls=[
            ("analyze_point({'point': (3, 4)})", "Positive quadrant: (3, 4)"),
            ("analyze_point({'data': 'other'})", "Not a point"),
        ],
    ),
    MatchTestCase(
        name="test_dataclass_patterns",
        description="Test pattern matching with dataclasses.",
        source=textwrap.dedent("""
    import dataclasses

    @dataclasses.dataclass
    class Point:
        x: int
        y: int

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
    """),
        expected=textwrap.dedent("""
    import dataclasses

    @dataclasses.dataclass
    class Point:
        x: int
        y: int

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
    """),
        test_calls=[
            ("where_is(Point(0, 0))", "Origin"),
            ("where_is(Point(0, 5))", "Y=5"),
            ("where_is(Point(3, 0))", "X=3"),
            ("where_is(Point(2, 4))", "Somewhere else"),
            ("where_is('not a point')", "Not a point"),
        ],
    ),
    MatchTestCase(
        name="test_empty_class_patterns",
        description="Test class patterns without any attribute constraints.",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        expected=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

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
    """),
        test_calls=[
            ("classify_object(Point(1, 2))", "It's a Point"),
            ("classify_object([1, 2, 3])", "It's a list"),
            ("classify_object({'key': 'value'})", "It's a dict"),
            ("classify_object('hello')", "It's a string"),
            ("classify_object(42)", "Something else"),
        ],
    ),
    MatchTestCase(
        name="test_nested_sequence_point_patterns",
        description="Test complex nested patterns with sequences and points from PEP 636.",
        source=textwrap.dedent("""
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
    """),
        # We need to convert the dataclass too for this to work.
        conversions=(
            _converters.convert_dataclass,
            _converters.convert_match_statement,
        ),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("analyze_points([])", "No points"),
            ("analyze_points([DCPoint(0, 0)])", "The origin"),
            ("analyze_points([DCPoint(3, 4)])", "Single point 3, 4"),
            (
                "analyze_points([DCPoint(0, 2), DCPoint(0, 5)])",
                "Two on the Y axis at 2, 5",
            ),
            (
                "analyze_points([DCPoint(3, 2), DCPoint(3, 8)])",
                "Two points on vertical line x=3: (3, 2), (3, 8)",
            ),
            (
                "analyze_points([DCPoint(1, 2), DCPoint(4, 5)])",
                "Two points: (1, 2), (4, 5)",
            ),
            (
                "analyze_points([DCPoint(1, 1), DCPoint(2, 2), DCPoint(3, 3)])",
                "Complex or invalid points",
            ),
        ],
    ),
    MatchTestCase(
        name="test_guard_diagonal_patterns",
        description="Test guard conditions for diagonal point checking from PEP 636.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("check_diagonal(Point(3, 3))", "Y=X at 3"),
            ("check_diagonal(Point(2, 5))", "Not on the diagonal: (2, 5)"),
            ("check_diagonal('not a point')", "Not a point"),
            ("check_diagonal(Point(4, 3))", "Below the y=x curve (4, 3)"),
        ],
    ),
    MatchTestCase(
        name="test_point_no_match_args",
        description="Test point class without __match_args__ should raise TypeError.",
        source=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def check_diagonal(point):
        match point:
            case Point(x, y) if x == y:
                return f"Y=X at {x}"
            case _:
                return "Not a point"
    """),
        expected=textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def check_diagonal(point):
        if isinstance(point, Point) and not (hasattr(Point, "__match_args__") and len(Point.__match_args__) >= 2):
            raise TypeError("Point() accepts 0 positional sub-patterns (2 given)")
        elif isinstance(point, Point) and getattr(point, Point.__match_args__[0]) == getattr(point, Point.__match_args__[1]):
            x = getattr(point, Point.__match_args__[0])
            y = getattr(point, Point.__match_args__[1])
            return f"Y=X at {x}"
        else:
            return "Not a point"
    """),
        failing_calls=[
            (
                "check_diagonal(Point(3, 3))",
                pytest.raises(
                    TypeError,
                    match=re.escape(
                        "Point() accepts 0 positional sub-patterns (2 given)",
                    ),
                ),
            ),
        ],
    ),
    MatchTestCase(
        name="test_tuple_unpacking_no_parens",
        description="Test tuple pattern matching without parentheses as mentioned in PEP 636.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_tuple_variants(('go', 'north'))", "Action: go, Object: north"),
            ("process_tuple_variants(('quit',))", "Single: quit"),
            (
                "process_tuple_variants(('drop', 'sword', 'shield', 'potion'))",
                "First: drop, Rest: ['sword', 'shield', 'potion']",
            ),
            ("process_tuple_variants('string')", "No match"),
        ],
    ),
    MatchTestCase(
        name="test_builtin_type_patterns",
        description="Test pattern matching against built-in types like int(), str(), list().",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("classify_builtin_type(42)", "Positive integer: 42"),
            ("classify_builtin_type(-5)", "Non-positive integer: -5"),
            ("classify_builtin_type('hello world')", "Long string: hello world"),
            ("classify_builtin_type('hi')", "Short string: hi"),
            ("classify_builtin_type([])", "Empty list"),
            ("classify_builtin_type([1, 2, 3])", "List with 3 items"),
            ("classify_builtin_type({'a': 1, 'b': 2})", "Dictionary with 2 keys"),
            ("classify_builtin_type(3.14)", "Other type: float"),
        ],
    ),
    MatchTestCase(
        name="test_deeply_nested_as_patterns",
        description="Test as patterns nested within other as patterns.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            ("process_nested_data([3, 4])", "Coords [3, 4] in wrapper [3, 4]"),
            (
                "process_nested_data({'outer': {'inner': 'treasure'}})",
                "Inner dict {'inner': 'treasure'} in outer {'outer': {'inner': 'treasure'}}",
            ),
            (
                "process_nested_data({'name': 'Alice'})",
                "Record {'name': 'Alice'} in container {'name': 'Alice'}",
            ),
            ("process_nested_data('no match')", "No match"),
        ],
    ),
    MatchTestCase(
        name="test_multiple_as_patterns_different_levels",
        description="Test multiple as patterns at different nesting levels.",
        source=textwrap.dedent("""
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
    """),
        expected=textwrap.dedent("""
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
    """),
        test_calls=[
            (
                "analyze_complex_structure({'items': [1, 2, 3]})",
                "First: 1, List: [1, 2, 3], Full: {'items': [1, 2, 3]}",
            ),
            ("analyze_complex_structure(Point(5, 10))", "Point(5, 10) = Point(5, 10)"),
            (
                "analyze_complex_structure({'metadata': {'id': 'doc123'}})",
                "ID: doc123, Meta: {'id': 'doc123'}, Doc: {'metadata': {'id': 'doc123'}}",
            ),
            ("analyze_complex_structure('no match')", "No match"),
        ],
    ),
    MatchTestCase(
        name="test_as_patterns_with_star_expressions_invalid_syntax",
        description="Test that invalid syntax with as patterns and star expressions produces the same error.",
        source=textwrap.dedent("""
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
    """),
        syntax_error="invalid syntax",
        test_calls=[],
    ),
    MatchTestCase(
        name="test_complex_as_pattern_combinations_invalid_syntax",
        description="Test that complex invalid as pattern combinations produce the same error.",
        source=textwrap.dedent("""
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
    """),
        syntax_error="invalid syntax",
        test_calls=[],
    ),
]


#############################################
#             Test execution                #
#############################################

validate_assumptions_for_unconverted_cases = []
validate_converted_cases = []
validate_assumptions_for_unconverted_error_cases = []
validate_converted_error_cases = []
for test_case in match_statement_cases:
    for test_call in test_case.test_calls:
        call_args = list(test_call[:2])

        marks = getattr(test_call, "marks", ())

        call_id = f"{test_case.name}--" + str(
            getattr(test_call, "id", None) or call_args[0],
        )
        validate_converted_cases.append(
            pytest.param(
                *([test_case.expected] + call_args),
                marks=marks,
                id=call_id,
            ),
        )
        marks = ()
        if test_case.syntax_error:
            marks = pytest.mark.skip(reason="Source syntax error")
        validate_assumptions_for_unconverted_cases.append(
            pytest.param(
                *[test_case.source] + call_args,
                marks=marks,
                id=call_id,
            ),
        )
    for test_call in test_case.failing_calls:
        call_args = list(test_call[:2])

        marks = getattr(test_call, "marks", ())

        call_id = f"{test_case.name}--" + str(
            getattr(test_call, "id", None) or call_args[0],
        )
        validate_converted_error_cases.append(
            pytest.param(
                *([test_case.expected] + call_args),
                marks=marks,
                id=call_id,
            ),
        )
        marks = ()
        if test_case.syntax_error:
            marks = pytest.mark.skip(reason="Source syntax error")
        validate_assumptions_for_unconverted_error_cases.append(
            pytest.param(
                *[test_case.source] + call_args,
                marks=marks,
                id=call_id,
            ),
        )

all_valid_cases = []
all_syntax_error_cases = []

for case in match_statement_cases:
    if case.syntax_error is not None:
        all_syntax_error_cases.append(
            pytest.param(
                case.source,
                case.syntax_error,
                marks=case.conversion_markers,
                id=case.name,
            ),
        )
    else:
        all_valid_cases.append(
            pytest.param(
                case.source,
                case.expected,
                case.conversions,
                marks=case.conversion_markers,
                id=case.name,
            ),
        )


def execute_code_with_results(source_code):
    """Execute code and return the namespace containing results."""
    namespace = {}
    exec(source_code, namespace)
    return namespace


@pytest.mark.parametrize(
    ["case_source", "call_input", "call_expected"],
    validate_assumptions_for_unconverted_cases,
)
@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Python 3.10+ required for match statements",
)
def test_validate_assumptions_for_unconverted(
    case_source: str,
    call_input: str,
    call_expected: str,
):
    """EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only). Checks that our assumptions are correct."""

    # Test that original and converted produce the same result for this specific call
    original_code = case_source + "\n" + f"result = {call_input}"
    result = execute_code_with_results(original_code)
    assert call_expected == result["result"]


@pytest.mark.parametrize(
    ["case_expected", "call_input", "call_expected"],
    validate_converted_cases,
)
def test_validate_converted(case_expected: str, call_input: str, call_expected: str):
    """EXECUTION VALIDATION: Test converted code behavior (all Python versions)"""
    # Execute the converted code with this specific test call
    full_code = case_expected + "\n" + f"result = {call_input}"

    # This call should succeed
    results = execute_code_with_results(full_code)
    assert results["result"] == call_expected


@pytest.mark.parametrize(
    ["case_source", "call_input", "expectation"],
    validate_assumptions_for_unconverted_error_cases,
)
@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Python 3.10+ required for match statements",
)
def test_validate_assumptions_for_unconverted_failing(
    case_source: str,
    call_input: str,
    expectation,
):
    """EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only). Checks that our assumptions are correct."""
    original_code = case_source + "\n" + f"result = {call_input}"
    with expectation:
        execute_code_with_results(original_code)


@pytest.mark.parametrize(
    ["case_expected", "call_input", "expectation"],
    validate_converted_error_cases,
)
def test_validate_converted_failing(case_expected: str, call_input: str, expectation):
    """EXECUTION VALIDATION: Test converted code behavior (all Python versions)"""
    full_code = case_expected + "\n" + f"result = {call_input}"

    with expectation:
        execute_code_with_results(full_code)


@pytest.mark.parametrize(
    ["case_source", "case_expected", "conversions"],
    all_valid_cases,
)
def test_case_conversion_as_expected(case_source: str, case_expected: str, conversions):
    # TODO: We may need to skip for certain versions of libcst.
    module = cst.parse_module(case_source)
    for converter in conversions:
        module = converter(module)
    try:
        assert module.code == case_expected
    except:
        print(module.code)
        raise


if all_syntax_error_cases:

    @pytest.mark.parametrize(
        ["case_source", "syntax_error_match"],
        all_syntax_error_cases,
    )
    @pytest.mark.skipif(
        sys.version_info < (3, 10),
        reason="Python 3.10+ required for match statements",
    )
    def test_case_syntax_error(case_source: str, syntax_error_match: str):
        with pytest.raises(SyntaxError, match=syntax_error_match):
            exec(case_source, {})
        with pytest.raises(libcst.ParserSyntaxError):
            cst.parse_module(case_source)
