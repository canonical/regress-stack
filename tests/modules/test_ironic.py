# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import subprocess

from regress_stack.modules import ironic


def test_profile_defaults_to_fake_hardware(monkeypatch):
    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    assert ironic.profile() == ironic.PROFILE_FAKE_HARDWARE


def test_profile_honors_explicit_ipmi_selection(monkeypatch):
    monkeypatch.setenv("IRONIC_PROFILE", "ipmi")
    assert ironic.profile() == ironic.PROFILE_IPMI


def test_enabled_is_always_true(monkeypatch):
    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    assert ironic.enabled() is True


def test_tempest_regexes_default_to_ironic_api(monkeypatch):
    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    includes, excludes = ironic.tempest_regexes()
    assert includes == ["ironic_tempest_plugin.tests.api.admin"]
    assert excludes == ironic.TEST_EXCLUDE_REGEXES
    assert "TestNodesVif" in excludes
    assert "test_set_console_mode" in excludes
    assert "TestAllocations" in excludes


def test_tempest_regexes_ipmi_profile_is_conservative(monkeypatch):
    monkeypatch.setenv("IRONIC_PROFILE", "ipmi")
    includes, excludes = ironic.tempest_regexes()
    assert includes == []
    assert excludes == ironic.TEST_EXCLUDE_REGEXES


def test_determine_packages_default_fake_hardware(monkeypatch):
    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    pkgs = ironic.determine_packages()
    assert "ironic-api" in pkgs
    assert "ironic-conductor" in pkgs
    assert "ironic-tempest-plugin" in pkgs
    assert "python3-ironicclient" in pkgs
    assert "virtualbmc" not in pkgs
    assert "qemu-system-x86" not in pkgs


def test_determine_packages_ipmi_profile(monkeypatch):
    monkeypatch.setenv("IRONIC_PROFILE", "ipmi")
    pkgs = ironic.determine_packages()
    assert "virtualbmc" in pkgs
    assert "qemu-system-x86" in pkgs
    assert "qemu-kvm" not in pkgs


def test_determine_packages_no_tempest(monkeypatch):
    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    pkgs = ironic.determine_packages(no_tempest=True)
    assert "ironic-tempest-plugin" not in pkgs


def test_smoke_test_default_fake_hardware(monkeypatch):
    calls = []

    monkeypatch.delenv("IRONIC_PROFILE", raising=False)
    monkeypatch.setattr(ironic, "_wait_for_api", lambda: calls.append("api"))
    monkeypatch.setattr(
        ironic, "_ensure_fake_hardware_node", lambda: calls.append("fake-node")
    )
    monkeypatch.setattr(ironic, "_ensure_ironic_flavor", lambda: calls.append("flavor"))
    monkeypatch.setattr(
        ironic, "_ensure_node_available", lambda: calls.append("available")
    )

    ironic.smoke_test(None)

    assert calls == ["api", "fake-node", "flavor", "available"]


def test_smoke_test_orchestrates_virtualbmc_flow(monkeypatch):
    calls = []

    monkeypatch.setenv("IRONIC_PROFILE", "ipmi")
    monkeypatch.setattr(ironic, "_wait_for_api", lambda: calls.append("api"))
    monkeypatch.setattr(ironic, "_ensure_pxe_directories", lambda: calls.append("pxe"))
    monkeypatch.setattr(ironic, "_ensure_baremetal_vm", lambda: calls.append("vm"))
    monkeypatch.setattr(ironic, "_ensure_virtualbmc", lambda: calls.append("vbmc"))
    monkeypatch.setattr(
        ironic,
        "_ensure_provisioning_network",
        lambda: calls.append("network") or "net-id",
    )
    monkeypatch.setattr(
        ironic,
        "_ensure_tinyipa_images",
        lambda: calls.append("ipa") or ("kernel", "initrd"),
    )
    monkeypatch.setattr(ironic, "_ensure_ironic_flavor", lambda: calls.append("flavor"))
    monkeypatch.setattr(
        ironic,
        "_ensure_baremetal_node",
        lambda network_id, kernel_id, initrd_id: calls.append(
            ("node", network_id, kernel_id, initrd_id)
        ),
    )
    monkeypatch.setattr(
        ironic, "_ensure_node_available", lambda: calls.append("available")
    )

    ironic.smoke_test(None)

    assert calls == [
        "api",
        "pxe",
        "vm",
        "vbmc",
        "network",
        "ipa",
        ("node", "net-id", "kernel", "initrd"),
        "flavor",
        "available",
    ]


def test_ensure_node_available_from_enroll(monkeypatch):
    state = {
        "provision_state": "enroll",
    }
    calls = []

    def fake_openstack(args, system_scope=True):
        calls.append(args)
        if args[:3] == ["baremetal", "node", "validate"]:
            return ""
        if args[:3] == ["baremetal", "node", "show"]:
            return json.dumps(state)
        if args[:3] == ["baremetal", "node", "manage"]:
            state["provision_state"] = "manageable"
            return ""
        if args[:3] == ["baremetal", "node", "provide"]:
            state["provision_state"] = "available"
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(ironic, "_openstack", fake_openstack)
    monkeypatch.setattr(ironic, "_find_node", lambda name: state)
    ironic._ensure_node_available()
    assert [
        "baremetal",
        "node",
        "manage",
        ironic.BAREMETAL_VM_NAME,
        "--wait",
        ironic.IRONIC_PROVISION_TIMEOUT,
    ] in calls
    assert [
        "baremetal",
        "node",
        "provide",
        ironic.BAREMETAL_VM_NAME,
        "--wait",
        ironic.IRONIC_PROVISION_TIMEOUT,
    ] in calls


def test_service_url_uses_provisioning_ip(monkeypatch):
    monkeypatch.setattr(ironic, "provisioning_ip", lambda: "10.0.0.2")
    assert ironic.service_url() == "http://10.0.0.2:6385/"


def test_wait_for_api_retries_connection_refused_from_stderr(monkeypatch):
    calls = []

    def fake_openstack(args, system_scope=True):
        calls.append((list(args), system_scope))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(
                1, ["openstack", *args], stderr="Connection refused"
            )
        return "[]"

    monkeypatch.setattr(ironic, "_openstack", fake_openstack)
    monkeypatch.setattr(ironic.time, "sleep", lambda _: None)

    ironic._wait_for_api()

    assert len(calls) == 2


def test_configure_tempest(tmp_path):
    tempest_conf = tmp_path / "tempest.conf"
    cfg_calls = []

    def fake_cfg_set(config_file, *args):
        cfg_calls.append((config_file, args))

    from regress_stack.modules import utils as module_utils

    original = module_utils.cfg_set
    module_utils.cfg_set = fake_cfg_set
    original_flavor = ironic._ensure_ironic_flavor
    original_release = ironic.core_utils.release
    ironic._ensure_ironic_flavor = lambda: "flavor-id"
    ironic.core_utils.release = lambda: "noble"
    try:
        ironic.configure_tempest(tempest_conf)
    finally:
        module_utils.cfg_set = original
        ironic._ensure_ironic_flavor = original_flavor
        ironic.core_utils.release = original_release

    assert cfg_calls
    assert cfg_calls[0][0] == str(tempest_conf)
    written = dict(
        (section + "." + key, value) for section, key, value in cfg_calls[0][1]
    )
    assert written["service_available.baremetal"] == "True"
    assert written["service_available.ironic"] == "True"
    assert written["baremetal.driver"] == "fake-hardware"
    assert written["baremetal.enabled_drivers"] == "fake,fake-hardware"
    assert written["baremetal.enabled_hardware_types"] == "fake-hardware"
    assert written["auth.admin_system"] == "all"
    assert written["enforce_scope.ironic"] == "True"


def test_configure_tempest_ipmi(tmp_path, monkeypatch):
    tempest_conf = tmp_path / "tempest.conf"
    cfg_calls = []

    def fake_cfg_set(config_file, *args):
        cfg_calls.append((config_file, args))

    from regress_stack.modules import utils as module_utils

    monkeypatch.setenv("IRONIC_PROFILE", "ipmi")
    original = module_utils.cfg_set
    module_utils.cfg_set = fake_cfg_set
    original_flavor = ironic._ensure_ironic_flavor
    original_release = ironic.core_utils.release
    ironic._ensure_ironic_flavor = lambda: "flavor-id"
    ironic.core_utils.release = lambda: "noble"
    try:
        ironic.configure_tempest(tempest_conf)
    finally:
        module_utils.cfg_set = original
        ironic._ensure_ironic_flavor = original_flavor
        ironic.core_utils.release = original_release

    assert cfg_calls
    assert cfg_calls[0][0] == str(tempest_conf)
    written = dict(
        (section + "." + key, value) for section, key, value in cfg_calls[0][1]
    )
    assert written["service_available.baremetal"] == "True"
    assert written["service_available.ironic"] == "True"
    assert written["baremetal.driver"] == "ipmi"
    assert written["baremetal.enabled_drivers"] == "ipmi,intel-ipmi"
    assert written["baremetal.enabled_hardware_types"] == "ipmi,intel-ipmi"
    assert written["auth.admin_system"] == "all"
    assert written["enforce_scope.ironic"] == "True"


def test_configure_tempest_older_release_skips_system_scope(tmp_path):
    tempest_conf = tmp_path / "tempest.conf"
    cfg_calls = []

    def fake_cfg_set(config_file, *args):
        cfg_calls.append((config_file, args))

    from regress_stack.modules import utils as module_utils

    original = module_utils.cfg_set
    module_utils.cfg_set = fake_cfg_set
    original_flavor = ironic._ensure_ironic_flavor
    original_release = ironic.core_utils.release
    ironic._ensure_ironic_flavor = lambda: "flavor-id"
    ironic.core_utils.release = lambda: "jammy"
    try:
        ironic.configure_tempest(tempest_conf)
    finally:
        module_utils.cfg_set = original
        ironic._ensure_ironic_flavor = original_flavor
        ironic.core_utils.release = original_release

    written = dict(
        (section + "." + key, value) for section, key, value in cfg_calls[0][1]
    )
    assert "auth.admin_system" not in written
    assert "enforce_scope.ironic" not in written


def test_ensure_vm_xml_uses_openvswitch_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(ironic, "IRONIC_VM_XML", tmp_path / "ironic-vm.xml")
    monkeypatch.setattr(ironic, "IRONIC_VM_DISK", tmp_path / "disk.qcow2")
    monkeypatch.setattr(ironic.nova, "virt_type", lambda: "qemu")

    xml_path = ironic._ensure_vm_xml()
    content = xml_path.read_text()

    assert "<domain type='qemu'>" in content
    assert "<source bridge='br-ex'/>" in content
    assert "<virtualport type='openvswitch'/>" in content
