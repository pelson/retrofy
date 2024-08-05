import sys
import pathlib
import typing

from importlib.abc import MetaPathFinder

from ._converters import convert_union
import libcst as cst


import sys
from os.path import isdir
from importlib import invalidate_caches
from importlib.abc import SourceLoader
from importlib.machinery import FileFinder


class OnTheFlyConverter(SourceLoader):
    def __init__(self, path):
        self.path = path

    def get_filename(self, fullname):
        return self.path

    def get_data(self, filename):
        """exec_module is already defined for us, we just have to provide a way
        of getting the source code of the module"""
        with open(filename) as f:
            data = f.read()

        mod = cst.parse_module(data)
        new_code = convert_union(mod).code
        return new_code


class MyMetaPathFinder(MetaPathFinder):
    def __init__(self, package_names: typing.Sequence[str] = ()):
        self.package_names = set()
        self.add_package_handling(package_names)

    def add_package_handling(self, package_names: typing.Sequence[str]):
        self.package_names.update(package_names)

    def find_spec(self, fullname, path, target=None):
        for prefix in self.package_names:
            if fullname != prefix and not fullname.startswith(f'{prefix}.'):
                return None

        # If your custom logic doesn't handle it, defer to the next finder
        for finder in sys.meta_path:
            if isinstance(finder, MyMetaPathFinder):
                continue

            spec = finder.find_spec(fullname, path, target)
            if spec:
                break
        else:
            return None

        spec.loader = OnTheFlyConverter(spec.origin)
        return spec


def register_hook(package_names):
    for finder in sys.meta_path:
        if isinstance(finder, MyMetaPathFinder):
            existing_hook = finder
            break
    else:
        existing_hook = MyMetaPathFinder()
        sys.meta_path.insert(0, MyMetaPathFinder(package_names))

    existing_hook.add_package_handling(package_names)
