locals {
  runtime_patch_checksum = sha256(join("", [
    filesha256("${path.module}/charts/ipt-server/templates/deployment.yaml"),
    filesha256("${path.module}/charts/ipt-server/templates/runtime-patches.yaml"),
    filesha256("${path.module}/charts/ipt-server/files/sitecustomize.py"),
  ]))
}

resource "helm_release" "ipt_server" {
  name             = var.name
  namespace        = var.namespace
  create_namespace = false
  chart            = "${path.module}/charts/ipt-server"
  dependency_update = true

  values = [
    yamlencode({
      namespace = var.namespace
      name      = var.name
      images = {
        iptServer = var.ipt_server_image
        powerdns  = var.powerdns_image
        frr       = var.frr_image
      }
      routes         = var.routes
      pbrInterfaces  = var.pbr_interfaces
      nicAttach      = var.nic_attach
      cleanConntrack = var.clean_conntrack
      domainRouteTtl = var.domain_route_ttl
      pinningEgress  = var.pinning_egress
      pinningTtl     = var.pinning_ttl
      pinningApiPort = var.pinning_api_port
      runtimePatchChecksum = local.runtime_patch_checksum
      labels         = var.labels
      ospf           = var.ospf
    })
  ]
}
