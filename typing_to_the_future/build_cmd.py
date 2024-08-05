import typing
from typing import Dict, List
import pathlib
import site

try:
    import setuptools
except ImportError:
    raise ImportError("Unable to import setuptools. Be sure to declare it as a build dependency")


# Follow the advice in https://github.com/pypa/setuptools/discussions/3762.

import logging
from pathlib import Path
from setuptools.command.build_py import build_py

import libcst as cst

from ._converters import convert_union


class ConvertCodeToLegacyForm(build_py):
    def convert_to_legacy(self, py_path: str) -> bool:
        """
        Convert the given code to legacy form. Return True if changes made.

        """
        mod = cst.parse_module(Path(py_path).read_text())
        new = convert_union(mod)
        if mod.code != new.code:
            Path(py_path).write_text(new.code)

    def run(self):
        super().run()
        if self.editable_mode:
            top_level_pkgs = [
                pkg for pkg in self.distribution.packages if '.' not in pkg
            ]

            sp = pathlib.Path(site.getsitepackages()[0])

            for pkg in top_level_pkgs:
                fn = sp / f'_typing_to_the_future.__editable_compat__.{pkg}.pth'
                self.announce('Writing editable file at fn)
                fn.write_text(f'''import typing_to_the_future._meta_hook_converter as c; c.register_hook(['{pkg}']);''')

    def build_module(self, module, module_file, package):
        # Note that this does not get called in editable mode.
        outfile, copied = super().build_module(module, module_file, package)

        # self.has_announced_downgrade = getattr(self, 'has_announced_downgrade', False)
        # if not self.has_announced_downgrade:
        #     self.announce(f'Downgrading modern code to legacy form', level=logging.INFO)
        #     self.has_announced_downgrade = True

        if self.convert_to_legacy(outfile) and copied:
            self.announce(f'Downgrading {outfile}', level=logging.INFO)
            # Access the name-mangled dict... :(
            self._build_py__updated_files.append(outfile)
        return outfile, copied


def cmd_class(
        existing_cmd_class: typing.Optional[typing.Dict[str, typing.Type]] = None,
) -> typing.Dict[str, typing.Type]:
    return dict(**(existing_cmd_class or {}), **{
        'build_py': ConvertCodeToLegacyForm,
    })
