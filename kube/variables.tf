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
  description = "Image reference for the frr-sidecar container. Required when ospf != null; ignored otherwise."
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

variable "labels" {
  description = "Extra metadata labels merged into the pod and deployment labels."
  type        = map(string)
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
