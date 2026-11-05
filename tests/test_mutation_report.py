"""Tests for the survivor diffs saved by the mutation workflow."""

import runpy
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from typing import TextIO

REPORT_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "mutation_report.py"
FIRST_MUTANT = "stocknews.earnings.x__structured__mutmut_9"
SECOND_MUTANT = "stocknews.earnings.x__field_result__mutmut_64"


@pytest.fixture
def report_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run the report from an isolated workflow working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def show_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace only the external mutation command and record its arguments."""
    calls: list[str] = []

    def show(command: list[str], *, check: bool, stdout: TextIO, stderr: int) -> None:
        """Write a recognizable diff without starting mutation tooling."""
        assert command[:-1] == ["uv", "run", "--locked", "mutmut", "show"]
        assert check
        assert stderr == subprocess.STDOUT
        calls.append(command[-1])
        stdout.write(f"# {command[-1]}\n- original\n+ mutated\n")

    monkeypatch.setattr(subprocess, "run", show)
    return calls


@pytest.mark.parametrize(
    ("results", "survivors"),
    [
        ("", []),
        (f"Results collection failed\n    {FIRST_MUTANT}: killed\n", []),
        (f"    {FIRST_MUTANT}: survived\n", [FIRST_MUTANT]),
        (
            (
                f"    ignored: killed\n    {FIRST_MUTANT}: survived\n"
                f"    ignored: no tests\n    {SECOND_MUTANT}: survived\n"
            ),
            [FIRST_MUTANT, SECOND_MUTANT],
        ),
    ],
)
def test_report_exports_only_survivor_diffs(
    report_directory: Path,
    show_calls: list[str],
    results: str,
    survivors: list[str],
) -> None:
    """Export every survivor in order without claiming collection succeeded."""
    (report_directory / "mutmut-results.txt").write_text(results)

    runpy.run_path(str(REPORT_SCRIPT), run_name="__main__")

    assert show_calls == survivors
    expected = "".join(f"# {name}\n- original\n+ mutated\n" for name in survivors)
    assert (report_directory / "mutmut-survivor-diffs.txt").read_text() == (expected or "No survivor entries found.\n")


def test_report_keeps_failed_command_diagnostics(
    report_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep partial output and fail the reporting step when a diff cannot be read."""
    (report_directory / "mutmut-results.txt").write_text(f"    {FIRST_MUTANT}: survived\n")

    def fail(command: list[str], *, check: bool, stdout: TextIO, stderr: int) -> None:
        """Simulate an external command that writes its error before failing."""
        stdout.write("Unable to read mutant\n")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(subprocess.CalledProcessError):
        runpy.run_path(str(REPORT_SCRIPT), run_name="__main__")

    assert (report_directory / "mutmut-survivor-diffs.txt").read_text() == "Unable to read mutant\n"


def test_report_requires_results(report_directory: Path, show_calls: list[str]) -> None:
    """Fail on a missing results file instead of creating a misleading report."""
    with pytest.raises(FileNotFoundError, match=r"mutmut-results\.txt"):
        runpy.run_path(str(REPORT_SCRIPT), run_name="__main__")

    assert not show_calls
    assert not (report_directory / "mutmut-survivor-diffs.txt").exists()
