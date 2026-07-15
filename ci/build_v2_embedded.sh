#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
CI_DIR="${ROOT}/.ci-v2-embedded"
BUILD_DIR="${ROOT}/build-v2-embedded"
PROJECT_DIR="${CI_DIR}/project"
ENGINE_DIR="${CI_DIR}/engine"
TEMPLATE_DIR="${CI_DIR}/template"
ARCHIVE="${CI_DIR}/vcgame-v2.tar.gz"

VOXEL_TOOLS_TAG="v1.6"
GODOT_EDITOR_ARCHIVE="godot.linuxbsd.editor.x86_64.zip"
GODOT_EDITOR_SHA256="98e2ead648590ae135d2f162e225433d15d17c84ff90a4b41384c4b1f4bcf6ae"
WINDOWS_TEMPLATE_ARCHIVE="godot.windows.template_release.x86_64.exe.zip"
WINDOWS_TEMPLATE_SHA256="9556c3893a07f39451c654789e16a057eec3d344428044b0d3e7ed44ae905857"

mkdir -p "${PROJECT_DIR}" "${ENGINE_DIR}" "${TEMPLATE_DIR}" "${BUILD_DIR}/windows"

check_log() {
  local log_file="$1"
  local stage="$2"
  if grep -Eqi 'SCRIPT ERROR|Parse Error|Failed to load script|Invalid call|Invalid assignment|Cannot get class|No loader found for resource' "${log_file}"; then
    echo "${stage} failed: script, resource, or runtime error detected." >&2
    return 1
  fi
}

run_logged() {
  local log_file="$1"
  shift
  set +e
  "$@" >"${log_file}" 2>&1
  local exit_code=$?
  set -e
  cat "${log_file}"
  return "${exit_code}"
}

echo '== Reconstruct verified V2 project =='
python3 "${ROOT}/source/v2/repair_bundle.py" "${ROOT}/source/v2" "${ARCHIVE}"
tar -xzf "${ARCHIVE}" -C "${PROJECT_DIR}"
test -f "${PROJECT_DIR}/project.godot"
test -f "${PROJECT_DIR}/scripts/world/world_generator.gd"
test -f "${PROJECT_DIR}/scripts/player/player_controller.gd"

echo '== Build loader-free atlas and audio resources =='
python3 "${ROOT}/ci/patch_embedded_assets.py" "${PROJECT_DIR}"
test -s "${PROJECT_DIR}/scripts/core/embedded_assets.gd"
if grep -RInE 'load\("res://assets/(textures|audio)/|preload\("res://assets/(textures|audio)/' \
  "${PROJECT_DIR}/scripts"; then
  echo 'A raw runtime PNG/WAV load remained after the embedded-resource patch.' >&2
  exit 1
fi

echo '== Download pinned custom Godot and export template =='
base_url="https://github.com/Zylann/godot_voxel/releases/download/${VOXEL_TOOLS_TAG}"
curl --fail --location --retry 5 --retry-all-errors \
  --output "${CI_DIR}/${GODOT_EDITOR_ARCHIVE}" \
  "${base_url}/${GODOT_EDITOR_ARCHIVE}"
curl --fail --location --retry 5 --retry-all-errors \
  --output "${CI_DIR}/${WINDOWS_TEMPLATE_ARCHIVE}" \
  "${base_url}/${WINDOWS_TEMPLATE_ARCHIVE}"
echo "${GODOT_EDITOR_SHA256}  ${CI_DIR}/${GODOT_EDITOR_ARCHIVE}" | sha256sum --check
echo "${WINDOWS_TEMPLATE_SHA256}  ${CI_DIR}/${WINDOWS_TEMPLATE_ARCHIVE}" | sha256sum --check
unzip -q "${CI_DIR}/${GODOT_EDITOR_ARCHIVE}" -d "${ENGINE_DIR}"
unzip -q "${CI_DIR}/${WINDOWS_TEMPLATE_ARCHIVE}" -d "${TEMPLATE_DIR}"

GODOT_BIN="$(find "${ENGINE_DIR}" -type f -name 'godot*' ! -name '*.zip' -print -quit)"
TEMPLATE_BIN="$(find "${TEMPLATE_DIR}" -type f -iname '*.exe' -print -quit)"
if [[ -z "${GODOT_BIN}" || ! -f "${GODOT_BIN}" ]]; then
  echo 'Custom Godot editor was not found.' >&2
  find "${ENGINE_DIR}" -maxdepth 4 -type f -print >&2
  exit 1
fi
if [[ -z "${TEMPLATE_BIN}" || ! -f "${TEMPLATE_BIN}" ]]; then
  echo 'Windows export template was not found.' >&2
  find "${TEMPLATE_DIR}" -maxdepth 4 -type f -print >&2
  exit 1
fi
chmod +x "${GODOT_BIN}"
GODOT_BIN="$(realpath "${GODOT_BIN}")"
TEMPLATE_BIN="$(realpath "${TEMPLATE_BIN}")"

echo '== Configure pinned Windows export template =='
PROJECT_DIR="${PROJECT_DIR}" TEMPLATE_PATH="${TEMPLATE_BIN}" python3 - <<'PY'
from pathlib import Path
import os

preset = Path(os.environ["PROJECT_DIR"]) / "export_presets.cfg"
text = preset.read_text(encoding="utf-8")
template = os.environ["TEMPLATE_PATH"].replace("\\", "/").replace('"', '\\"')
marker = 'custom_template/release=""'
replacement = f'custom_template/release="{template}"'
if marker not in text:
    raise SystemExit("custom_template/release marker was not found")
preset.write_text(text.replace(marker, replacement, 1), encoding="utf-8")
PY

echo '== Import every Godot resource =='
run_logged "${BUILD_DIR}/import.log" "${GODOT_BIN}" \
  --headless --path "${PROJECT_DIR}" --import || true
check_log "${BUILD_DIR}/import.log" 'Resource import'
imported_count="$(find "${PROJECT_DIR}/.godot/imported" -type f 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${imported_count}" -lt 3 ]]; then
  echo "Resource import produced only ${imported_count} cached files." >&2
  exit 1
fi
echo "Imported cache files: ${imported_count}"

echo '== Verify main menu boot =='
if ! run_logged "${BUILD_DIR}/menu.log" "${GODOT_BIN}" \
  --headless --path "${PROJECT_DIR}" --quit-after 8; then
  check_log "${BUILD_DIR}/menu.log" 'Menu boot' || true
  exit 1
fi
check_log "${BUILD_DIR}/menu.log" 'Menu boot'
grep -q 'VOXELCRAFT_BOOT_OK' "${BUILD_DIR}/menu.log" || {
  echo 'Main menu did not reach VOXELCRAFT_BOOT_OK.' >&2
  exit 1
}

echo '== Generate terrain and verify surface spawn =='
if ! run_logged "${BUILD_DIR}/world.log" env VOXELCRAFT_SMOKE_WORLD=1 "${GODOT_BIN}" \
  --headless --path "${PROJECT_DIR}" --quit-after 5400; then
  check_log "${BUILD_DIR}/world.log" 'World smoke test' || true
  exit 1
fi
check_log "${BUILD_DIR}/world.log" 'World smoke test'
grep -q 'VOXELCRAFT_WORLD_READY' "${BUILD_DIR}/world.log" || {
  echo 'Terrain did not mesh around the calculated surface spawn.' >&2
  exit 1
}

echo '== Export Windows x64 =='
if ! run_logged "${BUILD_DIR}/export.log" "${GODOT_BIN}" \
  --headless --path "${PROJECT_DIR}" \
  --export-release 'Windows Desktop' \
  "${BUILD_DIR}/windows/VoxelCraftSurvival.exe"; then
  check_log "${BUILD_DIR}/export.log" 'Windows export' || true
  exit 1
fi
check_log "${BUILD_DIR}/export.log" 'Windows export'
test -s "${BUILD_DIR}/windows/VoxelCraftSurvival.exe"

cat > "${BUILD_DIR}/windows/README.txt" <<'TXT'
VOXELCRAFT SURVIVAL V2 — WINDOWS X64

Extract this ZIP, then open VoxelCraftSurvival.exe.

Controls:
WASD move | Mouse look | Space jump | Shift sprint | Ctrl crouch
Left click mine/attack | Right click place | 1-9 hotbar
E inventory/crafting | F eat/use | Escape pause

This is an original clean-room voxel survival project. It does not contain
Minecraft source code or Mojang assets.

The executable is unsigned. Windows SmartScreen may show an
unknown-publisher warning for this development build.
TXT

(
  cd "${BUILD_DIR}/windows"
  zip -9 "${BUILD_DIR}/VoxelCraftSurvival-Windows-x64.zip" \
    VoxelCraftSurvival.exe README.txt
)

test -s "${BUILD_DIR}/VoxelCraftSurvival-Windows-x64.zip"
echo 'VOXELCRAFT_V2_EMBEDDED_BUILD_OK'
