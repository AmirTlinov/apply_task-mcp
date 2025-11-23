#!/usr/bin/env python3
"""Thin loader delegating CLI/TUI logic to the interface layer."""

import sys

from core.desktop.devtools.interface import tasks_app as _tasks_app

if __name__ != "__main__":
    # When imported, expose the full interface implementation directly.
    sys.modules[__name__] = _tasks_app
else:
    sys.exit(_tasks_app.main())
