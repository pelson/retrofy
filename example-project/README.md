# How to test this locally (in-repository)

The build dependency of `retrofy` will, by default, go to the package repository to
resolve the dependency. If you wish to test the `retrofy` that is being developed
in the repository, the best approach is to make a build venv, install `retrofy`, and then
build the `example-project` with the appropriate "" flag. In practice:

```
python -m venv ./build-venv
./build-venv/bin/python -m pip install build setuptools -e ../
./build-venv/bin/python -m build --skip-dependency-check ./
```
