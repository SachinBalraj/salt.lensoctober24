"""Entry point for ``python -m ml.ingestion``.

Without this module the package is a library that can only be reached by
importing :func:`ml.ingestion.pipeline.main` from a script, which makes the four
commands awkward to run from a shell or from another tool. ``python -m`` is the
standard way to expose a package's CLI, and it is what the documentation tells a
reader to type, so the file has to exist for the instructions to be true.
"""

from __future__ import annotations

import sys

from .pipeline import main

if __name__ == "__main__":
    sys.exit(main())
