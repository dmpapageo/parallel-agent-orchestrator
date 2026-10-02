"""The orchestrator: discover failing modules, assign a sub-agent to each.

The whole job is four steps:

    1. DISCOVER  which modules exist and which have failing tests
    2. ASSIGN    one sub-agent per FAILING module (healthy modules get nobody)
    3. RUN       all sub-agents at once, each as its own process
    4. COLLECT   each sub-agent's verdict from its exit code

The orchestrator never edits code and has no tools of its own. It decides WHO
works on WHAT, and reads results. All editing happens inside sub-agents, each
confined to its own module by ModuleScope.

Run it (inside the container) with:  python -m orchestrator.run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agent import guards
from . import discover, report


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Assignment:
    """One module handed to one sub-agent, and how that turned out."""

    module: str
    returncode: int | None = None     # the sub-agent process's exit code
    pid: int | None = None            # OS pid, proof these are separate processes
    finished_at: float | None = None  # seconds from batch start to this agent's exit
    log_path: str | None = None       # captured transcript, when not streaming
    usage: dict | None = None         # turns / input_tokens / output_tokens, if reported

    @property
    def went_green(self) -> bool:
        # The sub-agent exits 0 only if its module's tests pass. Same contract
        # as discovery: we read an exit code, we do not parse output.
        return self.returncode == 0


def start_assignment(assignment: Assignment, log_dir: str | None) -> subprocess.Popen:
    """Start ONE sub-agent as its own process and return IMMEDIATELY.

    This is the blocking-free version of a normal subprocess call: Popen instead
    of subprocess.run. That single difference is what makes concurrency
    possible, because the parent gets a handle back instead of an exit code.

    The sub-agent is spawned by CLI exactly as a human would run it. The
    environment is INHERITED, which is how ANTHROPIC_API_KEY and the proxy
    settings reach it. Nothing is hardcoded here, and the key is never read,
    logged, or passed explicitly by the orchestrator.

    With log_dir set, the child's output goes to its own FILE rather than to our
    stdout. Writing to separate files (instead of reading pipes) means the
    parent never has to drain anything, so a chatty agent can never fill a pipe
    buffer and block itself while we are busy waiting on someone else.
    """
    command = [sys.executable, "-m", "agent.subagent", "--module", assignment.module]

    if log_dir is None:
        proc = subprocess.Popen(command, cwd=REPO_ROOT, env=os.environ.copy())
    else:
        assignment.log_path = os.path.join(log_dir, f"{assignment.module}.log")
        handle = open(assignment.log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        proc._log_handle = handle  # closed when the process is reaped

    assignment.pid = proc.pid
    return proc


def run_assignments(assignments: list[Assignment], log_dir: str | None) -> list[Assignment]:
    """Start every sub-agent, then wait for all of them.

    Two phases, and the separation is the whole point:

      START  every sub-agent is launched before any of them is waited on, so
             they overlap. Waiting inside the start loop would just be a
             sequential run with extra steps.
      WAIT   poll until each process exits, recording its code and finish time.

    WHY THERE ARE NO RACES
    These are separate OS PROCESSES, not threads, so there is no shared memory
    to corrupt in the first place. Beyond that:

      - Each sub-agent's write_file calls land only inside its own module
        directory, enforced by ModuleScope. Those writable sets are disjoint,
        so concurrent write_file edits cannot collide. This is the payoff for
        making the modules independent. (Code an agent runs through run_tests
        is not path guarded; it can write anywhere the `agent` user can,
        which includes every module in target_repo/.)
      - Each runs pytest with cwd set to its own module, so even the
        .pytest_cache directories are separate. No file is written by two agents.
      - A child's only channel back to the parent is its exit code, which the OS
        delivers exactly once, to us alone.
      - Every mutation of an Assignment happens HERE, in the parent, in one
        thread. The children never touch these objects. Results are matched to
        modules by holding the (assignment, process) pair together, so a result
        cannot be attributed to the wrong module no matter what order they
        finish in.
    """
    report.rule(f"RUN {len(assignments)} SUB-AGENTS CONCURRENTLY")
    started = time.monotonic()

    # PHASE 1: start them all.
    running = [(a, start_assignment(a, log_dir)) for a in assignments]
    for assignment, _ in running:
        print(f"  started  {assignment.module:<12} pid={assignment.pid}", flush=True)
    print(flush=True)

    # PHASE 2: wait for all. Poll rather than wait() in list order, so each
    # agent's finish time is when it ACTUALLY exited, not when we got around to
    # noticing. The timeline is only honest if these numbers are.
    while running:
        still_running = []
        for assignment, proc in running:
            returncode = proc.poll()
            if returncode is None:
                still_running.append((assignment, proc))
                continue

            assignment.returncode = returncode
            assignment.finished_at = time.monotonic() - started
            handle = getattr(proc, "_log_handle", None)
            if handle:
                handle.close()

            verdict = "GREEN" if assignment.went_green else "NOT FIXED"
            print(
                f"  finished {assignment.module:<12} "
                f"{verdict:<10} after {assignment.finished_at:5.1f}s",
                flush=True,
            )

        running = still_running
        if running:
            time.sleep(0.1)

    return assignments


def collect_usage(assignments: list[Assignment], usage_dir: str):
    """Read each sub-agent's usage file, if it left one. Missing is fine."""
    for assignment in assignments:
        path = os.path.join(usage_dir, f"{assignment.module}.json")
        try:
            with open(path, encoding="utf-8") as handle:
                assignment.usage = json.load(handle)
        except (OSError, ValueError):
            assignment.usage = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fix every failing module by assigning one sub-agent to each."
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="stream sub-agent output live. Shows the real interleaving of "
             "concurrent agents, at the cost of readability.",
    )
    parser.add_argument(
        "--logs",
        action="store_true",
        help="after the summary, print each sub-agent's full transcript, "
             "grouped by agent instead of interleaved.",
    )
    args = parser.parse_args(argv)

    # 1) DISCOVER
    statuses = discover.discover()
    report.discovery_table(statuses)

    # 2) ASSIGN. Only failing modules get a sub-agent: a healthy module is not
    #    work, and handing it to an agent would burn tokens to change nothing.
    to_fix = discover.failing_modules(statuses)
    skipped = [s.module for s in statuses if not s.failing]

    if not to_fix:
        report.rule("ASSIGN")
        print("  nothing failing, no sub-agents needed", flush=True)
        return 0

    report.assignment_plan(to_fix, skipped)
    assignments = [Assignment(module=m) for m in to_fix]

    # 3) RUN, concurrently.
    log_dir = None if args.stream else tempfile.mkdtemp(prefix="subagent-logs-")
    usage_dir = tempfile.mkdtemp(prefix="subagent-usage-")
    os.environ["AGENT_USAGE_DIR"] = usage_dir  # inherited by every sub-agent
    wall_start = time.monotonic()
    run_assignments(assignments, log_dir)
    wall_elapsed = time.monotonic() - wall_start

    # 4) COLLECT and present.
    collect_usage(assignments, usage_dir)
    report.timeline(assignments)
    report.summary(assignments, wall_elapsed)

    if args.logs:
        report.agent_logs(assignments)
    elif log_dir:
        report.log_location(assignments)

    # Non-zero if any assigned module did not go green.
    return 0 if all(a.went_green for a in assignments) else 1


if __name__ == "__main__":
    sys.exit(main())
