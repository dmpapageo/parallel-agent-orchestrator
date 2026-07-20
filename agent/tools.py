"""Tools — a sub-agent's entire set of actions on the world.

The model can do exactly three things: read a file, write a file, and run the
tests. Nothing else. There is no shell tool, no network tool, no "run arbitrary
code" tool. Reads and writes are confined to the sub-agent's OWN module by the
ModuleScope guard, and run_tests only ever runs that module's tests.

`TOOLS` is the schema the model sees. `dispatch()` is how the loop actually
executes a tool the model asked for.
"""

import subprocess
import sys
from dataclasses import dataclass

from . import guards


# ---- What the model sees -----------------------------------------------------
# Each entry is an Anthropic tool definition: name, description, and a JSON
# Schema for its input. The model picks a tool by name and fills in the schema.
# Note the paths are relative to the MODULE, not the repo — a sub-agent has no
# vocabulary for talking about files outside its own module.

TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from your assigned module. "
            "Use this to see the failing test and the code under test. "
            "Paths are relative to your module directory (e.g. 'pricing.py')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to your module directory.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Overwrite a text file in your assigned module with new contents. "
            "Writes the ENTIRE file, so include the full intended contents. "
            "Only paths inside your own module are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to your module directory.",
                },
                "content": {
                    "type": "string",
                    "description": "The complete new contents of the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the pytest suite for your assigned module and return its output. "
            "Use this to verify whether the tests pass after an edit. "
            "Takes no arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ---- Results the loop consumes ----------------------------------------------

@dataclass
class TestResult:
    returncode: int
    output: str


@dataclass
class ToolResult:
    content: str                    # the text handed back to the model
    is_error: bool = False          # marks a tool_result as an error for the model
    test_result: "TestResult | None" = None  # set only by run_tests


# ---- The tools themselves ----------------------------------------------------

def _read_file(scope: guards.ModuleScope, path: str) -> str:
    target = scope.resolve(path)
    if not target.is_file():
        raise FileNotFoundError(f"no such file in {scope.module}/: {path!r}")
    return target.read_text(encoding="utf-8")


def _write_file(scope: guards.ModuleScope, path: str, content: str) -> str:
    target = scope.resolve(path)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


def _run_tests(scope: guards.ModuleScope) -> TestResult:
    # cwd is the module directory, so pytest collects ONLY this module's tests
    # (equivalent to `pytest target_repo/<module>/`). A sub-agent therefore
    # verifies its own fix independently and is never shown, or blamed for,
    # another module's failures.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v"],
        cwd=scope.root,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    return TestResult(returncode=proc.returncode, output=output)


# ---- Dispatch: the single door from the loop to the tools --------------------

def dispatch(scope: guards.ModuleScope, name: str, tool_input: dict) -> ToolResult:
    """Execute the tool the model asked for and return a ToolResult.

    Guard violations (e.g. a path outside the module) are returned as an error
    ToolResult rather than raised, so the loop can hand the message back to the
    model and let it correct course — while the offending action never happens.
    """
    try:
        if name == "read_file":
            return ToolResult(content=_read_file(scope, tool_input["path"]))
        if name == "write_file":
            return ToolResult(
                content=_write_file(scope, tool_input["path"], tool_input["content"])
            )
        if name == "run_tests":
            result = _run_tests(scope)
            return ToolResult(content=result.output, test_result=result)
        return ToolResult(content=f"unknown tool: {name}", is_error=True)
    except (guards.PathGuardError, FileNotFoundError, KeyError) as exc:
        return ToolResult(content=f"ERROR: {exc}", is_error=True)
