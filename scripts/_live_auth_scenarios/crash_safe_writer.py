"""Hammer the canonical storage writer so the parent can kill it mid-write.

Unlike the other cells this one prints nothing and is *expected* to be killed:
``Matrix.phase_crash_safety`` starts it, sleeps briefly, sends ``SIGKILL``, and
then re-reads the target file. The file must always parse and must still hold
the required Google session cookies, proving ``replace_from_login`` never leaves
a torn canonical write behind.

Usage: ``python -m scripts._live_auth_scenarios.crash_safe_writer <target> <source>``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from notebooklm._auth.storage import replace_from_login

#: Number of back-to-back canonical writes attempted before exiting normally.
WRITE_ITERATIONS = 10000


def main(argv: list[str] | None = None) -> None:
    """Rewrite ``argv[0]`` from the storage state in ``argv[1]``, repeatedly."""
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0])
    state = json.loads(Path(args[1]).read_text())
    for _ in range(WRITE_ITERATIONS):
        replace_from_login(target, state, include_domains=None)


if __name__ == "__main__":
    main()
