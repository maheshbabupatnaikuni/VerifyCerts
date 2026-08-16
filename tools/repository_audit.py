#!/usr/bin/env python3
"""Reject credentials, personal records, and generated artifacts before publishing."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "database", "runtime"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pdf", ".pem", ".key", ".pfx", ".p12"}
TEXT_SUFFIXES = {"", ".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".svg", ".example", ".sol"}
SAFE_EMAIL_DOMAINS = {"example.com", "example.edu"}


def decoded(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


BANNED_TERMS = [
    decoded(value)
    for value in (
        "TGVuZGk=",
        "MjFLRA==",
        "TElFVA==",
    )
]
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
WINDOWS_PATH_RE = re.compile(r"(?i)(?:(?<![A-Za-z0-9])[a-z]:\\|\\\\[^\s\\]+\\[^\s\\]+)")
SECRET_RE = re.compile(
    r"(?i)\b(?:secret[_-]?key|api[_-]?key|password|private[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"']([^\"']{8,})[\"']"
)


def files():
    for current, directories, filenames in os.walk(ROOT):
        directories[:] = sorted(name for name in directories if name not in SKIP)
        for filename in sorted(filenames):
            yield Path(current) / filename


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for path in files():
        relative = path.relative_to(ROOT)
        scanned += 1
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"{relative}: generated or sensitive file type")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"requirements.txt", ".gitignore", ".dockerignore", "Dockerfile"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(content.splitlines(), start=1):
            folded = line.casefold()
            if path.resolve() != Path(__file__).resolve():
                if any(term.casefold() in folded for term in BANNED_TERMS):
                    findings.append(f"{relative}:{number}: private identifier")
                if WINDOWS_PATH_RE.search(line):
                    findings.append(f"{relative}:{number}: absolute Windows path")
            for email in EMAIL_RE.finditer(line):
                if email.group(1).lower() not in SAFE_EMAIL_DOMAINS:
                    findings.append(f"{relative}:{number}: non-example email")
            secret = SECRET_RE.search(line)
            if secret and not any(marker in folded for marker in ("test-only", "replace-with", "os.getenv", "getattr(settings", "validated_data", "request.data")):
                findings.append(f"{relative}:{number}: possible hard-coded secret")

    if findings:
        print("Repository audit: FAIL")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1
    print(f"Repository audit: PASS ({scanned} files, 0 findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
