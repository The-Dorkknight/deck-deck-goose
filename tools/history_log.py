#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Keeps docs/HISTORY.md: one entry per build with its main code changes.

Release zips only carry the last three builds of git history (to stay small), so this log is
where every older build stays on record. Run it before trimming history and on every build:
    python3 tools/history_log.py [repo-folder]
Entries already in the file are kept as they are (even when their commit is no longer in git);
commits not yet logged are added. Standard library only.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HEAD = """# Build history

Every build of this project, newest first, with the main code changes in each. Release zips keep
only the last three builds in git history, so older builds live on here (and in CHANGELOG.md).
Written by `tools/history_log.py`; entries are never removed.
"""
CODE = re.compile(r"^(src|lib|app|firmware|main|components)/|\.(py|sh|c|cpp|h|hpp|js|ts|kt|java|rs|go|ino)$")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=True).stdout


def entry(repo: Path, sha: str) -> tuple:
    subject, date, short = git(repo, "log", "-1", "--format=%s%x00%ad%x00%h", "--date=short", sha).strip().split("\0")
    stat = git(repo, "show", "--shortstat", "--format=", sha).strip()
    rows = []
    for line in git(repo, "show", "--numstat", "--format=", "-M", sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-":
            continue
        add, rem, path = int(parts[0]), int(parts[1]), parts[2]
        if CODE.search(path.split(" => ")[-1].strip("{}")):
            rows.append((add + rem, add, rem, path))
    rows.sort(reverse=True)
    key = subject.split(":")[0].strip()
    lines = ["## %s (%s)" % (subject, date), "", "- %s" % (stat or "no file changes")]
    if rows:
        lines.append("- Main code changes:")
        lines += ["  - `%s` +%d -%d" % (p, a, r) for _, a, r, p in rows[:10]]
        if len(rows) > 10:
            lines.append("  - ... and %d more code files" % (len(rows) - 10))
    return key, "\n".join(lines) + "\n"


def main() -> int:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out = repo / "docs" / "HISTORY.md"
    old = out.read_text() if out.exists() else HEAD
    blocks = re.split(r"(?m)^(?=## )", old)
    head, kept = blocks[0], blocks[1:]
    have = {re.match(r"## ([^:(]+)", b).group(1).strip() for b in kept if re.match(r"## ([^:(]+)", b)}
    new = []
    for sha in git(repo, "rev-list", "--reverse", "HEAD").split():
        key, text = entry(repo, sha)
        if key not in have:
            new.append(text)
            have.add(key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(head.rstrip() + "\n\n" + "\n".join(list(reversed(new)) + [b.rstrip() + "\n" for b in kept]))
    print("%s: %d new, %d kept" % (out, len(new), len(kept)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
