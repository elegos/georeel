"""Tests for photo_compositor._build_frame_tasks."""

import pytest
from pathlib import Path
from georeel.core.photo_compositor import _build_frame_tasks


def _fly_block(frames):
    return {"is_pause": False, "photo_path": None, "frames": frames}


def _pause_block(frames, path="/photo.jpg"):
    return {"is_pause": True, "photo_path": path, "frames": frames}


def _run_fly(frames):
    return [_fly_block(frames)]


def _run_pause(blocks):
    return blocks  # list of pause blocks = a carousel run


# ── fly-block handling ────────────────────────────────────────────────

class TestFlyBlocks:
    def test_fly_run_produces_copy_tasks(self, tmp_path):
        runs = [_run_fly([1, 2, 3])]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert all(t["op"] == "copy" for t in tasks)
        assert len(tasks) == 3

    def test_fly_run_frame_nums_correct(self, tmp_path):
        runs = [_run_fly([5, 6, 7])]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert [t["frame_num"] for t in tasks] == [5, 6, 7]

    def test_fly_run_src_path_uses_frame_minus_one(self, tmp_path):
        runs = [_run_fly([3])]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert tasks[0]["src_path"] == str(tmp_path / "000002.png")

    def test_fly_run_photo_key_is_none(self, tmp_path):
        runs = [_run_fly([1, 2])]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert all(t["photo_key"] is None for t in tasks)
        assert all(t["next_photo_key"] is None for t in tasks)

    def test_fly_run_out_dimensions(self, tmp_path):
        runs = [_run_fly([1])]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 640, 480, "fade", 5)
        assert tasks[0]["out_w"] == 640
        assert tasks[0]["out_h"] == 480

    def test_multiple_fly_blocks_in_one_run(self, tmp_path):
        # A fly run with multiple blocks (shouldn't normally happen, but defensive)
        run = [_fly_block([1, 2]), _fly_block([3, 4])]
        tasks = _build_frame_tasks([run], tmp_path, tmp_path, 1920, 1080, "cut", 0)
        assert len(tasks) == 4
        assert all(t["op"] == "copy" for t in tasks)


# ── pause block — no-photo / empty frames edge cases ─────────────────

class TestPauseEdgeCases:
    def test_no_photo_path_falls_back_to_copy(self, tmp_path):
        block = {"is_pause": True, "photo_path": None, "frames": [1, 2, 3]}
        runs = [[block]]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert all(t["op"] == "copy" for t in tasks)

    def test_empty_photo_path_string_falls_back_to_copy(self, tmp_path):
        block = {"is_pause": True, "photo_path": "", "frames": [1, 2, 3]}
        runs = [[block]]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert all(t["op"] == "copy" for t in tasks)

    def test_empty_frames_list_produces_no_tasks(self, tmp_path):
        block = {"is_pause": True, "photo_path": "/a.jpg", "frames": []}
        runs = [[block]]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert tasks == []


# ── single pause block — photo op (no fades) ─────────────────────────

class TestSinglePauseNoFade:
    def test_cut_transition_all_photo_ops(self, tmp_path):
        block = _pause_block([1, 2, 3, 4, 5])
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "cut", 10)
        assert all(t["op"] == "photo" for t in tasks)

    def test_fade_zero_frames_all_photo_ops(self, tmp_path):
        block = _pause_block([1, 2, 3])
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 0)
        assert all(t["op"] == "photo" for t in tasks)

    def test_photo_key_set_correctly(self, tmp_path):
        block = _pause_block([1, 2], "/my_photo.jpg")
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "cut", 5)
        assert all(t["photo_key"] == "/my_photo.jpg" for t in tasks)

    def test_next_photo_key_is_none_for_last_block(self, tmp_path):
        block = _pause_block([1, 2])
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 5)
        # Single block = is_last, so no next key
        assert all(t["next_photo_key"] is None for t in tasks)


# ── single pause block — fade_in at start (first block) ──────────────

class TestFadeIn:
    def test_first_frames_are_fade_in(self, tmp_path):
        # 10 frames, 3 fade frames → first 3 are fade_in
        block = _pause_block(list(range(1, 11)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        ops = [t["op"] for t in tasks]
        assert ops[0] == "fade_in"
        assert ops[1] == "fade_in"
        assert ops[2] == "fade_in"
        assert ops[3] == "photo"

    def test_fade_in_alpha_increases(self, tmp_path):
        block = _pause_block(list(range(1, 11)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        fade_tasks = [t for t in tasks if t["op"] == "fade_in"]
        alphas = [t["alpha"] for t in fade_tasks]
        assert alphas == sorted(alphas)
        assert all(0 < a < 1 for a in alphas)

    def test_fade_in_alpha_never_reaches_1(self, tmp_path):
        block = _pause_block(list(range(1, 11)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        fade_tasks = [t for t in tasks if t["op"] == "fade_in"]
        assert all(t["alpha"] < 1.0 for t in fade_tasks)

    def test_actual_fade_capped_at_half_n(self, tmp_path):
        # 4 frames, 10 fade → actual_fade = min(10, 4//2) = 2
        block = _pause_block([1, 2, 3, 4])
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 10)
        fade_in = [t for t in tasks if t["op"] == "fade_in"]
        assert len(fade_in) == 2

    def test_no_fade_in_for_non_first_block(self, tmp_path):
        # carousel: first block is not-first if p_idx > 0, but _is_first means p_idx==0
        # Here we test a carousel where block 2 is NOT first → no fade_in
        b1 = _pause_block([1, 2, 3, 4, 5], "/a.jpg")
        b2 = _pause_block([6, 7, 8, 9, 10], "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        b2_tasks = [t for t in tasks if t["photo_key"] == "/b.jpg"]
        assert not any(t["op"] == "fade_in" for t in b2_tasks)


# ── single pause block — fade_out at end (last block) ────────────────

class TestFadeOut:
    def test_last_frames_are_fade_out(self, tmp_path):
        block = _pause_block(list(range(1, 11)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        ops = [t["op"] for t in tasks]
        assert ops[-1] == "fade_out"
        assert ops[-2] == "fade_out"
        assert ops[-3] == "fade_out"

    def test_fade_out_alpha_decreases(self, tmp_path):
        block = _pause_block(list(range(1, 11)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        fade_tasks = [t for t in tasks if t["op"] == "fade_out"]
        alphas = [t["alpha"] for t in fade_tasks]
        assert alphas == sorted(alphas, reverse=True)
        assert all(0 < a < 1 for a in alphas)

    def test_no_fade_out_for_non_last_block(self, tmp_path):
        b1 = _pause_block([1, 2, 3, 4, 5], "/a.jpg")
        b2 = _pause_block([6, 7, 8, 9, 10], "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        b1_tasks = [t for t in tasks if t["photo_key"] == "/a.jpg"]
        assert not any(t["op"] == "fade_out" for t in b1_tasks)


# ── carousel — crossfade between photos ──────────────────────────────

class TestCrossfade:
    def test_crossfade_op_between_two_photos(self, tmp_path):
        b1 = _pause_block(list(range(1, 11)), "/a.jpg")
        b2 = _pause_block(list(range(11, 21)), "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        b1_tasks = [t for t in tasks if t["photo_key"] == "/a.jpg"]
        crossfade_tasks = [t for t in b1_tasks if t["op"] == "crossfade"]
        assert len(crossfade_tasks) == 3

    def test_crossfade_next_photo_key_set(self, tmp_path):
        b1 = _pause_block(list(range(1, 11)), "/a.jpg")
        b2 = _pause_block(list(range(11, 21)), "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        crossfade_tasks = [t for t in tasks if t["op"] == "crossfade"]
        assert all(t["next_photo_key"] == "/b.jpg" for t in crossfade_tasks)

    def test_crossfade_alpha_increases(self, tmp_path):
        b1 = _pause_block(list(range(1, 11)), "/a.jpg")
        b2 = _pause_block(list(range(11, 21)), "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        alphas = [t["alpha"] for t in tasks if t["op"] == "crossfade"]
        assert alphas == sorted(alphas)
        assert all(0 < a < 1 for a in alphas)

    def test_cut_transition_no_crossfade(self, tmp_path):
        b1 = _pause_block(list(range(1, 11)), "/a.jpg")
        b2 = _pause_block(list(range(11, 21)), "/b.jpg")
        tasks = _build_frame_tasks([[b1, b2]], tmp_path, tmp_path, 1920, 1080, "cut", 5)
        assert not any(t["op"] == "crossfade" for t in tasks)


# ── total frame count preserved ───────────────────────────────────────

class TestTaskCount:
    def test_fly_total_count(self, tmp_path):
        runs = [_run_fly(list(range(1, 21)))]
        tasks = _build_frame_tasks(runs, tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert len(tasks) == 20

    def test_pause_total_count(self, tmp_path):
        block = _pause_block(list(range(1, 16)))
        tasks = _build_frame_tasks([[block]], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        assert len(tasks) == 15

    def test_mixed_runs_total_count(self, tmp_path):
        fly_run = _run_fly(list(range(1, 6)))
        pause_run = [_pause_block(list(range(6, 16)))]
        tasks = _build_frame_tasks([fly_run, pause_run], tmp_path, tmp_path, 1920, 1080, "fade", 3)
        assert len(tasks) == 15

    def test_empty_runs_empty_tasks(self, tmp_path):
        tasks = _build_frame_tasks([], tmp_path, tmp_path, 1920, 1080, "fade", 5)
        assert tasks == []
