# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

mock_provider "lxd" {}

override_resource {
  target = lxd_network.bridge["management"]
  values = { ipv4_address = "10.240.0.1/24" }
}
override_resource {
  target = lxd_network.bridge["provider"]
  values = { ipv4_address = "10.241.0.1/24" }
}

variables {
  release      = "noble"
  run_id       = "rs12345678"
  storage_pool = "test-pool"
}

run "hyperconverged" {
  command = apply

  assert {
    condition = (
      length(lxd_instance.node) == 3 &&
      alltrue([for node in lxd_instance.node :
        node.type == "virtual-machine" && node.image == "ubuntu:24.04" &&
        node.config["limits.cpu"] == "8" && node.config["limits.memory"] == "24GiB" &&
        length(node.profiles) == 0 &&
        alltrue([for device in node.device : device.name != "root" ||
          (device.properties["pool"] == "test-pool" && device.properties["size"] == "80GiB")
        ])
      ])
    )
    error_message = "The fixture must contain exactly three isolated 8-CPU/24GiB/80GiB VMs."
  }
  assert {
    condition = (
      output.inventory.profile == "hyperconverged" && length(output.inventory.computes) == 0 &&
      output.inventory.api_address == "10.240.0.10" &&
      output.inventory.controllers[*].address == ["10.240.0.11", "10.240.0.12", "10.240.0.13"] &&
      output.inventory.provider.gateway == "10.241.0.1" &&
      output.inventory.provider.allocation_start == "10.241.0.20" &&
      output.inventory.provider.allocation_end == "10.241.0.80"
    )
    error_message = "The inventory must reserve the VIP and provider allocation range."
  }
  assert {
    condition = alltrue([for node in lxd_instance.node :
      !yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0.dhcp4 &&
      !yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0.dhcp6 &&
      !yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0["accept-ra"] &&
      length(yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0["link-local"]) == 0 &&
      yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0.optional &&
      !contains(keys(yamldecode(node.config["cloud-init.network-config"]).ethernets.provider0), "addresses")
    ])
    error_message = "Provider interfaces must stay unnumbered."
  }
  assert {
    condition = (
      lxd_project.ci.config["features.networks"] == "false" &&
      lxd_project.ci.config["features.profiles"] == "true" &&
      alltrue([for network in lxd_network.bridge : network.project == "default" &&
        network.config["ipv4.dhcp"] == "false" && network.config["ipv4.nat"] == "true"
      ])
    )
    error_message = "Only test-owned bridges and a private VM project may be created."
  }
}

run "jammy_yoga" {
  command = apply
  variables { release = "jammy" }
  assert {
    condition     = alltrue([for node in lxd_instance.node : node.image == "ubuntu:22.04"])
    error_message = "Jammy/Yoga must use the Ubuntu 22.04 image."
  }
}

run "resolute_gazpacho" {
  command = apply
  variables { release = "resolute" }
  assert {
    condition     = alltrue([for node in lxd_instance.node : node.image == "ubuntu:26.04"])
    error_message = "Resolute/Gazpacho must use the Ubuntu 26.04 image."
  }
}
