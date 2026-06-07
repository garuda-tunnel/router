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

run "chart_path_resolves_to_bundled_chart" {
  command = plan

  assert {
    condition     = endswith(helm_release.ipt_server.chart, "/charts/ipt-server")
    error_message = "helm_release.ipt_server.chart must point at $${path.module}/charts/ipt-server"
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
