# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

output "inventory" {
  value = local.inventory
}

output "project" {
  value = lxd_project.ci.name
}

output "bridges" {
  value = [for network in lxd_network.bridge : network.name]
}
