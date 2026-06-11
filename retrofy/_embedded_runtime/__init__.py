"""Canonical source tree for runtime helpers injected into converted packages.

The contents of ``_embedded_runtime/_retrofy/`` are copied verbatim into every
converted package that needs them — by the wheel-build hook
(:func:`retrofy._pep517_hooks.compatibility_via_rewrite`) for installed
wheels, and synthesised on-the-fly by
:class:`retrofy._meta_hook_converter.MyMetaPathFinder` for editable
installs and the pytest plugin. Converted source imports from
``.<pkg>._retrofy.<helper>``, never from ``retrofy.*`` — wheel installs
must remain retrofy-free at runtime.

**Hard rules every module in this tree must obey:**

* **Standard library only.** No third-party imports, not even
  retrofy-flavoured ones. Anything in here ships *inside* the user's
  wheel; pulling in a dependency from here would mean every retrofy
  user inherits it as a runtime requirement.
* **Must work on the oldest Python retrofy supports.** This code runs
  on the user's interpreter, which may be older than the one that
  built the wheel. Stick to syntax and stdlib APIs that are available
  on the floor declared by retrofy's own ``requires-python``.
* **No runtime parsing of user code** (no ``ast``, ``tokenize``,
  ``compile``, ``exec`` on user source). Conversion is a build-time
  activity; the embedded runtime is just the small set of helpers
  that the converted code calls into.
"""
