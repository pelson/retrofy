"""PEP 758 (3.14): ``except`` / ``except*`` with several exception types
and no parentheses, lowered to the parenthesised form."""

import textwrap

import libcst as cst
import pytest

from retrofy import _converters


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            """
            try:
                pass
            except ValueError, TypeError:
                pass
            """,
            """
            try:
                pass
            except (ValueError, TypeError):
                pass
            """,
            id="except",
        ),
        pytest.param(
            """
            try:
                pass
            except* ValueError, TypeError:
                pass
            """,
            """
            try:
                pass
            except* (ValueError, TypeError):
                pass
            """,
            id="except-star",
        ),
        pytest.param(
            """
            try:
                pass
            except mod.Error, OSError, KeyError:  # comment
                pass
            except RuntimeError:
                pass
            """,
            """
            try:
                pass
            except (mod.Error, OSError, KeyError):  # comment
                pass
            except RuntimeError:
                pass
            """,
            id="three-types-and-others",
        ),
        pytest.param(
            """
            try:
                pass
            except (ValueError, TypeError) as err:
                pass
            except (KeyError,):
                pass
            except ERRORS:
                pass
            """,
            """
            try:
                pass
            except (ValueError, TypeError) as err:
                pass
            except (KeyError,):
                pass
            except ERRORS:
                pass
            """,
            id="already-valid-unchanged",
        ),
    ],
)
def test_pep758_except(source, expected):
    module = cst.parse_module(textwrap.dedent(source))
    result = _converters.convert_pep758_except(module)
    assert result.code == textwrap.dedent(expected)


def test_convert_catches_each_type():
    source = textwrap.dedent("""
    def catch(exc):
        try:
            raise exc
        except KeyError, ValueError:
            return "caught"
    """)
    namespace: dict = {}
    exec(_converters.convert(source), namespace)
    assert namespace["catch"](KeyError()) == "caught"
    assert namespace["catch"](ValueError()) == "caught"
    with pytest.raises(TypeError):
        namespace["catch"](TypeError())
