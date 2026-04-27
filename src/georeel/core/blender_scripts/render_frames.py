"""
Blender script: loads the terrain scene, builds a fly-through camera from
keyframe JSON data, and renders each frame to the output directory.

Invoked headlessly by frame_renderer.py:
    blender --background scene.blend --python render_frames.py \
        -- keyframes.json output_dir engine resolution quality

Progress is reported by printing  Fra:<n>/<total>  after each frame so the
host process can track it.
"""

import json
import socket
import sys


def _zero_roll_quat(pos, look_at, Vector, Matrix, Quaternion):
    """Return a quaternion that points the camera -Z toward look_at with zero
    roll (X axis is always horizontal, i.e. world Z is never tilted sideways).
    """
    world_z = Vector((0.0, 0.0, 1.0))

    fwd = look_at - pos
    if fwd.length > 1e-6:
        fwd.normalize()
    else:
        fwd = Vector((0.0, 1.0, 0.0))

    # If forward is (nearly) straight up or down, fall back to a safe up vector
    if abs(fwd.dot(world_z)) > 0.9999:
        right = Vector((1.0, 0.0, 0.0))
    else:
        right = fwd.cross(world_z)
        right.normalize()

    up = right.cross(fwd)          # derived up — guaranteed no roll
    up.normalize()

    # Blender camera convention: X right, Y up, -Z forward
    # Build rotation matrix from column vectors
    mat = Matrix((
        ( right.x,  right.y,  right.z),   # row 0 = X axis of camera
        (    up.x,     up.y,     up.z),   # row 1 = Y axis of camera
        (  -fwd.x,   -fwd.y,   -fwd.z),  # row 2 = -Z axis of camera (forward)
    )).transposed()   # transpose: columns become rows expected by from_matrix

    return mat.to_quaternion()


def _detect_n_static(keyframes_data: list, n_intro: int) -> int:
    """Count leading intro frames that share the same camera position (the static hold phase)."""
    if not n_intro or not keyframes_data:
        return 0
    x0, y0, z0 = keyframes_data[0]["x"], keyframes_data[0]["y"], keyframes_data[0]["z"]
    count = 0
    for kf in keyframes_data[:n_intro]:
        if abs(kf["x"] - x0) + abs(kf["y"] - y0) + abs(kf["z"] - z0) < 1e-3:
            count += 1
        else:
            break
    return count


def _detect_n_clearance(keyframes_data: list, n_intro: int) -> int:
    """Count trailing intro frames whose position matches the last intro frame.

    This includes the final descent snap frame plus any clearance frames — all
    of which are at the flythrough start position.  The result is used as an
    offset: ``n_intro - _detect_n_clearance()`` is the index of the descent
    snap frame, which is the correct Blender control point for ending the SINE
    EASE_IN_OUT segment.  Works even for JSON files that predate the
    ``is_clearance`` field.
    """
    if not n_intro or not keyframes_data:
        return 0
    last = keyframes_data[n_intro - 1]
    x0, y0, z0 = last["x"], last["y"], last["z"]
    count = 0
    for kf in reversed(keyframes_data[:n_intro]):
        if abs(kf["x"] - x0) + abs(kf["y"] - y0) + abs(kf["z"] - z0) < 1e-3:
            count += 1
        else:
            break
    return count


# Resolution presets (width, height)
_RESOLUTIONS = {
    # Landscape (16:9)
    "720p":  (1280,  720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k":    (3840, 2160),
    # Portrait (9:16)
    "portrait_720p":  ( 720, 1280),
    "portrait_1080p": (1080, 1920),
    "portrait_1440p": (1440, 2560),
    "portrait_4k":    (2160, 3840),
    # Square (1:1)
    "square_720":  ( 720,  720),
    "square_1080": (1080, 1080),
    "square_1440": (1440, 1440),
    "square_2160": (2160, 2160),
}

# Render samples per quality/engine combination
_SAMPLES = {
    "eevee":  {"low": 32, "medium": 64,  "high": 128},
    "cycles": {"low": 64, "medium": 128, "high": 256},
}


def _select_keyframe_indices(keyframes_data: list, stride: int, n_intro: int = 0, n_static: int = 0, n_clearance: int = 0) -> list[int]:
    """Return sorted indices into keyframes_data to use as Blender keyframes.

    Intro: sparse control points — hold start, descent start, descent end,
    clearance end (if clearance > 0).  The SINE EASE_IN_OUT on descent-start
    ends at descent-end so the camera holds still during clearance.
    Flythrough frames use the given stride; pause-segment boundaries are always
    included.
    """
    n = len(keyframes_data)
    selected: set[int] = set()
    selected.add(0)
    selected.add(n - 1)
    if n_intro > 0:
        selected.add(0)                         # hold start
        if 0 < n_static < n_intro:
            selected.add(n_static - 1)          # last hold frame = descent start
        if n_clearance > 0:
            selected.add(n_intro - n_clearance)  # descent snap frame = clearance start
        selected.add(n_intro - 1)               # last intro frame (clearance end or descent end)
    for i in range(max(n_intro, 0), n, stride):
        selected.add(i)
    in_pause = False
    for i, kf in enumerate(keyframes_data):
        is_pause = kf.get("is_pause", False)
        if is_pause and not in_pause:
            selected.add(i)
            in_pause = True
        elif not is_pause and in_pause:
            selected.add(i)
            in_pause = False
    return sorted(selected)


def _setup_intro_overview(scene, keyframes_data: list, track_lift_m: float = 50.0) -> int:
    """Prepare scene for intro-overview frames prepended to the flythrough.

    Shifts all existing scene animation forward by n_intro frames, creates a
    static full-track ribbon that fades out during the overview, and hides
    waypoint markers while the intro plays.

    Returns n_intro (0 when no intro frames are present).
    """
    import bpy  # noqa: PLC0415

    n_intro = sum(1 for kf in keyframes_data if kf.get("is_intro", False))
    if n_intro == 0:
        return 0

    # ── 1+2. Shift all existing scene animation forward by n_intro frames ── #
    for obj in scene.objects:
        if not (obj.animation_data and obj.animation_data.action):
            continue
        for fc in obj.animation_data.action.fcurves:
            # frame_start F-curves store absolute scene frame numbers as Y values
            is_frame_ref = "frame_start" in fc.data_path
            for kp in fc.keyframe_points:
                kp.co.x           += n_intro
                kp.handle_left.x  += n_intro
                kp.handle_right.x += n_intro
                if is_frame_ref:
                    kp.co.y           += n_intro
                    kp.handle_left.y  += n_intro
                    kp.handle_right.y += n_intro
            fc.update()

    # ── 3a. Static full-track ribbon visible during the overview ──────────── #
    track_obj = scene.objects.get("Track")
    if track_obj is not None:
        ov_mesh = track_obj.data.copy()
        ov_obj  = bpy.data.objects.new("TrackOverview", ov_mesh)
        # Link to the same collections as Track, plus always to the scene
        # master collection so it is never excluded from any view layer.
        linked_cols: set = set()
        for col in track_obj.users_collection:
            col.objects.link(ov_obj)
            linked_cols.add(id(col))
        if id(scene.collection) not in linked_cols:
            scene.collection.objects.link(ov_obj)
        ov_obj.matrix_world = track_obj.matrix_world.copy()

        for v in ov_mesh.vertices:
            v.co.z += track_lift_m

        print(f"[georeel] TrackOverview created: {len(ov_mesh.polygons)} faces, "
              f"hide_render={ov_obj.hide_render}")

        # Fading emission material matching the track ribbon colour
        mat = bpy.data.materials.new("TrackOverviewMat")
        mat.use_nodes = True
        try:
            mat.surface_render_method = "BLENDED"   # EEVEE Next (Blender 4.2+)
        except AttributeError:
            try:
                mat.blend_method = "BLEND"           # legacy EEVEE
            except AttributeError:
                pass
        nt = mat.node_tree
        nt.nodes.clear()

        out_node = nt.nodes.new("ShaderNodeOutputMaterial"); out_node.location = (700,    0)
        mix_node = nt.nodes.new("ShaderNodeMixShader");      mix_node.location = (500,    0)
        transp   = nt.nodes.new("ShaderNodeBsdfTransparent"); transp.location  = (300, -100)
        emit     = nt.nodes.new("ShaderNodeEmission");        emit.location    = (300,  100)
        emit.inputs["Strength"].default_value = 10.0
        vcol     = nt.nodes.new("ShaderNodeVertexColor");     vcol.location    = (100,  100)
        vcol.layer_name = "TrackColor"
        fac_node = nt.nodes.new("ShaderNodeValue");           fac_node.location = (100, -100)
        fac_node.name = "OverviewAlpha"

        nt.links.new(vcol.outputs["Color"],      emit.inputs["Color"])
        nt.links.new(fac_node.outputs[0],        mix_node.inputs[0])
        nt.links.new(transp.outputs["BSDF"],     mix_node.inputs[1])
        nt.links.new(emit.outputs["Emission"],   mix_node.inputs[2])
        nt.links.new(mix_node.outputs["Shader"], out_node.inputs["Surface"])

        ov_obj.data.materials.clear()
        ov_obj.data.materials.append(mat)

        n_static = _detect_n_static(keyframes_data, n_intro)
        # Fade completes exactly at the snap frame (camera arrives at track start).
        # During clearance the ribbon is already fully transparent.
        n_clearance_kfs = sum(1 for kf in keyframes_data[:n_intro] if kf.get("is_clearance", False))
        if n_clearance_kfs > 0:
            fade_end = n_intro - n_clearance_kfs  # snap frame Blender number
        else:
            n_tail = _detect_n_clearance(keyframes_data, n_intro)
            fade_end = (n_intro - n_tail + 1) if n_tail > 1 else n_intro
        for frm, val in ((1, 1.0), (n_static, 1.0), (fade_end, 0.0)):
            fac_node.outputs[0].default_value = val
            fac_node.outputs[0].keyframe_insert("default_value", frame=frm)

        if nt.animation_data and nt.animation_data.action:
            for fc in nt.animation_data.action.fcurves:
                fc.extrapolation = "CONSTANT"
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"

        # Hide overview ribbon once the intro ends
        ov_obj.hide_render   = False
        ov_obj.hide_viewport = False
        ov_obj.keyframe_insert("hide_render",   frame=1)
        ov_obj.keyframe_insert("hide_viewport", frame=1)
        ov_obj.keyframe_insert("hide_render",   frame=n_intro)
        ov_obj.keyframe_insert("hide_viewport", frame=n_intro)
        ov_obj.hide_render   = True
        ov_obj.hide_viewport = True
        ov_obj.keyframe_insert("hide_render",   frame=n_intro + 1)
        ov_obj.keyframe_insert("hide_viewport", frame=n_intro + 1)

        if ov_obj.animation_data and ov_obj.animation_data.action:
            for fc in ov_obj.animation_data.action.fcurves:
                fc.extrapolation = "CONSTANT"
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"

    # ── 3b. Hide waypoint markers during the intro ────────────────────────── #
    for marker_name in ("TrackMarker", "TrackMarkerHole"):
        marker_obj = scene.objects.get(marker_name)
        if marker_obj is None:
            continue
        marker_obj.hide_render   = True
        marker_obj.hide_viewport = True
        marker_obj.keyframe_insert("hide_render",   frame=1)
        marker_obj.keyframe_insert("hide_viewport", frame=1)
        marker_obj.hide_render   = False
        marker_obj.hide_viewport = False
        marker_obj.keyframe_insert("hide_render",   frame=n_intro + 1)
        marker_obj.keyframe_insert("hide_viewport", frame=n_intro + 1)
        if marker_obj.animation_data and marker_obj.animation_data.action:
            for fc in marker_obj.animation_data.action.fcurves:
                fc.extrapolation = "CONSTANT"
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"

    return n_intro


def _retime_ribbon_and_marker(scene, keyframes_data: list) -> None:
    """Re-time ribbon Build modifier and track marker to match camera arc-length.

    Fixes two bugs:
    1. Dynamic-speed desync: ribbon/marker use constant speed but camera varies.
    2. Ribbon bleeds into intro: without photo pauses the Build modifier has no
       animation, so the shift in _setup_intro_overview is a no-op and the
       ribbon starts revealing from frame 1 during the intro overview.

    By re-creating Build modifier animation that starts at the first flythrough
    frame, CONSTANT extrapolation automatically holds 0 faces during intro and
    pre-photo frames.  Camera look_at positions drive timing for exact sync.
    """
    import math
    import bpy

    track_obj = scene.objects.get("Track")
    if track_obj is None:
        return
    build_mod = next((m for m in track_obj.modifiers if m.type == "BUILD"), None)
    if build_mod is None:
        return

    mesh = track_obj.data
    n_faces = len(mesh.polygons)
    if n_faces < 2:
        return

    from mathutils import Vector as _V
    mat_w = track_obj.matrix_world
    fc_x: list[float] = []
    fc_y: list[float] = []
    fc_z: list[float] = []
    for poly in mesh.polygons:
        cx, cy, cz = poly.center
        p = mat_w @ _V((cx, cy, cz))
        fc_x.append(p.x)
        fc_y.append(p.y)
        fc_z.append(p.z)

    # Build per-flythrough-frame list (scene_frame, look_at_xy, is_pause)
    fly_frames: list[tuple[int, float, float, bool]] = []
    for idx, kf in enumerate(keyframes_data):
        if kf.get("is_intro", False):
            continue
        fly_frames.append((
            idx + 1,
            kf["look_at_x"], kf["look_at_y"],
            kf.get("is_pause", False),
        ))
    if not fly_frames:
        return

    n_fly = len(fly_frames)
    # Window: how many ribbon faces to scan forward per frame (with safety margin)
    window = max(50, int(n_faces / n_fly * 20) + 1)

    # Map each flythrough frame to the nearest ribbon face (monotone forward scan)
    face_ptr = 0
    frame_face: list[tuple[int, int]] = []  # (scene_frame, face_idx)
    for sf, lx, ly, is_pause in fly_frames:
        if is_pause and frame_face:
            frame_face.append((sf, frame_face[-1][1]))
            continue
        best_j = face_ptr
        best_d2 = (fc_x[face_ptr] - lx) ** 2 + (fc_y[face_ptr] - ly) ** 2
        for j in range(face_ptr + 1, min(face_ptr + window, n_faces)):
            d2 = (fc_x[j] - lx) ** 2 + (fc_y[j] - ly) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_j = j
            elif d2 > best_d2 * 25:
                break
        face_ptr = best_j
        frame_face.append((sf, best_j))

    # --- Re-keyframe Build modifier frame_start ---------------------------
    # Formula: frame_start(f) = f − frame_duration × face_idx(f) / n_faces
    # This ensures n_visible = face_idx(f) at frame f.
    # CONSTANT extrapolation before first flythrough KF → 0 faces during intro.
    frame_duration = float(build_mod.frame_duration)
    dp = 'modifiers["Unfold"].frame_start'

    if track_obj.animation_data and track_obj.animation_data.action:
        for fc in list(track_obj.animation_data.action.fcurves):
            if "frame_start" in fc.data_path and "Unfold" in fc.data_path:
                track_obj.animation_data.action.fcurves.remove(fc)
    if track_obj.animation_data is None:
        track_obj.animation_data_create()
    if track_obj.animation_data.action is None:
        track_obj.animation_data.action = bpy.data.actions.new("TrackAction")

    _STRIDE = 10
    for i, (sf, fi) in enumerate(frame_face):
        fs = sf - frame_duration * fi / n_faces
        is_edge = i == 0 or i == n_fly - 1
        is_stride = i % _STRIDE == 0
        is_jump = i > 0 and abs(frame_face[i][1] - frame_face[i - 1][1]) > 1
        if is_edge or is_stride or is_jump:
            build_mod.frame_start = fs
            track_obj.keyframe_insert(data_path=dp, frame=sf)

    if track_obj.animation_data and track_obj.animation_data.action:
        for fc in track_obj.animation_data.action.fcurves:
            if "frame_start" in fc.data_path and "Unfold" in fc.data_path:
                fc.extrapolation = "CONSTANT"
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"

    # --- Re-keyframe TrackMarker only (TrackMarkerHole is a child; it inherits) ---
    # TrackMarkerHole is parented to TrackMarker with a fixed local offset —
    # we must NOT give it world-space location keyframes.
    # The marker in build_scene.py sits 4 m above the ribbon (z_offset=4.0 vs
    # ribbon's z_offset=2.0), so lift face-center Z by +2 to match that gap.
    _MARKER_Z_EXTRA = 2.0

    mobj = scene.objects.get("TrackMarker")
    if mobj is not None:
        if mobj.animation_data and mobj.animation_data.action:
            for fc in list(mobj.animation_data.action.fcurves):
                if fc.data_path == "location":
                    mobj.animation_data.action.fcurves.remove(fc)
        if mobj.animation_data is None:
            mobj.animation_data_create()
        if mobj.animation_data.action is None:
            mobj.animation_data.action = bpy.data.actions.new("TrackMarkerAction")

        prev_fi = -1
        for i, (sf, fi) in enumerate(frame_face):
            is_edge = i == 0 or i == n_fly - 1
            is_stride = i % _STRIDE == 0
            is_face_change = fi != prev_fi
            if is_edge or is_stride or is_face_change:
                mobj.location = _V((fc_x[fi], fc_y[fi], fc_z[fi] + _MARKER_Z_EXTRA))
                mobj.keyframe_insert("location", frame=sf)
                prev_fi = fi

        if mobj.animation_data and mobj.animation_data.action:
            for fc in mobj.animation_data.action.fcurves:
                if fc.data_path == "location":
                    fc.extrapolation = "CONSTANT"
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"

    # Diagnostic: print first few frame→face mappings to help debug sync issues
    _diag = frame_face[:8]
    print(f"[georeel] Ribbon + marker retimed: {n_fly} fly-frames → {n_faces} faces  "
          f"frame_duration={frame_duration:.0f}  "
          f"first_ff={_diag}")


def _inject_locality_banners(banners_path: str, cam_obj, scene) -> None:  # noqa: ANN001
    """Create world-space locality name banners below the tracking point.

    Each banner is placed at a fixed world position computed at spawn time to sit
    below the camera's look-at point in screen space.  It stays there for its
    entire lifetime (does not follow the camera).

    Shape: a thin slab in the XZ plane (width=X, height=Z, depth=Y).
    TRACK_TO keeps the front face pointing at the camera as the camera moves.
    A LOC_DIFF scale driver keeps the apparent angular size constant.

    Animated alpha (ShaderNodeValue keyframes) drives the fade-in / fade-out.
    """
    import bpy  # noqa: PLC0415

    if hasattr(scene, "eevee") and hasattr(scene.eevee, "use_bloom"):
        scene.eevee.use_bloom = False

    try:
        with open(banners_path) as fh:
            banners = json.load(fh)
    except Exception as exc:
        print(f"[georeel] Could not load banners JSON: {exc}", file=sys.stderr)
        return

    for i, b in enumerate(banners):
        label     = b.get("name", f"Banner_{i}")
        bx, by, bz = float(b["x"]), float(b["y"]), float(b["z"])
        width_m   = float(b["width_m"])
        height_m  = float(b["height_m"])
        fs        = int(b["frame_start"])
        fe        = int(b["frame_end"])
        ff        = int(b["fade_frames"])
        tex_path  = str(b["texture"])
        scale_f   = float(b.get("scale_factor", 0.08))

        # Normalised aspect ratio (mesh width = 1.0, height = aspect)
        aspect = height_m / max(width_m, 1e-6)
        d      = 0.06   # slab depth (Y)

        # ── Mesh: slab in XZ plane ───────────────────────────────────────── #
        # Front face (+Y) has CCW winding → normal = +Y.
        # TRACK_NEGATIVE_Y points that normal at the camera.
        # Width in X, height in Z; origin at slab centre-bottom (Z=0).
        hw = 0.5
        verts = [
            # Front face (y=+d/2), CCW from +Y: BL BR TR TL
            (-hw,  d/2,      0.0),   # 0
            ( hw,  d/2,      0.0),   # 1
            ( hw,  d/2,   aspect),   # 2
            (-hw,  d/2,   aspect),   # 3
            # Back face (y=-d/2)
            (-hw, -d/2,      0.0),   # 4
            ( hw, -d/2,      0.0),   # 5
            ( hw, -d/2,   aspect),   # 6
            (-hw, -d/2,   aspect),   # 7
        ]
        faces = [
            (0, 1, 2, 3),   # 0  front ← textured
            (7, 6, 5, 4),   # 1  back
            (3, 2, 6, 7),   # 2  top
            (0, 4, 5, 1),   # 3  bottom
            (0, 3, 7, 4),   # 4  left
            (2, 1, 5, 6),   # 5  right
        ]

        mesh = bpy.data.meshes.new(f"BannerMesh_{i:04d}")
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        obj = bpy.data.objects.new(f"LocalityBanner_{i:04d}", mesh)
        obj.location = (bx, by, bz)
        scene.collection.objects.link(obj)

        # ── Material: object-coordinate texture projection ───────────────── #
        # U = local_X + 0.5  (X ∈ [-0.5, 0.5] → [0, 1])
        # V = clamp(local_Z / aspect)  (Z ∈ [0, aspect] → [0, 1])
        mat = bpy.data.materials.new(f"BannerMat_{i:04d}")
        mat.use_nodes = True
        # Transparent alpha blending: EEVEE Next (4.2+) uses surface_render_method;
        # legacy EEVEE uses blend_method.  Try both so the script works across versions.
        try:
            mat.surface_render_method = "BLENDED"  # type: ignore[attr-defined]  # Blender 4.2+
        except AttributeError:
            mat.blend_method = "BLEND"  # type: ignore[attr-defined]  # Blender < 4.2
        try:
            mat.show_transparent_back = False
        except AttributeError:
            pass
        nt = mat.node_tree
        nt.nodes.clear()

        out_node   = nt.nodes.new("ShaderNodeOutputMaterial"); out_node.location   = (900, 0)
        mix_node   = nt.nodes.new("ShaderNodeMixShader");      mix_node.location   = (700, 0)
        transp     = nt.nodes.new("ShaderNodeBsdfTransparent"); transp.location    = (500, -120)
        emit       = nt.nodes.new("ShaderNodeEmission");       emit.location       = (500, 100)
        emit.inputs["Strength"].default_value = 1.0
        tex_node   = nt.nodes.new("ShaderNodeTexImage");       tex_node.location   = (200, 100)
        alpha_node = nt.nodes.new("ShaderNodeValue");          alpha_node.location = (200, -100)
        alpha_node.name = "BannerAlpha"
        mul_node   = nt.nodes.new("ShaderNodeMath");           mul_node.location   = (400, -60)
        mul_node.operation = "MULTIPLY"

        coord_node = nt.nodes.new("ShaderNodeTexCoord");       coord_node.location = (-600, 100)
        sep_node   = nt.nodes.new("ShaderNodeSeparateXYZ");    sep_node.location   = (-400, 100)
        add_u      = nt.nodes.new("ShaderNodeMath");           add_u.location      = (-200, 200)
        add_u.operation = "ADD"
        add_u.inputs[1].default_value = 0.5            # U = local_X + 0.5
        div_v      = nt.nodes.new("ShaderNodeMath");           div_v.location      = (-200, 0)
        div_v.operation = "DIVIDE"
        div_v.inputs[1].default_value = max(aspect, 1e-6)     # V = local_Z / aspect
        clamp_v    = nt.nodes.new("ShaderNodeClamp");          clamp_v.location    = (-50, 0)
        clamp_v.inputs["Min"].default_value = 0.02
        clamp_v.inputs["Max"].default_value = 0.98
        comb_node  = nt.nodes.new("ShaderNodeCombineXYZ");     comb_node.location  = (50, 100)

        bpy_img = bpy.data.images.load(tex_path)
        tex_node.image = bpy_img

        # X → U, Z → V
        nt.links.new(coord_node.outputs["Object"],   sep_node.inputs["Vector"])
        nt.links.new(sep_node.outputs["X"],          add_u.inputs[0])
        nt.links.new(sep_node.outputs["Z"],          div_v.inputs[0])
        nt.links.new(div_v.outputs[0],               clamp_v.inputs["Value"])
        nt.links.new(add_u.outputs[0],               comb_node.inputs["X"])
        nt.links.new(clamp_v.outputs["Result"],      comb_node.inputs["Y"])
        nt.links.new(comb_node.outputs["Vector"],    tex_node.inputs["Vector"])
        nt.links.new(tex_node.outputs["Color"],      emit.inputs["Color"])
        nt.links.new(tex_node.outputs["Alpha"],      mul_node.inputs[0])
        nt.links.new(alpha_node.outputs[0],          mul_node.inputs[1])
        nt.links.new(mul_node.outputs[0],            mix_node.inputs[0])
        nt.links.new(transp.outputs["BSDF"],         mix_node.inputs[1])
        nt.links.new(emit.outputs["Emission"],       mix_node.inputs[2])
        nt.links.new(mix_node.outputs["Shader"],     out_node.inputs["Surface"])
        mesh.materials.append(mat)

        # ── Animate BannerAlpha ───────────────────────────────────────────── #
        fade_peak = min(fs + ff, fe)
        fade_out  = max(fe - ff, fade_peak)
        for frm, val in ((fs, 0.0), (fade_peak, 1.0), (fade_out, 1.0), (fe, 0.0)):
            alpha_node.outputs[0].default_value = val
            alpha_node.outputs[0].keyframe_insert("default_value", frame=frm)

        if nt.animation_data and nt.animation_data.action:
            for fc in nt.animation_data.action.fcurves:
                fc.extrapolation = "CONSTANT"
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"

        # ── Scale driver: constant angular size (scale = dist × scale_f) ─── #
        for axis_i in range(3):
            fc = obj.driver_add("scale", axis_i)
            drv = fc.driver
            drv.type = "SCRIPTED"
            drv.expression = f"dist * {scale_f}"
            var = drv.variables.new()
            var.name = "dist"
            var.type = "LOC_DIFF"
            var.targets[0].id = cam_obj
            var.targets[1].id = obj

        # ── TRACK_TO: front face always points at the camera ─────────────── #
        con = obj.constraints.new("TRACK_TO")
        con.target     = cam_obj
        con.track_axis = "TRACK_NEGATIVE_Y"
        con.up_axis    = "UP_Z"

        print(f"[georeel] Banner '{label}' frames {fs}–{fe}")


def main() -> None:
    import bpy
    from mathutils import Matrix, Quaternion, Vector

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 5:
        print("Usage: render_frames.py -- keyframes.json output_dir engine resolution quality",
              file=sys.stderr)
        sys.exit(1)

    keyframes_path, output_dir, engine, resolution, quality = argv[:5]
    # Optional segmented-render arguments (added by frame_renderer.py in multi-segment mode)
    frame_start_arg  = int(argv[5])   if len(argv) > 5 else None
    frame_end_arg    = int(argv[6])   if len(argv) > 6 else None
    tile_filter_str   = (argv[7] or None) if len(argv) > 7 else None  # "" → None
    tex_scale         = float(argv[8]) if len(argv) > 8 else 1.0
    png_compression   = int(argv[9])   if len(argv) > 9 else 1       # zlib level 0–9
    compression_port  = int(argv[10])  if len(argv) > 10 else 0      # 0 = no server
    banners_path      = (argv[11] or None) if len(argv) > 11 else None  # "" → None
    intro_track_lift  = float(argv[12])    if len(argv) > 12 else 5.0   # metres; fallback = DEFAULTS[KEY_INTRO_TRACK_LIFT]

    with open(keyframes_path) as f:
        keyframes_data = json.load(f)

    if not keyframes_data:
        print("[georeel] No keyframes — nothing to render.", file=sys.stderr)
        sys.exit(1)

    total = len(keyframes_data)
    width, height = _RESOLUTIONS.get(resolution, (1920, 1080))
    samples = _SAMPLES.get(engine, _SAMPLES["eevee"]).get(quality, 64)

    # ------------------------------------------------------------------ #
    # Tile filter: delete excluded terrain objects so their textures are  #
    # never uploaded to VRAM — the primary memory-reduction mechanism for  #
    # segmented rendering of large satellite textures.                    #
    # ------------------------------------------------------------------ #
    if tile_filter_str:
        allowed_tiles    = set(tile_filter_str.split(","))
        to_remove_mats   = []
        to_remove_objs   = []
        to_remove_meshes = []

        for obj in list(bpy.data.objects):
            if not obj.name.startswith("Terrain_"):
                continue
            tile_id = obj.name[len("Terrain_"):]
            if tile_id in allowed_tiles:
                continue
            for ms in obj.material_slots:
                if ms.material:
                    to_remove_mats.append(ms.material)
            to_remove_meshes.append(obj.data)
            to_remove_objs.append(obj)

        for obj in to_remove_objs:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in to_remove_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        # Remove materials — this decrements the image user count.
        for mat in to_remove_mats:
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        # Now images whose only users were the removed materials can be freed.
        for img in list(bpy.data.images):
            if img.users == 0:
                bpy.data.images.remove(img)

        n_kept    = len(allowed_tiles)
        n_removed = len(to_remove_objs)
        print(f"[georeel] Tile filter: kept {n_kept}, removed {n_removed} terrain tile(s)")

    # ------------------------------------------------------------------ #
    # Texture downscale (draft / viewport mode)                            #
    #                                                                     #
    # Halving each dimension → 4× less VRAM.  For satellite terrain the   #
    # GPU is typically texture-bandwidth-bound; reducing texture size is   #
    # far more effective than reducing sample count.                       #
    # img.scale() loads the full image then resamples in-place, so peak   #
    # RAM = original + scaled briefly, then drops to the scaled size.     #
    # ------------------------------------------------------------------ #
    if tex_scale < 1.0:
        seen_imgs: set[str] = set()
        n_scaled = 0
        for obj in bpy.data.objects:
            if not obj.name.startswith("Terrain_"):
                continue
            for ms in obj.material_slots:
                mat = ms.material
                if not mat or not mat.use_nodes:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type != 'TEX_IMAGE' or not node.image:
                        continue
                    img = node.image
                    if img.name in seen_imgs:
                        continue
                    seen_imgs.add(img.name)
                    orig_w, orig_h = img.size
                    if orig_w < 2 or orig_h < 2:
                        continue
                    new_w = max(1, int(orig_w * tex_scale))
                    new_h = max(1, int(orig_h * tex_scale))
                    img.scale(new_w, new_h)
                    n_scaled += 1
                    print(f"[georeel] Texture downscaled: '{img.name}' "
                          f"{orig_w}×{orig_h} → {new_w}×{new_h}")
        if n_scaled:
            print(f"[georeel] Downscaled {n_scaled} terrain texture(s) "
                  f"(scale={tex_scale:.2f})")

    scene = bpy.context.scene

    # ------------------------------------------------------------------ #
    # Render engine                                                        #
    # ------------------------------------------------------------------ #

    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        # Use GPU if available; fall back to CPU silently
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.get_devices()
            if any(d.type in ("OPTIX", "HIP", "METAL", "ONEAPI")
                   for d in prefs.devices):
                prefs.compute_device_type = next(
                    d.type for d in prefs.devices
                    if d.type in ("OPTIX", "HIP", "METAL", "ONEAPI")
                )
                scene.cycles.device = "GPU"
        except Exception:
            pass  # GPU not available; CPU rendering continues
    elif engine == "viewport":
        # Draft renderer: EEVEE at 4 samples with shadows and AO disabled.
        # Equivalent to Blender's real-time viewport "Rendered" shading mode —
        # an order of magnitude faster than even EEVEE "low" quality.
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
            scene.eevee.taa_render_samples = 4
        except AttributeError:
            scene.render.engine = "BLENDER_EEVEE"
            scene.eevee.taa_render_samples = 4
        scene.eevee.use_shadows = False
        if hasattr(scene.eevee, "use_gtao"):
            scene.eevee.use_gtao = False
    else:
        # EEVEE Next (Blender 4.2+); fall back to legacy name
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
            scene.eevee.taa_render_samples = samples
        except AttributeError:
            scene.render.engine = "BLENDER_EEVEE"
            scene.eevee.taa_render_samples = samples

    # ------------------------------------------------------------------ #
    # Output settings                                                      #
    # ------------------------------------------------------------------ #

    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format    = "PNG"
    scene.render.image_settings.color_mode     = "RGB"
    # Map zlib level 0–9 to Blender's 0–100 compression scale.
    scene.render.image_settings.compression    = round(png_compression / 9 * 100)

    # ------------------------------------------------------------------ #
    # Camera                                                               #
    # Reuse the existing FlyCamera placeholder created by build_scene.py  #
    # so that LOCKED_TRACK constraints on pins and the marker keep their   #
    # target reference pointing at the actual render camera.               #
    # ------------------------------------------------------------------ #

    cam_obj = bpy.data.objects.get("FlyCamera")
    if cam_obj is None:
        cam_data = bpy.data.cameras.new("FlyCamera")
        cam_obj = bpy.data.objects.new("FlyCamera", cam_data)
        scene.collection.objects.link(cam_obj)
    cam_obj.data.lens       = 35
    cam_obj.data.clip_start = 1.0
    cam_obj.data.clip_end   = 100_000.0
    scene.camera = cam_obj

    # ------------------------------------------------------------------ #
    # Intro overview: shift existing animation and create static ribbon    #
    # ------------------------------------------------------------------ #
    n_intro     = _setup_intro_overview(scene, keyframes_data, intro_track_lift)
    n_static    = _detect_n_static(keyframes_data, n_intro)
    n_clearance = _detect_n_clearance(keyframes_data, n_intro)

    # ------------------------------------------------------------------ #
    # Insert subsampled camera animation keyframes                         #
    #                                                                     #
    # We use 1-based frame numbers (idx + 1) so the camera animation      #
    # aligns with the ribbon Build modifier and waypoint marker            #
    # animations that scene_builder.py inserts at 1-indexed frames.       #
    # Output filenames (000001.png … {N:06d}.png) are matched by the      #
    # compositor using frame_num directly (no -1 adjustment needed).      #
    #                                                                     #
    # Intro: 3 sparse control points (hold start, descent start, end).    #
    # Stride=10 → ~10× fewer keyframe_insert calls vs per-frame.          #
    # Blender uses LINEAR interpolation between subsampled keyframes;     #
    # since the camera path is already smooth this is visually exact.     #
    # Pause segments (is_pause=True) use CONSTANT interpolation so the    #
    # camera holds precisely at photo waypoint positions.                 #
    # ------------------------------------------------------------------ #

    _STRIDE = 10
    indices = _select_keyframe_indices(keyframes_data, _STRIDE, n_intro, n_static, n_clearance)

    # Map from Blender frame (1-indexed) → interpolation type
    frame_interp: dict[int, str] = {}

    cam_obj.rotation_mode = "QUATERNION"
    prev_quat = None
    for idx in indices:
        kf      = keyframes_data[idx]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix, Quaternion)

        # Keep all quaternions in the same hemisphere so Blender's component-wise
        # interpolation always takes the short arc.
        if prev_quat is not None and rot_quat.dot(prev_quat) < 0:
            rot_quat = -rot_quat
        prev_quat = rot_quat

        cam_obj.location            = pos
        cam_obj.rotation_quaternion = rot_quat

        blender_frame = idx + 1
        cam_obj.keyframe_insert(data_path="location",            frame=blender_frame)
        cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=blender_frame)

        frame_interp[blender_frame] = 'CONSTANT' if kf.get("is_pause", False) else 'LINEAR'

    print(f"[georeel] Inserted {len(indices)} keyframes "
          f"(stride={_STRIDE}, total={total})")

    # LINEAR for smooth motion; CONSTANT for pause segments; CONSTANT
    # extrapolation beyond first/last keyframe keeps camera fixed.
    if cam_obj.animation_data and cam_obj.animation_data.action:
        for fcurve in cam_obj.animation_data.action.fcurves:
            fcurve.extrapolation = "CONSTANT"
            for kp in fcurve.keyframe_points:
                kp.interpolation = frame_interp.get(round(kp.co.x), 'LINEAR')

    # Apply SINE EASE_IN_OUT to the descent-start keyframe so the camera
    # smoothly accelerates away from the hold and decelerates into the
    # flythrough start position.  blender_frame = idx + 1, so the descent-
    # start frame (index n_static-1) maps to Blender frame n_static.
    if n_intro > 0 and 0 < n_static < n_intro and cam_obj.animation_data and cam_obj.animation_data.action:
        descent_start_frame = n_static   # index (n_static-1) + 1
        for fc in cam_obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                if round(kp.co.x) == descent_start_frame:
                    kp.interpolation = 'SINE'
                    kp.easing        = 'EASE_IN_OUT'
            fc.update()

    # ------------------------------------------------------------------ #
    # Sync ribbon and marker to camera arc-length                          #
    # ------------------------------------------------------------------ #
    _retime_ribbon_and_marker(scene, keyframes_data)

    # ------------------------------------------------------------------ #
    # Render the full animation in a single pass                           #
    #                                                                     #
    # animation=True keeps the render engine (and GPU) alive across every  #
    # frame, eliminating the per-frame init / teardown that causes the     #
    # GPU idle cycles visible in hardware monitoring tools.                #
    # Blender prints "Fra:N Mem:…" to stdout for each frame; the host     #
    # process parses those lines for progress updates.                     #
    # "######" in the filepath → 6-digit zero-padded frame number.        #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Locality banner objects (3D banner mode only)                       #
    # ------------------------------------------------------------------ #
    if banners_path:
        _inject_locality_banners(banners_path, cam_obj, scene)

    scene.frame_start = frame_start_arg if frame_start_arg is not None else 1
    scene.frame_end   = frame_end_arg   if frame_end_arg   is not None else total
    scene.render.filepath = f"{output_dir}/######"

    n_frames = scene.frame_end - scene.frame_start + 1

    # ------------------------------------------------------------------ #
    # Background compression: connect to the host compression server so   #
    # it can re-compress each PNG after Blender writes it.  Blender       #
    # always writes at compression=0 in this mode; the host thread pool   #
    # does the actual zlib work while the GPU renders the next frame.     #
    # ------------------------------------------------------------------ #
    _comp_sock: socket.socket | None = None
    if compression_port:
        try:
            _comp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _comp_sock.connect(("127.0.0.1", compression_port))

            def _on_render_post(scene, depsgraph):  # noqa: ANN001
                if _comp_sock:
                    path = f"{output_dir}/{scene.frame_current:06d}.png\n"
                    try:
                        _comp_sock.sendall(path.encode())
                    except OSError:
                        pass

            bpy.app.handlers.render_post.append(_on_render_post)
        except OSError as exc:
            print(f"[georeel] Could not connect to compression server: {exc}",
                  file=sys.stderr)
            _comp_sock = None

    bpy.ops.render.render(animation=True)

    if _comp_sock:
        bpy.app.handlers.render_post.remove(_on_render_post)
        _comp_sock.close()

    print(f"[georeel] Rendered {n_frames} frames "
          f"({scene.frame_start}–{scene.frame_end}) to {output_dir}")


main()
