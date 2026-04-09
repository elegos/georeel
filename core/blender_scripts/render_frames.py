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

    with open(keyframes_path) as f:
        keyframes_data = json.load(f)

    if not keyframes_data:
        print("[georeel] No keyframes — nothing to render.", file=sys.stderr)
        sys.exit(1)

    total = len(keyframes_data)
    width, height = _RESOLUTIONS.get(resolution, (1920, 1080))
    samples = _SAMPLES.get(engine, _SAMPLES["eevee"]).get(quality, 64)

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
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"

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
    # Render frame by frame                                                #
    # ------------------------------------------------------------------ #

    for seq_idx, kf in enumerate(keyframes_data):
        frame   = kf["frame"]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix, Quaternion)

        cam_obj.location       = pos
        cam_obj.rotation_mode  = "QUATERNION"
        cam_obj.rotation_quaternion = rot_quat

        scene.frame_set(frame)
        scene.render.filepath = f"{output_dir}/{seq_idx:06d}"
        bpy.ops.render.render(write_still=True)

        # Report sequential index (not timeline frame number) so the host
        # progress bar stays in [0, total-1].
        print(f"Fra:{seq_idx}/{total - 1}", flush=True)

    print(f"[georeel] Rendered {total} frames to {output_dir}")


main()
