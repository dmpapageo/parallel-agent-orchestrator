"""The sub-agent loop — one agent, one module.

gather context -> act -> verify -> repeat, until this module's tests are green
or a guard stops us. This is a hand-written agentic loop over the Anthropic
Messages API: we send messages, the model asks to use a tool, we execute it and
feed the result back, and we repeat. Every turn is printed so the cycle is
legible.

This is the single-agent loop, narrowed to one module. It knows nothing about
the orchestrator, other modules, or other agents — it is just a code fixer with
a small world. That ignorance is what makes it safe to run several at once.

Run one (inside the container) with:
    python -m agent.subagent --module pricing

Exit code: 0 if the module went green, 1 otherwise (the orchestrator reads this).
"""

import argparse
import os
import sys

from anthropic import Anthropic, DefaultHttpxClient

from . import guards, tools


# --- Configuration -----------------------------------------------------------
# Default model is claude-opus-4-8 (override with AGENT_MODEL for a cheaper run).
MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
MAX_TOKENS = 16000
# The iteration-cap guard. Lower it (AGENT_MAX_ITERATIONS) to see the cap fire fast.
MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "10"))

SYSTEM_PROMPT = """You are a coding agent whose one job is to make a failing \
pytest suite pass in a single small Python module.

You are assigned exactly ONE module and can only see and edit files inside it. \
Other modules exist but are none of your concern — they are being handled \
separately, and you cannot read or write them.

Work the loop: run the tests to see what fails, read the failing test and the \
code it exercises, make the smallest edit to the code that fixes the bug, then \
run the tests again to verify. Repeat until all tests pass.

Rules:
- Fix the CODE under test. Do NOT edit the test files to make them pass.
- Paths are relative to your module directory (e.g. "pricing.py").
- write_file overwrites the whole file, so include the full file contents.
- When all tests pass, stop and say so briefly."""


def _initial_task(scope: guards.ModuleScope) -> str:
    return (
        f"You are assigned the '{scope.module}' module. It contains: "
        f"{', '.join(scope.files())}. Its tests are currently failing. "
        f"Investigate and fix the code so that all of its tests pass. "
        f"Start by running the tests."
    )


# --- Printing helpers (make each turn visible) -------------------------------
# Every line is tagged with the module name, because in the next stage several
# of these run at once and their output interleaves on one terminal.

class Printer:
    def __init__(self, module: str):
        self.tag = f"[{module}]"

    def say(self, text: str):
        print(f"{self.tag} {text}", flush=True)

    def rule(self, label: str):
        print(f"{self.tag} {'=' * 8} {label} {'=' * 8}", flush=True)


def _summarize_tool_call(name, tool_input):
    if name == "read_file":
        return f"read_file(path={tool_input.get('path')!r})"
    if name == "write_file":
        content = tool_input.get("content", "")
        return f"write_file(path={tool_input.get('path')!r}, {len(content)} bytes)"
    if name == "run_tests":
        return "run_tests()"
    return f"{name}({tool_input})"


def _summarize_result(result):
    if result.test_result is not None:
        last = result.content.strip().splitlines()[-1] if result.content.strip() else ""
        verdict = "GREEN" if guards.is_green(result.test_result.returncode) else "RED"
        return f"[verify: {verdict}] {last}"
    text = result.content.strip().replace("\n", " ")
    if len(text) > 200:
        text = text[:200] + "…"
    return ("ERROR: " if result.is_error else "") + text


# --- The loop ----------------------------------------------------------------

def run(module: str) -> bool:
    """Fix one module. Returns True if it ended green."""
    # GUARD 2: the path allowlist, rooted at THIS module only. Built before
    # anything else so a bad --module fails on its own terms.
    scope = guards.ModuleScope(module)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set — pass it into the container.")

    out = Printer(module)
    out.say(f"pid={os.getpid()} scope={scope.root}")

    # The agent has no direct internet route; it must egress through the proxy.
    # Hand the proxy to the SDK explicitly instead of relying on httpx's implicit
    # env pickup. httpx CONNECT-tunnels HTTPS through it, so the PROXY resolves
    # api.anthropic.com — the agent only ever resolves the "proxy" container name.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        out.say(f"egress via proxy {proxy}")
        client = Anthropic(http_client=DefaultHttpxClient(proxy=proxy))
    else:
        client = Anthropic()

    iteration_guard = guards.IterationGuard(MAX_ITERATIONS)  # GUARD 1: iteration cap

    messages = [{"role": "user", "content": _initial_task(scope)}]

    while True:
        # GUARD 1 checkpoint: bail out if the agent has taken too many turns.
        try:
            turn = iteration_guard.tick()
        except guards.IterationLimitExceeded as exc:
            out.rule("STOPPED BY GUARD")
            out.say(str(exc))
            return False

        out.rule(f"TURN {turn}")

        # 1) Ask the model what to do next.
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools.TOOLS,
            messages=messages,
        )

        # Show any text the model wrote (its plan / conclusions).
        for block in response.content:
            if block.type == "text" and block.text.strip():
                out.say(f"[model] {block.text.strip()}")

        # Preserve the full assistant turn (tool_use blocks included) in history.
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # The model ended its turn without acting — nothing left to do.
            out.rule("DONE (model ended its turn)")
            return False

        # 2) Execute each tool the model requested, and 3) collect results.
        tool_results = []
        went_green = False
        for tool_use in tool_uses:
            out.say(f"[tool ] {_summarize_tool_call(tool_use.name, tool_use.input)}")
            # GUARD 2 lives inside dispatch, via the scope.
            result = tools.dispatch(scope, tool_use.name, tool_use.input)
            out.say(f"        -> {_summarize_result(result)}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result.content,
                "is_error": result.is_error,
            })

            # GUARD 3 checkpoint: stop-when-green.
            if result.test_result is not None and guards.is_green(
                result.test_result.returncode
            ):
                went_green = True

        # Feed all tool results back to the model as the next user turn.
        messages.append({"role": "user", "content": tool_results})

        if went_green:
            out.rule("SUCCESS — module tests pass")
            return True


def main():
    parser = argparse.ArgumentParser(
        description="Fix one module's failing tests."
    )
    parser.add_argument(
        "--module",
        required=True,
        help=f"Module to fix. Available: {', '.join(guards.available_modules())}",
    )
    args = parser.parse_args()

    try:
        went_green = run(args.module)
    except guards.PathGuardError as exc:
        sys.exit(f"ERROR: {exc}")

    sys.exit(0 if went_green else 1)


if __name__ == "__main__":
    main()
