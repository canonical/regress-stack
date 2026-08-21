# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from regress_stack.cli.test import _write_clouds_yaml


def test_write_clouds_yaml_places_system_scope_at_cloud_level(tmp_path):
    clouds_yaml = tmp_path / "clouds.yaml"
    auth = {
        "OS_AUTH_URL": "http://keystone:5000/v3",
        "OS_USERNAME": "admin",
        "OS_PASSWORD": "secret",
        "OS_PROJECT_NAME": "admin",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_REGION_NAME": "RegionOne",
        "OS_IDENTITY_API_VERSION": "3",
    }

    _write_clouds_yaml(clouds_yaml, auth)

    content = clouds_yaml.read_text()

    assert "  regress-system:\n" in content
    assert "    auth:\n" in content
    assert "    system_scope: all\n" in content
    assert "      system_scope: all\n" not in content
