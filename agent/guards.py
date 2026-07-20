"""Guardrails — a sub-agent's safety boundaries.

Guards are deliberately separate from the tools and the loop: the tools ask the
guards for permission, and the loop asks the guards when to stop. Every safety
decision lives here, in one place you can read top to bottom.

The one difference from the single-agent version is ModuleScope. There, the
agent's world was the whole target repo. Here, each sub-agent's world is ONE
module directory inside it — which is what lets several agents run at once
without being able to touch each other's work.
"""

import os
from pathlib import Path


class PathGuardError(Exception):
    """Raised when a file operation tries to touch something outside the module."""


class IterationLimitExceeded(Exception):
    """Raised when a sub-agent has taken too many turns without going green."""


# The repo holding all the modules. guards.py lives in agent/, so it's ../target_repo.
TARGET_REPO = (Path(__file__).resolve().parent.parent / "target_repo").resolve()


def available_modules() -> list[str]:
    """Every module directory in the target repo, sorted.

    A "module" is just a subdirectory. The orchestrator will reuse this in the
    next stage to decide what work exists.
    """
    return sorted(p.name for p in TARGET_REPO.iterdir() if p.is_dir())


class ModuleScope:
    """One sub-agent's entire writable world: target_repo/<module>/.

    GUARD 2 (the path allowlist) lives here. It is a *containment* check, not a
    string check: every requested path is joined onto this module's root and
    then fully resolved — following `..` segments and symlinks — and the result
    must still land inside that root. Because the check runs on the resolved
    path, the usual escapes all fail:

      "../inventory/inventory.py"  -> resolves out of the module -> rejected
      "/etc/passwd"                -> an absolute path joins to ITSELF, landing
                                      outside the root -> rejected
      a symlink pointing elsewhere -> resolve() follows it, so the real
                                      destination is what gets checked

    The root is resolved once, at construction, so it cannot be moved later.
    """

    def __init__(self, module: str):
        # The module name itself must be a plain directory name — no separators,
        # no "..". This is checked before touching the filesystem so a bad
        # --module argument fails immediately and obviously.
        if not module or module != Path(module).name or module in (".", ".."):
            raise PathGuardError(f"not a plain module name: {module!r}")

        root = (TARGET_REPO / module).resolve()
        if not root.is_relative_to(TARGET_REPO) or root == TARGET_REPO:
            raise PathGuardError(f"module escapes target_repo/: {module!r}")
        if not root.is_dir():
            raise PathGuardError(
                f"no such module: {module!r} (available: {', '.join(available_modules())})"
            )

        self.module = module
        self.root = root

    def resolve(self, relative_path: str) -> Path:
        """Resolve `relative_path` and guarantee it stays inside this module."""
        if not relative_path or not relative_path.strip():
            raise PathGuardError("empty path")

        candidate = (self.root / relative_path).resolve()

        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise PathGuardError(
                f"path escapes {self.module}/: {relative_path!r} -> {candidate}"
            )
        return candidate

    def files(self) -> list[str]:
        """The Python files in this module, for the sub-agent's opening context."""
        return sorted(p.name for p in self.root.glob("*.py"))


class IterationGuard:
    """Hard cap on how many agent turns may run.

    Call `tick()` once at the top of each iteration. When the cap is passed it
    raises IterationLimitExceeded, so a stuck or looping agent always halts.
    """

    def __init__(self, max_iterations: int):
        self.max_iterations = max_iterations
        self.count = 0

    def tick(self) -> int:
        self.count += 1
        if self.count > self.max_iterations:
            raise IterationLimitExceeded(
                f"exceeded max_iterations={self.max_iterations}"
            )
        return self.count


# pytest exit code 0 means every collected test passed. That is the ONLY
# condition we treat as success ("green").
PYTEST_ALL_PASSED = 0


def is_green(pytest_returncode: int) -> bool:
    """The stop-when-green success check: True only if all tests passed."""
    return pytest_returncode == PYTEST_ALL_PASSED
