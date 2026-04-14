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
import math
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


def _select_keyframe_indices(keyframes_data: list, stride: int) -> list[int]:
    """Return sorted indices into keyframes_data to use as Blender keyframes.

    Always includes first/last frame and pause-segment boundaries so that
    CONSTANT interpolation can be applied precisely at photo waypoints.
    """
    n = len(keyframes_data)
    selected: set[int] = set()
    selected.add(0)
    selected.add(n - 1)
    for i in range(0, n, stride):
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
    tile_filter_str  = (argv[7] or None) if len(argv) > 7 else None  # "" → None
    tex_scale        = float(argv[8]) if len(argv) > 8 else 1.0
    png_compression  = int(argv[9])   if len(argv) > 9 else 6       # zlib level 0–9

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
    # Insert subsampled camera animation keyframes                         #
    #                                                                     #
    # We use 0-based frame numbers (seq_idx) so output filenames match    #
    # the 000000.png … {N-1:06d}.png pattern the compositor expects.      #
    #                                                                     #
    # Stride=10 → ~10× fewer keyframe_insert calls vs per-frame.         #
    # Blender uses LINEAR interpolation between subsampled keyframes;     #
    # since the camera path is already smooth this is visually exact.     #
    # Pause segments (is_pause=True) use CONSTANT interpolation so the    #
    # camera holds precisely at photo waypoint positions.                 #
    # ------------------------------------------------------------------ #

    _STRIDE = 10
    indices = _select_keyframe_indices(keyframes_data, _STRIDE)

    # Map from Blender frame (seq_idx) → interpolation type
    frame_interp: dict[int, str] = {}

    cam_obj.rotation_mode = "QUATERNION"
    for idx in indices:
        kf      = keyframes_data[idx]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix, Quaternion)

        cam_obj.location            = pos
        cam_obj.rotation_quaternion = rot_quat

        cam_obj.keyframe_insert(data_path="location",            frame=idx)
        cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=idx)

        frame_interp[idx] = 'CONSTANT' if kf.get("is_pause", False) else 'LINEAR'

    print(f"[georeel] Inserted {len(indices)} keyframes "
          f"(stride={_STRIDE}, total={total})")

    # LINEAR for smooth motion; CONSTANT for pause segments; CONSTANT
    # extrapolation beyond first/last keyframe keeps camera fixed.
    if cam_obj.animation_data and cam_obj.animation_data.action:
        for fcurve in cam_obj.animation_data.action.fcurves:
            fcurve.extrapolation = "CONSTANT"
            for kp in fcurve.keyframe_points:
                kp.interpolation = frame_interp.get(round(kp.co.x), 'LINEAR')

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

    scene.frame_start = frame_start_arg if frame_start_arg is not None else 0
    scene.frame_end   = frame_end_arg   if frame_end_arg   is not None else total - 1
    scene.render.filepath = f"{output_dir}/######"

    n_frames = scene.frame_end - scene.frame_start + 1

    bpy.ops.render.render(animation=True)

    print(f"[georeel] Rendered {n_frames} frames "
          f"({scene.frame_start}–{scene.frame_end}) to {output_dir}")


main()
