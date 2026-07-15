#!/usr/bin/env python3
"""Repair connector-mutated Base64 chunks and verify the canonical V2 bundle."""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

EXPECTED_B64_SHA256 = "d6c7f93e5a2642984e627279e4ac8a3e7ecdd828290c7c978955e51f29513303"
EXPECTED_ARCHIVE_SHA256 = "95683529d9a0bf297735280d4e16ff7eb438aa315e26972275db8745beb14b41"

# GitHub connector mutations measured from the checked-out PR branch.
REPLACEMENTS: dict[int, dict[int, str]] = {
    0: {13000: "G"},
    2: {12392: "f"},
    5: {3007: "f"},
    7: {3000: "+", 6649: "I"},
}
INSERTIONS: dict[int, dict[int, str]] = {
    4: {14581: "M"},
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: repair_bundle.py <chunk-directory> <output-archive>", file=sys.stderr)
        return 2

    chunk_dir = Path(sys.argv[1])
    output_archive = Path(sys.argv[2])
    paths = sorted(chunk_dir.glob("part-*.b64"))
    if len(paths) != 9:
        raise SystemExit(f"Expected 9 V2 chunks, found {len(paths)}")

    repaired_parts: list[str] = []
    for index, path in enumerate(paths):
        chars = list("".join(path.read_text(encoding="utf-8").split()))

        for position, value in sorted(REPLACEMENTS.get(index, {}).items()):
            if not 0 <= position < len(chars):
                raise SystemExit(f"Replacement index out of range: part {index}, {position}")
            chars[position] = value

        offset = 0
        for position, value in sorted(INSERTIONS.get(index, {}).items()):
            if not 0 <= position <= len(chars):
                raise SystemExit(f"Insertion index out of range: part {index}, {position}")
            chars.insert(position + offset, value)
            offset += 1

        repaired_parts.append("".join(chars))

    repaired_b64 = "".join(repaired_parts).encode("ascii")
    actual_b64_hash = sha256(repaired_b64)
    if actual_b64_hash != EXPECTED_B64_SHA256:
        raise SystemExit(
            f"Repaired Base64 hash mismatch: {actual_b64_hash} != {EXPECTED_B64_SHA256}"
        )

    archive = base64.b64decode(repaired_b64, validate=True)
    actual_archive_hash = sha256(archive)
    if actual_archive_hash != EXPECTED_ARCHIVE_SHA256:
        raise SystemExit(
            f"Archive hash mismatch: {actual_archive_hash} != {EXPECTED_ARCHIVE_SHA256}"
        )

    output_archive.parent.mkdir(parents=True, exist_ok=True)
    output_archive.write_bytes(archive)
    print(f"V2 bundle verified: {actual_archive_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
