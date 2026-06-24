locals {
  effective_mtu = var.mtu_policy.site_mtu != null ? var.mtu_policy.site_mtu : var.mtu_policy.effective_mtu
  fixed_mss     = var.mtu_policy.site_mtu != null ? var.mtu_policy.site_mtu - 40 : var.mtu_policy.fixed_mss
  # mss_clamp_enabled defaults to true via optional(bool, true) in the policy type.
  # Task 5 wires it to IPT_MSS_CLAMP_ENABLED to gate the ipt_server_mss nft table install.
  mss_clamp_enabled = var.mtu_policy.mss_clamp_enabled

  images_override = merge(
    var.ipt_server_image == "" ? {} : { iptServer = var.ipt_server_image },
    var.powerdns_image == "" ? {} : { powerdns = var.powerdns_image },
    # frr key dropped: frr-sidecar is now MAP-injected (Decision #5 — var.frr_image kept inert below).
  )
}

resource "kubernetes_config_map" "garuda_extra" {
  for_each = var.configmaps
  metadata {
    name      = each.key
    namespace = var.namespace
  }
  # each.value is a { filename => content } map (Decision #11).
  data = each.value
}

resource "helm_release" "ipt_server" {
  name             = var.name
  namespace        = var.namespace
  create_namespace = false

  # Consume the published chart from OCI by an exact pinned version.
  # Source stays in kube/charts/ipt-server for release-please / CI / local dev.
  repository = "oci://ghcr.io/garuda-tunnel/charts"
  chart      = "ipt-server"
  version    = var.chart_version

  # No-op for the OCI path (dependency is vendored in the published tgz);
  # kept so the local-path dev/hotfix escape hatch still resolves frr-sidecar.
  dependency_update = true

  values = [
    yamlencode({
      namespace      = var.namespace
      name           = var.name
      images         = local.images_override
      routes         = var.routes
      pbrInterfaces  = var.pbr_interfaces
      nicAttach      = var.nic_attach
      cleanConntrack = var.clean_conntrack
      domainRouteTtl = var.domain_route_ttl
      pinningEgress  = var.pinning_egress
      pinningTtl     = var.pinning_ttl
      pinningApiPort = var.pinning_api_port
      mtuPolicy      = {
        fixedMss        = local.fixed_mss
        mssClampEnabled = local.mss_clamp_enabled
      }
      # podLabels/podAnnotations: pass garuda_guest outputs to chart pod template (Decision #2).
      podLabels      = var.labels
      podAnnotations = var.annotations
      labels         = var.labels
      ospf           = var.ospf
    })
  ]

  depends_on = [kubernetes_config_map.garuda_extra]
}
