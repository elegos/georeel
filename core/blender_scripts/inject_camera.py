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


def main() -> None:
    import bpy
    from mathutils import Matrix, Vector

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 2:
        print("Usage: inject_camera.py -- keyframes.json output.blend",
              file=sys.stderr)
        sys.exit(1)

    keyframes_path, output_path = argv[0], argv[1]

    with open(keyframes_path) as f:
        keyframes_data = json.load(f)

    if not keyframes_data:
        print("[georeel] No keyframes — nothing to inject.", file=sys.stderr)
        sys.exit(1)

    scene = bpy.context.scene

    # ------------------------------------------------------------------ #
    # Remove any existing preview camera                                  #
    # ------------------------------------------------------------------ #
    for obj in list(bpy.data.objects):
        if obj.type == 'CAMERA' and obj.name in ("FlyCamera", "PreviewCam"):
            bpy.data.objects.remove(obj, do_unlink=True)

    # ------------------------------------------------------------------ #
    # Create camera                                                       #
    # ------------------------------------------------------------------ #
    cam_data = bpy.data.cameras.new("FlyCamera")
    cam_data.lens       = 35
    cam_data.clip_start = 1.0
    cam_data.clip_end   = 100_000.0

    cam_obj = bpy.data.objects.new("FlyCamera", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    cam_obj.rotation_mode = "QUATERNION"

    # ------------------------------------------------------------------ #
    # Insert a location + rotation keyframe for every camera keyframe     #
    # ------------------------------------------------------------------ #
    first_frame = keyframes_data[0]["frame"]
    last_frame  = keyframes_data[-1]["frame"]

    scene.frame_start = first_frame
    scene.frame_end   = last_frame

    for kf in keyframes_data:
        frame   = kf["frame"]
        pos     = Vector((kf["x"],        kf["y"],        kf["z"]))
        look_at = Vector((kf["look_at_x"], kf["look_at_y"], kf["look_at_z"]))

        rot_quat = _zero_roll_quat(pos, look_at, Vector, Matrix)

        cam_obj.location            = pos
        cam_obj.rotation_quaternion = rot_quat
        cam_obj.keyframe_insert(data_path="location",            frame=frame)
        cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)

    # Set all keyframes to LINEAR interpolation for smooth motion
    if cam_obj.animation_data and cam_obj.animation_data.action:
        for fc in cam_obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'

    # ------------------------------------------------------------------ #
    # Position timeline at first frame and save                           #
    # ------------------------------------------------------------------ #
    scene.frame_set(first_frame)
    bpy.ops.wm.save_as_mainfile(filepath=output_path)
    print(f"[georeel] Scene with camera saved: {output_path}")


main()
