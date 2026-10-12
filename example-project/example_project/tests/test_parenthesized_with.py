"""
Test parenthesised context managers (PEP 617).
"""

import contextlib


@contextlib.contextmanager
def _recording(events, name):
    events.append(("enter", name))
    yield name
    events.append(("exit", name))


def test_parenthesized_with():
    events = []
    with (
        _recording(events, "a") as a,  # comment
        _recording(events, "b") as b,
    ):
        events.append(("body", a, b))

    assert events == [
        ("enter", "a"),
        ("enter", "b"),
        ("body", "a", "b"),
        ("exit", "b"),
        ("exit", "a"),
    ]
