"""Automated checks for the three guards and the egress config.

These run without an API key or Docker: they exercise the guard code directly
and assert on the committed proxy / compose / Dockerfile so the containment
story is verified on every push, not just by hand.

The one thing not covered here is the live network test (that the proxy really
refuses example.com) — that needs the containers up; see the README.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent import guards, tools

REPO = Path(__file__).resolve().parent.parent


# --- Fixture: a throwaway target_repo so tests never touch the real one --------

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A temp target_repo with two modules; ModuleScope is rooted here."""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.py").write_text("X = 1\n")
    (tmp_path / "outside.txt").write_text("not yours\n")
    monkeypatch.setattr(guards, "TARGET_REPO", tmp_path)
    return tmp_path


# --- GUARD 2: ModuleScope (path allowlist) -------------------------------------

@pytest.mark.parametrize("bad", ["", ".", "..", "../alpha", "alpha/../beta", "a/b", "/alpha"])
def test_module_name_must_be_plain(fake_repo, bad):
    with pytest.raises(guards.PathGuardError):
        guards.ModuleScope(bad)


def test_unknown_module_rejected(fake_repo):
    with pytest.raises(guards.PathGuardError, match="no such module"):
        guards.ModuleScope("gamma")


def test_scope_root_is_the_module_dir(fake_repo):
    scope = guards.ModuleScope("alpha")
    assert scope.root == (fake_repo / "alpha").resolve()
    assert scope.files() == ["alpha.py"]


@pytest.mark.parametrize("ok", ["alpha.py", "sub/new.py", "./alpha.py", "."])
def test_paths_inside_module_resolve(fake_repo, ok):
    scope = guards.ModuleScope("alpha")
    resolved = scope.resolve(ok)
    assert resolved == scope.root or resolved.is_relative_to(scope.root)


@pytest.mark.parametrize(
    "escape",
    [
        "../beta/beta.py",          # sibling module via ..
        "../outside.txt",           # repo root via ..
        "sub/../../beta/beta.py",   # .. buried mid-path
        "/etc/hosts",               # absolute path joins to itself
        str(REPO / "agent" / "guards.py"),  # absolute path to the agent's own code
        "",                         # empty
        "   ",                      # whitespace
    ],
)
def test_paths_outside_module_rejected(fake_repo, escape):
    scope = guards.ModuleScope("alpha")
    with pytest.raises(guards.PathGuardError):
        scope.resolve(escape)


def test_symlink_pointing_outside_is_followed_and_rejected(fake_repo):
    link = fake_repo / "alpha" / "sneaky.py"
    link.symlink_to(fake_repo / "beta" / "beta.py")
    scope = guards.ModuleScope("alpha")
    with pytest.raises(guards.PathGuardError):
        scope.resolve("sneaky.py")


# --- The tools honour the scope, and the escape never happens ------------------

def test_write_outside_module_is_error_and_writes_nothing(fake_repo):
    scope = guards.ModuleScope("alpha")
    before = (fake_repo / "beta" / "beta.py").read_text()

    result = tools.dispatch(scope, "write_file", {"path": "../beta/beta.py", "content": "pwned"})

    assert result.is_error
    assert "escapes" in result.content
    assert (fake_repo / "beta" / "beta.py").read_text() == before
    assert not (fake_repo / "pwned").exists()


def test_read_outside_module_is_error(fake_repo):
    scope = guards.ModuleScope("alpha")
    result = tools.dispatch(scope, "read_file", {"path": "../outside.txt"})
    assert result.is_error
    assert "not yours" not in result.content


def test_write_inside_module_works(fake_repo):
    scope = guards.ModuleScope("alpha")
    result = tools.dispatch(scope, "write_file", {"path": "alpha.py", "content": "X = 2\n"})
    assert not result.is_error
    assert (fake_repo / "alpha" / "alpha.py").read_text() == "X = 2\n"


def test_unknown_tool_is_error_not_exception(fake_repo):
    scope = guards.ModuleScope("alpha")
    result = tools.dispatch(scope, "run_shell", {"cmd": "rm -rf /"})
    assert result.is_error


def test_tool_surface_is_exactly_read_write_run_tests():
    names = sorted(t["name"] for t in tools.TOOLS)
    assert names == ["read_file", "run_tests", "write_file"]


def test_run_tests_only_collects_own_module(fake_repo):
    # alpha has a passing test; beta has a failing one. alpha's run_tests must be
    # green — it must not see beta's failure.
    (fake_repo / "alpha" / "test_alpha.py").write_text("def test_ok():\n    assert True\n")
    (fake_repo / "beta" / "test_beta.py").write_text("def test_bad():\n    assert False\n")
    scope = guards.ModuleScope("alpha")

    result = tools.dispatch(scope, "run_tests", {})

    assert result.test_result is not None
    assert guards.is_green(result.test_result.returncode)
    assert "test_beta" not in result.content


# --- GUARD 1: iteration cap ------------------------------------------------------

def test_iteration_guard_allows_exactly_max_ticks():
    guard = guards.IterationGuard(2)
    assert guard.tick() == 1
    assert guard.tick() == 2
    with pytest.raises(guards.IterationLimitExceeded):
        guard.tick()


def test_iteration_guard_zero_stops_before_any_work():
    with pytest.raises(guards.IterationLimitExceeded):
        guards.IterationGuard(0).tick()


def test_subagent_cap_fires_without_any_api_call(fake_repo, monkeypatch):
    # AGENT_MAX_ITERATIONS=0 must stop the loop before the model is ever called.
    # A bogus key proves it: if a request were made it would fail loudly.
    env = dict(
        os.environ,
        AGENT_MAX_ITERATIONS="0",
        ANTHROPIC_API_KEY="not-a-real-key",
        AGENT_USAGE_DIR=str(fake_repo / "usage"),
    )
    env.pop("HTTPS_PROXY", None)
    env.pop("HTTP_PROXY", None)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from agent import guards, subagent; import sys\n"
         f"guards.TARGET_REPO = __import__('pathlib').Path({str(fake_repo)!r})\n"
         "sys.exit(0 if subagent.run('alpha') else 1)"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert "STOPPED BY GUARD" in proc.stdout
    assert "exceeded max_iterations=0" in proc.stdout
    usage_file = fake_repo / "usage" / "alpha.json"
    assert usage_file.exists()
    assert '"input_tokens": 0' in usage_file.read_text()


# --- GUARD 3: stop-when-green ------------------------------------------------------

@pytest.mark.parametrize("code,green", [(0, True), (1, False), (2, False), (5, False)])
def test_only_exit_zero_is_green(code, green):
    assert guards.is_green(code) is green


# --- Egress boundary: the committed config says what the README says --------------

def test_squid_allowlist_is_exactly_anthropic():
    conf = (REPO / "proxy" / "squid.conf").read_text()
    active = [l.strip() for l in conf.splitlines() if l.strip() and not l.strip().startswith("#")]
    allow_acls = [l for l in active if l.startswith("acl allowed_domains")]
    assert allow_acls == ["acl allowed_domains dstdomain api.anthropic.com"]
    assert "acl SSL_ports port 443" in active
    assert "http_access deny CONNECT !SSL_ports" in active
    assert "http_access allow allowed_domains" in active
    assert active[-1 - active[::-1].index("http_access deny all")] == "http_access deny all"
    # deny-all must come after the allow, so it is the default not an override
    assert active.index("http_access allow allowed_domains") < active.index("http_access deny all")


def test_compose_orchestrator_has_no_internet_route():
    compose = (REPO / "docker-compose.yml").read_text()
    active = "\n".join(l for l in compose.splitlines() if not l.strip().startswith("#"))
    assert "internal: true" in active
    orchestrator = active.split("orchestrator:", 1)[1].split("networks:\n  internal_net", 1)[0]
    assert "internal_net" in orchestrator
    assert "egress_net" not in orchestrator
    assert "HTTPS_PROXY=http://proxy:3128" in orchestrator
    assert "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" in orchestrator


def test_dockerfile_runs_as_non_root_and_agent_code_is_root_owned():
    dockerfile = (REPO / "Dockerfile").read_text()
    lines = [l.strip() for l in dockerfile.splitlines()]
    assert any(l.startswith("USER agent") for l in lines)
    assert any("useradd" in l and "agent" in l for l in lines)
    copies = [l for l in lines if l.startswith("COPY")]
    assert any("--chown=agent:agent target_repo/" in l for l in copies)
    assert not any("--chown" in l and ("agent/" in l.split()[-2:] or "orchestrator/" in l) for l in copies)
