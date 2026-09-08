# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from regress_stack.multinode import common


def configuration(context):
    nodes = context.deployment.controllers
    config = {
        "bind-address": context.local.address,
        "report_host": context.local.address,
        "server_id": str(nodes.index(context.local) + 1),
        "gtid_mode": "ON",
        "enforce_gtid_consistency": "ON",
        "log_bin": "mysql-bin",
        "binlog_format": "ROW",
        "log_replica_updates": "ON",
        "binlog_expire_logs_seconds": "604800",
    }
    if len(nodes) == 3:
        config.update(
            {
                # All controller service pools connect to the single primary.
                "max_connections": "500",
                "sync_relay_log": "1",
                "plugin_load_add": "group_replication.so",
                "group_replication_group_name": context.deployment_id,
                "group_replication_local_address": f"{context.local.address}:33061",
                "group_replication_group_seeds": ",".join(
                    f"{node.address}:33061" for node in nodes
                ),
                "group_replication_ip_allowlist": context.deployment.management_cidr,
                "group_replication_single_primary_mode": "ON",
                "group_replication_start_on_boot": "OFF",
                "group_replication_bootstrap_group": "OFF",
                "group_replication_recovery_get_public_key": "ON",
                "group_replication_consistency": "BEFORE_ON_PRIMARY_FAILOVER",
                "group_replication_exit_state_action": "READ_ONLY",
                "group_replication_autorejoin_tries": "2016",
                "super_read_only": "ON",
            }
        )
    return (
        "[mysqld]\n"
        + "\n".join(f"{key}={value}" for key, value in config.items())
        + "\n"
    )


def setup():
    context = common.context()
    if common.done("mysql"):
        return
    common.write(
        "/etc/mysql/mysql.conf.d/zz-regress-stack.cnf",
        configuration(context),
        mode=0o644,
    )
    common.restart("mysql")
    password = common.token(context.secret("mysql/recovery"))
    check_password = common.token(context.secret("mysql/check"))
    # Recovery/check users are local instance setup, not replicated deployment
    # resources. Their credentials are the same on all three members.
    common.sql(f"""SET SESSION sql_log_bin=0;
SET GLOBAL super_read_only=OFF;
CREATE USER IF NOT EXISTS 'regress_recovery'@'%' IDENTIFIED BY '{password}';
GRANT REPLICATION SLAVE, CONNECTION_ADMIN ON *.* TO 'regress_recovery'@'%';
CREATE USER IF NOT EXISTS 'regress_check'@'%' IDENTIFIED BY '{check_password}';
""")
    if len(context.deployment.controllers) == 3:
        common.sql(f"""CHANGE REPLICATION SOURCE TO SOURCE_USER='regress_recovery',
SOURCE_PASSWORD='{password}' FOR CHANNEL 'group_replication_recovery';
SET GLOBAL super_read_only=ON;
""")
        # Bootstrap is a one-time operation, never a persistent startup option.
        if context.bootstrap:
            common.sql("SET GLOBAL group_replication_bootstrap_group=ON;")
        try:
            common.sql("START GROUP_REPLICATION;")
        finally:
            if context.bootstrap:
                common.sql("SET GLOBAL group_replication_bootstrap_group=OFF;")
        common.wait_for(
            lambda: common.sql(
                "SELECT MEMBER_STATE FROM performance_schema.replication_group_members WHERE MEMBER_ID=@@server_uuid;"
            ).strip()
            == "ONLINE",
            "local MySQL group membership",
            timeout=300,
        )
        common.sql("SET PERSIST group_replication_start_on_boot=ON;")
    common.mark("mysql")


def ensure_service(name):
    context = common.context()
    name = common.token(name)
    password = common.token(context.secret(f"mysql/{name}"))
    if context.bootstrap:
        common.sql(f"""CREATE DATABASE IF NOT EXISTS `{name}`;
CREATE USER IF NOT EXISTS '{name}'@'%' IDENTIFIED BY '{password}';
GRANT ALL PRIVILEGES ON `{name}`.* TO '{name}'@'%';
""")
    return name, password
