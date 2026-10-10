# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Exercise the shell CLI with fake host commands, not a live deployment."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import threading

import pytest


DIRECTORY = Path(__file__).with_name("multinode")
SANITIZER = runpy.run_path(str(DIRECTORY / "sanitize.py"))


def test_redaction_covers_encoded_and_transient_credentials():
    result = SANITIZER["redact"](
        "failure this/is+a&password\nfailure this%2Fis%2Ba%26password\n"
        'X-Auth-Token: transient-token\n{"password": "generated-user-value"}\n'
        "amqp://nova:another-generated-value@10.0.0.1\nuseful diagnostic",
        ["this/is+a&password"],
    )
    for value in (
        "this/is+a&password",
        "this%2Fis%2Ba%26password",
        "transient-token",
        "generated-user-value",
        "another-generated-value",
    ):
        assert value not in result
    assert "useful diagnostic" in result


def test_log_output_cannot_inject_workflow_commands_or_nuls():
    result = SANITIZER["redact"]("\x1b[31m::error::message\0", [])
    assert "::" not in result
    assert "\x1b" not in result
    assert "\0" not in result
    assert "message" in result


def test_sanitizer_cli_withholds_output_for_invalid_credentials(tmp_path):
    (tmp_path / "node1.json").write_text('{"values": {"password": 123}}')
    result = subprocess.run(
        ["python3", str(DIRECTORY / "sanitize.py"), "--credentials", str(tmp_path)],
        input="raw private output",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "withheld" in result.stderr


@pytest.fixture
def shell_cli(tmp_path):
    work = tmp_path / "private"
    artifacts = tmp_path / "artifacts"
    (work / "terraform" / ".terraform").mkdir(parents=True)
    (work / "logs").mkdir()
    artifacts.mkdir()
    (work / "terraform" / "terraform.tfvars.json").write_text(
        json.dumps(
            {"release": "noble", "run_id": "rs12345678", "storage_pool": "test-pool"}
        )
    )
    (work / "commit").write_text("tested-commit\n")
    executables = tmp_path / "bin"
    executables.mkdir()
    stub = executables / "stub"
    stub.write_text(
        "#!/usr/bin/python3\n"
        + r"""import json
import os
from pathlib import Path
import sys
import time

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as output:
    output.write(json.dumps([name, *args]) + "\n")
if name == "iptables":
    sys.exit(0)
if name == "terraform":
    directory = Path(args[0].removeprefix("-chdir="))
    if "init" in args:
        (directory / ".terraform").mkdir(exist_ok=True)
    if "apply" in args and os.environ.get("FAIL") == "provision":
        print("apply failed", file=sys.stderr)
        sys.exit(23)
    if "destroy" in args and os.environ.get("FAIL") == "cleanup":
        print("destroy failed", file=sys.stderr)
        sys.exit(29)
    if "output" in args:
        if args[-1] == "project":
            values = json.loads((directory / "terraform.tfvars.json").read_text())
            print(values["run_id"] + "-ci")
        else:
            print(json.dumps({"schema": 1, "profile": "hyperconverged"}))
    sys.exit(0)
if "cat" in args:
    if os.environ.get("MISSING_CREDENTIALS"):
        sys.exit(1)
    if "tempest.conf" in args[-1]:
        print("[identity]\npassword = generated-tempest-password")
    else:
        print(json.dumps({"values": {"example": "a-private-generated-value"}}))
    sys.exit(0)
if "storage" in args:
    print('[{"name": "test-pool"}]')
    sys.exit(0)
if "query" in args:
    print(json.dumps({"space": {"total": 500 * 1024**3, "used": 0}}))
    sys.exit(0)
if "pull" in args:
    Path(args[-1]).write_text("a-private-generated-value")
if "/root/multinode-guest.sh" in args and args[-1] == "test":
    print("{0} tempest.test_first [0.1s] ... ok", flush=True)
    if os.environ.get("TEST_GATE"):
        while not Path(os.environ["TEST_GATE"]).exists():
            time.sleep(0.01)
    print("Totals", flush=True)
    print("Ran: 1 tests in 0.1000 sec.", flush=True)
    print(" - Passed: 1", flush=True)
    print(" - Failed: 0", flush=True)
if "dpkg-query" in args:
    print("nova-compute\t1:version")
else:
    print("useful output a-private-generated-value")
    print("DEBUG diagnostic", file=sys.stderr)
if "/root/multinode-guest.sh" in args and args[args.index("/root/multinode-guest.sh") + 1] == os.environ.get("FAIL"):
    sys.exit(23)
"""
    )
    stub.chmod(0o755)
    for name in ("lxc", "terraform", "iptables"):
        (executables / name).symlink_to(stub)
    calls = tmp_path / "calls.jsonl"
    bash_env = tmp_path / "bash-env"
    bash_env.write_text(
        'test() { if [[ "$*" == "-e /dev/kvm" ]]; then return 0; '
        'else builtin test "$@"; fi; }\n'
        'getconf() { echo "${HOST_CPUS:-32}"; }\n'
        "awk() { echo 134217728; }\n"
    )
    env = {
        **os.environ,
        "PATH": str(executables) + ":" + os.environ["PATH"],
        "CALLS": str(calls),
        "MULTINODE_RELEASE": "noble",
        "MULTINODE_WORK_DIR": str(work),
        "MULTINODE_ARTIFACTS": str(artifacts),
        "BASH_ENV": str(bash_env),
    }

    def run(phase, *, streaming=False, **extra):
        if streaming:
            return subprocess.Popen(
                ["bash", str(DIRECTORY / "run.sh"), phase],
                env={**env, **extra},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        return subprocess.run(
            ["bash", str(DIRECTORY / "run.sh"), phase],
            env={**env, **extra},
            capture_output=True,
            text=True,
            timeout=15,
        )

    return run, work, artifacts, calls


def test_tempest_cli_publishes_progress_before_completion(shell_cli, tmp_path):
    run, _, artifacts, _ = shell_cli
    gate = tmp_path / "release"
    seen = threading.Event()
    lines = []
    process = run("test", streaming=True, TEST_GATE=str(gate))

    def console():
        for line in process.stdout:
            lines.append(line)
            if "tempest.test_first" in line:
                seen.set()

    thread = threading.Thread(target=console)
    thread.start()
    try:
        assert seen.wait(5), "Test result did not reach the CI console"
        assert process.poll() is None, "Results arrived only after process completion"
    finally:
        gate.touch()
        process.wait(timeout=10)
        thread.join(10)
    assert not thread.is_alive()
    assert process.returncode == 0, process.stderr.read()
    output = "".join(lines)
    assert output.count("tempest.test_first") == 1
    assert "Ran: 1 tests" in output
    assert " - Passed: 1" in output and " - Failed: 0" in output
    assert "tempest.test_first" in (artifacts / "node1-tempest.stdout.log").read_text()


@pytest.mark.parametrize("phase", ["install", "setup", "test"])
def test_cli_failure_reports_node_phase_and_preserves_exit_status(shell_cli, phase):
    run, work, artifacts, calls = shell_cli
    result = run(phase, FAIL=phase)
    assert result.returncode == 23, result.stderr
    assert "node1-" in result.stdout
    assert "exit status 23" in result.stdout
    assert json.loads((artifacts / f"phase-{phase}.json").read_text()) == {
        "phase": phase,
        "exit_status": 23,
    }
    assert json.loads((artifacts / "result.json").read_text())["status"] == "failed"
    if phase == "setup":
        assert "a-private-generated-value" not in result.stdout
        commands = calls.read_text()
        assert "node2-preseed" not in commands
    if phase == "install":
        assert "node2" not in calls.read_text()
    assert work.exists()  # CI cleanup is a separate, always-running step.


def test_missing_credentials_withholds_private_output(shell_cli):
    run, _, artifacts, _ = shell_cli
    result = run("setup", FAIL="setup", MISSING_CREDENTIALS="1")
    assert result.returncode == 23
    assert "withheld" in result.stdout
    assert "useful output" not in result.stdout
    assert "a-private-generated-value" not in result.stdout
    assert not list(artifacts.glob("node1-bootstrap.*.log"))


def test_setup_joins_sequentially_with_private_preseeds(shell_cli):
    run, work, artifacts, calls = shell_cli
    result = run("setup")
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    joins = [command for command in commands if command[-1] in {"setup", "join"}]
    assert [command[5] for command in joins] == ["node1", "node2", "node3"]
    pushes = [command for command in commands if "push" in command]
    assert len(pushes) == 2
    assert all(command[-2:] == ["--mode", "0600"] for command in pushes)
    assert not list(work.glob("*-preseed.json"))
    assert "a-private-generated-value" not in result.stdout
    assert all(
        "a-private-generated-value" not in path.read_text()
        for path in artifacts.glob("*.log")
    )


def test_command_streams_remain_separate(shell_cli):
    run, _, artifacts, _ = shell_cli
    result = run("install")
    assert result.returncode == 0, result.stderr
    assert (
        "DEBUG diagnostic" not in (artifacts / "node1-install.stdout.log").read_text()
    )
    assert "DEBUG diagnostic" in (artifacts / "node1-install.stderr.log").read_text()
    assert "nova-compute" in (artifacts / "node1-packages.stdout.log").read_text()


@pytest.mark.parametrize("failure", [None, "cleanup"])
def test_cleanup_uses_only_own_state_and_bridge_rules(shell_cli, failure):
    run, work, artifacts, calls = shell_cli
    result = run("cleanup", FAIL=failure or "")
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    destroys = [command for command in commands if "destroy" in command]
    assert len(destroys) == 1
    assert destroys[0][1] == f"-chdir={work}/terraform"
    assert not any("delete" in command for command in commands)
    deleted_rules = [command for command in commands if "-D" in command]
    assert len(deleted_rules) == 4
    assert all(
        any(bridge in command for bridge in ("rs12345678-m", "rs12345678-p"))
        for command in deleted_rules
    )
    assert not any("-F" in command or "-P" in command for command in commands)
    assert result.returncode == (29 if failure else 0)
    assert work.exists() == bool(failure)
    assert (
        json.loads((artifacts / "phase-cleanup.json").read_text())["exit_status"]
        == result.returncode
    )


def test_cli_rejects_overlapping_private_and_public_directories(tmp_path):
    result = subprocess.run(
        [
            "bash",
            str(DIRECTORY / "run.sh"),
            "prepare",
            "--release",
            "noble",
            "--work-dir",
            str(tmp_path),
            "--artifacts",
            str(tmp_path / "artifacts"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "must be separate" in result.stderr
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("failure", ["provision", "install", "setup", "test", None])
def test_all_cli_always_cleans_up_and_propagates_failure(shell_cli, failure):
    run, work, artifacts, calls = shell_cli
    shutil.rmtree(work)
    shutil.rmtree(artifacts)
    result = run("all", FAIL=failure or "")
    assert result.returncode == (23 if failure else 0), result.stderr
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert any("destroy" in command for command in commands)
    assert not work.exists()
    report = json.loads((artifacts / "result.json").read_text())
    assert report["status"] == ("failed" if failure else "passed")
    if failure in {"install", "setup"}:
        assert not any(command[-1] == "test" for command in commands)
    if failure is None:
        ready = [command for command in commands if command[-1] == "ready"]
        assert len(ready) == 6


def test_preflight_failure_creates_no_lxd_resources(shell_cli):
    run, work, artifacts, calls = shell_cli
    shutil.rmtree(work)
    shutil.rmtree(artifacts)
    result = run("all", HOST_CPUS="8")
    assert result.returncode == 1
    assert not calls.exists()
    assert not work.exists()
    assert "At least 26 host CPUs" in result.stdout
    assert json.loads((artifacts / "result.json").read_text())["status"] == "failed"


def test_prepare_refusal_preserves_existing_state_and_artifacts(shell_cli):
    run, work, artifacts, calls = shell_cli
    state = work / "terraform" / "terraform.tfstate"
    state.write_text("existing state")
    result = run("all")
    assert result.returncode == 1
    assert not calls.exists()
    assert state.read_text() == "existing state"
    assert not list(artifacts.iterdir())


def test_cleanup_after_failed_prepare_removes_own_private_directory(shell_cli):
    run, work, artifacts, calls = shell_cli
    shutil.rmtree(work)
    shutil.rmtree(artifacts)
    result = run("prepare", HOST_CPUS="8")
    assert result.returncode == 1
    assert work.exists()
    assert run("cleanup").returncode == 0
    assert not work.exists()
    assert not calls.exists()
    assert json.loads((artifacts / "result.json").read_text())["status"] == "failed"
