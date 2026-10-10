# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import subprocess

import pytest

from regress_stack.core import quiet


def test_private_command_hides_secrets(caplog, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, ["mysql", "secret"], "stdout-secret", "stderr-secret"
        )

    monkeypatch.setattr(quiet.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError) as error:
        quiet.run("mysql", ["password-secret"], input="sql-secret")
    assert "secret" not in str(error.value)
    assert error.value.cmd == ["mysql"]
    assert error.value.stdout is None and error.value.stderr is None
    assert "secret" not in caplog.text
