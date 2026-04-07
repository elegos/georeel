"""
Blender script: builds a 3D terrain scene and saves it as a .blend file.

Invoked headlessly by scene_builder.py:
    blender --background --python build_scene.py -- <meta.json> <data.bin> <texture.png> <output.blend>

Row 0 of the elevation grid is the northernmost row (max_lat).
"""

import json
import math
import struct
import sys


def main() -> None:
    import bpy

    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 4:
        print("Usage: build_scene.py -- meta.json data.bin texture.png output.blend",
              file=sys.stderr)
        sys.exit(1)

    if len(argv) < 7:
        print("Usage: build_scene.py -- meta.json data.bin texture.png output.blend "
              "track.json pins.json pin_color [sun_x sun_y sun_z]",
              file=sys.stderr)
        sys.exit(1)

    meta_path, data_path, texture_path, output_path, track_path, pins_path, pin_color = argv[:7]

    sun_vec = None
    if len(argv) >= 10:
        try:
            sun_vec = (float(argv[7]), float(argv[8]), float(argv[9]))
        except ValueError:
            pass

    # ------------------------------------------------------------------ #
    # Load DEM                                                             #
    # ------------------------------------------------------------------ #

    with open(meta_path) as f:
        meta = json.load(f)

    rows     = meta["rows"]
    cols     = meta["cols"]
    min_lat  = meta["min_lat"]
    max_lat  = meta["max_lat"]
    min_lon  = meta["min_lon"]
    max_lon  = meta["max_lon"]

    with open(data_path, "rb") as f:
        raw = f.read()

    # float32, row-major; row 0 = max_lat (north)
    elev = struct.unpack(f"{rows * cols}f", raw)

    # Replace NaN / extreme values (ocean / missing SRTM tiles) with 0
    elev = [0.0 if (not math.isfinite(v) or v < -500 or v > 9000) else v
            for v in elev]

    # ------------------------------------------------------------------ #
    # Compute physical dimensions (metres)                                #
    # ------------------------------------------------------------------ #

    mean_lat_rad = math.radians((min_lat + max_lat) / 2)
    lat_m = (max_lat - min_lat) * 111_320.0
    lon_m = (max_lon - min_lon) * 111_320.0 * math.cos(mean_lat_rad)

    # ------------------------------------------------------------------ #
    # Build vertex list                                                    #
    # ------------------------------------------------------------------ #
    # Y axis → north (row 0 = max_lat → Y = lat_m; last row = min_lat → Y = 0)
    # X axis → east  (col 0 = min_lon → X = 0;     last col = max_lon → X = lon_m)
    # Z axis → elevation (metres)

    verts = []
    for r in range(rows):
        y = (1.0 - r / (rows - 1)) * lat_m
        for c in range(cols):
            x = (c / (cols - 1)) * lon_m
            z = elev[r * cols + c]
            verts.append((x, y, z))

    # ------------------------------------------------------------------ #
    # Build quad faces                                                     #
    # ------------------------------------------------------------------ #

    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            v0 = r * cols + c
            v1 = r * cols + (c + 1)
            v2 = (r + 1) * cols + (c + 1)
            v3 = (r + 1) * cols + c
            faces.append((v0, v1, v2, v3))

    # ------------------------------------------------------------------ #
    # Start a clean Blender scene                                          #
    # ------------------------------------------------------------------ #

    bpy.ops.wm.read_factory_settings(use_empty=True)

    # ------------------------------------------------------------------ #
    # Create terrain mesh                                                  #
    # ------------------------------------------------------------------ #

    mesh = bpy.data.meshes.new("Terrain")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # UV layer: U = col / (cols-1), V = 1 - row / (rows-1)
    # V=1 = north (row 0), V=0 = south (last row) — matches standard map orientation
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            vi = mesh.loops[loop_idx].vertex_index
            r = vi // cols
            c = vi % cols
            uv_layer.data[loop_idx].uv = (
                c / (cols - 1),
                1.0 - r / (rows - 1),
            )

    obj = bpy.data.objects.new("Terrain", mesh)
    bpy.context.scene.collection.objects.link(obj)

    # ------------------------------------------------------------------ #
    # Satellite texture material                                           #
    # ------------------------------------------------------------------ #

    mat = bpy.data.materials.new("Satellite")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(texture_path)
    tex_node.location = (-300, 0)

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)

    if sun_vec is not None:
        # Sun position known: use Principled BSDF so the terrain receives
        # directional shading, revealing topographic relief.
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        bsdf.inputs["Roughness"].default_value = 1.0
        bsdf.inputs["Metallic"].default_value = 0.0
        if "Specular" in bsdf.inputs:
            bsdf.inputs["Specular"].default_value = 0.0
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    else:
        # No timestamp available: flat emission so the terrain is always visible.
        emit_node = nodes.new("ShaderNodeEmission")
        emit_node.inputs["Strength"].default_value = 1.0
        emit_node.location = (0, 0)
        links.new(tex_node.outputs["Color"], emit_node.inputs["Color"])
        links.new(emit_node.outputs["Emission"], out_node.inputs["Surface"])

    mesh.materials.append(mat)

    # ------------------------------------------------------------------ #
    # Sun lamp + sky background (only when sun position is known)         #
    # ------------------------------------------------------------------ #

    if sun_vec is not None:
        import mathutils

        sx, sy, sz = sun_vec

        # Sun lamp
        bpy.ops.object.light_add(type='SUN', location=(0, 0, 0))
        sun_obj = bpy.context.object
        sun_obj.data.energy = 5.0
        # Lamp emits along its -Z; point -Z toward the ground (opposite of sun_vec)
        toward_ground = mathutils.Vector((-sx, -sy, -sz)).normalized()
        sun_obj.rotation_euler = toward_ground.to_track_quat('-Z', 'Y').to_euler()

        # Sky background (Nishita physically-based sky)
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
        world.use_nodes = True
        wnodes = world.node_tree.nodes
        wlinks = world.node_tree.links
        wnodes.clear()

        sky_node = wnodes.new("ShaderNodeTexSky")
        sky_node.sky_type = 'NISHITA'
        sky_node.sun_direction = (sx, sy, sz)
        sky_node.location = (-300, 0)

        bg_node = wnodes.new("ShaderNodeBackground")
        bg_node.inputs["Strength"].default_value = 1.0
        bg_node.location = (0, 0)

        world_out = wnodes.new("ShaderNodeOutputWorld")
        world_out.location = (300, 0)

        wlinks.new(sky_node.outputs["Color"], bg_node.inputs["Color"])
        wlinks.new(bg_node.outputs["Background"], world_out.inputs["Surface"])

    # ------------------------------------------------------------------ #
    # GPX path ribbon                                                      #
    # ------------------------------------------------------------------ #

    import os
    if os.path.isfile(track_path):
        with open(track_path) as f:
            track_data = json.load(f)
        if len(track_data) >= 2:
            _build_ribbon(bpy, track_data)

    # ------------------------------------------------------------------ #
    # Photo waypoint pins (billboards)                                     #
    # ------------------------------------------------------------------ #

    if os.path.isfile(pins_path):
        with open(pins_path) as f:
            pins_data = json.load(f)
        if pins_data:
            _build_pins(bpy, pins_data, pin_color)

    # Pack the texture so the .blend is self-contained
    bpy.ops.file.pack_all()

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    bpy.ops.wm.save_as_mainfile(filepath=output_path)
    print(f"[georeel] Scene saved: {output_path} "
          f"({rows}×{cols} vertices, {len(faces)} quads)")


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return r, g, b


def _build_pins(bpy, pins_data: list[dict], pin_color_hex: str,
                pin_height: float = 40.0, pin_width: float = 24.0,
                photo_fraction: float = 0.65,
                border: float = 1.5, z_offset: float = 3.0) -> None:
    """Build one billboard pin per waypoint.

    Each pin is a flat mesh in the XY plane (local space) shaped like a
    Google Maps pin: a rounded rectangle on top with a downward-pointing
    triangle.  The upper portion shows the photo thumbnail; the body uses
    the pin color.  A Locked Track constraint keeps the pin always facing
    the camera.
    """
    import mathutils

    pin_r, pin_g, pin_b = _hex_to_rgb(pin_color_hex)

    # Ensure a camera object exists for the constraint target
    cam_obj = next((o for o in bpy.data.objects if o.type == 'CAMERA'), None)

    for i, pin in enumerate(pins_data):
        base_x = pin["x"]
        base_y = pin["y"]
        base_z = pin["z"] + z_offset

        hw = pin_width / 2.0     # half width
        body_top = pin_height    # top of rounded rect
        body_bot = pin_height * (1.0 - photo_fraction) * 0.5  # bottom of rect body
        tip_z    = 0.0            # tip of triangle at local origin

        # ---------------------------------------------------------------- #
        # Build the pin outline as a flat mesh in the local XZ plane        #
        # (X = horizontal, Z = vertical; Y = 0 faces toward camera)         #
        # Local origin = pin tip (bottom point)                              #
        # ---------------------------------------------------------------- #

        # Triangle: tip → lower-left corner → lower-right corner
        tri_verts = [
            (0.0,   0.0, tip_z),
            (-hw,   0.0, body_bot),
            ( hw,   0.0, body_bot),
        ]
        # Rectangle body (above triangle)
        rect_verts = [
            (-hw,   0.0, body_bot),
            ( hw,   0.0, body_bot),
            ( hw,   0.0, body_top),
            (-hw,   0.0, body_top),
        ]
        all_verts = tri_verts + rect_verts  # indices 0-2 triangle, 3-6 rect

        faces = [
            (0, 1, 2),          # triangle
            (3, 4, 5, 6),       # rectangle body
        ]

        pin_mesh = bpy.data.meshes.new(f"Pin_{i}")
        pin_mesh.from_pydata(all_verts, [], faces)
        pin_mesh.update()

        # ---------------------------------------------------------------- #
        # Material: pin body color (emission)                               #
        # ---------------------------------------------------------------- #
        body_mat = bpy.data.materials.new(f"PinBody_{i}")
        body_mat.use_nodes = True
        bnodes = body_mat.node_tree.nodes
        blinks = body_mat.node_tree.links
        bnodes.clear()

        emit = bnodes.new("ShaderNodeEmission")
        emit.inputs["Color"].default_value = (pin_r, pin_g, pin_b, 1.0)
        emit.inputs["Strength"].default_value = 2.0
        bout = bnodes.new("ShaderNodeOutputMaterial")
        blinks.new(emit.outputs["Emission"], bout.inputs["Surface"])
        pin_mesh.materials.append(body_mat)

        pin_obj = bpy.data.objects.new(f"Pin_{i}", pin_mesh)
        pin_obj.location = (base_x, base_y, base_z)
        bpy.context.scene.collection.objects.link(pin_obj)

        # ---------------------------------------------------------------- #
        # Photo thumbnail plane (upper portion of pin)                      #
        # ---------------------------------------------------------------- #
        photo_path = pin.get("photo_path", "")
        if photo_path and os.path.isfile(photo_path):
            _add_photo_face(bpy, pin_obj, i, photo_path,
                            hw, body_bot, body_top, border)

        # ---------------------------------------------------------------- #
        # Always-face-camera constraint                                      #
        # ---------------------------------------------------------------- #
        con = pin_obj.constraints.new(type='LOCKED_TRACK')
        con.track_axis = 'TRACK_Y'
        con.lock_axis  = 'LOCK_Z'
        if cam_obj:
            con.target = cam_obj

        # ---------------------------------------------------------------- #
        # Thin black border outline (slightly larger, rendered behind)       #
        # ---------------------------------------------------------------- #
        outline_verts = [
            (0.0,          0.001, tip_z - border),
            (-hw - border, 0.001, body_bot - border * 0.5),
            ( hw + border, 0.001, body_bot - border * 0.5),
            (-hw - border, 0.001, body_bot),
            ( hw + border, 0.001, body_bot),
            ( hw + border, 0.001, body_top + border),
            (-hw - border, 0.001, body_top + border),
        ]
        outline_faces = [
            (0, 1, 2),
            (3, 4, 5, 6),
        ]
        out_mesh = bpy.data.meshes.new(f"PinOutline_{i}")
        out_mesh.from_pydata(outline_verts, [], outline_faces)
        out_mesh.update()

        out_mat = bpy.data.materials.new(f"PinOutline_{i}")
        out_mat.use_nodes = True
        onodes = out_mat.node_tree.nodes
        olinks = out_mat.node_tree.links
        onodes.clear()
        oemit = onodes.new("ShaderNodeEmission")
        oemit.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)
        oemit.inputs["Strength"].default_value = 2.0
        oout = onodes.new("ShaderNodeOutputMaterial")
        olinks.new(oemit.outputs["Emission"], oout.inputs["Surface"])
        out_mesh.materials.append(out_mat)

        out_obj = bpy.data.objects.new(f"PinOutline_{i}", out_mesh)
        out_obj.location = (base_x, base_y, base_z - 0.1)
        bpy.context.scene.collection.objects.link(out_obj)
        con2 = out_obj.constraints.new(type='LOCKED_TRACK')
        con2.track_axis = 'TRACK_Y'
        con2.lock_axis  = 'LOCK_Z'
        if cam_obj:
            con2.target = cam_obj


def _add_photo_face(bpy, pin_obj, index: int, photo_path: str,
                    hw: float, body_bot: float, body_top: float,
                    border: float) -> None:
    """Add a textured quad showing the photo thumbnail inside the pin body."""
    pad = border * 1.5
    x0, x1 = -hw + pad,  hw - pad
    z0, z1 = body_bot + pad, body_top - pad

    photo_verts = [
        (x0, -0.01, z0),
        (x1, -0.01, z0),
        (x1, -0.01, z1),
        (x0, -0.01, z1),
    ]
    photo_mesh = bpy.data.meshes.new(f"PinPhoto_{index}")
    photo_mesh.from_pydata(photo_verts, [], [(0, 1, 2, 3)])
    photo_mesh.update()

    uv = photo_mesh.uv_layers.new(name="UVMap")
    uv.data[0].uv = (0, 0)
    uv.data[1].uv = (1, 0)
    uv.data[2].uv = (1, 1)
    uv.data[3].uv = (0, 1)

    mat = bpy.data.materials.new(f"PinPhoto_{index}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex_node = nodes.new("ShaderNodeTexImage")
    try:
        img = bpy.data.images.load(photo_path)
        # Downscale to a small thumbnail to keep the .blend lightweight
        img.scale(256, 256)
        tex_node.image = img
    except Exception:
        pass
    tex_node.location = (-300, 0)

    emit_node = nodes.new("ShaderNodeEmission")
    emit_node.inputs["Strength"].default_value = 2.0
    emit_node.location = (0, 0)

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)

    links.new(tex_node.outputs["Color"], emit_node.inputs["Color"])
    links.new(emit_node.outputs["Emission"], out_node.inputs["Surface"])

    photo_mesh.materials.append(mat)

    photo_obj = bpy.data.objects.new(f"PinPhoto_{index}", photo_mesh)
    photo_obj.location = pin_obj.location
    bpy.context.scene.collection.objects.link(photo_obj)

    # Inherit the same face-camera constraint
    con = photo_obj.constraints.new(type='LOCKED_TRACK')
    con.track_axis = 'TRACK_Y'
    con.lock_axis  = 'LOCK_Z'
    cam_obj = next((o for o in bpy.data.objects if o.type == 'CAMERA'), None)
    if cam_obj:
        con.target = cam_obj


def _slope_color(slope: float) -> tuple[float, float, float]:
    """Return an sRGB color interpolated by slope grade.

    0%  → light blue  (0.40, 0.75, 1.00)
    20% → yellow      (1.00, 0.90, 0.10)
    40%+→ red         (1.00, 0.15, 0.10)
    """
    t = min(slope / 0.40, 1.0)   # normalise to [0, 1] where 1 = 40% grade
    if t <= 0.5:
        u = t * 2.0
        r = 0.40 + u * (1.00 - 0.40)
        g = 0.75 + u * (0.90 - 0.75)
        b = 1.00 + u * (0.10 - 1.00)
    else:
        u = (t - 0.5) * 2.0
        r = 1.00
        g = 0.90 + u * (0.15 - 0.90)
        b = 0.10
    return r, g, b


def _build_ribbon(bpy, track_data: list[dict],
                  half_width: float = 5.0, z_offset: float = 2.0) -> None:
    """Build a flat ribbon mesh along the track, colored by slope grade."""
    n = len(track_data)
    verts: list[tuple[float, float, float]] = []
    vert_colors: list[tuple[float, float, float]] = []

    for i, pt in enumerate(track_data):
        x, y, z = pt["x"], pt["y"], pt["z"] + z_offset
        slope = pt.get("slope", 0.0)

        # Tangent from adjacent points
        if i < n - 1:
            nx = track_data[i + 1]["x"] - x
            ny = track_data[i + 1]["y"] - y
        else:
            nx = x - track_data[i - 1]["x"]
            ny = y - track_data[i - 1]["y"]

        norm = math.sqrt(nx * nx + ny * ny)
        if norm > 1e-6:
            nx, ny = nx / norm, ny / norm
        else:
            nx, ny = 1.0, 0.0

        # Perpendicular (right side of travel direction)
        px, py = ny, -nx

        verts.append((x - half_width * px, y - half_width * py, z))  # left
        verts.append((x + half_width * px, y + half_width * py, z))  # right
        color = _slope_color(slope)
        vert_colors.extend([color, color])

    faces = []
    for i in range(n - 1):
        l0, r0 = i * 2,       i * 2 + 1
        l1, r1 = (i + 1) * 2, (i + 1) * 2 + 1
        faces.append((l0, r0, r1, l1))

    # Create mesh
    mesh = bpy.data.meshes.new("Track")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # Vertex color attribute (Blender 3.3+ uses Color Attribute)
    try:
        color_attr = mesh.color_attributes.new(
            name="TrackColor", type='FLOAT_COLOR', domain='POINT'
        )
        for vi, (r, g, b) in enumerate(vert_colors):
            color_attr.data[vi].color = (r, g, b, 1.0)
    except AttributeError:
        # Older Blender: use vertex_colors (loop-based)
        vcol = mesh.vertex_colors.new(name="TrackColor")
        for poly in mesh.polygons:
            for loop_idx in poly.loop_indices:
                vi = mesh.loops[loop_idx].vertex_index
                r, g, b = vert_colors[vi]
                vcol.data[loop_idx].color = (r, g, b, 1.0)

    obj = bpy.data.objects.new("Track", mesh)
    bpy.context.scene.collection.objects.link(obj)

    # Material: emission reading vertex color so the ribbon is always visible
    mat = bpy.data.materials.new("TrackRibbon")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    vcol_node = nodes.new("ShaderNodeVertexColor")
    vcol_node.layer_name = "TrackColor"
    vcol_node.location = (-300, 0)

    emit_node = nodes.new("ShaderNodeEmission")
    emit_node.inputs["Strength"].default_value = 2.0
    emit_node.location = (0, 0)

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)

    links.new(vcol_node.outputs["Color"], emit_node.inputs["Color"])
    links.new(emit_node.outputs["Emission"], out_node.inputs["Surface"])

    mesh.materials.append(mat)


main()
