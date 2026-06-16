mock_provider "helm" {}
mock_provider "kubernetes" {}

variables {
  namespace        = "garuda"
  name             = "ipt-server"
  ipt_server_image = "ghcr.io/alexmkx/garuda-ipt-server:latest"
  powerdns_image   = "ghcr.io/alexmkx/garuda-powerdns:latest"
  frr_image        = "ghcr.io/alexmkx/garuda-frr-sidecar:latest"
  routes = [
    {
      route = [{ gw = "192.0.2.130" }, { dev = "border" }]
      rules = [".*", "0.0.0.0/0"]
    }
  ]
  pbr_interfaces = ["backbone"]
  ospf = {
    router_id  = "198.51.100.99"
    interfaces = ["backbone"]
  }
}

run "chart_resolves_from_oci" {
  command = plan

  assert {
    condition     = helm_release.ipt_server.repository == "oci://ghcr.io/garuda-tunnel/charts"
    error_message = "helm_release.repository must be the garuda OCI charts registry"
  }
  assert {
    condition     = helm_release.ipt_server.chart == "ipt-server"
    error_message = "helm_release.chart must be the OCI chart name 'ipt-server'"
  }
  assert {
    condition     = can(regex("^\\d+\\.\\d+\\.\\d+$", helm_release.ipt_server.version))
    error_message = "helm_release.version must be an exact semver from var.chart_version"
  }
}

run "values_include_routes_and_python_pbr_interfaces" {
  command = plan

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"pbrInterfaces\":")
    error_message = "rendered values must include pbrInterfaces"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"rules\":")
    error_message = "rendered values must include route rules"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "- \"backbone\"")
    error_message = "rendered values must include backbone in pbrInterfaces"
  }
}

run "values_include_images_and_defaults" {
  command = plan

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"iptServer\": \"ghcr.io/alexmkx/garuda-ipt-server:latest\"")
    error_message = "rendered values must include images.iptServer"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"powerdns\": \"ghcr.io/alexmkx/garuda-powerdns:latest\"")
    error_message = "rendered values must include images.powerdns"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"pinningTtl\": 86400")
    error_message = "default pinningTtl must be 86400"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"pinningApiPort\": 80")
    error_message = "default pinningApiPort must be 80"
  }
}

run "nic_attach_default_is_backbone_only" {
  command = plan

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "- \"backbone\"")
    error_message = "default nic_attach must include backbone for PBR/OSPF/pinning runtime"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "- \"border\"")
    error_message = "default nic_attach must NOT include border (egress moved to border_router)"
  }
}

run "ospf_set_propagates_router_id_and_interfaces" {
  command = plan

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"router_id\": \"198.51.100.99\"")
    error_message = "rendered values must contain ospf.router_id"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "- \"backbone\"")
    error_message = "rendered values must contain ospf.interfaces entry"
  }
}

run "default_ospf_null_skips_frr" {
  command = plan

  variables {
    ospf = null
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "\"ospf\": null")
    error_message = "rendered values must contain 'ospf: null' when ospf is unset"
  }
}

run "output_deployment_name" {
  command = plan

  assert {
    condition     = output.deployment_name == "ipt-server"
    error_message = "output.deployment_name must equal var.name"
  }
}

run "reject_empty_pbr_interfaces" {
  command = plan

  variables {
    pbr_interfaces = []
  }

  expect_failures = [var.pbr_interfaces]
}

run "reject_empty_nic_attach" {
  command = plan

  variables {
    nic_attach = []
  }

  expect_failures = [var.nic_attach]
}

run "reject_invalid_ospf_router_id" {
  command = plan

  variables {
    ospf = {
      router_id  = "not-an-ip"
      interfaces = ["backbone"]
    }
  }

  expect_failures = [var.ospf]
}

run "empty_image_vars_omit_keys" {
  command = plan

  variables {
    namespace        = "garuda"
    name             = "ipt-server"
    ipt_server_image = ""
    powerdns_image   = ""
    frr_image        = ""
    routes = [
      {
        route = [{ gw = "192.0.2.130" }, { dev = "border" }]
        rules = [".*", "0.0.0.0/0"]
      }
    ]
    pbr_interfaces = ["backbone"]
    ospf = {
      router_id  = "198.51.100.99"
      interfaces = ["backbone"]
    }
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "garuda-ipt-server@sha256:")
    error_message = "empty ipt_server_image must omit iptServer key so chart digest wins"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "garuda-powerdns@sha256:")
    error_message = "empty powerdns_image must omit powerdns key so chart digest wins"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "\"frr\":")
    error_message = "empty frr_image must omit frr key"
  }
}

run "nonempty_image_vars_override" {
  command = plan

  variables {
    namespace        = "garuda"
    name             = "ipt-server"
    ipt_server_image = "ghcr.io/garuda-tunnel/garuda-ipt-server@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    powerdns_image   = "ghcr.io/garuda-tunnel/garuda-powerdns@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    frr_image        = ""
    routes = [
      {
        route = [{ gw = "192.0.2.130" }, { dev = "border" }]
        rules = [".*", "0.0.0.0/0"]
      }
    ]
    pbr_interfaces = ["backbone"]
    ospf = {
      router_id  = "198.51.100.99"
      interfaces = ["backbone"]
    }
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "garuda-ipt-server@sha256:1111111111111111111111111111111111111111111111111111111111111111")
    error_message = "nonempty ipt_server_image must appear in rendered values"
  }

  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "garuda-powerdns@sha256:2222222222222222222222222222222222222222222222222222222222222222")
    error_message = "nonempty powerdns_image must appear in rendered values"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "\"frr\":")
    error_message = "empty frr_image must omit frr key"
  }
}

run "mixed_image_vars_partial_override" {
  command = plan

  variables {
    namespace        = "garuda"
    name             = "ipt-server"
    ipt_server_image = "ghcr.io/garuda-tunnel/garuda-ipt-server@sha256:3333333333333333333333333333333333333333333333333333333333333333"
    powerdns_image   = ""
    frr_image        = ""
    routes = [
      {
        route = [{ gw = "192.0.2.130" }, { dev = "border" }]
        rules = [".*", "0.0.0.0/0"]
      }
    ]
    pbr_interfaces = ["backbone"]
    ospf = {
      router_id  = "198.51.100.99"
      interfaces = ["backbone"]
    }
  }

  # Only iptServer is overridden; powerdns + frr are omitted so their chart-pinned
  # defaults win. Proves partial-map merge() (the real CI single-key commit state).
  assert {
    condition     = strcontains(helm_release.ipt_server.values[0], "garuda-ipt-server@sha256:3333333333333333333333333333333333333333333333333333333333333333")
    error_message = "non-empty ipt_server_image must appear under images.iptServer"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "garuda-powerdns@sha256:")
    error_message = "with empty powerdns_image, the powerdns digest override must be omitted"
  }

  assert {
    condition     = !strcontains(helm_release.ipt_server.values[0], "\"frr\":")
    error_message = "with empty frr_image, the frr key must be omitted"
  }
}
