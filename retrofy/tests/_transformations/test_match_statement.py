from __future__ import annotations

import sys
import textwrap
import typing
from dataclasses import dataclass

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
    conversion_markers: typing.Collection[MarkDecorator] = ()


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
            pytest.param(
                "check_literal('world')",
                "Something else",
                marks=pytest.mark.xfail(strict=True, reason="Doesn;t work"),
            ),
            ("check_literal('world')", "Something else"),
        ],
    ),
]


#############################################
#             Test execution                #
#############################################

validate_assumptions_for_unconverted_cases = []
validate_converted_cases = []
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
def test_validate_assumptions_for_unconverted(case_source, call_input, call_expected):
    """EQUIVALENCE VALIDATION: Compare with original (Python 3.10+ only). Checks that our assumptions are correct."""

    # Test that original and converted produce the same result for this specific call
    original_code = case_source + "\n" + f"result = {call_input}"
    result = execute_code_with_results(original_code)
    assert call_expected == result["result"]


@pytest.mark.parametrize(
    ["case_expected", "call_input", "call_expected"],
    validate_converted_cases,
)
def test_validate_converted(case_expected: str, call_input, call_expected):
    """EXECUTION VALIDATION: Test converted code behavior (all Python versions)"""
    # Execute the converted code with this specific test call
    full_code = case_expected + "\n" + f"result = {call_input}"

    # This call should succeed
    results = execute_code_with_results(full_code)
    assert results["result"] == call_expected


@pytest.mark.parametrize(["case_source", "case_expected"], all_valid_cases)
@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Python 3.10+ required for match statements",
)
def test_case_conversion_as_expected(case_source: str, case_expected: str):
    module = cst.parse_module(case_source)
    result = _converters.convert_match_statement(module)
    try:
        assert result.code == case_expected
    except:
        print(result.code)
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
