# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from click.testing import CliRunner
from regress_stack.cli.packages import packages


def test_packages_command_help():
    """Test that packages command shows help correctly."""
    runner = CliRunner()
    result = runner.invoke(packages, ["--help"])
    assert result.exit_code == 0
    assert "List packages needed to reach the specified target" in result.output
    assert "regress-stack packages nova" in result.output
    assert "apt install" in result.output


def test_packages_command_nova():
    """Test that packages command works with nova target."""
    runner = CliRunner()
    result = runner.invoke(packages, ["nova"])
    assert result.exit_code == 0

    # Check that output contains expected packages
    output_packages = result.output.strip().split()
    assert "nova-api" in output_packages
    assert "nova-conductor" in output_packages
    assert "mysql-server" in output_packages
    assert "keystone" in output_packages
    assert "crudini" in output_packages


def test_packages_command_utils():
    """Test that packages command works with utils target."""
    runner = CliRunner()
    result = runner.invoke(packages, ["utils"])
    assert result.exit_code == 0

    # Utils should only return crudini
    output_packages = result.output.strip().split()
    assert output_packages == ["crudini"]


def test_packages_command_all():
    """Test that packages command works without target (all modules)."""
    runner = CliRunner()
    result = runner.invoke(packages, [])
    assert result.exit_code == 0

    # Should include packages from multiple modules
    output_packages = result.output.strip().split()
    assert "nova-api" in output_packages
    assert "keystone" in output_packages
    assert "heat-api" in output_packages
    assert "crudini" in output_packages


def test_packages_command_invalid_target():
    """Test that packages command fails gracefully with invalid target."""
    runner = CliRunner()
    result = runner.invoke(packages, ["invalid"])
    assert result.exit_code == 1
    assert "Error: Target 'invalid' not found!" in result.output


def test_packages_command_output_format():
    """Test that packages command output is properly formatted for apt install."""
    runner = CliRunner()
    result = runner.invoke(packages, ["keystone"])
    assert result.exit_code == 0

    # Output should be space-separated packages on a single line
    lines = result.output.strip().split("\n")
    assert len(lines) == 1

    # Should contain expected packages for keystone
    output_packages = lines[0].split()
    assert "crudini" in output_packages
    assert "mysql-server" in output_packages
    assert "keystone" in output_packages
    assert "apache2" in output_packages
