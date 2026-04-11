"""Tests for encoder_registry."""

import pytest
from unittest.mock import patch, MagicMock
from georeel.core.encoder_registry import (
    ALL_ENCODERS,
    EncoderConfig,
    get_encoder,
    encoders_for_codec,
    detect_available_encoders,
)


class TestAllEncoders:
    def test_all_encoders_not_empty(self):
        assert len(ALL_ENCODERS) > 0

    def test_all_have_required_fields(self):
        for enc in ALL_ENCODERS:
            assert enc.name
            assert enc.label
            assert enc.codec in ("h264", "h265", "av1")
            assert isinstance(enc.cq_range, tuple) and len(enc.cq_range) == 2
            assert enc.cq_range[0] <= enc.default_cq <= enc.cq_range[1]

    def test_codec_coverage(self):
        codecs = {e.codec for e in ALL_ENCODERS}
        assert "h264" in codecs
        assert "h265" in codecs
        assert "av1" in codecs

    def test_software_encoders_present(self):
        names = {e.name for e in ALL_ENCODERS}
        assert "libx264" in names
        assert "libx265" in names
        assert "libsvtav1" in names or "libaom-av1" in names

    def test_hw_type_values(self):
        valid_hw = {"", "nvenc", "amf", "qsv", "videotoolbox"}
        for enc in ALL_ENCODERS:
            assert enc.hw_type in valid_hw

    def test_no_duplicate_names(self):
        names = [e.name for e in ALL_ENCODERS]
        assert len(names) == len(set(names))


class TestGetEncoder:
    def test_known_encoder_returned(self):
        enc = get_encoder("libx264")
        assert enc is not None
        assert enc.name == "libx264"
        assert enc.codec == "h264"

    def test_unknown_encoder_returns_none(self):
        enc = get_encoder("not_a_real_encoder")
        assert enc is None

    def test_empty_string_returns_none(self):
        enc = get_encoder("")
        assert enc is None

    def test_libx265(self):
        enc = get_encoder("libx265")
        assert enc is not None
        assert enc.codec == "h265"


class TestEncodersForCodec:
    def test_h264_encoders(self):
        available = {"libx264", "h264_nvenc", "libx265"}
        result = encoders_for_codec("h264", available)
        names = [e.name for e in result]
        assert "libx264" in names
        assert "h264_nvenc" in names
        assert "libx265" not in names

    def test_empty_available(self):
        result = encoders_for_codec("h264", set())
        assert result == []

    def test_hw_encoders_listed_before_sw(self):
        # ALL_ENCODERS lists HW before SW, so the filter result should too
        available = {"h264_nvenc", "libx264"}
        result = encoders_for_codec("h264", available)
        names = [e.name for e in result]
        assert names.index("h264_nvenc") < names.index("libx264")

    def test_unknown_codec_returns_empty(self):
        available = {"libx264"}
        result = encoders_for_codec("vp9", available)
        assert result == []

    def test_av1_codec(self):
        available = {"libsvtav1", "libaom-av1"}
        result = encoders_for_codec("av1", available)
        assert len(result) == 2


class TestDetectAvailableEncoders:
    def _make_ffmpeg_output(self) -> bytes:
        # Simulate the encoding section of ffmpeg -encoders output
        lines = [
            b"Encoders:",
            b" V..... libx264              H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
            b" V..... libx265              H.265 / HEVC",
            b" A..... aac                  AAC (Advanced Audio Coding)",
            b" V..... h264_nvenc           NVIDIA NVENC H.264 encoder",
            b"short",         # too short — should be skipped
            b" Xasdf  bad_flag_at_pos_0",  # wrong type char
        ]
        return b"\n".join(lines)

    def test_parses_known_encoders(self):
        output = self._make_ffmpeg_output()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=output, stderr=b"")
            result = detect_available_encoders("ffmpeg")
        assert "libx264" in result
        assert "libx265" in result
        assert "h264_nvenc" in result
        assert "aac" in result

    def test_skips_short_lines(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"short\n", stderr=b"")
            result = detect_available_encoders()
        assert result == set()

    def test_returns_empty_set_on_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
            result = detect_available_encoders("nonexistent_ffmpeg")
        assert result == set()

    def test_returns_set_type(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"", stderr=b"")
            result = detect_available_encoders()
        assert isinstance(result, set)
