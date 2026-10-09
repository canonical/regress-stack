# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Run local commands without exposing arguments, input, or output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import subprocess


class CommandError(subprocess.CalledProcessError):
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
