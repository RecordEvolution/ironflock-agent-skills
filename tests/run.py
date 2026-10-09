#!/usr/bin/env python3
"""Regression tests for the skill's validator.

    python3 tests/run.py

For the skill's references/example-board.yml and every tests/boards/<name>.yml:
copy tests/app (an app with a data-template.yml) to a temporary folder, add the
board as board-template.yml, run scripts/validate.py and compare its findings:

- the example board must produce no findings at all;
- every other board must produce each finding its `# expect:` lines list, as
  `# expect: LEVEL <exact path> :: <part of the message>`.

Needs network access for the widget schemas (cached after the first run).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = ROOT.parent / "plugins/ironflock/skills/ironflock-templates"
VALIDATE = SKILL / "scripts/validate.py"
EXAMPLE = SKILL / "references/example-board.yml"
FINDING = re.compile(r"^(ERROR|WARN)\s+(\S+) (\S+): (.*)$")
EXPECT = re.compile(r"^# expect:\s*(ERROR|WARN)\s+(\S+)\s+::\s+(.*)$")


def run_board(board: Path) -> list[tuple[str, str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app"
        shutil.copytree(ROOT / "app", app)
        shutil.copy(board, app / ".ironflock" / "board-template.yml")
        done = subprocess.run([sys.executable, str(VALIDATE), str(app)], capture_output=True, text=True)
    if done.returncode not in (0, 1):
        sys.exit(f"validate.py crashed on {board.name}:\n{done.stdout}{done.stderr}")
    return [(m[1], m[3], m[4]) for m in map(FINDING.match, done.stdout.splitlines()) if m]


def main() -> int:
    failed = 0
    for board in [EXAMPLE, *sorted((ROOT / "boards").glob("*.yml"))]:
        found = run_board(board)
        expected = [m.groups() for m in map(EXPECT.match, board.read_text().splitlines()) if m]
        if board == EXAMPLE:
            problems = [f"unexpected {lvl} {path}: {msg}" for lvl, path, msg in found]
        else:
            problems = [
                f"missing {lvl} {path} :: {text}"
                for lvl, path, text in expected
                if not any(f[0] == lvl and f[1] == path and text in f[2] for f in found)
            ]
        status = "ok  " if not problems else "FAIL"
        print(f"{status} {board.name}: {len(found)} finding(s), {len(expected)} expected")
        for problem in problems:
            print(f"     {problem}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
