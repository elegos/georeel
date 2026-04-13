"""
Blender script: render a single top-down preview frame from an existing .blend.

Invoked headlessly by preview_map.py:
    blender --background scene.blend --python render_preview.py -- output.png [width height]

The script:
  1. Removes the Build (Unfold) modifier from the Track mesh so the full
     ribbon is visible.
  2. Replaces any existing camera with a top-down orthographic camera that
     is auto-fitted to the terrain bounding box.
  3. Renders a single frame (frame 1) to the requested output path.
"""

import math
import sys


def main() -> None:
    import bpy

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if not argv:
        print("Usage: render_preview.py -- output.png [width height]", file=sys.stderr)
        sys.exit(1)

    output_path = argv[0]
    width  = int(argv[1]) if len(argv) > 1 else 1920
    height = int(argv[2]) if len(argv) > 2 else 1080

    # ------------------------------------------------------------------ #
    # Remove Build modifier so ribbon is shown in full                     #
    # ------------------------------------------------------------------ #
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.name.startswith("Track"):
            for mod in list(obj.modifiers):
                if mod.type == 'BUILD':
                    obj.modifiers.remove(mod)

    # ------------------------------------------------------------------ #
    # Compute scene bbox from Terrain mesh vertices                        #
    # (needed before pin placement so we know z_max)                      #
    # ------------------------------------------------------------------ #
    terrain_tiles = [o for o in bpy.data.objects
                     if o.type == 'MESH' and o.name.startswith("Terrain")]
    if not terrain_tiles:
        print("[georeel preview] No Terrain object found; aborting.", file=sys.stderr)
        sys.exit(1)

    xs, ys, zs = [], [], []
    for tile in terrain_tiles:
        xs.extend(v.co.x for v in tile.data.vertices)
        ys.extend(v.co.y for v in tile.data.vertices)
        zs.extend(v.co.z for v in tile.data.vertices)

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    z_max        = max(zs)

    # ------------------------------------------------------------------ #
    # Flatten billboard pins for top-down view                             #
    #                                                                      #
    # Pin discs (Pin_, PinOutline_, TrackMarker) live in local XZ plane   #
    # (Y=0).  Rx(+π/2) maps local Y → world Z and local Z → world -Y, so #
    # the disc becomes horizontal, visible from above.                    #
    #                                                                      #
    # Photo faces (PinPhoto_) are quads whose front normal points in      #
    # local -Y (toward the flying camera).  Rx(-π/2) maps local -Y →     #
    # world +Z, so the photo faces upward and is visible from above. The  #
    # face also ends up *above* the disc (positive Z offset).             #
    #                                                                      #
    # All pins are then lifted to z_max + margin so steep terrain cannot  #
    # clip through them in the top-down render.                           #
    # ------------------------------------------------------------------ #
    import math as _math
    _PIN_DISC_PREFIXES  = ("Pin_", "PinOutline_", "TrackMarker")
    _PIN_PHOTO_PREFIXES = ("PinPhoto_",)
    pin_z = z_max + 50.0   # float all pins well above the highest terrain point

    for obj in bpy.data.objects:
        is_disc  = any(obj.name.startswith(p) for p in _PIN_DISC_PREFIXES)
        is_photo = any(obj.name.startswith(p) for p in _PIN_PHOTO_PREFIXES)
        if not (is_disc or is_photo):
            continue
        for con in list(obj.constraints):
            obj.constraints.remove(con)
        if is_photo:
            obj.rotation_euler = (-_math.pi / 2, 0.0, 0.0)
        else:
            obj.rotation_euler = (_math.pi / 2, 0.0, 0.0)
        obj.location.z = pin_z

    cx = (x_min + x_max) / 2
    cy = (y_min + y_max) / 2
    scene_w = x_max - x_min
    scene_h = y_max - y_min

    # ------------------------------------------------------------------ #
    # Build a top-down orthographic camera                                 #
    # ------------------------------------------------------------------ #
    # Remove all existing cameras
    for obj in list(bpy.data.objects):
        if obj.type == 'CAMERA':
            bpy.data.objects.remove(obj, do_unlink=True)

    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.type = 'ORTHO'

    # ortho_scale = length of the longer scene axis + 5 % margin
    aspect = width / height
    scene_aspect = scene_w / max(scene_h, 1e-6)
    if scene_aspect >= aspect:
        # scene is wider than the render — fit width
        cam_data.ortho_scale = scene_w * 1.05
    else:
        # scene is taller — fit height, convert to ortho_scale (width-based)
        cam_data.ortho_scale = scene_h * 1.05 * aspect

    cam_obj = bpy.data.objects.new("PreviewCam", cam_data)
    # Place camera high above the centre, looking straight down.
    # Keep a generous margin so the terrain is well within clip range.
    cam_height = z_max + max(scene_w, scene_h) * 2.0
    cam_data.clip_start = 1.0
    cam_data.clip_end   = cam_height + abs(z_max) + 1000.0
    cam_obj.location = (cx, cy, cam_height)
    # At rotation (0,0,0) the camera's local -Z points in world -Z (straight down).
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)

    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    # ------------------------------------------------------------------ #
    # Unpack any packed images to disk so EEVEE can read them.           #
    # External (FILE-source) images are loaded automatically from their  #
    # filepath; only packed images need the extra unpack step.           #
    # ------------------------------------------------------------------ #
    bpy.ops.file.unpack_all(method='WRITE_LOCAL')

    # ------------------------------------------------------------------ #
    # Convert every material to flat Emission so nothing depends on       #
    # lighting.  Strategy:                                                 #
    #   • If surface output is already Emission → leave it alone.         #
    #   • If there is a TEX_IMAGE node with an image → wire it to a new   #
    #     Emission node.                                                   #
    # Vertex-color and solid-color Emission materials are already correct. #
    # ------------------------------------------------------------------ #
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        out_node = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if not out_node:
            continue

        surface_links = [l for l in links
                         if l.to_node == out_node and l.to_socket.name == 'Surface']
        if surface_links and surface_links[0].from_node.type == 'EMISSION':
            continue  # already emission — ribbon, pins, marker are fine

        tex_node = next((n for n in nodes
                         if n.type == 'TEX_IMAGE' and n.image is not None), None)
        if tex_node is None:
            continue  # no texture to show; leave as-is

        # Remove all links going into the output node, add a fresh Emission
        for lnk in [l for l in links if l.to_node == out_node]:
            links.remove(lnk)
        emit = nodes.new("ShaderNodeEmission")
        emit.inputs["Strength"].default_value = 1.0
        links.new(tex_node.outputs["Color"], emit.inputs["Color"])
        links.new(emit.outputs["Emission"],  out_node.inputs["Surface"])

    # ------------------------------------------------------------------ #
    # Remove lights; black world background.                              #
    # ------------------------------------------------------------------ #
    for obj in list(bpy.data.objects):
        if obj.type == 'LIGHT':
            bpy.data.objects.remove(obj, do_unlink=True)

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    world.use_nodes = True
    bpy.context.scene.world = world
    wnodes = world.node_tree.nodes
    wnodes.clear()
    bg = wnodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value    = (0.0, 0.0, 0.0, 1.0)
    bg.inputs["Strength"].default_value = 0.0
    wout = wnodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(bg.outputs["Background"], wout.inputs["Surface"])

    # ------------------------------------------------------------------ #
    # Render settings                                                      #
    # ------------------------------------------------------------------ #
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'

    # Fast single-sample, no shadows
    scene.eevee.taa_render_samples   = 1
    scene.eevee.use_shadows          = False
    if hasattr(scene.eevee, 'use_gtao'):
        scene.eevee.use_gtao         = False

    scene.render.resolution_x            = width
    scene.render.resolution_y            = height
    scene.render.resolution_percentage   = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath                = output_path
    scene.frame_set(1)

    bpy.ops.render.render(write_still=True)
    print(f"[georeel] Preview map saved: {output_path}")


main()
