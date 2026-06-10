# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import logging
import pathlib
import tempfile
import time
import typing
import uuid
from urllib import request
from urllib.error import HTTPError, URLError

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql, rabbitmq}
PACKAGES = [
    "mistral-api",
    "mistral-engine",
    "mistral-executor",
    "mistral-event-engine",
    "python3-mistralclient",
]
LOGS = ["/var/log/mistral/"]

CONF = "/etc/mistral/mistral.conf"
SERVICE = "mistral"
SERVICE_TYPE = "workflowv2"
URL = f"http://{core_utils.my_ip()}:8989/v2"
API_SERVICES = ["mistral-api"]
ENGINE_SERVICES = ["mistral-engine", "mistral-executor", "mistral-event-engine"]
API_TIMEOUT = 60
SMOKE_TIMEOUT = 60


def _mistral(args: typing.Sequence[str]) -> str:
    return core_utils.run("mistral", list(args), env=keystone.auth_env())


def _openstack(args: typing.Sequence[str], system_scope: bool = False) -> str:
    env = keystone.system_auth_env() if system_scope else keystone.auth_env()
    return core_utils.run("openstack", list(args), env=env)


def _http_status(url: str) -> int | None:
    req = request.Request(url, method="GET")
    opener = request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(req, timeout=5) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except URLError:
        return None


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _wait_for_api() -> None:
    deadline = time.monotonic() + API_TIMEOUT
    while time.monotonic() < deadline:
        status = _http_status(URL)
        if status in {200, 300, 301, 302, 401}:
            return
        LOG.info("Waiting for mistral-api to start...")
        time.sleep(5)
    status = _http_status(URL)
    raise RuntimeError(f"Unexpected status from {URL}: {status!r}")


def setup() -> None:
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("api", "host", core_utils.my_ip()),
        ("api", "port", "8989"),
        ("executor", "type", "local"),
        ("engine", "host", core_utils.fqdn()),
        ("engine", "topic", "mistral_engine"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "service_credentials", keystone.account_dict(username, password)
        ),
    )
    core_utils.sudo("mistral-db-manage", ["upgrade", "head"], user=SERVICE)
    for daemon in API_SERVICES + ENGINE_SERVICES:
        core_utils.restart_service(daemon)
    _wait_for_api()


def smoke_test(_workspace_dir: pathlib.Path) -> None:
    _wait_for_api()
    services = json.loads(
        _openstack(["endpoint", "list", "--service", SERVICE, "-f", "json"])
    )
    if len(services) < 3:
        raise RuntimeError("Mistral service endpoints are incomplete")
    _run_smoke_workflow(_workspace_dir)


def _run_smoke_workflow(workspace_dir: pathlib.Path | None) -> None:
    workflow_name = f"regress-stack-mistral-smoke-{uuid.uuid4().hex[:8]}"
    workflow_yaml = (
        "version: '2.0'\n\n"
        f"{workflow_name}:\n"
        "  type: direct\n"
        "  tasks:\n"
        "    finish:\n"
        "      action: std.noop\n"
    )
    if workspace_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="regress-stack-mistral-")
        workflow_path = pathlib.Path(temp_dir.name) / "workflow.yaml"
    else:
        temp_dir = None
        workspace_dir.mkdir(parents=True, exist_ok=True)
        workflow_path = workspace_dir / f"{workflow_name}.yaml"
    workflow_path.write_text(workflow_yaml)
    created = False
    try:
        _mistral(["workflow-create", str(workflow_path), "-f", "json"])
        created = True
        execution = json.loads(
            _mistral(["execution-create", workflow_name, "{}", "-f", "json"])
        )
        execution_id = _json_field(execution, "id")
        deadline = time.monotonic() + SMOKE_TIMEOUT
        while time.monotonic() < deadline:
            status = json.loads(_mistral(["execution-get", execution_id, "-f", "json"]))
            state = _json_field(status, "state").upper()
            if state == "SUCCESS":
                return
            if state in {"ERROR", "CANCELLED"}:
                raise RuntimeError(f"Mistral smoke workflow failed with state {state}")
            time.sleep(2)
        raise RuntimeError("Timed out waiting for Mistral smoke workflow to finish")
    finally:
        if created:
            try:
                _mistral(["workflow-delete", workflow_name])
            except Exception:
                LOG.exception(
                    "Failed to clean up Mistral smoke workflow %s", workflow_name
                )
        if temp_dir is not None:
            temp_dir.cleanup()


def _json_field(
    data: dict[str, typing.Any] | list[dict[str, typing.Any]], field: str
) -> str:
    if isinstance(data, list):
        if not data:
            raise RuntimeError(f"No records returned while looking for {field!r}")
        data = data[0]
    for key, value in data.items():
        if key.lower() == field.lower():
            return str(value)
    raise RuntimeError(f"Field {field!r} not found in {data!r}")
