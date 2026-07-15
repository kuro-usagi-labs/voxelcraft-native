#!/usr/bin/env python3
"""Replace runtime PNG/WAV loads with self-contained Godot resources.

The pinned Voxel Tools custom build can import PNG/WAV files in editor mode but
its headless/runtime ResourceLoader does not expose loaders for those source
formats. This patch keeps the exact voxel atlas pixels and WAV PCM data while
constructing ImageTexture and AudioStreamWAV resources directly at runtime.
"""

from __future__ import annotations

import base64
import re
import struct
import sys
import wave
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgba(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"Not a PNG file: {path}")

    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    palette = b""
    transparency = b""
    compressed = bytearray()

    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break

    if None in (width, height, bit_depth, color_type, interlace):
        raise ValueError(f"Incomplete PNG header: {path}")
    if bit_depth != 8 or interlace != 0:
        raise ValueError(f"Unsupported PNG encoding in {path}: depth={bit_depth}, interlace={interlace}")

    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channels_by_type:
        raise ValueError(f"Unsupported PNG color type {color_type}: {path}")
    channels = channels_by_type[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError(f"Unexpected PNG payload size for {path}: {len(raw)} != {expected}")

    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(stride)
    for _y in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        reconstructed = bytearray(stride)
        for x, value in enumerate(scan):
            left = reconstructed[x - channels] if x >= channels else 0
            up = previous[x]
            upper_left = previous[x - channels] if x >= channels else 0
            if filter_type == 0:
                result = value
            elif filter_type == 1:
                result = (value + left) & 0xFF
            elif filter_type == 2:
                result = (value + up) & 0xFF
            elif filter_type == 3:
                result = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                result = (value + _paeth(left, up, upper_left)) & 0xFF
            else:
                raise ValueError(f"Unsupported PNG filter {filter_type}: {path}")
            reconstructed[x] = result
        rows.append(reconstructed)
        previous = reconstructed

    rgba = bytearray(width * height * 4)
    out = 0
    for row in rows:
        for x in range(width):
            base = x * channels
            if color_type == 6:
                r, g, b, a = row[base : base + 4]
            elif color_type == 2:
                r, g, b = row[base : base + 3]
                a = 255
            elif color_type == 0:
                r = g = b = row[base]
                a = 255
            elif color_type == 4:
                r = g = b = row[base]
                a = row[base + 1]
            else:
                index = row[base]
                palette_offset = index * 3
                r, g, b = palette[palette_offset : palette_offset + 3]
                a = transparency[index] if index < len(transparency) else 255
            rgba[out : out + 4] = bytes((r, g, b, a))
            out += 4

    return int(width), int(height), bytes(rgba)


def decode_wav_pcm16(path: Path) -> tuple[int, bool, bytes]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())

    if channels not in (1, 2):
        raise ValueError(f"Unsupported channel count {channels}: {path}")

    output = bytearray()
    if width == 1:
        for value in frames:
            output.extend(struct.pack("<h", (value - 128) << 8))
    elif width == 2:
        output.extend(frames)
    elif width == 3:
        for offset in range(0, len(frames), 3):
            value = int.from_bytes(frames[offset : offset + 3], "little", signed=True)
            output.extend(struct.pack("<h", max(-32768, min(32767, value >> 8))))
    elif width == 4:
        for offset in range(0, len(frames), 4):
            value = int.from_bytes(frames[offset : offset + 4], "little", signed=True)
            output.extend(struct.pack("<h", max(-32768, min(32767, value >> 16))))
    else:
        raise ValueError(f"Unsupported sample width {width}: {path}")

    return rate, channels == 2, bytes(output)


def gd_string_chunks(value: str, width: int = 160) -> str:
    chunks = [value[i : i + width] for i in range(0, len(value), width)]
    return " +\n\t\t".join(f'"{chunk}"' for chunk in chunks)


def make_embedded_script(project: Path) -> str:
    atlas_path = project / "assets/textures/voxel_atlas.png"
    atlas_width, atlas_height, atlas_rgba = decode_png_rgba(atlas_path)
    atlas_b64 = gd_string_chunks(base64.b64encode(atlas_rgba).decode("ascii"))

    audio_entries: list[str] = []
    for wav_path in sorted((project / "assets/audio").glob("*.wav")):
        rate, stereo, pcm = decode_wav_pcm16(wav_path)
        pcm_b64 = gd_string_chunks(base64.b64encode(pcm).decode("ascii"))
        audio_entries.append(
            f'''\t\t"{wav_path.stem}":\n\t\t\tstream.mix_rate = {rate}\n\t\t\tstream.stereo = {str(stereo).lower()}\n\t\t\tstream.data = Marshalls.base64_to_raw(\n\t\t\t\t{pcm_b64}\n\t\t\t)'''
        )

    audio_match = "\n".join(audio_entries)
    return f'''extends RefCounted

static var _textures: Dictionary = {{}}
static var _audio: Dictionary = {{}}

static func texture(asset_name: String):
\tif _textures.has(asset_name):
\t\treturn _textures[asset_name]
\tvar image: Image
\tmatch asset_name:
\t\t"voxel_atlas":
\t\t\tvar raw: PackedByteArray = Marshalls.base64_to_raw(
\t\t\t\t{atlas_b64}
\t\t\t)
\t\t\timage = Image.create_from_data({atlas_width}, {atlas_height}, false, Image.FORMAT_RGBA8, raw)
\t\t"menu_panorama":
\t\t\timage = _make_menu_panorama()
\t\t_:
\t\t\timage = Image.create(16, 16, false, Image.FORMAT_RGBA8)
\t\t\timage.fill(Color(1.0, 0.0, 1.0, 1.0))
\tvar result = ImageTexture.create_from_image(image)
\t_textures[asset_name] = result
\treturn result

static func audio(asset_name: String):
\tif _audio.has(asset_name):
\t\treturn _audio[asset_name]
\tvar stream: AudioStreamWAV = AudioStreamWAV.new()
\tstream.format = AudioStreamWAV.FORMAT_16_BITS
\tmatch asset_name:
{audio_match}
\t\t_:
\t\t\tstream.mix_rate = 22050
\t\t\tstream.stereo = false
\t\t\tstream.data = PackedByteArray()
\t_audio[asset_name] = stream
\treturn stream

static func _make_menu_panorama() -> Image:
\tvar width: int = 512
\tvar height: int = 288
\tvar image: Image = Image.create(width, height, false, Image.FORMAT_RGBA8)
\tfor y in range(height):
\t\tvar t: float = float(y) / float(height - 1)
\t\tvar sky: Color = Color(0.19, 0.43, 0.72).lerp(Color(0.72, 0.86, 0.96), t)
\t\tfor x in range(width):
\t\t\timage.set_pixel(x, y, sky)
\tfor y in range(32, 76):
\t\tfor x in range(378, 422):
\t\t\tvar dx: float = float(x - 400)
\t\t\tvar dy: float = float(y - 54)
\t\t\tif dx * dx + dy * dy < 430.0:
\t\t\t\timage.set_pixel(x, y, Color(1.0, 0.88, 0.45))
\tfor x in range(width):
\t\tvar mountain_y: int = 162 + int(28.0 * sin(float(x) * 0.018)) + int(13.0 * sin(float(x) * 0.051))
\t\tfor y in range(max(0, mountain_y), height):
\t\t\tvar depth: float = float(y - mountain_y) / float(max(1, height - mountain_y))
\t\t\timage.set_pixel(x, y, Color(0.18, 0.35, 0.20).lerp(Color(0.08, 0.18, 0.10), depth))
\tfor trunk_x in range(18, width, 46):
\t\tvar tree_height: int = 38 + ((trunk_x * 17) % 34)
\t\tvar ground_y: int = 220 + int(8.0 * sin(float(trunk_x) * 0.05))
\t\tfor y in range(ground_y - tree_height, ground_y):
\t\t\tfor x in range(trunk_x - 3, trunk_x + 4):
\t\t\t\tif x >= 0 and x < width and y >= 0 and y < height:
\t\t\t\t\timage.set_pixel(x, y, Color(0.28, 0.16, 0.08))
\t\tfor layer in range(4):
\t\t\tvar crown_y: int = ground_y - tree_height + layer * 10
\t\t\tvar half_width: int = 18 - layer * 3
\t\t\tfor y in range(crown_y - 8, crown_y + 9):
\t\t\t\tfor x in range(trunk_x - half_width, trunk_x + half_width + 1):
\t\t\t\t\tif x >= 0 and x < width and y >= 0 and y < height:
\t\t\t\t\t\timage.set_pixel(x, y, Color(0.08, 0.30 + float(layer) * 0.025, 0.12))
\treturn image
'''


def patch_gdscript(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    original = source

    texture_pattern = re.compile(r'(?:load|preload)\("res://assets/textures/([^"/]+)\.png"\)')
    audio_pattern = re.compile(r'(?:load|preload)\("res://assets/audio/([^"/]+)\.wav"\)')
    source = texture_pattern.sub(r'EmbeddedAssets.texture("\1")', source)
    source = audio_pattern.sub(r'EmbeddedAssets.audio("\1")', source)

    if source != original and "const EmbeddedAssets" not in source:
        lines = source.splitlines()
        insert_at = 1 if lines and lines[0].startswith("extends ") else 0
        lines.insert(insert_at, 'const EmbeddedAssets = preload("res://scripts/core/embedded_assets.gd")')
        source = "\n".join(lines) + ("\n" if original.endswith("\n") else "")

    source = re.sub(
        r'var\s+(\w+)\s*=\s*StandardMaterial3D\.new\(\)',
        r'var \1: StandardMaterial3D = StandardMaterial3D.new()',
        source,
    )

    style_vars = set(
        re.findall(r'var\s+(\w+)(?:\s*:\s*StyleBoxFlat)?\s*=\s*StyleBoxFlat\.new\(\)', source)
    )
    if style_vars:
        filtered: list[str] = []
        for line in source.splitlines():
            stripped = line.strip()
            if any(stripped.startswith(f"{name}.texture_filter") for name in style_vars):
                continue
            filtered.append(line)
        source = "\n".join(filtered) + ("\n" if original.endswith("\n") else "")

    if "res://assets/textures/" in source or "res://assets/audio/" in source:
        raise RuntimeError(f"Unpatched runtime asset load remains in {path}")
    path.write_text(source, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_embedded_assets.py <project-directory>", file=sys.stderr)
        return 2

    project = Path(sys.argv[1]).resolve()
    helper_path = project / "scripts/core/embedded_assets.gd"
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(make_embedded_script(project), encoding="utf-8")

    patched = 0
    for gd_path in sorted(project.rglob("*.gd")):
        if gd_path == helper_path:
            continue
        before = gd_path.read_bytes()
        patch_gdscript(gd_path)
        if gd_path.read_bytes() != before:
            patched += 1

    print(f"Embedded runtime assets generated; patched GDScript files: {patched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
