"""Discovery — what units of work exist, and which ones need fixing.

This is the reusability seam. The orchestrator itself never mentions pytest or
Python modules; it just asks this file two questions:

    what work is there?      -> guards.available_modules()
    which of it is broken?   -> module_status() per module

Point these two answers at a different kind of task (a different repo, a
different test runner) and the assignment/parallelism logic above is unchanged.

HOW WE DECIDE A MODULE IS FAILING
We run each module's tests as its own pytest process and read the EXIT CODE —
we never parse pytest's output to find failures. Parsing the summary text would
work, but it couples discovery to pytest's formatting. Exit codes are the
contract pytest actually promises, and this reuses the very same is_green()
check the sub-agent uses to decide it's done. Discovery and verification
therefore agree by construction, rather than by two similar-looking rules that
could drift apart.

The cost is one pytest process per module instead of one for the whole suite.
For a handful of fast modules that is nothing, and it buys independence:
a module that fails to even import cannot poison another module's result.
"""

import subprocess
import sys
from dataclasses import dataclass

from agent import guards


@dataclass
class ModuleStatus:
    """The result of running one module's tests."""

    module: str
    returncode: int
    summary: str        # the one-line pytest tally, e.g. "1 failed, 4 passed"

    @property
    def failing(self) -> bool:
        return not guards.is_green(self.returncode)


def _summary_line(output: str) -> str:
    """Pull pytest's tally line out of its output, for display only.

    Never used to decide pass/fail — that is the exit code's job. If the format
    changes, we show a less pretty line and nothing else breaks.
    """
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip("= \t")
        if "passed" in stripped or "failed" in stripped or "error" in stripped:
            return stripped
    return "(no test summary)"


def module_status(module: str) -> ModuleStatus:
    """Run one module's tests and report how it did."""
    scope = guards.ModuleScope(module)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=scope.root,
        capture_output=True,
        text=True,
    )
    return ModuleStatus(
        module=module,
        returncode=proc.returncode,
        summary=_summary_line(proc.stdout + proc.stderr),
    )


def discover() -> list[ModuleStatus]:
    """Run every module's tests and report the state of each."""
    return [module_status(m) for m in guards.available_modules()]


def failing_modules(statuses: list[ModuleStatus]) -> list[str]:
    """The subset of modules that need a sub-agent assigned."""
    return [s.module for s in statuses if s.failing]
