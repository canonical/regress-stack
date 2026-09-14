# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from pathlib import Path

from regress_stack.core.deployment import Context
from regress_stack.multinode import common


API_PORTS = (
    5000,
    8774,
    8775,
    8776,
    8778,
    9292,
    9696,
    9311,
    8000,
    8004,
    9511,
    9322,
    6082,
)


def configuration(context: Context) -> str:
    nodes = context.deployment.controllers
    result = [
        "global",
        "    external-check",
        "    insecure-fork-wanted",
        "    user haproxy",
        "    group haproxy",
        "    daemon",
        "defaults",
        "    mode tcp",
        "    timeout connect 5s",
        "    timeout client 60s",
        "    timeout server 60s",
        "listen mysql",
        "    option external-check",
        "    timeout client 3h",
        "    timeout server 3h",
        "    bind 127.0.0.1:13306",
        "    external-check command /usr/local/lib/regress-stack/mysql-primary",
    ]
    result += [
        f"    server {node.name} {node.address}:3306 check inter 2s fall 2 rise 1 on-marked-down shutdown-sessions"
        for node in nodes
    ]
    if len(nodes) == 3:
        for port in API_PORTS:
            result += [
                f"listen api-{port}",
                f"    bind {context.deployment.api_address}:{port + 10000}",
                "    balance roundrobin",
            ]
            result += [
                f"    server {node.name} {node.address}:{port} check inter 2s fall 2 rise 1"
                for node in nodes
            ]
    return "\n".join(result) + "\n"


def keepalived_configuration(context: Context) -> str:
    peers = "\n".join(
        f"        {node.address}"
        for node in context.deployment.controllers
        if node != context.local
    )
    vrid = int(context.deployment_id.replace("-", "")[:4], 16) % 254 + 1
    prefix = context.deployment.management_cidr.split("/")[1]
    return f"""global_defs {{
    enable_script_security
    script_user root
}}
vrrp_script haproxy_running {{
    script \"/usr/bin/systemctl is-active --quiet haproxy\"
    interval 2
    fall 2
    rise 1
}}
vrrp_instance regress_stack {{
    state BACKUP
    interface {context.local.management_interface}
    virtual_router_id {vrid}
    priority 100
    advert_int 1
    unicast_src_ip {context.local.address}
    unicast_peer {{
{peers}
    }}
    virtual_ipaddress {{
        {context.deployment.api_address}/{prefix}
    }}
    track_script {{
        haproxy_running
    }}
}}
"""


def setup() -> None:
    context = common.context()
    helpers = Path("/usr/local/lib/regress-stack")
    helpers.mkdir(parents=True, exist_ok=True)
    helpers.chmod(0o755)
    common.write(
        "/etc/sysctl.d/90-regress-stack-ha.conf",
        "net.ipv4.ip_nonlocal_bind=1\n",
        mode=0o644,
    )
    common.run("sysctl", ["-p", "/etc/sysctl.d/90-regress-stack-ha.conf"])
    common.write(
        "/etc/haproxy/mysql-check.cnf",
        "[client]\nuser=regress_check\npassword="
        + common.token(context.secret("mysql/check"))
        + "\n",
        user="haproxy",
    )
    common.write(
        "/usr/local/lib/regress-stack/mysql-primary",
        """#!/bin/sh
result=$(/usr/bin/mysql --defaults-extra-file=/etc/haproxy/mysql-check.cnf --batch --skip-column-names --connect-timeout=2 --host="$HAPROXY_SERVER_ADDR" --port="$HAPROXY_SERVER_PORT" --execute='SELECT @@global.read_only' 2>/dev/null) || exit 1
[ "$result" = 0 ]
""",
        mode=0o755,
    )
    common.write("/etc/haproxy/haproxy.cfg", configuration(context), mode=0o644)
    common.run("haproxy", ["-c", "-f", "/etc/haproxy/haproxy.cfg"])
    common.restart("haproxy")
    if len(context.deployment.controllers) == 3:
        common.write(
            "/etc/keepalived/keepalived.conf",
            keepalived_configuration(context),
            mode=0o600,
        )
        common.run(
            "keepalived",
            ["--config-test", "--use-file=/etc/keepalived/keepalived.conf"],
        )
        common.restart("keepalived")
