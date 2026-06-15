"""Transform typing imports/usages so newer features fall back to typing_extensions.

This module is a thin configuration over :mod:`retrofy._transformations._backport_engine`.
"""

from __future__ import annotations

import libcst as cst

from ._backport_engine import (
    BackportConfig,
    BackportFeature,
    transform,
    transform_module,
)

TYPING_EXTENSIONS_CONFIG = BackportConfig(
    source_module="typing",
    fallback_module="typing_extensions",
    features=(
        BackportFeature("Literal", (3, 8)),
        BackportFeature("get_args", (3, 10)),
        BackportFeature("get_origin", (3, 10)),
        BackportFeature("final", (3, 8)),
        BackportFeature("TypedDict", (3, 8)),
    ),
)


def transform_typing_extensions(source_code: str) -> str:
    return transform(source_code, TYPING_EXTENSIONS_CONFIG)


def convert(module: cst.Module) -> cst.Module:
    return transform_module(module, TYPING_EXTENSIONS_CONFIG)
