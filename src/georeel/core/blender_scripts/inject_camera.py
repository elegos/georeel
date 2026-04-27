"""
Blender script: injects a fly-through camera (with all keyframes) into an
existing .blend scene and saves it to a new path.

Invoked headlessly by open_in_blender.py:
    blender --background scene.blend --python inject_camera.py \
        -- keyframes.json output.blend

The resulting .blend can be opened interactively so the user can inspect the
camera path, scrub the timeline, and see the ribbon unfold.
"""

import json
import sys


def _zero_roll_quat(pos, look_at, Vector, Matrix):
    """Return a quaternion pointing camera -Z toward look_at with zero roll."""
    world_z = Vector((0.0, 0.0, 1.0))

    fwd = look_at - pos
    if fwd.length > 1e-6:
        fwd.normalize()
    else:
        fwd = Vector((0.0, 1.0, 0.0))

    if abs(fwd.dot(world_z)) > 0.9999:
        right = Vector((1.0, 0.0, 0.0))
    else:
        right = fwd.cross(world_z)
        right.normalize()

    up = right.cross(fwd)
    up.normalize()

    mat = Matrix((
        ( right.x,  right.y,  right.z),
        (    up.x,     up.y,     up.z),
        (  -fwd.x,   -fwd.y,   -fwd.z),
    )).transposed()

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


def _setup_intro_overview(scene, keyframes_data: list) -> int:
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
        for col in track_obj.users_collection:
            col.objects.link(ov_obj)
        ov_obj.matrix_world = track_obj.matrix_world.copy()

        # Remove Build modifier — full ribbon must be always visible
        for mod in list(ov_obj.modifiers):
            if mod.type == "BUILD":
                ov_obj.modifiers.remove(mod)

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
        emit.inputs["Strength"].default_value = 2.0
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


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 2:
        print("Usage: inject_camera.py -- keyframes.json output.blend [resolution] [fps]",
              file=sys.stderr)
        sys.exit(1)

    keyframes_path, output_path = argv[0], argv[1]
    resolution = argv[2] if len(argv) > 2 else "1080p"
    fps        = int(argv[3]) if len(argv) > 3 else 30  # fallback = DEFAULTS[KEY_FPS]

    _RESOLUTIONS = {
        "720p":  (1280,  720), "1080p": (1920, 1080),
        "1440p": (2560, 1440), "4k":    (3840, 2160),
        "portrait_720p":  ( 720, 1280), "portrait_1080p": (1080, 1920),
        "portrait_1440p": (1440, 2560), "portrait_4k":    (2160, 3840),
        "square_720":  ( 720,  720), "square_1080": (1080, 1080),
        "square_1440": (1440, 1440), "square_2160": (2160, 2160),
    }
    render_w, render_h = _RESOLUTIONS.get(resolution, (1920, 1080))

    with open(keyframes_path) as f:
        keyframes_data = json.load(f)

    if not keyframes_data:
        print("[georeel] No keyframes — nothing to inject.", file=sys.stderr)
        sys.exit(1)

    scene = bpy.context.scene

    # ------------------------------------------------------------------ #
    # Find or create FlyCamera                                            #
    # Reuse the existing object (created as a placeholder by             #
    # build_scene.py) so that LOCKED_TRACK constraints on the marker and  #
    # pins keep their target reference.  Only fall back to creating a new  #
    # object if the placeholder is absent.                                #
    # ------------------------------------------------------------------ #
    for obj in list(bpy.data.objects):
        if obj.type == 'CAMERA' and obj.name == "PreviewCam":
            bpy.data.objects.remove(obj, do_unlink=True)

    cam_obj = bpy.data.objects.get("FlyCamera")
    if cam_obj is None:
        cam_data = bpy.data.cameras.new("FlyCamera")
        cam_obj = bpy.data.objects.new("FlyCamera", cam_data)
        scene.collection.objects.link(cam_obj)
    else:
        # Clear any previous keyframes so we can re-bake from scratch
        if cam_obj.animation_data:
            cam_obj.animation_data_clear()

    cam_obj.data.lens       = 35
    cam_obj.data.clip_start = 1.0
    cam_obj.data.clip_end   = 100_000.0
    scene.camera = cam_obj
    cam_obj.rotation_mode = "QUATERNION"

    # ------------------------------------------------------------------ #
    # Intro overview: shift existing animation and create static ribbon    #
    # ------------------------------------------------------------------ #
    n_intro     = _setup_intro_overview(scene, keyframes_data)
    n_static    = _detect_n_static(keyframes_data, n_intro)
    n_clearance = _detect_n_clearance(keyframes_data, n_intro)

    # ------------------------------------------------------------------ #
    # Insert subsampled keyframes; Blender interpolates between them.     #
    #                                                                     #
    # Intro: sparse control points (hold start, descent start, descent    #
    # end, clearance end).  SINE EASE_IN_OUT on descent-start produces a  #
    # smooth arrival at descent-end; camera then holds during clearance.  #
    #                                                                     #
    # Flythrough: stride = fps → 1 keyframe per second, LINEAR.           #
    # Pause segments: CONSTANT so the camera holds at the waypoint.       #
    # ------------------------------------------------------------------ #
    first_frame = keyframes_data[0]["frame"]
    last_frame  = keyframes_data[-1]["frame"]

    scene.frame_start = first_frame
    scene.frame_end   = last_frame

    stride  = max(1, fps)
    indices = _select_keyframe_indices(keyframes_data, stride, n_intro, n_static, n_clearance)

    # Map from Blender frame number → interpolation type
    frame_interp: dict[int, str] = {}

    prev_quat = None
    for idx in indices:
        kf      = keyframes_data[idx]
        frame   = kf["frame"]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix)

        # Keep all quaternions in the same hemisphere so Blender's component-wise
        # interpolation always takes the short arc.  Without this, a dot < 0
        # between adjacent keyframe quaternions causes a visible mid-descent flip.
        if prev_quat is not None and rot_quat.dot(prev_quat) < 0:
            rot_quat = -rot_quat
        prev_quat = rot_quat

        cam_obj.location            = pos
        cam_obj.rotation_quaternion = rot_quat
        cam_obj.keyframe_insert(data_path="location",            frame=frame)
        cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)

        frame_interp[frame] = 'CONSTANT' if kf.get("is_pause", False) else 'LINEAR'

    print(f"[georeel] Injected {len(indices)} keyframes "
          f"(stride={stride}, total={len(keyframes_data)})")

    # Apply per-keyframe interpolation (CONSTANT for pauses, LINEAR for motion)
    if cam_obj.animation_data and cam_obj.animation_data.action:
        for fc in cam_obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = frame_interp.get(round(kp.co.x), 'LINEAR')

    # Apply SINE EASE_IN_OUT to the descent-start keyframe.
    #
    # This uses Blender's built-in sinusoidal easing, which mathematically
    # guarantees zero velocity at both ends of the segment:
    #   f(t) = 0.5 * (1 - cos(π·t))  →  f'(0) = 0,  f'(1) = 0
    #
    # The easing applies to the single segment from descent-start → descent-end
    # (both position and rotation f-curves share the same parameter), producing
    # true ease-in-ease-out without any handle arithmetic.
    if n_intro > 0 and 0 < n_static < n_intro and cam_obj.animation_data and cam_obj.animation_data.action:
        descent_start_frame = keyframes_data[n_static - 1]["frame"]
        for fc in cam_obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                if round(kp.co.x) == descent_start_frame:
                    kp.interpolation = 'SINE'
                    kp.easing        = 'EASE_IN_OUT'
            fc.update()

    # ------------------------------------------------------------------ #
    # Apply render resolution so the camera aspect ratio is correct       #
    # ------------------------------------------------------------------ #
    scene.render.resolution_x          = render_w
    scene.render.resolution_y          = render_h
    scene.render.resolution_percentage = 100

    # ------------------------------------------------------------------ #
    # Position timeline at first frame and save                           #
    # ------------------------------------------------------------------ #
    scene.frame_set(first_frame)
    bpy.ops.wm.save_as_mainfile(filepath=output_path)
    print(f"[georeel] Scene with camera saved: {output_path}")


main()
