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


def _select_keyframe_indices(keyframes_data: list, stride: int) -> list[int]:
    """Return sorted indices into keyframes_data to use as Blender keyframes.

    Rules:
    - Always include first and last frame.
    - Include every `stride`-th frame for smooth motion.
    - Include the first frame of every pause segment (is_pause transitions
      False→True) and the first frame after each pause (True→False), so
      CONSTANT interpolation can be applied precisely at pause boundaries.
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
    from mathutils import Matrix, Vector

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 2:
        print("Usage: inject_camera.py -- keyframes.json output.blend [resolution] [fps]",
              file=sys.stderr)
        sys.exit(1)

    keyframes_path, output_path = argv[0], argv[1]
    resolution = argv[2] if len(argv) > 2 else "1080p"
    fps        = int(argv[3]) if len(argv) > 3 else 30

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
    # Insert subsampled keyframes; Blender interpolates between them.     #
    #                                                                     #
    # Stride = fps → 1 keyframe per second.  This reduces keyframe count  #
    # by ~fps× compared to inserting every frame, while LINEAR            #
    # interpolation between the pre-smoothed camera positions is          #
    # visually indistinguishable from per-frame injection.                #
    # Pause segments (is_pause=True) use CONSTANT interpolation so the    #
    # camera holds exactly at the photo waypoint position.                #
    # ------------------------------------------------------------------ #
    first_frame = keyframes_data[0]["frame"]
    last_frame  = keyframes_data[-1]["frame"]

    scene.frame_start = first_frame
    scene.frame_end   = last_frame

    stride  = max(1, fps)
    indices = _select_keyframe_indices(keyframes_data, stride)

    # Map from Blender frame number → interpolation type
    frame_interp: dict[int, str] = {}

    for idx in indices:
        kf      = keyframes_data[idx]
        frame   = kf["frame"]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix)

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
