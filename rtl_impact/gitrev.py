"""Read files and diffs out of a git repository without touching the working tree."""
from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile


def git(repo: str | pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def file_at(repo, commit: str, path: str, dest_dir: str | pathlib.Path) -> pathlib.Path:
    text = git(repo, "show", f"{commit}:{path}")
    sub = pathlib.Path(dest_dir) / commit.replace("^", "_parent").replace("~", "_t")
    sub.mkdir(parents=True, exist_ok=True)
    dest = sub / pathlib.Path(path).name   # keep the real file name so Yosys src annotations read "picorv32.v:LINE"
    dest.write_text(text)
    return dest


def changed_lines(repo, commit: str, path: str) -> set[int]:
    """Line numbers in the *new* version touched by the commit's diff of `path`."""
    diff = git(repo, "diff", "--unified=0", f"{commit}^", commit, "--", path)
    lines: set[int] = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


def subject(repo, commit: str) -> str:
    return git(repo, "log", "-1", "--format=%s", commit).strip()


def short(repo, commit: str) -> str:
    return git(repo, "rev-parse", "--short", commit).strip()


def scratch_dir() -> str:
    return tempfile.mkdtemp(prefix="rtl-impact-")
