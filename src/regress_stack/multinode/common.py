# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Local recipe primitives; no peer command execution or file transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from regress_stack.core.deployment import Context, current, private_write


STATE = Path("/var/lib/regress-stack/multinode")


def context() -> Context:
    value = current()
    if value is None:
        raise RuntimeError("An explicit deployment context is required")
    return value


class CommandError(subprocess.CalledProcessError, RuntimeError):
    """A subprocess failure retaining only the executable and exit status."""


def run(
    command: str,
    args: Sequence[str] = (),
    *,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float | None = 120,
) -> str:
    """Run locally without logging arguments, stdin, or command output.

    SQL, service configuration, and errors may contain credentials. Exceptions
    deliberately exclude subprocess details, including their chained cause.
    """
    try:
        result = subprocess.run(
            [command, *args],
            input=input,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as error:
        raise CommandError(error.returncode, [Path(command).name]) from None
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Local executable {Path(command).name} was not found"
        ) from None
    except (subprocess.TimeoutExpired, OSError):
        raise RuntimeError(
            f"Local {Path(command).name} command failed; sensitive output suppressed"
        ) from None
    return result.stdout


def write(
    path: str | os.PathLike[str],
    content: str,
    user: str = "root",
    mode: int = 0o600,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    private_write(path, content)
    shutil.chown(path, user=user)
    path.chmod(mode)


def restart(*units: str) -> None:
    run("systemctl", ["enable", *units])
    run("systemctl", ["restart", *units])


def sql(
    statement: str,
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> str:
    args = ["--batch", "--skip-column-names", "--connect-timeout=10"]
    env = None
    if host:
        if user is None or password is None:
            raise ValueError("Remote MySQL queries require a user and password")
        args += ["--host", host, "--user", user]
        env = {**os.environ, "MYSQL_PWD": password}
    return run("mysql", args, input=statement, env=env)


def token(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("Invalid credential token")
    return value


def wait_for(check: Callable[[], bool], description: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if check():
                return
        except RuntimeError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {description}")
        time.sleep(2)


def done(name: str) -> bool:
    return (STATE / f"{name}.json").exists()


def mark(name: str) -> None:
    write(
        STATE / f"{name}.json", json.dumps({"deployment_id": context().deployment_id})
    )
