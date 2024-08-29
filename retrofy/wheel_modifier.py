import pathlib
import shutil
import zipfile

from setuptools_ext import WheelModifier

from ._converters import convert


def compatibility_via_import_hook(wheel: pathlib.Path):
    """Add a pth hook to ensure make imported code compatible at import time (i.e. suitable for editable mode)"""
    editable_copy = wheel.parent / (wheel.name + '.copy.whl')
    shutil.copy(wheel, editable_copy)

    with zipfile.ZipFile(str(editable_copy), "r") as whl_zip:
        whl = WheelModifier(whl_zip)

        top_level = whl.read(
            whl.dist_info_dirname() + '/top_level.txt',
        ).decode('utf-8').splitlines()
        top_level_pkgs = [pkg for pkg in top_level if pkg]

        for pkg in top_level_pkgs:
            fn = f'_retrofy.__editable_compat__.{pkg}.pth'
            script = f"import retrofy._meta_hook_converter as c; c.register_hook(['{pkg}']);"
            whl.write(zipfile.ZipInfo(fn), script)

        with wheel.open("wb") as whl_fh:
            whl.write_wheel(whl_fh)

    editable_copy.unlink()


def compatibility_via_rewrite(wheel: pathlib.Path):
    """Change code within the given wheel to be compatible"""
    editable_copy = wheel.parent / (wheel.name + '.copy.whl')
    shutil.copy(wheel, editable_copy)

    has_modifications = False

    with zipfile.ZipFile(str(editable_copy), "r") as whl_zip:
        whl = WheelModifier(whl_zip)

        for filename in whl_zip.namelist():
            if filename.endswith('.py'):
                code = whl.read(filename).decode('utf-8')
                new_code = convert(code)
                if new_code != code:
                    print(f"Converted {filename} to compatibility syntax")
                    whl.write(filename, new_code)
                    has_modifications = True
        if has_modifications:
            with wheel.open("wb") as whl_fh:
                whl.write_wheel(whl_fh)

    editable_copy.unlink()
