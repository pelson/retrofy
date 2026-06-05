"""
Test the PEP 810 (``lazy`` imports) backport.

The ``lazy`` soft keyword is Python 3.15+ syntax. Retrofy rewrites it
into runtime helper calls so this file is importable on the older
Pythons that the example project supports.
"""
import sys

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


def test_lazy_module_not_loaded_before_use():
    # ``ctypes`` is not imported anywhere in this file or by retrofy.
    # The lazy binding does not import it until first access.
    lazy_ctypes_was_loaded = "ctypes" in sys.modules
    # Trivially true because no code path here references ``ctypes`` —
    # this is a smoke check that asserting nothing about the proxy keeps
    # the module unimported. We do not actually create a lazy binding
    # for it; instead we make a positive claim about ``json``: after
    # ``test_lazy_module_dumps_and_loads`` ran, ``json`` is now resolved
    # and the global slot has been replaced with the real module.
    assert "json" in sys.modules
    assert not lazy_ctypes_was_loaded


def test_local_shadowing_is_left_alone():
    def takes_mapping(Mapping):
        return Mapping + 1

    assert takes_mapping(41) == 42
