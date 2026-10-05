from __future__ import annotations

import subprocess
import sys

from ._logger import setup_script_logging


def run_nuitka(arguments: list[str] | None = None, *, skip_setup_logging: bool = False) -> None:
    if not skip_setup_logging:
        setup_script_logging()

    command = [sys.executable, '-m', 'nuitka', *(arguments or [])]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    run_nuitka()


if __name__ == '__main__':
    main()
