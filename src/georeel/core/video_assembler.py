"""
Stage 9 — Video Assembler.

Encodes the composited frame sequence into the final output video using FFmpeg.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .encoder_registry import EncoderConfig, get_encoder
from . import temp_manager


class VideoAssembleError(Exception):
    pass


def assemble_video(
    frames_dir: str,
    output_path: str,
    settings: dict,
    total_frames: int,
    gpx_path: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    title_progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """Encode *frames_dir*/%06d.png → *output_path* using settings from QSettings dict."""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VideoAssembleError(
            "FFmpeg not found. Install FFmpeg and ensure it is in your PATH."
        )

    encoder_name = settings.get("output/encoder", "libx265")
    enc = get_encoder(encoder_name)
    if enc is None:
        raise VideoAssembleError(f"Unknown encoder '{encoder_name}'.")

    fps       = int(settings.get("render/fps", 30))
    cq        = int(settings.get("output/cq",  enc.default_cq))
    preset    = settings.get("output/preset",  enc.default_preset)
    container = settings.get("output/container", "mkv")

    # Ensure output extension matches container
    out = Path(output_path)
    expected_ext = f".{container}"
    if out.suffix.lower() != expected_ext:
        out = out.with_suffix(expected_ext)

    # Write settings JSON to a temp file so it can be attached for MKV
    settings_json = _serialise_settings(settings)
    tmp_settings = Path(tempfile.mktemp(suffix="_georeel_settings.json"))
    tmp_settings.write_text(settings_json, encoding="utf-8")

    # Title is composited onto frames by PIL before ffmpeg runs so there is
    # no dependency on ffmpeg's drawtext/libfreetype build flag.
    #
    # The title must start at t=0 of the output video, which may begin with a
    # black pre-clip (from the video fade-in setting).  Since tpad generates
    # those black frames *inside* ffmpeg — after PIL has already run — we
    # materialise them as real PNGs first so PIL can composite on them.
    #
    # When title fade-in is enabled but video fade-in is not, we still prepend
    # black frames (for title_fi_dur seconds) so the title genuinely fades in
    # from black rather than over content.
    title_dir: Optional[Path] = None
    title_enabled = bool(settings.get("clip_effects/title_enabled", False))
    fi_enabled    = bool(settings.get("clip_effects/fade_in_enabled", False))
    fi_black = float(settings.get("clip_effects/fade_in_black_dur", 5.0)) if fi_enabled else 0.0
    fi_fade  = float(settings.get("clip_effects/fade_in_fade_dur",  1.0)) if fi_enabled else 0.0

    # Determine how many black frames to prepend as real PNGs.
    n_black_frames = 0
    if title_enabled:
        if fi_black > 0:
            # Video fade-in black provides the pre-clip; use its full duration.
            n_black_frames = round(fi_black * fps)
        elif bool(settings.get("clip_effects/title_fade_in_enabled", True)):
            # No video black, but title has its own fade-in: prepend that many
            # black frames so the title fades in from black rather than content.
            title_fi_dur = float(settings.get("clip_effects/title_fade_in_dur", 3.0))
            if title_fi_dur > 0:
                n_black_frames = round(title_fi_dur * fps)

    prepend_black_as_frames = n_black_frames > 0
    skip_prepend = fi_enabled and prepend_black_as_frames

    if prepend_black_as_frames:
        prep_dir = temp_manager.make_temp_dir("georeel_blackprepend_")
        _prepend_black_frames(frames_dir, prep_dir, n_black_frames)
        frames_dir = str(prep_dir)
        total_frames += n_black_frames
    else:
        prep_dir = None  # type: ignore[assignment]

    if title_enabled:
        title_dir = temp_manager.make_temp_dir("georeel_title_")
        # When skip_prepend=True the luminance fade-in for content is baked into
        # the PIL frames (content_start/content_fade) rather than delegated to
        # ffmpeg's fade filter.  This prevents the ffmpeg filter from darkening
        # the black+title frames (which are already in the sequence as real PNGs).
        _composite_title_frames(
            frames_dir, title_dir, settings, fps,
            t_offset=0.0,
            content_start=(fi_black if skip_prepend else 0.0),
            content_fade=(fi_fade  if skip_prepend else 0.0),
            progress_cb=title_progress_cb,
        )
        frames_dir = str(title_dir)

    # skip_prepend: black frames are real PNGs (tpad start omitted) and the
    # luminance fade is already baked by PIL (fade=in filter also omitted).
    vf_filters, total_frames = _fade_filters(
        settings, total_frames, fps,
        skip_prepend=skip_prepend,
    )
    vf_args = ["-vf", ",".join(vf_filters)] if vf_filters else []

    total_duration_s = total_frames / fps
    pre_music, music_input, af_args, music_codec_args = _music_audio_cmd_parts(
        settings, total_duration_s
    )

    cmd = (
        [ffmpeg, "-y",
         "-framerate", str(fps),
         "-i", str(Path(frames_dir) / "%06d.png")]
        + pre_music       # -stream_loop -1 (if loop enabled), before music -i
        + music_input     # -i music_path
        + ["-c:v", enc.name]
        + _quality_args(enc, cq, preset)
        + _pix_fmt_args(enc)
        + vf_args
        + af_args         # -af "adelay=...,afade=...,atrim=...,asetpts=...,afade=..."
        + _container_args(enc, container)
        + music_codec_args  # -map 0:v -map 1:a -c:a aac -b:a 192k
        + _attach_args(gpx_path, container)
        + _attach_settings_args(str(tmp_settings), container)
        + [str(out)]
    )

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.info(
        "Video assembler: title=%s fi=%s fi_black=%.1f "
        "n_black_frames=%d prepend=%s skip_prepend=%s vf=%s",
        title_enabled, fi_enabled, fi_black,
        n_black_frames, prepend_black_as_frames,
        fi_enabled and prepend_black_as_frames,
        vf_filters,
    )
    _log.debug(
        "Video assembler: %d frames → %s  (encoder=%s fps=%d)",
        total_frames, out, encoder_name, fps,
    )
    _log.debug("FFmpeg command: %s", shlex.join(cmd))

    stderr_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            shlex.join(cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            # FFmpeg progress: "frame=  123 fps=..."
            m = re.search(r"frame=\s*(\d+)", line)
            if m and progress_cb:
                progress_cb(int(m.group(1)), total_frames)

            if cancel_check and cancel_check():
                proc.terminate()
                proc.wait()
                out.unlink(missing_ok=True)
                raise VideoAssembleError("Encoding cancelled.")

        proc.wait()
    except VideoAssembleError:
        raise
    except Exception as e:
        raise VideoAssembleError(f"FFmpeg error: {e}") from e
    finally:
        tmp_settings.unlink(missing_ok=True)
        if title_dir and title_dir.exists():
            shutil.rmtree(title_dir, ignore_errors=True)
        if prep_dir and prep_dir.exists():
            shutil.rmtree(prep_dir, ignore_errors=True)

    stderr_tail = "".join(stderr_lines[-40:])
    _log.debug("FFmpeg output:\n%s", "".join(stderr_lines))

    if proc.returncode != 0:
        raise VideoAssembleError(
            f"FFmpeg exited with code {proc.returncode}.\n{stderr_tail}"
        )

    if not out.is_file():
        raise VideoAssembleError("FFmpeg finished but output file was not created.")

    size_mb = out.stat().st_size / 1_048_576
    duration_s = total_frames / fps if fps > 0 else 0
    _log.info(
        "Video ready: %s  (%.1f s, %d frames at %d fps, %.1f MB, encoder=%s)",
        out, duration_s, total_frames, fps, size_mb, encoder_name,
    )

    _copy_gpx_alongside(gpx_path, out, container)
    _write_settings(settings, out, container)


# ------------------------------------------------------------------
# Command-line argument helpers
# ------------------------------------------------------------------

def _prepend_black_frames(src_dir: str, dst_dir: Path, n_black: int) -> None:
    """Write n_black pure-black PNGs then the src_dir frames into dst_dir.

    The black frames get indices 000000 … 0000N-1; the original frames are
    renumbered starting at N.  Frame dimensions are read from the first source
    frame; if no frames exist a 1×1 black pixel is used as fallback.
    """
    from PIL import Image

    src_frames = sorted(Path(src_dir).glob("*.png"))
    if src_frames:
        first = Image.open(src_frames[0])
        w, h = first.size
    else:
        w, h = 1, 1

    black = Image.new("RGB", (w, h), (0, 0, 0))
    for i in range(n_black):
        black.save(dst_dir / f"{i:06d}.png", format="PNG")

    for src in src_frames:
        try:
            idx = int(src.stem) + n_black
        except ValueError:
            continue
        dst = dst_dir / f"{idx:06d}.png"
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)


def _fade_filters(
    settings: dict,
    total_frames: int,
    fps: int,
    skip_prepend: bool = False,
) -> tuple[list[str], int]:
    """Return (raw_filter_list, adjusted_frame_count) for fade-in/out effects.

    Timeline (both effects enabled):
      [fi_black][content fading in][content][content fading out][fo_black]

    tpad pads the stream; fade operates on the padded result.  Both filters
    run inside a single FFmpeg pass — no re-encode of the composited frames.

    When *skip_prepend* is True the fade-in black has already been materialised
    as real PNG frames (to allow title compositing on them), so the tpad
    start_* part is omitted — the fade luminance ramp still applies.
    """
    fade_in  = settings.get("clip_effects/fade_in_enabled",  False)
    fade_out = settings.get("clip_effects/fade_out_enabled", False)

    if not fade_in and not fade_out:
        return [], total_frames

    fi_black = float(settings.get("clip_effects/fade_in_black_dur",  5.0)) if fade_in  else 0.0
    fi_fade  = float(settings.get("clip_effects/fade_in_fade_dur",   1.0)) if fade_in  else 0.0
    fo_black = float(settings.get("clip_effects/fade_out_black_dur", 5.0)) if fade_out else 0.0
    fo_fade  = float(settings.get("clip_effects/fade_out_fade_dur",  1.0)) if fade_out else 0.0

    orig_dur = total_frames / fps
    filters: list[str] = []

    # tpad: prepend and/or append black frames in one pass.
    # start_mode/stop_mode must be "add" (fills with color, default black).
    # skip_prepend: fade-in black frames already exist as PNGs, omit start_*.
    tpad_parts: list[str] = []
    if fi_black > 0 and not skip_prepend:
        tpad_parts += [f"start_duration={fi_black}", "start_mode=add"]
    if fo_black > 0:
        tpad_parts += [f"stop_duration={fo_black}", "stop_mode=add"]
    if tpad_parts:
        filters.append("tpad=" + ":".join(tpad_parts))

    # When skip_prepend=True the black frames are real PNGs in the sequence and
    # the luminance fade was already baked into those files by PIL.  Adding a
    # fade=in filter here would multiply every frame before fi_black by 0
    # (ffmpeg's fade=in sets everything before st to black), destroying the
    # title that was carefully composited on the black frames.
    if fi_fade > 0 and not skip_prepend:
        filters.append(f"fade=t=in:st={fi_black}:d={fi_fade}")

    if fo_fade > 0:
        # When skip_prepend=True the prepended black frames are real PNGs already
        # counted in total_frames, so orig_dur already includes fi_black.
        # Content ends at orig_dur (no tpad start was added).
        # When skip_prepend=False the tpad start adds fi_black seconds before the
        # content, so content ends at fi_black + orig_dur.
        content_end = orig_dur if skip_prepend else fi_black + orig_dur
        fo_start = content_end - fo_fade
        filters.append(f"fade=t=out:st={fo_start:.6f}:d={fo_fade}")

    # When skip_prepend=True the fi_black frames are already in total_frames.
    prepend_extra = 0 if skip_prepend else round(fi_black * fps)
    extra_frames = prepend_extra + round(fo_black * fps)
    return filters, total_frames + extra_frames


def _resolve_fontfile(font_name: str) -> Optional[str]:
    """Return the absolute font file path for *font_name* via fc-match, or None."""
    try:
        r = subprocess.run(
            ["fc-match", "--format=%{file}", font_name],
            capture_output=True, text=True, timeout=5,
        )
        path = r.stdout.strip()
        return path if path and Path(path).is_file() else None
    except Exception:
        return None


def _title_alpha(t: float, duration: float,
                 fi_on: bool, fi_dur: float,
                 fo_on: bool, fo_dur: float) -> float:
    """Return the title opacity [0.0, 1.0] at time *t* (seconds)."""
    if t < 0 or t > duration:
        return 0.0
    alpha = 1.0
    if fi_on and fi_dur > 0:
        alpha = min(alpha, min(1.0, t / fi_dur))
    if fo_on and fo_dur > 0:
        alpha = min(alpha, min(1.0, (duration - t) / fo_dur))
    return max(0.0, alpha)


def _composite_title_frames(
    src_dir: str,
    dst_dir: Path,
    settings: dict,
    fps: int,
    t_offset: float = 0.0,
    content_start: float = 0.0,
    content_fade: float = 0.0,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """Composite the title text onto every frame using PIL and write to *dst_dir*.

    Uses PIL instead of ffmpeg's drawtext filter so there is no dependency on
    libfreetype being compiled into ffmpeg.  Frames that fall outside the title
    duration are hard-linked (or copied) unchanged for speed.

    *t_offset* shifts the title clock so that output t=0 aligns with the start
    of the overall video (including any prepended black clip from tpad).

    *content_start* / *content_fade*: when the black frames are materialised as
    real PNGs (skip_prepend=True), the ffmpeg fade=in filter is omitted to
    prevent it from blackening those frames.  Instead, the luminance ramp for
    the content fade-in is baked here: content frames at t in
    [content_start, content_start+content_fade) are multiplied by
    (t-content_start)/content_fade so the content smoothly fades in from black.

    Frame compositing is parallelised with a thread pool.  PIL's C extensions
    (PNG decode/encode via zlib, alpha_composite) release the GIL, so threads
    give genuine multi-core throughput without the pickling overhead of a
    process pool.
    """
    import concurrent.futures
    from PIL import Image, ImageColor, ImageDraw, ImageFont

    text      = settings.get("clip_effects/title_text", "").strip()
    font_name = settings.get("clip_effects/title_font",      "Noto Serif")
    font_size = int(settings.get("clip_effects/title_font_size", 95))
    anchor    = settings.get("clip_effects/title_anchor",    "bottom-right")
    margin    = int(settings.get("clip_effects/title_margin", 40))
    alignment = settings.get("clip_effects/title_alignment", "right")
    color_hex = settings.get("clip_effects/title_color",     "#ffffff")
    shadow    = bool(settings.get("clip_effects/title_shadow",    True))
    duration  = float(settings.get("clip_effects/title_duration", 10.0))
    fi_on     = bool(settings.get("clip_effects/title_fade_in_enabled",  True))
    fi_dur    = float(settings.get("clip_effects/title_fade_in_dur",  3.0))
    fo_on     = bool(settings.get("clip_effects/title_fade_out_enabled", True))
    fo_dur    = float(settings.get("clip_effects/title_fade_out_dur", 3.0))

    frames = sorted(Path(src_dir).glob("*.png"))
    total  = len(frames)

    if not frames:
        return

    if not text:
        # No text — just hard-link / copy every frame unchanged
        for src in frames:
            dst = dst_dir / src.name
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        return

    # ------------------------------------------------------------------
    # Pre-compute everything that is constant across all frames
    # ------------------------------------------------------------------

    # Font — prefer embedded path (set by load_project when font is bundled)
    font_path = settings.get("clip_effects/title_font_path") or _resolve_fontfile(font_name)
    try:
        pil_font = ImageFont.truetype(font_path or font_name, font_size)
    except (OSError, TypeError):
        try:
            pil_font = ImageFont.truetype(font_name, font_size)
        except OSError:
            pil_font = ImageFont.load_default()

    pil_align = {"left": "left", "center": "center", "right": "right"}.get(alignment, "left")
    shadow_off = max(1, round(font_size * 0.03))

    # Anchor
    parts  = anchor.split("-") if anchor != "center" else ["center", "center"]
    v_part = parts[0]
    h_part = parts[1] if len(parts) > 1 else "center"

    # Text block size (identical for every frame)
    _dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox   = _dummy_draw.textbbox((0, 0), text, font=pil_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Frame dimensions — read from first frame (all frames share the same size)
    with Image.open(frames[0]) as _probe:
        frame_w, frame_h = _probe.size

    # Text origin — margin applied on the anchor sides only
    if h_part == "left":
        tx = margin
    elif h_part == "right":
        tx = frame_w - text_w - margin
    else:
        tx = (frame_w - text_w) // 2

    if v_part == "top":
        ty = margin
    elif v_part == "bottom":
        ty = frame_h - text_h - margin
    else:
        ty = (frame_h - text_h) // 2

    tx, ty = max(0, tx), max(0, ty)

    # Base fill color (alpha applied per-frame)
    try:
        base_rgb = ImageColor.getrgb(color_hex)
    except Exception:
        base_rgb = (255, 255, 255)

    import logging as _log_mod
    import threading
    _log_cf = _log_mod.getLogger(__name__)

    # PIL's FreeTypeFont (FT_Face) is NOT thread-safe when the same object is
    # rendered concurrently.  Use a threading.local to give each worker its own
    # font instance, loaded lazily on first use.
    _font_local = threading.local()

    def _thread_font() -> "ImageFont.FreeTypeFont":
        if not hasattr(_font_local, "instance"):
            try:
                _font_local.instance = ImageFont.truetype(
                    font_path or font_name, font_size
                )
            except (OSError, TypeError):
                try:
                    _font_local.instance = ImageFont.truetype(font_name, font_size)
                except OSError:
                    _font_local.instance = ImageFont.load_default()
        return _font_local.instance  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Per-frame worker (closure — captures pre-computed constants)
    # ------------------------------------------------------------------

    def _process_frame(frame_path: Path) -> bool:
        """Return True if frame was composited, False if hard-linked."""
        try:
            idx = int(frame_path.stem)
        except ValueError:
            idx = 0

        t     = t_offset + idx / fps
        dst   = dst_dir / frame_path.name
        alpha = _title_alpha(t, duration, fi_on, fi_dur, fo_on, fo_dur)

        # Luminance factor for content fade-in baked into PIL frames so we can
        # omit ffmpeg's fade=in filter (which would otherwise black out the
        # prepended black+title frames as a side-effect).
        if content_fade > 0 and t >= content_start:
            elapsed = t - content_start
            luma = min(1.0, elapsed / content_fade)
        else:
            luma = 1.0

        if alpha <= 0.0:
            if luma >= 1.0:
                # Frame needs no changes — hard-link for speed.
                try:
                    os.link(frame_path, dst)
                except OSError:
                    shutil.copy2(frame_path, dst)
            else:
                # Frame luminance must be reduced; can't hard-link.
                img = Image.open(frame_path).convert("RGB")
                img.point(lambda p: int(p * luma)).save(dst, format="PNG")
            return False

        a_int        = round(alpha * 255)
        fill_color   = (*base_rgb, a_int)
        shadow_color = (0, 0, 0, round(0.7 * a_int))

        font = _thread_font()
        img  = Image.open(frame_path).convert("RGBA")

        # Dim the background content before compositing the title so the title
        # is not affected by the fade-in luma ramp (it should stay at its own
        # alpha regardless of how the underlying content is fading in).
        if luma < 1.0:
            img = img.convert("RGB").point(lambda p: int(p * luma)).convert("RGBA")

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)

        if shadow:
            draw.multiline_text(
                (tx + shadow_off, ty + shadow_off), text,
                font=font, fill=shadow_color, align=pil_align,
            )

        draw.multiline_text(
            (tx, ty), text,
            font=font, fill=fill_color, align=pil_align,
        )

        Image.alpha_composite(img, overlay).convert("RGB").save(dst, format="PNG")
        return True

    # ------------------------------------------------------------------
    # Parallel execution — threads are enough because PIL's C extensions
    # (zlib PNG encode/decode, alpha_composite) release the GIL.
    # Each thread gets its own FreeTypeFont instance via _thread_font().
    # ------------------------------------------------------------------

    n_workers = max(1, os.cpu_count() or 1)
    composited = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_process_frame, fp): fp for fp in frames}
        for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            if fut.result():  # re-raises on exception; True = composited
                composited += 1
            if progress_cb:
                progress_cb(done, total)

    _log_cf.info(
        "Title compositing done: %d/%d frames composited (alpha>0), %d hard-linked",
        composited, total, total - composited,
    )


def _music_audio_cmd_parts(
    settings: dict,
    total_duration_s: float,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (pre_input_args, input_args, filter_args, map_codec_args) for music.

    pre_input_args: args placed before the first music -i (e.g. -stream_loop -1 for
                    single-file loop).
    input_args:     one or more [-i, path] pairs for all music inputs.
    filter_args:    [-af, chain] for a single file, or [-filter_complex, graph] for
                    multiple files; empty when music is disabled.
    map_codec_args: explicit stream mapping + AAC codec args; empty when no music.

    Multiple files are concatenated (with optional acrossfade crossfade between each
    pair) and trimmed to the exact video duration.  Fade-out is applied only at the
    end of the video.  Loop mode repeats the whole playlist enough times to cover T.
    """
    if not settings.get("clip_effects/music_enabled", False):
        return [], [], [], []

    # ── Resolve file list ─────────────────────────────────────────────────────
    paths_raw = settings.get("clip_effects/music_paths") or ""
    if isinstance(paths_raw, list):
        music_paths: list[str] = paths_raw
    else:
        try:
            music_paths = json.loads(paths_raw) if paths_raw else []
        except (ValueError, TypeError):
            music_paths = []
    # Backward compat: if new key absent/empty, fall back to old single-path key.
    if not music_paths:
        old = settings.get("clip_effects/music_path") or ""
        music_paths = [old] if old else []

    music_paths = [p for p in music_paths if p and Path(p).is_file()]
    if not music_paths:
        return [], [], [], []

    # ── Settings ──────────────────────────────────────────────────────────────
    delay  = max(0.0, float(settings.get("clip_effects/music_delay",               0.0)))
    loop   = bool(settings.get("clip_effects/music_loop",                          False))
    fi_on  = bool(settings.get("clip_effects/music_fade_in_enabled",               False))
    fi_dur = max(0.0, float(settings.get("clip_effects/music_fade_in_dur",         1.0)))
    fo_on  = bool(settings.get("clip_effects/music_fade_out_enabled",              True))
    fo_dur = max(0.0, float(settings.get("clip_effects/music_fade_out_dur",        5.0)))
    cf_on  = bool(settings.get("clip_effects/music_crossfade_enabled",             True))
    cf_dur = max(0.0, float(settings.get("clip_effects/music_crossfade_dur",       5.0)))

    T = total_duration_s

    # ── Single file with loop: use -stream_loop -1 (infinite), simple -af ─────
    if len(music_paths) == 1:
        pre_input  = ["-stream_loop", "-1"] if loop else []
        input_args = ["-i", music_paths[0]]

        af: list[str] = []
        if delay > 0:
            af.append(f"adelay={round(delay * 1000)}:all=1")
        if fi_on and fi_dur > 0 and delay < T:
            af.append(f"afade=t=in:st={delay:.6f}:d={fi_dur:.6f}")
        af.append(f"atrim=end={T:.6f}")
        af.append("asetpts=PTS-STARTPTS")
        if fo_on and fo_dur > 0 and fo_dur < T:
            af.append(f"afade=t=out:st={T - fo_dur:.6f}:d={fo_dur:.6f}")

        filter_args    = ["-af", ",".join(af)]
        map_codec_args = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k"]
        return pre_input, input_args, filter_args, map_codec_args

    # ── Multiple files: build -filter_complex ─────────────────────────────────
    # In loop mode repeat the whole playlist enough times to cover T.
    # Assume a minimum track length of 10 s; cap at 50 repetitions (≥500 s coverage
    # per file pair, enough for any realistic video length).
    if loop:
        min_cycle_s = 10.0 * len(music_paths)
        reps = min(50, max(2, int(T / min_cycle_s) + 2))
        file_sequence = music_paths * reps
    else:
        file_sequence = list(music_paths)

    # Build flat input list (video = input 0; audio files start at index 1).
    input_args = []
    for f in file_sequence:
        input_args += ["-i", f]

    n = len(file_sequence)
    # FFmpeg input indices for the audio files (1-based).
    idx = list(range(1, n + 1))

    fc_parts: list[str] = []

    # First stream: apply delay and fade-in.
    pre_filters: list[str] = []
    if delay > 0:
        pre_filters.append(f"adelay={round(delay * 1000)}:all=1")
    if fi_on and fi_dur > 0:
        pre_filters.append(f"afade=t=in:st={delay:.6f}:d={fi_dur:.6f}")

    if pre_filters:
        fc_parts.append(f"[{idx[0]}:a]{','.join(pre_filters)}[afirst]")
        current = "[afirst]"
    else:
        current = f"[{idx[0]}:a]"

    # Chain the remaining streams with acrossfade (crossfade) or concat.
    for i, audio_idx in enumerate(idx[1:], start=1):
        next_stream = f"[{audio_idx}:a]"
        out_label   = f"[ac{i}]"
        if cf_on and cf_dur > 0:
            fc_parts.append(
                f"{current}{next_stream}acrossfade=d={cf_dur:.6f}:c1=tri:c2=tri{out_label}"
            )
        else:
            fc_parts.append(
                f"{current}{next_stream}concat=n=2:v=0:a=1{out_label}"
            )
        current = out_label

    # Final trim at T and fade-out applied only at the very end of the video.
    final_af: list[str] = [f"atrim=end={T:.6f}", "asetpts=PTS-STARTPTS"]
    if fo_on and fo_dur > 0 and fo_dur < T:
        final_af.append(f"afade=t=out:st={T - fo_dur:.6f}:d={fo_dur:.6f}")
    fc_parts.append(f"{current}{','.join(final_af)}[aout]")

    filter_args    = ["-filter_complex", ";".join(fc_parts)]
    map_codec_args = ["-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    return [], input_args, filter_args, map_codec_args


def _quality_args(enc: EncoderConfig, cq: int, preset: str) -> list[str]:
    args: list[str] = []

    # NVENC H.264/H.265 require VBR RC mode for CQ to work correctly
    if enc.hw_type == "nvenc" and enc.codec in ("h264", "h265"):
        args += ["-rc", "vbr", "-cq", str(cq)]
    elif enc.cq_flag:
        args += [enc.cq_flag, str(cq)]

    # SVT-AV1 / libaom need explicit bitrate=0 to engage CRF mode
    if enc.name in ("libsvtav1", "libaom-av1"):
        args += ["-b:v", "0"]

    if preset and enc.preset_flag:
        args += [enc.preset_flag, preset]

    return args


def _pix_fmt_args(enc: EncoderConfig) -> list[str]:
    # yuv420p is required for broad player compatibility on H.264/H.265;
    # AV1 software encoders also default to it for 8-bit output.
    return ["-pix_fmt", "yuv420p"]


_ATTACHMENT_CONTAINERS = {"mkv", "mp4"}


def _attach_args(gpx_path: str | None, container: str) -> list[str]:
    if not gpx_path or not Path(gpx_path).is_file():
        return []
    if container not in _ATTACHMENT_CONTAINERS:
        return []
    return [
        "-attach", gpx_path,
        "-metadata:s:t:0", "mimetype=application/gpx+xml",
        "-metadata:s:t:0", f"filename={Path(gpx_path).name}",
    ]


_SETTINGS_ATTACHMENT_CONTAINERS = {"mkv"}   # MP4 attachment support is unreliable for JSON


def _serialise_settings(settings: dict) -> str:
    """Return a pretty-printed JSON string of settings, excluding the API key."""
    safe = {k: v for k, v in settings.items() if k != "imagery/api_key"}
    return json.dumps(safe, indent=2, sort_keys=True, default=str)


def _attach_settings_args(settings_path: str, container: str) -> list[str]:
    if container not in _SETTINGS_ATTACHMENT_CONTAINERS:
        return []
    return [
        "-attach", settings_path,
        "-metadata:s:t:1", "mimetype=application/json",
        "-metadata:s:t:1", "filename=georeel_settings.json",
    ]


def _write_settings(settings: dict, out: Path, container: str) -> None:
    """For non-MKV containers write <stem>_settings.json next to the video."""
    if container in _SETTINGS_ATTACHMENT_CONTAINERS:
        return
    dest = out.with_name(out.stem + "_settings.json")
    dest.write_text(_serialise_settings(settings), encoding="utf-8")


def _copy_gpx_alongside(gpx_path: str | None, out: Path, container: str) -> None:
    """For containers that don't support attachments, copy the GPX next to the video."""
    if not gpx_path or not Path(gpx_path).is_file():
        return
    if container in _ATTACHMENT_CONTAINERS:
        return
    dest = out.with_suffix(".gpx")
    shutil.copy2(gpx_path, dest)


def _container_args(enc: EncoderConfig, container: str) -> list[str]:
    args: list[str] = []
    if container == "mp4":
        args += ["-movflags", "+faststart"]
        # Apple devices need hvc1 tag for H.265 in MP4
        if enc.codec == "h265":
            args += ["-tag:v", "hvc1"]
    return args
