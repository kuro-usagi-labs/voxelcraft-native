#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
BUILD_DIR="${ROOT}/build"
CHECK_DIR="${ROOT}/.ci-v3-check"
B64_FILE="${CHECK_DIR}/visual-survival-v3.patch.gz.b64"
GZ_FILE="${CHECK_DIR}/visual-survival-v3.patch.gz"
EXPECTED_B64_SIZE=30788
EXPECTED_B64_SHA256="7570f135c35a916af73274212600fd6a2bcdaca43e68b920dc4509578226a4d3"
EXPECTED_GZ_SHA256="e2e2258901e90e358eb8c79e4ee61832db4d0f72bd4f58b3653865a384f00a8e"

mkdir -p "${BUILD_DIR}" "${CHECK_DIR}"

set +e
{
  echo '== V3 source chunk inventory =='
  find "${ROOT}/source/v3-patch" -maxdepth 1 -type f -name 'chunk-*.b64' -print | sort
  wc -c "${ROOT}"/source/v3-patch/chunk-*.b64

  echo '== Reconstructed Base64 =='
  cat "${ROOT}"/source/v3-patch/chunk-*.b64 | tr -d '\r\n' > "${B64_FILE}"
  actual_size="$(wc -c < "${B64_FILE}")"
  actual_b64_sha="$(sha256sum "${B64_FILE}" | awk '{print $1}')"
  echo "size=${actual_size}"
  echo "sha256=${actual_b64_sha}"
  echo "expected_size=${EXPECTED_B64_SIZE}"
  echo "expected_sha256=${EXPECTED_B64_SHA256}"

  if [[ "${actual_size}" -ne "${EXPECTED_B64_SIZE}" ]]; then
    echo 'V3 Base64 size mismatch.' >&2
    exit 11
  fi
  if [[ "${actual_b64_sha}" != "${EXPECTED_B64_SHA256}" ]]; then
    echo 'V3 Base64 SHA-256 mismatch.' >&2
    exit 12
  fi

  echo '== Decode and verify gzip =='
  base64 --decode "${B64_FILE}" > "${GZ_FILE}"
  gzip -t "${GZ_FILE}"
  actual_gz_sha="$(sha256sum "${GZ_FILE}" | awk '{print $1}')"
  echo "gzip_sha256=${actual_gz_sha}"
  echo "expected_gzip_sha256=${EXPECTED_GZ_SHA256}"
  if [[ "${actual_gz_sha}" != "${EXPECTED_GZ_SHA256}" ]]; then
    echo 'V3 gzip SHA-256 mismatch.' >&2
    exit 13
  fi

  echo 'VOXELCRAFT_V3_CHUNKS_OK'
} > "${BUILD_DIR}/patch.log" 2>&1
status=$?
set -e
cat "${BUILD_DIR}/patch.log"
exit "${status}"
