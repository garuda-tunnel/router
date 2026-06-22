# AGENTS.md

Security and contribution rules for garuda-router.

## Security

- Never commit or use real public IP addresses. Use RFC5737 (TEST-NET) / RFC1918 / CGNAT ranges only.
- Never commit or use domains other than well-known examples or `example.net`.
- Never commit secrets, tokens, private keys, or customer data.

## Garuda platform rules

This repo is part of garuda-tunnel. Platform rules (annotation-layer design, MAP/VAP
injection engine, `garuda_guest` contract, vanilla guest contract, bootstrap timing,
Multus attach-race fix, anti-patterns):

**See: https://github.com/garuda-tunnel/garuda/blob/main/docs/AGENTS-platform.md**
Local path: `../garuda/docs/AGENTS-platform.md`

## Naming

Repo is `garuda-router` (renamed from `ipt_server`); chart stays `ipt-server`
(`oci://ghcr.io/garuda-tunnel/charts/ipt-server`); images stay `garuda-ipt-server`
and `garuda-powerdns`; TF inputs and Helm value keys keep `ipt_server`/`iptServer`.
The mismatch is intentional to avoid a breaking rename.
