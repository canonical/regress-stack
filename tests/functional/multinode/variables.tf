# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

variable "release" {
  type = string
  validation {
    condition     = contains(["jammy", "noble", "resolute"], var.release)
    error_message = "Choose jammy/yoga, noble/caracal, or resolute/gazpacho."
  }
}

variable "run_id" {
  type        = string
  description = "Unique identifier, also used for Linux bridge names."
  validation {
    condition     = can(regex("^rs[0-9a-f]{8}$", var.run_id))
    error_message = "run_id must be rs followed by eight hexadecimal digits."
  }
}

variable "storage_pool" {
  type        = string
  description = "An existing LXD storage pool; the test does not manage it."
}
