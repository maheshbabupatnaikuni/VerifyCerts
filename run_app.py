"""Prepare and run VerifyCerts locally."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def run(*arguments: str) -> None:
    subprocess.run([sys.executable, "manage.py", *arguments], cwd=ROOT, check=True)


def main() -> int:
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-local-secret-key-change-before-hosting")
    os.environ.setdefault("DJANGO_DEBUG", "True")
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
    os.environ.setdefault("SUPER_ADMIN_USERNAME", "admin")
    os.environ.setdefault("SUPER_ADMIN_PASSWORD", "test-only-local-admin-password")
    os.environ.setdefault("SUPER_ADMIN_EMAIL", "admin@example.edu")
    os.environ.setdefault("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")

    run("migrate", "--noinput")
    run("ensure_super_admin")
    print("Starting VerifyCerts at http://127.0.0.1:8000")
    return subprocess.call(
        [sys.executable, "manage.py", "runserver", "127.0.0.1:8000"],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
