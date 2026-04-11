"""Tests for MatchResult."""

from georeel.core.match_result import MatchResult


class TestMatchResultOk:
    def test_ok_with_index_no_error(self):
        r = MatchResult(photo_path="/a.jpg", trackpoint_index=5)
        assert r.ok is True

    def test_not_ok_no_index(self):
        r = MatchResult(photo_path="/a.jpg")
        assert r.ok is False

    def test_not_ok_has_error(self):
        r = MatchResult(photo_path="/a.jpg", trackpoint_index=0, error="No GPS")
        assert r.ok is False

    def test_not_ok_index_none_with_error(self):
        r = MatchResult(photo_path="/a.jpg", error="No timestamp in EXIF")
        assert r.ok is False


class TestMatchResultStatusText:
    def test_status_error(self):
        r = MatchResult(photo_path="/a.jpg", error="No GPS coordinates in EXIF")
        assert r.status_text == "No GPS coordinates in EXIF"

    def test_status_warning(self):
        r = MatchResult(photo_path="/a.jpg", trackpoint_index=2, warning="Disagree by 200 m")
        assert "⚠" in r.status_text
        assert "200" in r.status_text

    def test_status_ok(self):
        r = MatchResult(photo_path="/a.jpg", trackpoint_index=7)
        assert "✓" in r.status_text
        assert "7" in r.status_text

    def test_status_no_match(self):
        r = MatchResult(photo_path="/a.jpg")
        assert r.status_text == "—"

    def test_error_takes_precedence_over_warning(self):
        r = MatchResult(photo_path="/a.jpg", error="fatal", warning="minor")
        assert r.status_text == "fatal"


class TestMatchResultDefaults:
    def test_default_position_is_track(self):
        r = MatchResult(photo_path="/a.jpg")
        assert r.position == "track"

    def test_default_sort_key_is_zero(self):
        r = MatchResult(photo_path="/a.jpg")
        assert r.sort_key == 0.0

    def test_pre_position(self):
        r = MatchResult(photo_path="/a.jpg", position="pre", sort_key=-60.0)
        assert r.position == "pre"
        assert r.sort_key == -60.0

    def test_post_position(self):
        r = MatchResult(photo_path="/a.jpg", trackpoint_index=10, position="post", sort_key=3600.0)
        assert r.position == "post"
