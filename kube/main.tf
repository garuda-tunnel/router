locals {
  images_override = merge(
    var.ipt_server_image == "" ? {} : { iptServer = var.ipt_server_image },
    var.powerdns_image == "" ? {} : { powerdns = var.powerdns_image },
    var.frr_image == "" ? {} : { frr = var.frr_image },
  )
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
      iptServer      = { mssClampValue = var.mss_clamp_value }
      labels         = var.labels
      ospf           = var.ospf
    })
  ]
}
