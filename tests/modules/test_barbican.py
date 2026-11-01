# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pathlib

from regress_stack.modules import barbican


def _make_tempest_conf(tmp_path: pathlib.Path, img_file: str) -> pathlib.Path:
    """Create a minimal tempest.conf-like structure with scenario.img_file."""
    workspace = tmp_path / "mycloud01"
    etc = workspace / "etc"
    etc.mkdir(parents=True)
    tempest_conf = etc / "tempest.conf"
    tempest_conf.write_text("")
    return tempest_conf


def _setup_img_dir_fallback(monkeypatch, tmp_path, img_file):
    """Configure mocks for the img_dir fallback path."""
    cfg_calls = []
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_get",
        lambda _conf, _section, _key: img_file,
    )
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_set",
        lambda conf, *args: cfg_calls.extend(args),
    )
    monkeypatch.setattr(
        barbican.core_utils,
        "tempest_version",
        lambda: (44, 0, 0),
    )

    class _OldPlugin:
        def __lt__(self, other):
            return other == barbican.BARBICAN_PLUGIN_NO_IMG_DIR

    monkeypatch.setattr(
        barbican.core_apt,
        "PkgVersionCompare",
        lambda *args, **kwargs: _OldPlugin(),
    )

    tempest_conf = _make_tempest_conf(tmp_path, img_file)
    return tempest_conf, cfg_calls


def test_img_dir_fallback(tmp_path, monkeypatch):
    """When tempest >= 26.0.0 and barbican < 4.0.0, set img_dir + filename."""
    tempest_conf, cfg_calls = _setup_img_dir_fallback(
        monkeypatch, tmp_path, "noble-server-cloudimg-amd64.img"
    )
    workspace = tempest_conf.parent.parent

    barbican._configure_tempest_image(tempest_conf)

    assert len(cfg_calls) == 2
    section, key, val = cfg_calls[0]
    assert section == "scenario"
    assert key == "img_dir"
    assert val == str(workspace)
    section, key, val = cfg_calls[1]
    assert section == "scenario"
    assert key == "img_file"
    assert val == "noble-server-cloudimg-amd64.img"
    assert (workspace / "sitecustomize.py").exists()


def test_img_dir_fallback_absolute_skip(tmp_path, monkeypatch):
    """When img_file is already absolute, no changes are made."""
    tempest_conf, cfg_calls = _setup_img_dir_fallback(
        monkeypatch, tmp_path, "/absolute/path/to/image.img"
    )
    workspace = tempest_conf.parent.parent

    barbican._configure_tempest_image(tempest_conf)

    assert cfg_calls == []
    assert not (workspace / "sitecustomize.py").exists()


def test_modern_plugin_uses_full_path(tmp_path, monkeypatch):
    """When barbican >= 4.0.0, use full img_file path."""
    tempest_conf = _make_tempest_conf(tmp_path, "noble-server-cloudimg-amd64.img")
    cfg_calls = []

    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_get",
        lambda _conf, _section, _key: "noble-server-cloudimg-amd64.img",
    )
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_set",
        lambda conf, *args: cfg_calls.extend(args),
    )
    monkeypatch.setattr(
        barbican.core_utils,
        "tempest_version",
        lambda: (44, 0, 0),
    )

    class _NewPlugin:
        def __lt__(self, _other):
            return False

    monkeypatch.setattr(
        barbican.core_apt,
        "PkgVersionCompare",
        lambda *args, **kwargs: _NewPlugin(),
    )

    workspace = tempest_conf.parent.parent
    barbican._configure_tempest_image(tempest_conf)

    expected = str(workspace / "noble-server-cloudimg-amd64.img")
    assert cfg_calls == [("scenario", "img_file", expected)]
    assert not (workspace / "sitecustomize.py").exists()


def test_tempest_version_none_uses_full_path(tmp_path, monkeypatch):
    """When tempest version is unknown, use full path (no img_dir)."""
    tempest_conf = _make_tempest_conf(tmp_path, "noble-server-cloudimg-amd64.img")
    cfg_calls = []
    warnings = []

    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_get",
        lambda _conf, _section, _key: "noble-server-cloudimg-amd64.img",
    )
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_set",
        lambda conf, *args: cfg_calls.extend(args),
    )
    monkeypatch.setattr(
        barbican.core_utils,
        "tempest_version",
        lambda: None,
    )
    monkeypatch.setattr(
        barbican.LOG,
        "warning",
        lambda msg, *args: warnings.append(msg),
    )

    workspace = tempest_conf.parent.parent
    barbican._configure_tempest_image(tempest_conf)

    expected = str(workspace / "noble-server-cloudimg-amd64.img")
    assert cfg_calls == [("scenario", "img_file", expected)]
    assert not (workspace / "sitecustomize.py").exists()
    assert any("Could not determine tempest" in w for w in warnings)


def test_barbican_pkg_not_found_uses_full_path(tmp_path, monkeypatch):
    """When barbican-tempest-plugin is not in apt cache, use full path."""
    tempest_conf = _make_tempest_conf(tmp_path, "noble-server-cloudimg-amd64.img")
    cfg_calls = []
    warnings = []

    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_get",
        lambda _conf, _section, _key: "noble-server-cloudimg-amd64.img",
    )
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_set",
        lambda conf, *args: cfg_calls.extend(args),
    )
    monkeypatch.setattr(
        barbican.core_utils,
        "tempest_version",
        lambda: (44, 0, 0),
    )

    def raise_value_error(*args, **kwargs):
        raise ValueError("Package not found")

    monkeypatch.setattr(
        barbican.core_apt,
        "PkgVersionCompare",
        raise_value_error,
    )
    monkeypatch.setattr(
        barbican.LOG,
        "warning",
        lambda msg, *args: warnings.append(msg),
    )

    workspace = tempest_conf.parent.parent
    barbican._configure_tempest_image(tempest_conf)

    expected = str(workspace / "noble-server-cloudimg-amd64.img")
    assert cfg_calls == [("scenario", "img_file", expected)]
    assert not (workspace / "sitecustomize.py").exists()
    assert any("Could not determine" in w for w in warnings)


def test_img_file_empty_noop(tmp_path, monkeypatch):
    """When img_file is empty, return early with no changes."""
    tempest_conf = _make_tempest_conf(tmp_path, "")
    cfg_calls = []

    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_get",
        lambda _conf, _section, _key: "",
    )
    monkeypatch.setattr(
        barbican.module_utils,
        "cfg_set",
        lambda conf, *args: cfg_calls.extend(args),
    )

    barbican._configure_tempest_image(tempest_conf)

    assert cfg_calls == []
    assert not (tempest_conf.parent.parent / "sitecustomize.py").exists()


class _PluginVersion:
    def __init__(self, old):
        self.old = old

    def __lt__(self, other):
        return self.old and other == barbican.BARBICAN_PLUGIN_NO_IMG_DIR


def test_legacy_plugin_excludes_rbac_tests(monkeypatch):
    """With barbican-tempest-plugin < 4.0.0, the rbac tests are excluded."""
    monkeypatch.setattr(
        barbican, "TEST_EXCLUDE_REGEXES", list(barbican.TEST_EXCLUDE_REGEXES)
    )
    monkeypatch.setattr(
        barbican.core_apt,
        "PkgVersionCompare",
        lambda *args, **kwargs: _PluginVersion(old=True),
    )

    barbican._exclude_legacy_policy_tests()
    barbican._exclude_legacy_policy_tests()

    rbac = "barbican_tempest_plugin.tests.rbac"
    assert barbican.TEST_EXCLUDE_REGEXES.count(rbac) == 1


def test_modern_plugin_runs_rbac_tests(monkeypatch):
    """With barbican-tempest-plugin >= 4.0.0, the rbac tests stay included."""
    monkeypatch.setattr(
        barbican, "TEST_EXCLUDE_REGEXES", list(barbican.TEST_EXCLUDE_REGEXES)
    )
    monkeypatch.setattr(
        barbican.core_apt,
        "PkgVersionCompare",
        lambda *args, **kwargs: _PluginVersion(old=False),
    )

    barbican._exclude_legacy_policy_tests()

    assert "barbican_tempest_plugin.tests.rbac" not in barbican.TEST_EXCLUDE_REGEXES
