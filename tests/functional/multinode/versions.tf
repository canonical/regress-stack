# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    lxd = {
      source  = "terraform-lxd/lxd"
      version = "3.0.2"
    }
  }
}

provider "lxd" {
  default_remote = "local"
  remote {
    name    = "local"
    address = "unix:///var/snap/lxd/common/lxd/unix.socket"
  }
  remote {
    name     = "ubuntu"
    address  = "https://cloud-images.ubuntu.com/releases"
    protocol = "simplestreams"
  }
}
