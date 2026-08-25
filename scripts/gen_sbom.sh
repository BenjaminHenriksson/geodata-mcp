#!/usr/bin/env bash
# Regenerate per-service CycloneDX SBOMs for geodata-mcp v2 and merge them into
# one aggregate BOM. Supports ska-krav #4 (SBOM) and #23 (dependency inventory)
# of the Sundsvalls kommun tender UH-2026-159.
#
# The checked-in sbom/geodata-mcp.cdx.json is a curated, human-reviewed aggregate
# of DIRECT dependencies plus notable transitives. This script produces the FULL
# transitive closure per service (for CI / vulnerability scanning) and, when a
# merge tool is available, a single merged closure at sbom/geodata-mcp.full.cdx.json.
#
# Usage:
#   scripts/gen_sbom.sh                 # generate + merge (best effort)
#   OUT_DIR=build/sbom scripts/gen_sbom.sh
#
# Requirements (install into an isolated venv so they don't pollute a service):
#   uv tool install cyclonedx-bom          # provides `cyclonedx-py`
#   # optional, for merging:  uv tool install cyclonedx-cli   OR  npm i -g @cyclonedx/cyclonedx-cli
#
# CI note: run this in a job that only READS the repo; the generated files under
# ${OUT_DIR} are build artefacts. Compare against the checked-in curated BOM and
# fail the build if a new *direct* dependency appears that is not yet inventoried
# in docs/beroenden.md.

set -euo pipefail

# Resolve repo root from this script's location (scripts/ -> repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/sbom}"
SPEC_VERSION="${SPEC_VERSION:-1.5}"

mkdir -p "${OUT_DIR}"

# --- locate the cyclonedx-py generator --------------------------------------
CDX_PY=""
if command -v cyclonedx-py >/dev/null 2>&1; then
  CDX_PY="cyclonedx-py"
elif command -v uvx >/dev/null 2>&1; then
  # Run without a persistent install.
  CDX_PY="uvx --from cyclonedx-bom cyclonedx-py"
else
  echo "ERROR: cyclonedx-py not found. Install with:  uv tool install cyclonedx-bom" >&2
  echo "       (or ensure 'uvx' is on PATH so this script can fetch it on demand)." >&2
  exit 127
fi
echo "Using generator: ${CDX_PY}"

generated=()

# --- pip/requirements-based services ----------------------------------------
# cyclonedx-py requirements <file> reads a requirements.txt directly. This
# captures the DECLARED (constraint) dependencies; for the resolved closure of a
# built image, run `cyclonedx-py environment` inside the running container instead.
gen_requirements() {
  local svc="$1" req="$2"
  local out="${OUT_DIR}/${svc}.cdx.json"
  echo ">> ${svc}: ${req}"
  # shellcheck disable=SC2086
  ${CDX_PY} requirements "${req}" \
    --spec-version "${SPEC_VERSION}" \
    --output-format JSON \
    --outfile "${out}"
  generated+=("${out}")
}

gen_requirements mcp    "${ROOT_DIR}/services/mcp/requirements.txt"
gen_requirements viewer "${ROOT_DIR}/services/viewer/requirements.txt"
gen_requirements worker "${ROOT_DIR}/services/worker/requirements.txt"

# --- uv/pyproject-based service (segmenter) ---------------------------------
# The segmenter ships a uv.lock, so we get the fully resolved closure.
SEG_DIR="${ROOT_DIR}/services/segmenter"
SEG_OUT="${OUT_DIR}/segmenter.cdx.json"
echo ">> segmenter: ${SEG_DIR}"
if ${CDX_PY} uv --help >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  ${CDX_PY} uv \
    --lock-file "${SEG_DIR}/uv.lock" \
    --spec-version "${SPEC_VERSION}" \
    --output-format JSON \
    --outfile "${SEG_OUT}"
else
  # Fallback for older cyclonedx-py without the 'uv' subcommand: export a
  # requirements list from the lock, then parse that.
  echo "   (cyclonedx-py has no 'uv' subcommand; exporting requirements from uv.lock)"
  ( cd "${SEG_DIR}" && uv export --format requirements-txt --no-hashes --all-extras \
      > "${OUT_DIR}/segmenter.requirements.txt" )
  # shellcheck disable=SC2086
  ${CDX_PY} requirements "${OUT_DIR}/segmenter.requirements.txt" \
    --spec-version "${SPEC_VERSION}" \
    --output-format JSON \
    --outfile "${SEG_OUT}"
fi
generated+=("${SEG_OUT}")

echo
echo "Per-service SBOMs written:"
for f in "${generated[@]}"; do echo "  - ${f}"; done

# --- merge into one aggregate closure ---------------------------------------
# cyclonedx-cli can merge several BOMs into one. This is optional: the curated
# sbom/geodata-mcp.cdx.json remains the canonical hand-reviewed deliverable.
MERGED="${OUT_DIR}/geodata-mcp.full.cdx.json"
MERGE_CLI=""
if command -v cyclonedx-cli >/dev/null 2>&1; then
  MERGE_CLI="cyclonedx-cli"
elif command -v cyclonedx >/dev/null 2>&1; then
  MERGE_CLI="cyclonedx"
fi

if [ -n "${MERGE_CLI}" ]; then
  echo
  echo ">> Merging ${#generated[@]} SBOMs -> ${MERGED}"
  merge_args=()
  for f in "${generated[@]}"; do merge_args+=(--input-files "${f}"); done
  "${MERGE_CLI}" merge "${merge_args[@]}" \
    --output-format json \
    --output-file "${MERGED}" \
    --name geodata-mcp --version 2
  echo "Merged closure: ${MERGED}"
else
  echo
  echo "NOTE: no cyclonedx-cli found; skipping merge step."
  echo "      Install with 'npm i -g @cyclonedx/cyclonedx-cli' (or 'uv tool install cyclonedx-cli')"
  echo "      then merge the per-service files listed above, e.g.:"
  echo "        cyclonedx-cli merge \\"
  for f in "${generated[@]}"; do echo "          --input-files ${f} \\"; done
  echo "          --output-format json --output-file ${MERGED} --name geodata-mcp --version 2"
fi

echo
echo "Done. Canonical curated BOM stays at: ${ROOT_DIR}/sbom/geodata-mcp.cdx.json"
echo "Update docs/beroenden.md whenever a new DIRECT dependency is introduced."
