import dataclasses

import libcst as cst

from ._transformations import (
    dataclass,
    match_statement,
    type_alias,
    typing_final,
    walrus,
)
from ._transformations.import_utils import EnhancedImportManager


class TypingTransformer(cst.CSTTransformer):
    def __init__(self, scope):
        self._scope = scope
        self._require_typing = False
        self.import_manager = EnhancedImportManager()

    def leave_Annotation(
        self,
        node: cst.Annotation,
        updated_node: cst.Annotation,
    ) -> cst.Annotation:
        if isinstance(updated_node.annotation, cst.BinaryOperation):
            # TODO: Use a matcher here.
            if isinstance(updated_node.annotation.operator, cst.BitOr):
                # print('ANNO scope', self._scope[node.annotation])
                self._require_typing = True
                new_node = cst.Subscript(
                    cst.Attribute(
                        cst.Name("typing"),
                        cst.Name("Union"),
                    ),
                    slice=(
                        cst.SubscriptElement(
                            updated_node.annotation.left,  # type: ignore
                        ),
                        cst.SubscriptElement(
                            updated_node.annotation.right,  # type: ignore
                        ),
                    ),
                )
                return cst.Annotation(new_node)

        return updated_node

    def leave_Module(self, node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self._require_typing:
            # Scan existing imports and add typing import if needed
            self.import_manager.scan_imports(updated_node.body)
            new_body = list(updated_node.body)
            new_body = self.import_manager.ensure_direct_import(new_body, "typing")

            return dataclasses.replace(updated_node, body=tuple(new_body))
        return updated_node


def convert_union(module: cst.Module) -> cst.Module:
    """
    Given typing such as `SomeClass | AnotherClass`, convert this to
    `typing.Union[SomeClass, AnotherClass]`, with the appropriate `typing`
    import included.

    Note that this typically doesn't need to be done at runtime, and mostly is
    a type checking feature.

    """

    wrapper = cst.metadata.MetadataWrapper(module)
    metadata = wrapper.resolve(cst.metadata.ScopeProvider)

    transformer = TypingTransformer(metadata)
    return module.visit(transformer)


def convert_sequence_subscript(module: cst.Module) -> cst.Module:
    """
    Convert the built-in sequence types such as `list[int]` to the
    `typing.List[int]` form.

    Currently only `list[int]` is supported.
    """
    # TODO: Do this correctly. Including adding the necessry typing import.
    orig = module.code
    new = orig.replace("list[str]", "typing.List[str]")
    if new != orig:
        return cst.parse_module(new)
    return module


def convert_walrus_operator(module: cst.Module) -> cst.Module:
    return module.visit(walrus.WalrusOperatorTransformer())


def convert_type_alias(module: cst.Module) -> cst.Module:
    return module.visit(type_alias.PEP695Transformer())


def convert_match_statement(module: cst.Module) -> cst.Module:
    return module.visit(match_statement.MatchStatementTransformer())


def convert_dataclass(module: cst.Module) -> cst.Module:
    return module.visit(dataclass.DataclassTransformer())


def convert_typing_final(module: cst.Module) -> cst.Module:
    return module.visit(typing_final.TypingFinalTransformer())


def convert(code: str) -> str:
    mod = cst.parse_module(code)
    mod = convert_sequence_subscript(mod)
    mod = convert_walrus_operator(mod)
    mod = convert_type_alias(mod)
    mod = convert_dataclass(mod)
    mod = convert_typing_final(mod)
    mod = convert_match_statement(mod)
    mod = convert_union(mod)
    return mod.code
