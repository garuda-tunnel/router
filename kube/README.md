# ipt_server/kube

Deploys the `garuda_ipt` + `garuda_pdns` stack as one multi-container
Kubernetes Deployment, plus an optional FRR sidecar in the same pod
network namespace.

This module replaces the docker-compose stack previously rendered by the
ipt_server Ansible role. The container contract (env vars and capabilities)
is preserved; only the runtime plane shifts to Kubernetes.

## Inputs

| Variable | Required | Description |
|---|---|---|
| `chart_version` | no | Pinned OCI chart version (exact semver). Default `0.1.0`. |
| `namespace` | yes | Existing namespace, typically `module.garuda_k8s_hub.namespace`. |
| `name` | no | Deployment name. Default `ipt-server`. |
| `ipt_server_image` | no  | Image reference for the `garuda_ipt` container. Empty ⇒ use the chart's pinned digest. |
| `powerdns_image`   | no  | Image reference for the powerdns recursor container. Empty ⇒ use the chart's pinned digest. |
| `frr_image` | when `ospf != null` | Image reference for the `frr-sidecar` container. |
| `routes` | no | Route configuration list (`IPT_ROUTES_JSON`). |
| `pbr_interfaces` | no | Backbone-side PBR interfaces (`IPT_INTERFACES_JSON`). Default `["backbone"]`. |
| `nic_attach` | no | Multus secondary interfaces. Default `["backbone", "border"]`. |
| `clean_conntrack` | no | `IPT_CLEAN_CONNTRACK`. Default `false`. |
| `domain_route_ttl` | no | `IPT_DOMAIN_ROUTE_TTL`. Default `300`. |
| `pinning_egress` | no | `IPT_PINNING_EGRESS_JSON`. Default `{}`. |
| `pinning_ttl` | no | `IPT_PINNING_TTL`. Default `86400`. |
| `pinning_api_port` | no | `IPT_PINNING_API_PORT`. Default `80`. |
| `labels` | no | Extra metadata labels merged into pod/deployment labels. |
| `ospf` | no | Structured OSPF intent. When `null`, no FRR sidecar is rendered. |

### `ospf` object

| Field | Required | Description |
|---|---|---|
| `router_id` | yes | IPv4-formatted OSPF router-id. |
| `area` | no | Default `"0.0.0.0"`. |
| `interfaces` | yes | OSPF-participating interfaces. Typically `["backbone"]`. |
| `passive_interfaces` | no | Marked `ip ospf passive`. |
| `default_originate` | no | Default `false`. |
| `redistribute` | no | Subset of `["connected", "kernel", "static"]`. |
| `extra_frr_conf` | no | Free-form FRR config appended verbatim. |

## Outputs

| Output | Description |
|---|---|
| `deployment_name` | Equals `var.name`. |

## Providers

```hcl
module "ipt_server_kube" {
  source = "../../../modules/ipt_server/kube"

  providers = {
    helm       = helm.hub
    kubernetes = kubernetes.hub
  }

  namespace        = module.garuda_k8s_hub.namespace
  ipt_server_image = var.ipt_server_image
  powerdns_image   = var.ipt_powerdns_image
  routes           = local.ipt_routes
  pbr_interfaces   = ["backbone"]
  nic_attach       = ["backbone", "border"]

  ospf = {
    router_id  = "198.51.100.99"
    interfaces = ["backbone"]
  }
}
```

## What this module does NOT do

- It does not own the ipt_server Python application logic. The image
  contract (env vars and capabilities) is preserved; this module only
  re-packages how that image is delivered.
- It does not install Multus. See `modules/garuda_k8s` for that.
- It does not generate routes from higher-level topology. Callers
  compute `routes` and `pbr_interfaces` upstream (mini-site `locals.tf`).

## Mapping from the Ansible role

| Ansible variable | Module input |
|---|---|
| `ipt_routes` | `routes` |
| `ipt_interfaces` | `pbr_interfaces` |
| `ipt_nic_attach` | `nic_attach` |
| `ipt_clean_conntrack` | `clean_conntrack` |
| `ipt_domain_route_ttl` | `domain_route_ttl` |
| `ipt_pinning_egress` | `pinning_egress` |
| `ipt_pinning_ttl` | `pinning_ttl` |
| `ipt_pinning_api_port` | `pinning_api_port` |
| `ipt_server_image` | `ipt_server_image` |
| `ipt_powerdns_image` | `powerdns_image` |
| `ipt_server_labels` | `labels` |
