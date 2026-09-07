"""Build hook: declare the optional C extension only when it is wanted.

The extension is OPTIONAL — installs without a compiler still succeed
(``optional=True`` below, the same fallback the no-compiler CI job
exercises).  On the release build, ``ERTFTM070_NO_FASTIO=1`` keeps
``ext_modules`` empty so the published wheel is pure (``py3-none-any``):
when the extension is declared at all, ``bdist_wheel`` tags the wheel
platform-specific even if the compile was skipped, and PyPI rejects
that tag.  The sdist always ships the C source, so
``pip install ertftm070 --no-binary ertftm070`` builds the fast path
on the target.
"""
import os

from setuptools import Extension, setup

ext_modules = []
if os.environ.get("ERTFTM070_NO_FASTIO") != "1":
    ext_modules = [
        Extension(
            "ertftm070._fastio",
            sources=["src/ertftm070/_fastio.c"],
            optional=True,
        )
    ]

setup(ext_modules=ext_modules)
