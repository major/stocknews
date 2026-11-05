"""Save surviving mutation diffs alongside the workflow's logs and statistics."""

# ruff: noqa: INP001 - This is a standalone workflow script, not a package.

import subprocess
from pathlib import Path


def main() -> None:
    """Export survivor entries serially, retaining diagnostics if a command fails."""
    results = Path("mutmut-results.txt").read_text()
    entries = (line.strip().partition(": ") for line in results.splitlines())
    survivors = [name for name, _, status in entries if status == "survived"]

    with Path("mutmut-survivor-diffs.txt").open("w") as report:
        if not survivors:
            report.write("No survivor entries found.\n")
        for name in survivors:
            subprocess.run(  # noqa: S603 - Mutant names come from mutmut, and no shell is used.
                ["uv", "run", "--locked", "mutmut", "show", name],  # noqa: S607 - CI installs uv on PATH.
                check=True,
                stdout=report,
                stderr=subprocess.STDOUT,
            )


if __name__ == "__main__":
    main()
