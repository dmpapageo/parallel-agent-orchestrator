"""Presentation: turn the orchestrator's raw results into something readable.

Everything here is display only. No decisions are made in this file, and
nothing it prints is used to determine success. The orchestrator's verdicts
come from exit codes (see run.py); this module just draws them.

Keeping it separate means the interesting logic in run.py stays short, and the
formatting can change without any risk to correctness.
"""

import os

BAR_WIDTH = 40


def rule(label: str):
    print(f"\n{'=' * 20} {label} {'=' * 20}", flush=True)


def discovery_table(statuses):
    rule("DISCOVER")
    for status in statuses:
        mark = "FAIL" if status.failing else "ok  "
        print(f"  [{mark}] {status.module:<12} {status.summary}", flush=True)


def assignment_plan(to_fix, skipped):
    rule("ASSIGN")
    for module in to_fix:
        print(f"  {module} -> sub-agent", flush=True)
    if skipped:
        print(f"  (no agent assigned: {', '.join(skipped)})", flush=True)


def timeline(assignments):
    """A small Gantt chart of the run.

    Every bar starts at column zero because every sub-agent was started before
    any of them was waited on. Seeing the bars line up on the left is the point:
    that overlap is the parallelism, and the longest bar is what the whole run
    costs.
    """
    rule("TIMELINE")

    slowest = max(a.finished_at for a in assignments)
    if slowest <= 0:
        return
    seconds_per_char = slowest / BAR_WIDTH

    print(f"  each bar character is about {seconds_per_char:.1f}s\n", flush=True)

    # Longest first, so the run's critical path is the top line.
    for assignment in sorted(assignments, key=lambda a: -a.finished_at):
        filled = max(1, round(assignment.finished_at / seconds_per_char))
        bar = "#" * filled
        verdict = "GREEN" if assignment.went_green else "NOT FIXED"
        print(
            f"  {assignment.module:<12} |{bar:<{BAR_WIDTH}}| "
            f"{assignment.finished_at:5.1f}s  {verdict}",
            flush=True,
        )

    print("\n  all agents started at 0.0s. longest agent set the total.", flush=True)


def summary(assignments, wall_elapsed):
    rule("SUMMARY")

    for assignment in assignments:
        verdict = "GREEN" if assignment.went_green else "NOT FIXED"
        print(
            f"  {assignment.module:<12} {verdict:<10} "
            f"pid={assignment.pid:<7} exit={assignment.returncode}",
            flush=True,
        )

    fixed = sum(1 for a in assignments if a.went_green)
    total = len(assignments)
    sum_of_runtimes = sum(a.finished_at for a in assignments)

    print(f"\n  modules fixed     : {fixed}/{total}", flush=True)
    print(f"  wall clock        : {wall_elapsed:5.1f}s", flush=True)
    print(
        f"  sum of runtimes   : {sum_of_runtimes:5.1f}s "
        f"(roughly what running them one at a time would cost)",
        flush=True,
    )

    if wall_elapsed > 0:
        speedup = sum_of_runtimes / wall_elapsed
        print(f"  speedup           : {speedup:.1f}x", flush=True)

    print(
        "\n  The run costs as much as the SLOWEST agent, not the sum of all of\n"
        "  them, because the modules are independent and were worked on at the\n"
        "  same time in separate processes.",
        flush=True,
    )


def agent_logs(assignments):
    """Dump each sub-agent's full transcript, grouped by agent (not interleaved)."""
    for assignment in assignments:
        if not assignment.log_path:
            continue
        rule(f"LOG: {assignment.module}")
        try:
            with open(assignment.log_path, encoding="utf-8") as handle:
                print(handle.read().rstrip(), flush=True)
        except OSError as exc:
            print(f"  (could not read log: {exc})", flush=True)


def log_location(assignments):
    paths = [a.log_path for a in assignments if a.log_path]
    if paths:
        print(
            f"\n  per-agent logs: {os.path.dirname(paths[0])}"
            f"  (re-run with --logs to print them)",
            flush=True,
        )
