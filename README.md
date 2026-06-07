# garuda-router

Terraform module + Helm chart + images for the IPT Server (PBR + nftables router with optional self-service pinning portal) in the Garuda topology.

## Name mapping

This repo is **garuda-router** (the planned rename of the legacy `ipt_server`
component). To avoid a gratuitous breaking rename, the chart, images, and
Terraform inputs keep their original names:

- TF module: `kube/` — consume via `git::https://github.com/garuda-tunnel/garuda-router.git//kube?ref=vX.Y.Z`.
- Helm chart: `oci://ghcr.io/garuda-tunnel/charts/ipt-server` (published on tag push).
- Images: `ghcr.io/garuda-tunnel/garuda-ipt-server` and `ghcr.io/garuda-tunnel/garuda-powerdns`.
- Module inputs and Helm value keys use `ipt_server`/`iptServer`.

See `kube/README.md` for module inputs/outputs and `AGENTS.md` for the FRR-sidecar reuse rule.
