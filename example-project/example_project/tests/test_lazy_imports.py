"""
Test the PEP 810 (``lazy`` imports) backport.

The ``lazy`` soft keyword is Python 3.15+ syntax. Retrofy rewrites it
into runtime helper calls so this file is importable on the older
Pythons that the example project supports.

Note on a property that's *not* tested here: pytest's collection
introspects every module-level value (it probes ``__bases__`` for
``issubclass(obj, TestCase)`` and ``__test__`` for the nose-compat
check, among others). Every such probe reifies a LazyProxy via
``__getattr__`` — and the same is true under native PEP 810, since
any ``getattr(module, lazy_name)`` triggers resolution. So the
"module not loaded until first read" property cannot be observed
from inside a pytest test in this file. The retrofy suite covers
it in a freshly exec'd namespace where collection-time
introspection does not apply — see
``retrofy/tests/_transformations/test_lazy_imports_native_equivalence.py``.
"""

lazy import json
lazy from collections.abc import Mapping, Iterable
lazy from xml.etree.ElementTree import fromstring


def test_lazy_module_dumps_and_loads():
    assert json.dumps({"x": 2}) == '{"x": 2}'
    assert json.loads('{"a": 1}') == {"a": 1}


def test_lazy_from_with_isinstance():
    # ``isinstance`` dispatches on the *type*, which is exactly the case
    # the rewriter wraps with ``resolve()`` so the proxy is reified.
    assert isinstance({}, Mapping)
    assert not isinstance([], Mapping)
    assert isinstance([], Iterable)


def test_lazy_from_callable_attribute():
    # ``fromstring`` is a function; the proxy forwards ``__call__``.
    root = fromstring("<a><b/></a>")
    assert root.tag == "a"


def test_local_shadowing_is_left_alone():
    def takes_mapping(Mapping):
        return Mapping + 1

    assert takes_mapping(41) == 42
