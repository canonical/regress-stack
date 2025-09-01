# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only
import pathlib
import platform

from regress_stack.core import apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import (
    cinder,
    glance,
    heat,
    keystone,
    mysql,
    neutron,
    nova,
    octavia,
    rabbitmq,
)
from regress_stack.modules import utils as module_utils

DEPENDENCIES = {keystone, mysql, rabbitmq, nova, neutron, heat, cinder, glance}
OPTIONAL_DEPENDENCIES = {octavia}
CONDUCTOR = "magnum-conductor"
PACKAGES = ["magnum-api", CONDUCTOR]
LOGS = ["/var/log/magnum/"]

CONF = "/etc/magnum/magnum.conf"
AUTH_POLICY = "/etc/magnum/keystone_auth_default_policy.json"
# Containers inside the coreos VM don't have DNS resolution necessarily working right
URL = f"http://{core_utils.my_ip()}:9511/v1"
SERVICE = "magnum"
SERVICE_TYPE = "container-infra"
MAGNUM_DOMAIN_ADMIN = "magnum_admin"
MAGNUM_ADMIN_DOMAIN_PASSWORD = "changeme"

TEST_INCLUDE_REGEXES = ["magnum_tempest_plugin"]
TEST_EXCLUDE_REGEXES = [
    # These two are failing and excluded from upstream CI
    "ClusterTest.test_create_cluster_with_zero_nodes",
    "ClusterTest.test_create_list_sign_delete_clusters",
    # Secure RBAC disabled for now
    "rbac",
    # Only works in `--serial`
    "ClusterTest.test_create_cluster_with_nonexisting_flavor",
]

AUTH_POLICY_TPL = """
[
   {
      "users":{
         "roles":[
            "k8s_admin"
         ],
         "projects":[
            "$PROJECT_ID"
         ]
      },
      "resource_permissions":{
         "*/*":[
            "*"
         ]
      },
      "nonresource_permissions":{
         "/healthz":[
            "get",
            "post"
         ]
      }
   },
   {
      "users":{
         "roles":[
            "k8s_developer"
         ],
         "projects":[
            "$PROJECT_ID"
         ]
      },
      "resource_permissions":{
         "!kube-system/['apiServices', 'bindings', 'componentstatuses', 'configmaps', 'cronjobs', 'customResourceDefinitions', 'deployments', 'endpoints', 'events', 'horizontalPodAutoscalers', 'ingresses', 'initializerConfigurations', 'jobs', 'limitRanges', 'localSubjectAccessReviews', 'namespaces', 'networkPolicies', 'persistentVolumeClaims', 'persistentVolumes', 'podDisruptionBudgets', 'podPresets', 'podTemplates', 'pods', 'replicaSets', 'replicationControllers', 'resourceQuotas', 'secrets', 'selfSubjectAccessReviews', 'serviceAccounts', 'services', 'statefulSets', 'storageClasses', 'subjectAccessReviews', 'tokenReviews']":[
            "*"
         ],
         "*/['clusterrolebindings', 'clusterroles', 'rolebindings', 'roles', 'controllerrevisions', 'nodes', 'podSecurityPolicies']":[
            "get",
            "list",
            "watch"
         ],
         "*/['certificateSigningRequests']":[
            "create",
            "delete",
            "get",
            "list",
            "watch",
            "update"
         ]
      }
   },
   {
      "users":{
         "roles":[
            "k8s_viewer"
         ],
         "projects":[
            "$PROJECT_ID"
         ]
      },
      "resource_permissions":{
         "!kube-system/['tokenReviews']":[
            "*"
         ],
         "!kube-system/['apiServices', 'bindings', 'componentstatuses', 'configmaps', 'cronjobs', 'customResourceDefinitions', 'deployments', 'endpoints', 'events', 'horizontalPodAutoscalers', 'ingresses', 'initializerConfigurations', 'jobs', 'limitRanges', 'localSubjectAccessReviews', 'namespaces', 'networkPolicies', 'persistentVolumeClaims', 'persistentVolumes', 'podDisruptionBudgets', 'podPresets', 'podTemplates', 'pods', 'replicaSets', 'replicationControllers', 'resourceQuotas', 'secrets', 'selfSubjectAccessReviews', 'serviceAccounts', 'services', 'statefulSets', 'storageClasses', 'subjectAccessReviews']":[
            "get",
            "list",
            "watch"
         ],
         "*/['clusterrolebindings', 'clusterroles', 'rolebindings', 'roles', 'controllerrevisions', 'nodes', 'podSecurityPolicies']":[
            "get",
            "list",
            "watch"
         ]
      }
   }
]
"""


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    domain = keystone.ensure_domain(SERVICE)
    magnum_domain_admin = keystone.ensure_user(
        MAGNUM_DOMAIN_ADMIN, MAGNUM_ADMIN_DOMAIN_PASSWORD, domain.id
    )
    keystone.grant_domain_role(magnum_domain_admin, keystone.admin_role(), domain)
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        ("DEFAULT", "host", core_utils.fqdn()),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("certificates", "cert_manager_type", "x509keypair"),
        ("cinder_client", "region_name", module_utils.REGION),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        ("keystone_auth", "auth_section", "keystone_authtoken"),
        ("conductor", "workers", "1"),
        ("api", "workers", "1"),
        ("oslo_messaging_notifications", "driver", "messagingv2"),
        *module_utils.dict_to_cfg_set_args(
            "trust",
            {
                "trustee_domain_name": SERVICE,
                "trustee_domain_admin_name": MAGNUM_DOMAIN_ADMIN,
                "trustee_domain_admin_password": MAGNUM_ADMIN_DOMAIN_PASSWORD,
                # cluster user trust necessary if using cinder for volumes
                "cluster_user_trust": "true",
            },
        ),
    )
    pathlib.Path(AUTH_POLICY).write_text(AUTH_POLICY_TPL)
    core_utils.sudo("magnum-db-manage", ["upgrade"], user=SERVICE)
    core_utils.restart_service("magnum-api")
    core_utils.restart_service("magnum-conductor")


COREOS_38 = "38.20230806.3.0"
COREOS_35 = "35.20220116.3.0"
COREOS_31 = "31.20200323.3.2"
COREOS = "https://builds.coreos.fedoraproject.org/prod/streams/stable/builds/{version}/{platform}/fedora-coreos-{version}-openstack.{platform}.qcow2.xz"


def configure_tempest(tempest_conf: pathlib.Path):
    """Configure tempest for magnum."""

    tempest_conf_dir = tempest_conf.parent

    version = apt.get_pkg_version(CONDUCTOR)

    coreos_version = None

    if version is None or version >= "17":
        coreos_version = COREOS_38
    elif "17" > version >= "14":
        coreos_version = COREOS_35
    else:
        coreos_version = COREOS_31

    coreos_image = COREOS.format(
        version=coreos_version, platform=platform.machine().lower()
    )
    filename = coreos_image.split("/")[-1]
    filepath = tempest_conf_dir / filename
    uncompressed_path = filepath.with_suffix("")
    if not uncompressed_path.exists():
        core_utils.run("wget", [coreos_image], cwd=str(tempest_conf_dir))
        core_utils.run("unxz", [filename], cwd=str(tempest_conf_dir))

    image = glance.ensure_image(
        "fedora-coreos",
        uncompressed_path,
        visibility="public",
        disk_format="qcow2",
        container_format="bare",
        properties={"os_distro": "fedora-coreos"},
    )

    flavor_minion = nova.ensure_flavor("magnum", ram=2048, vcpus=1, disk=15)
    flavor_master = nova.ensure_flavor("magnum-master", ram=4096, vcpus=2, disk=15)

    labels = {
        "kube_tag": "v1.28.9-rancher1",
        "container_runtime": "containerd",
        "containerd_version": "1.6.31",
        "containerd_tarball_sha256": "75afb9b9674ff509ae670ef3ab944ffcdece8ea9f7d92c42307693efa7b6109d",
        "cloud_provider_tag": "v1.27.3",
        "cinder_csi_plugin_tag": "v1.27.3",
        "k8s_keystone_auth_tag": "v1.27.3",
        "magnum_auto_healer_tag": "v1.27.3",
        "octavia_ingress_controller_tag": "v1.27.3",
        "calico_tag": "v3.26.4",
    }

    module_utils.cfg_set(
        str(tempest_conf),
        ("service_available", "magnum", "True"),
        *module_utils.dict_to_cfg_set_args(
            "magnum",
            {
                "image_id": image.id,
                "flavor_id": flavor_minion.id,
                "master_flavor_id": flavor_master.id,
                "nic_id": neutron.public_network().id,
                "docker_storage_driver": "overlay",
                # magnum_tempest_plugin uses `ast.literal_eval` on the labels field
                "labels": str(labels),
            },
        ),
    )
