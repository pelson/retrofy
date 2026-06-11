"""Canonical source tree for runtime helpers injected into converted packages.

The contents of ``_embedded_runtime/_retrofy/`` are copied verbatim into every
converted package that needs them — by the wheel-build hook
(:func:`retrofy._pep517_hooks.compatibility_via_rewrite`) for installed
wheels, and synthesised on-the-fly by
:class:`retrofy._meta_hook_converter.MyMetaPathFinder` for editable
installs and the pytest plugin. Converted source imports from
``.<pkg>._retrofy.<helper>``, never from ``retrofy.*`` — wheel installs
must remain retrofy-free at runtime.
"""
