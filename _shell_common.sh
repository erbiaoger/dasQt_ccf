#!/usr/bin/env bash
# Shared helpers for 03_ccf shell wrappers.

set -euo pipefail

CCF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASQT_REPO_DIR="/Users/zhangzhiyu/MyProjects/dasQt"
DASQT_VENV_ACTIVATE="${DASQT_REPO_DIR}/.venv/bin/activate"

ensure_dasqt_env() {
  if [[ ! -d "${DASQT_REPO_DIR}" ]]; then
    echo "[ERROR] dasQt repo not found: ${DASQT_REPO_DIR}" >&2
    exit 1
  fi
  if [[ ! -f "${DASQT_VENV_ACTIVATE}" ]]; then
    echo "[ERROR] Project virtualenv activate script not found: ${DASQT_VENV_ACTIVATE}" >&2
    echo "Run inside the dasQt project and create .venv first." >&2
    exit 1
  fi
}

run_uv_python() {
  ensure_dasqt_env
  (
    cd "${DASQT_REPO_DIR}"
    # shellcheck disable=SC1090
    source "${DASQT_VENV_ACTIVATE}"
    uv run python "$@"
  )
}

print_common_footer() {
  cat <<'EOF'

Extra passthrough:
  Put `--` at the end of this shell command, then append any extra Python CLI flags.
  Example:
    ./your_script.sh ... -- --dry-run
EOF
}
