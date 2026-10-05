# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from regress_stack.modules import watcher


def test_determine_packages_includes_tempest_plugin():
    assert watcher.determine_packages() == [
        *watcher.BASE_PACKAGES,
        "watcher-tempest-plugin",
    ]


def test_determine_packages_omits_tempest_plugin():
    assert watcher.determine_packages(no_tempest=True) == watcher.BASE_PACKAGES


def test_setup_configures_and_starts_services(monkeypatch):
    cfg_calls = []
    service_events = []

    monkeypatch.setattr(
        watcher.mysql, "ensure_service", lambda _service: ("db-user", "db-pass")
    )
    monkeypatch.setattr(
        watcher.rabbitmq,
        "ensure_service",
        lambda _service: ("rabbit-user", "rabbit-pass"),
    )
    monkeypatch.setattr(
        watcher.keystone,
        "ensure_service_account",
        lambda _service, _service_type, _url: ("watcher-user", "watcher-pass"),
    )
    monkeypatch.setattr(
        watcher.mysql,
        "connection_string",
        lambda *_args: "mysql+pymysql://watcher",
    )
    monkeypatch.setattr(
        watcher.rabbitmq,
        "transport_url",
        lambda *_args: "rabbit://watcher",
    )
    monkeypatch.setattr(
        watcher.keystone,
        "authtoken_service",
        lambda *_args: {"auth_type": "password", "username": "watcher-user"},
    )
    monkeypatch.setattr(
        watcher.keystone,
        "account_dict",
        lambda *_args: {"auth_type": "password", "username": "watcher-user"},
    )
    monkeypatch.setattr(watcher.core_utils, "my_ip", lambda: "192.0.2.10")
    monkeypatch.setattr(
        watcher.module_utils,
        "cfg_set",
        lambda config, *args: cfg_calls.append((config, args)),
    )
    monkeypatch.setattr(
        watcher.core_utils,
        "sudo",
        lambda command, args, user=None: service_events.append(
            ("sudo", command, args, user)
        ),
    )
    monkeypatch.setattr(
        watcher.core_utils,
        "restart_service",
        lambda service: service_events.append(("restart", service)),
    )
    monkeypatch.setattr(
        watcher.core_utils,
        "run",
        lambda command, args: service_events.append(("run", command, args)),
    )
    # Hibiscus: the standalone watcher-api unit is retired (WSGI under apache2),
    # while the decision engine and applier remain standalone daemons.
    monkeypatch.setattr(
        watcher.core_utils,
        "has_service",
        lambda service: service != "watcher-api",
    )
    monkeypatch.setattr(
        watcher.core_utils,
        "graceful_apache",
        lambda: service_events.append(("graceful-apache",)),
    )

    watcher.setup()

    assert cfg_calls == [
        (
            watcher.CONF,
            (
                ("database", "connection", "mysql+pymysql://watcher"),
                ("database", "max_pool_size", "1"),
                ("DEFAULT", "transport_url", "rabbit://watcher"),
                ("api", "host", "192.0.2.10"),
                ("keystone_authtoken", "auth_type", "password"),
                ("keystone_authtoken", "username", "watcher-user"),
                ("watcher_clients_auth", "auth_type", "password"),
                ("watcher_clients_auth", "username", "watcher-user"),
                ("oslo_messaging_notifications", "driver", "messagingv2"),
            ),
        )
    ]
    assert service_events == [
        *[("run", "systemctl", ["stop", service]) for service in watcher.SERVICES[1:]],
        ("sudo", "watcher-db-manage", ["upgrade"], watcher.SERVICE),
        ("graceful-apache",),
        *[
            event
            for service in watcher.SERVICES[1:]
            for event in (
                ("restart", service),
                ("run", "systemctl", ["is-active", "--quiet", service]),
            )
        ],
    ]


def test_configure_tempest_enables_watcher(tmp_path, monkeypatch):
    cfg_calls = []
    tempest_conf = tmp_path / "tempest.conf"
    monkeypatch.setattr(
        watcher.module_utils,
        "cfg_set",
        lambda config, *args: cfg_calls.append((config, args)),
    )

    watcher.configure_tempest(tempest_conf)

    assert cfg_calls == [
        (
            str(tempest_conf),
            (
                ("service_available", "watcher", "True"),
                ("optimize", "min_microversion", "1.0"),
                ("optimize", "max_microversion", "latest"),
            ),
        )
    ]
    assert watcher.TEST_INCLUDE_REGEXES == [r"watcher_tempest_plugin.tests.api"]
