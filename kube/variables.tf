variable "chart_version" {
  description = "Pinned OCI chart version (exact semver). Bumped in lockstep with Chart.yaml by release-please."
  type        = string
  default     = "1.2.1" # x-release-please-version

  validation {
    condition     = can(regex("^\\d+\\.\\d+\\.\\d+$", var.chart_version))
    error_message = "chart_version must be exact semver MAJOR.MINOR.PATCH (no range, no 'latest')."
  }
}

variable "namespace" {
  description = "Existing Kubernetes namespace, sourced from module.garuda_k8s.namespace."
  type        = string
}

variable "name" {
  description = "Deployment name, default 'ipt-server'."
  type        = string
  default     = "ipt-server"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.name))
    error_message = "name must be a valid DNS-1123 label."
  }
}

variable "ipt_server_image" {
  description = "Image reference for the garuda_ipt container. Empty ⇒ use the chart's pinned digest."
  type        = string
  default     = ""
}

variable "powerdns_image" {
  description = "Image reference for the powerdns recursor container. Empty ⇒ use the chart's pinned digest."
  type        = string
  default     = ""
}

variable "frr_image" {
  # DEPRECATED (Decision #5 — Phase 4+5): frr-sidecar is now MAP-injected; this variable is inert.
  # Callers may keep passing it without breaking anything. Will be removed in a future cleanup phase.
  description = "DEPRECATED: frr-sidecar image override. No-op since Phase 4+5 — MAP-injected. Kept to avoid caller-breaking variable removal."
  type        = string
  default     = ""
}

variable "routes" {
  description = <<EOT
Route configuration consumed by ipt_server. Mirrors the
`ipt_routes` payload of the Ansible role and is passed verbatim to the
container as the `IPT_ROUTES_JSON` env var.
EOT
  type = list(object({
    route = list(map(string))
    rules = list(string)
  }))
  default = []
}

variable "pbr_interfaces" {
  description = "Backbone-side interfaces that consume the PBR routing table. Becomes IPT_INTERFACES_JSON, matching the Python runtime contract."
  type        = list(string)
  default     = ["backbone"]

  validation {
    condition     = length(var.pbr_interfaces) > 0
    error_message = "pbr_interfaces must be non-empty."
  }
}

variable "nic_attach" {
  description = "Secondary networks the pod attaches to via Multus. Becomes the k8s.v1.cni.cncf.io/networks annotation. ipt_server is backbone-only; local egress now lives in modules/border_router."
  type        = list(string)
  default     = ["backbone"]

  validation {
    condition     = length(var.nic_attach) > 0
    error_message = "nic_attach must be non-empty."
  }
}

variable "clean_conntrack" {
  description = "Whether ipt_server should periodically clean conntrack entries (IPT_CLEAN_CONNTRACK)."
  type        = bool
  default     = false
}

variable "domain_route_ttl" {
  description = "Default TTL for domain-derived routes (IPT_DOMAIN_ROUTE_TTL)."
  type        = number
  default     = 300
}

variable "pinning_egress" {
  description = "Pinning egress map consumed by ipt_server (IPT_PINNING_EGRESS_JSON)."
  type        = map(any)
  default     = {}
}

variable "pinning_ttl" {
  description = "Pinning TTL in seconds (IPT_PINNING_TTL)."
  type        = number
  default     = 86400
}

variable "pinning_api_port" {
  description = "Pinning self-service portal API port (IPT_PINNING_API_PORT)."
  type        = number
  default     = 80
}

variable "mtu_policy" {
  description = "Site MTU/MSS policy. site_mtu derives effective_mtu and fixed_mss; otherwise effective_mtu and fixed_mss must be supplied explicitly."
  nullable    = false

  type = object({
    site_mtu          = optional(number)
    effective_mtu     = optional(number)
    fixed_mss         = optional(number)
    mss_clamp_enabled = optional(bool, true)
  })

  validation {
    condition = (
      (var.mtu_policy.site_mtu != null && var.mtu_policy.effective_mtu == null && var.mtu_policy.fixed_mss == null) ||
      (var.mtu_policy.site_mtu == null && var.mtu_policy.effective_mtu != null && var.mtu_policy.fixed_mss != null)
    )
    error_message = "Set either mtu_policy.site_mtu or both mtu_policy.effective_mtu and mtu_policy.fixed_mss."
  }

  validation {
    condition = (
      var.mtu_policy.site_mtu == null ||
      (var.mtu_policy.site_mtu >= 1280 && var.mtu_policy.site_mtu <= 1420)
    )
    error_message = "mtu_policy.site_mtu must be between 1280 and 1420."
  }

  validation {
    condition = (
      var.mtu_policy.effective_mtu == null ||
      (var.mtu_policy.effective_mtu >= 1280 && var.mtu_policy.effective_mtu <= 1420)
    )
    error_message = "mtu_policy.effective_mtu must be between 1280 and 1420."
  }

  validation {
    condition = (
      var.mtu_policy.fixed_mss == null ||
      (var.mtu_policy.fixed_mss >= 536 && var.mtu_policy.fixed_mss <= 1460)
    )
    error_message = "mtu_policy.fixed_mss must be between 536 and 1460."
  }

  validation {
    condition = (
      var.mtu_policy.fixed_mss == null ||
      var.mtu_policy.effective_mtu == null ||
      var.mtu_policy.fixed_mss <= var.mtu_policy.effective_mtu - 40
    )
    error_message = "mtu_policy.fixed_mss must be less than or equal to mtu_policy.effective_mtu - 40."
  }
}

variable "labels" {
  description = "Extra metadata labels merged into the pod and deployment labels."
  type        = map(string)
  default     = {}
}

variable "annotations" {
  description = "Pod-template annotations. From garuda_guest.annotations."
  type        = map(string)
  default     = {}
}

variable "configmaps" {
  description = "Extra ConfigMaps to create before pod admission. From garuda_guest.configmaps."
  type        = map(map(string))
  default     = {}
}

variable "ospf" {
  description = <<EOT
Structured OSPF intent. When null, no FRR sidecar is rendered. Interfaces
typically include `backbone` (the Multus secondary interface used for
transit).

The type is intentionally narrower than the `ospf` object on
wireguard/kube and firezone/kube modules: ipt_server is a provider by
definition, so provider invariants are hardcoded inside the chart
template (`charts/ipt-server/templates/deployment.yaml`) rather than
exposed here, and callers must not attempt to override them.
EOT
  type = object({
    router_id  = string
    interfaces = list(string)
  })
  default = null

  validation {
    condition     = var.ospf == null || can(regex("^\\d+\\.\\d+\\.\\d+\\.\\d+$", var.ospf.router_id))
    error_message = "ospf.router_id must be an IPv4-formatted string."
  }

  validation {
    condition     = var.ospf == null || length(var.ospf.interfaces) > 0
    error_message = "ospf.interfaces must be non-empty when ospf is set."
  }
}
