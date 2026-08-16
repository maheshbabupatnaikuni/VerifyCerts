#!/usr/bin/env python
import os
import sys
from pathlib import Path


def _bootstrap_paths() -> None:
    """Add moved source folders to Python path after project reorganization."""
    project_root = Path(__file__).resolve().parent
    extra_paths = [
        project_root / "core",
        project_root / "backend" / "apps",
    ]
    for path in extra_paths:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def main() -> None:
    _bootstrap_paths()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "university_verify.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django.") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
