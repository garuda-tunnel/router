#!/usr/bin/env bash
# Helm-level tests for modules/ipt_server/kube.
# helm lint + helm template diffed against tests/golden/*.yaml.
# Update goldens with: REGEN_GOLDEN=1 ./run-helm-tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="${SCRIPT_DIR}/../.."
CHART_DIR="${MODULE_DIR}/charts/ipt-server"
GOLDEN_DIR="${SCRIPT_DIR}/../golden"

# Resolve the frr-sidecar library dependency before templating. The
# wireguard/firezone runners already do this; ipt-server was missing it
# which caused `helm template` to fail with "no such file or directory"
# on first run after a clean checkout.
helm dependency update "${CHART_DIR}"

for scenario in default with-ospf; do
  values_file="${SCRIPT_DIR}/values-${scenario}.yaml"
  helm lint "${CHART_DIR}" -f "${values_file}"

  out="$(helm template ipt-server "${CHART_DIR}" --namespace garuda -f "${values_file}")"
  golden="${GOLDEN_DIR}/${scenario}.yaml"

  if [[ "${REGEN_GOLDEN:-0}" == "1" ]]; then
    printf '%s\n' "${out}" > "${golden}"
    echo "regenerated ${golden}"
    continue
  fi

  if ! diff -u "${golden}" <(printf '%s\n' "${out}"); then
    echo "golden mismatch for ${scenario}" >&2
    exit 1
  fi

  echo "ok: ${scenario}"
done
