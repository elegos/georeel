"""
Defines all supported video encoders, their FFmpeg flags, quality ranges,
and suggested default settings.  Also provides encoder auto-detection.
"""

import re
import shlex
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class EncoderConfig:
    name: str  # FFmpeg encoder name
    label: str  # display label in UI
    codec: str  # "h264" | "h265" | "av1"
    hw_type: str  # "" | "nvenc" | "amf" | "qsv" | "videotoolbox"
    cq_flag: str  # quality flag, e.g. "-crf", "-cq", "-qp"
    cq_range: tuple[int, int]  # (min, max) inclusive
    default_cq: int
    preset_flag: str  # e.g. "-preset", "-cpu-used", "" if none
    presets: tuple[tuple[str, str], ...]  # ((ffmpeg_value, display_label), ...)
    default_preset: str  # ffmpeg preset value
    suggestion: str  # human-readable guidance shown in UI


# ------------------------------------------------------------------
# Preset helpers
# ------------------------------------------------------------------


def _nvenc_presets() -> tuple[tuple[str, str], ...]:
    return (
        ("p1", "P1 – Fastest"),
        ("p2", "P2 – Faster"),
        ("p3", "P3 – Fast"),
        ("p4", "P4 – Medium"),
        ("p5", "P5 – Slow"),
        ("p6", "P6 – Slower"),
        ("p7", "P7 – Slowest"),
    )


def _x26x_presets() -> tuple[tuple[str, str], ...]:
    return tuple(
        (p, p.capitalize())
        for p in [
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        ]
    )


def _amf_presets() -> tuple[tuple[str, str], ...]:
    return (("speed", "Speed"), ("balanced", "Balanced"), ("quality", "Quality"))


def _qsv_presets() -> tuple[tuple[str, str], ...]:
    return tuple(
        (p, p.capitalize())
        for p in ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    )


def _svt_presets() -> tuple[tuple[str, str], ...]:
    def _label(i: int) -> str:
        if i == 0:
            return "0 – Slowest"
        if i == 13:
            return "13 – Fastest"
        return str(i)

    return tuple((str(i), _label(i)) for i in range(14))


def _aom_presets() -> tuple[tuple[str, str], ...]:
    def _label(i: int) -> str:
        if i == 0:
            return "0 – Slowest"
        if i == 8:
            return "8 – Fastest"
        return str(i)

    return tuple((str(i), _label(i)) for i in range(9))


# ------------------------------------------------------------------
# Full encoder catalogue
# HW encoders are listed before SW encoders within each codec so that
# encoders_for_codec() returns them first.
# ------------------------------------------------------------------

ALL_ENCODERS: tuple[EncoderConfig, ...] = (
    # ---------------------------------------------------------------- H.264
    EncoderConfig(
        name="h264_nvenc",
        label="H.264 – NVENC (NVIDIA GPU)",
        codec="h264",
        hw_type="nvenc",
        cq_flag="-cq",
        cq_range=(0, 51),
        default_cq=28,
        preset_flag="-preset",
        presets=_nvenc_presets(),
        default_preset="p4",
        suggestion="CQ 28 · Preset P4 (Medium) — reliable balance on NVIDIA hardware",
    ),
    EncoderConfig(
        name="h264_amf",
        label="H.264 – AMF (AMD GPU)",
        codec="h264",
        hw_type="amf",
        cq_flag="-qp",
        cq_range=(0, 51),
        default_cq=23,
        preset_flag="-quality",
        presets=_amf_presets(),
        default_preset="balanced",
        suggestion="QP 23 · Quality Balanced",
    ),
    EncoderConfig(
        name="h264_qsv",
        label="H.264 – QSV (Intel GPU)",
        codec="h264",
        hw_type="qsv",
        cq_flag="-global_quality",
        cq_range=(0, 51),
        default_cq=23,
        preset_flag="-preset",
        presets=_qsv_presets(),
        default_preset="medium",
        suggestion="Quality 23 · Preset Medium",
    ),
    EncoderConfig(
        name="h264_videotoolbox",
        label="H.264 – VideoToolbox (Apple)",
        codec="h264",
        hw_type="videotoolbox",
        cq_flag="-q:v",
        cq_range=(1, 100),
        default_cq=65,
        preset_flag="",
        presets=(),
        default_preset="",
        suggestion="Quality 65",
    ),
    EncoderConfig(
        name="libx264",
        label="H.264 – libx264 (software)",
        codec="h264",
        hw_type="",
        cq_flag="-crf",
        cq_range=(0, 51),
        default_cq=23,
        preset_flag="-preset",
        presets=_x26x_presets(),
        default_preset="medium",
        suggestion="CRF 23 · Preset Medium — standard quality/size trade-off",
    ),
    # ---------------------------------------------------------------- H.265
    EncoderConfig(
        name="hevc_nvenc",
        label="H.265 – NVENC (NVIDIA GPU)",
        codec="h265",
        hw_type="nvenc",
        cq_flag="-cq",
        cq_range=(0, 51),
        default_cq=30,
        preset_flag="-preset",
        presets=_nvenc_presets(),
        default_preset="p2",
        suggestion="CQ 30 · Preset P2 (Faster) — good balance for H.265 on NVIDIA",
    ),
    EncoderConfig(
        name="hevc_amf",
        label="H.265 – AMF (AMD GPU)",
        codec="h265",
        hw_type="amf",
        cq_flag="-qp",
        cq_range=(0, 51),
        default_cq=28,
        preset_flag="-quality",
        presets=_amf_presets(),
        default_preset="balanced",
        suggestion="QP 28 · Quality Balanced",
    ),
    EncoderConfig(
        name="hevc_qsv",
        label="H.265 – QSV (Intel GPU)",
        codec="h265",
        hw_type="qsv",
        cq_flag="-global_quality",
        cq_range=(0, 51),
        default_cq=28,
        preset_flag="-preset",
        presets=_qsv_presets(),
        default_preset="medium",
        suggestion="Quality 28 · Preset Medium",
    ),
    EncoderConfig(
        name="hevc_videotoolbox",
        label="H.265 – VideoToolbox (Apple)",
        codec="h265",
        hw_type="videotoolbox",
        cq_flag="-q:v",
        cq_range=(1, 100),
        default_cq=65,
        preset_flag="",
        presets=(),
        default_preset="",
        suggestion="Quality 65",
    ),
    EncoderConfig(
        name="libx265",
        label="H.265 – libx265 (software)",
        codec="h265",
        hw_type="",
        cq_flag="-crf",
        cq_range=(0, 51),
        default_cq=28,
        preset_flag="-preset",
        presets=_x26x_presets(),
        default_preset="medium",
        suggestion="CRF 28 · Preset Medium — standard quality/size trade-off",
    ),
    # ---------------------------------------------------------------- AV1
    EncoderConfig(
        name="av1_nvenc",
        label="AV1 – NVENC (NVIDIA RTX 40xx+)",
        codec="av1",
        hw_type="nvenc",
        cq_flag="-cq",
        cq_range=(0, 51),
        default_cq=36,
        preset_flag="-preset",
        presets=_nvenc_presets(),
        default_preset="p5",
        suggestion="CQ 36 · Preset P5 (Slow) — good balance between quality and size",
    ),
    EncoderConfig(
        name="av1_amf",
        label="AV1 – AMF (AMD RX 7000+)",
        codec="av1",
        hw_type="amf",
        cq_flag="-qp",
        cq_range=(0, 63),
        default_cq=32,
        preset_flag="-quality",
        presets=_amf_presets(),
        default_preset="balanced",
        suggestion="QP 32 · Quality Balanced",
    ),
    EncoderConfig(
        name="av1_qsv",
        label="AV1 – QSV (Intel Arc)",
        codec="av1",
        hw_type="qsv",
        cq_flag="-global_quality",
        cq_range=(0, 63),
        default_cq=32,
        preset_flag="-preset",
        presets=_qsv_presets(),
        default_preset="medium",
        suggestion="Quality 32 · Preset Medium",
    ),
    EncoderConfig(
        name="libsvtav1",
        label="AV1 – SVT-AV1 (software, fast)",
        codec="av1",
        hw_type="",
        cq_flag="-crf",
        cq_range=(0, 63),
        default_cq=35,
        preset_flag="-preset",
        presets=_svt_presets(),
        default_preset="5",
        suggestion="CRF 35 · Preset 5 — good balance; SVT-AV1 is far faster than libaom",
    ),
    EncoderConfig(
        name="libaom-av1",
        label="AV1 – libaom (software, slow)",
        codec="av1",
        hw_type="",
        cq_flag="-crf",
        cq_range=(0, 63),
        default_cq=35,
        preset_flag="-cpu-used",
        presets=_aom_presets(),
        default_preset="4",
        suggestion="CRF 35 · CPU-used 4 — very high quality, very slow encoding",
    ),
)

_BY_NAME: dict[str, EncoderConfig] = {e.name: e for e in ALL_ENCODERS}


def get_encoder(name: str) -> EncoderConfig | None:
    return _BY_NAME.get(name)


def encoders_for_codec(codec: str, available: set[str]) -> list[EncoderConfig]:
    """Return available encoders for a codec, hardware-accelerated first."""
    return [e for e in ALL_ENCODERS if e.codec == codec and e.name in available]


def detect_available_encoders(ffmpeg_exe: str = "ffmpeg") -> set[str]:
    """Return the set of encoder names present in the local FFmpeg build."""
    try:
        result = subprocess.run(
            shlex.join([ffmpeg_exe, "-hide_banner", "-encoders"]),
            capture_output=True,
            timeout=10,
            shell=True,
        )
        # Decode manually so a single bad byte never raises UnicodeDecodeError.
        # FFmpeg writes the encoder table to stdout; some builds use stderr.
        raw = result.stdout + result.stderr
        output = raw.decode("utf-8", errors="replace")

        available: set[str] = set()
        for line in output.splitlines():
            # Encoder lines have exactly this layout (one leading space):
            #   " V....D encoder_name   Description…"
            #     ^      ^
            #     0      7  (after the leading space + 6 flag chars + space)
            s = line.lstrip(" ")
            if len(s) < 9:
                continue
            # Position 0: codec type (V/A/S), position 6: separator space
            if s[0] not in "VAS" or s[6] != " ":
                continue
            name = s[7:].split()[0]
            if name:
                available.add(name)
        return available
    except Exception:
        return set()
