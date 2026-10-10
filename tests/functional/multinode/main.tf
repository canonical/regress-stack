# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

locals {
  versions = { jammy = "22.04", noble = "24.04", resolute = "26.04" }
  nodes    = { node1 = 1, node2 = 2, node3 = 3 }
}

resource "lxd_project" "ci" {
  name = "${var.run_id}-ci"
  config = {
    "features.images"   = "false"
    "features.profiles" = "true"
    "features.networks" = "false"
  }
}

# Bridge networks belong to the default project. LXD selects unused /24s.
# Both networks use static guest addressing, reserving the VIP and floating IPs.
resource "lxd_network" "bridge" {
  for_each = toset(["management", "provider"])
  name     = "${var.run_id}-${substr(each.key, 0, 1)}"
  project  = "default"
  type     = "bridge"
  config = {
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "false"
    "ipv6.address" = "none"
  }
}

locals {
  management = lxd_network.bridge["management"].ipv4_address
  provider   = lxd_network.bridge["provider"].ipv4_address
  inventory = {
    schema  = 1
    profile = "hyperconverged"
    controllers = [for name, index in local.nodes : {
      name                 = name
      address              = cidrhost(local.management, 10 + index)
      management_interface = "mgmt0"
      provider_interface   = "provider0"
    }]
    computes        = []
    api_address     = cidrhost(local.management, 10)
    management_cidr = "${cidrhost(local.management, 0)}/24"
    provider = {
      cidr             = "${cidrhost(local.provider, 0)}/24"
      gateway          = cidrhost(local.provider, 1)
      allocation_start = cidrhost(local.provider, 20)
      allocation_end   = cidrhost(local.provider, 80)
    }
  }
}

resource "lxd_instance" "node" {
  for_each = local.nodes
  name     = each.key
  project  = lxd_project.ci.name
  type     = "virtual-machine"
  image    = "ubuntu:${local.versions[var.release]}"
  profiles = []

  config = {
    "limits.cpu"           = "8"
    "limits.memory"        = "24GiB"
    "cloud-init.user-data" = "#cloud-config\n${yamlencode({ hostname = each.key, manage_etc_hosts = true })}"
    "cloud-init.network-config" = yamlencode({
      version = 2
      ethernets = {
        mgmt0 = {
          match       = { macaddress = format("00:16:3e:01:00:%02x", each.value) }
          "set-name"  = "mgmt0"
          dhcp4       = false
          dhcp6       = false
          addresses   = ["${cidrhost(local.management, 10 + each.value)}/24"]
          routes      = [{ to = "default", via = cidrhost(local.management, 1) }]
          nameservers = { addresses = [cidrhost(local.management, 1)] }
        }
        provider0 = {
          match        = { macaddress = format("00:16:3e:02:00:%02x", each.value) }
          "set-name"   = "provider0"
          dhcp4        = false
          dhcp6        = false
          "accept-ra"  = false
          "link-local" = []
          optional     = true
        }
      }
    })
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.storage_pool
      size = "80GiB"
    }
  }
  device {
    name = "management"
    type = "nic"
    properties = {
      network = lxd_network.bridge["management"].name
      name    = "mgmt0"
      hwaddr  = format("00:16:3e:01:00:%02x", each.value)
    }
  }
  device {
    name = "provider"
    type = "nic"
    properties = {
      network = lxd_network.bridge["provider"].name
      name    = "provider0"
      hwaddr  = format("00:16:3e:02:00:%02x", each.value)
    }
  }
  wait_for {
    type = "agent"
  }
  timeouts = {
    create = "20m"
    delete = "10m"
  }
  lifecycle {
    precondition {
      condition = (
        cidrnetmask(local.management) == "255.255.255.0" &&
        cidrnetmask(local.provider) == "255.255.255.0"
      )
      error_message = "The test fixture requires LXD to allocate /24 IPv4 networks."
    }
  }
}
