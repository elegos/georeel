"""
Blender script: builds a 3D terrain scene and saves it as a .blend file.

Invoked headlessly by scene_builder.py:
    blender --background --python build_scene.py -- <meta.json> <data.bin> <texture.png> <output.blend>

Row 0 of the elevation grid is the northernmost row (max_lat).
"""

import json
import math
import os
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

    height_offset = float(argv[7]) if len(argv) > 7  else 200.0
    fps           = float(argv[8]) if len(argv) > 8  else 30.0
    speed_mps     = float(argv[9]) if len(argv) > 9  else 80.0
    pauses_path   = argv[10]       if len(argv) > 10 else None

    pause_schedule: dict = {}
    if pauses_path and os.path.isfile(pauses_path):
        with open(pauses_path) as _f:
            pause_schedule = json.load(_f)

    sun_vec = None
    if len(argv) >= 14:
        try:
            sun_vec = (float(argv[11]), float(argv[12]), float(argv[13]))
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

    if os.path.isfile(track_path):
        with open(track_path) as f:
            track_data = json.load(f)
        if len(track_data) >= 2:
            _build_ribbon(bpy, track_data, fps=fps, speed_mps=speed_mps,
                          pause_schedule=pause_schedule)
            _build_marker(bpy, track_data, height_offset=height_offset,
                          fps=fps, speed_mps=speed_mps,
                          pause_schedule=pause_schedule)

    # ------------------------------------------------------------------ #
    # Photo waypoint pins (billboards)                                     #
    # ------------------------------------------------------------------ #

    if os.path.isfile(pins_path):
        with open(pins_path) as f:
            pins_data = json.load(f)
        if pins_data:
            _build_pins(bpy, pins_data, pin_color, height_offset)

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
                height_offset: float = 200.0) -> None:
    """Build one billboard pin per waypoint.

    Each pin is a circular disc in the local XZ plane, extruded via a Solidify
    modifier to give it physical depth.  Pin dimensions and thickness scale with
    *height_offset* so the pins appear consistent at different camera altitudes.
    A Locked Track constraint keeps each pin always facing the camera.
    """
    # Scale all dimensions proportionally to camera height so the pins
    # appear the same angular size regardless of altitude.
    scale      = height_offset / 200.0
    pin_width  = 24.0 * scale
    thickness  = max(2.0, pin_width * 0.3)   # 30% of width, minimum 2 m
    z_offset   = 3.0 * scale
    border     = 1.5 * scale
    _N_CIRCLE  = 20   # polygon segments for the disc

    # Photo face sits just in front of the solidified front face
    # (LOCKED_TRACK → Y toward camera → negative Y = toward camera)
    photo_front_y = -(thickness / 2.0 + 0.2 * scale)

    pin_r, pin_g, pin_b = _hex_to_rgb(pin_color_hex)
    cam_obj = next((o for o in bpy.data.objects if o.type == 'CAMERA'), None)

    for i, pin in enumerate(pins_data):
        base_x = pin["x"]
        base_y = pin["y"]
        base_z = pin["z"] + z_offset

        radius   = pin_width / 2.0
        center_z = radius   # circle bottom at z=0, top at z=2*radius

        # ---------------------------------------------------------------- #
        # Circular disc in local XZ plane (Y=0); Solidify adds depth       #
        # ---------------------------------------------------------------- #
        circle_verts = [
            (radius * math.cos(2 * math.pi * k / _N_CIRCLE),
             0.0,
             center_z + radius * math.sin(2 * math.pi * k / _N_CIRCLE))
            for k in range(_N_CIRCLE)
        ]
        circle_face = list(range(_N_CIRCLE))

        pin_mesh = bpy.data.meshes.new(f"Pin_{i}")
        pin_mesh.from_pydata(circle_verts, [], [circle_face])
        pin_mesh.update()

        # Convenience bounds for photo face (inscribed square)
        hw       = radius * 0.9
        body_bot = center_z - radius * 0.9
        body_top = center_z + radius * 0.9

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

        # Solidify: symmetric extrusion → front face at -thickness/2, back at +thickness/2
        mod = pin_obj.modifiers.new(name="Solidify", type='SOLIDIFY')
        mod.thickness = thickness
        mod.offset = 0.0

        # ---------------------------------------------------------------- #
        # Photo thumbnail (sits in front of the solidified front face)     #
        # ---------------------------------------------------------------- #
        photo_path = pin.get("photo_path", "")
        if photo_path and os.path.isfile(photo_path):
            _add_photo_face(bpy, pin_obj, i, photo_path,
                            hw, body_bot, body_top, border, photo_front_y)

        # ---------------------------------------------------------------- #
        # Always-face-camera constraint                                     #
        # ---------------------------------------------------------------- #
        con = pin_obj.constraints.new(type='LOCKED_TRACK')
        con.track_axis = 'TRACK_Y'
        con.lock_axis  = 'LOCK_Z'
        if cam_obj:
            con.target = cam_obj

        # ---------------------------------------------------------------- #
        # Dark border outline (slightly larger circle, same thickness)     #
        # ---------------------------------------------------------------- #
        out_r = radius + border
        outline_verts = [
            (out_r * math.cos(2 * math.pi * k / _N_CIRCLE),
             0.0,
             center_z + out_r * math.sin(2 * math.pi * k / _N_CIRCLE))
            for k in range(_N_CIRCLE)
        ]
        outline_faces = [list(range(_N_CIRCLE))]
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
        out_obj.location = (base_x, base_y, base_z)
        bpy.context.scene.collection.objects.link(out_obj)

        out_mod = out_obj.modifiers.new(name="Solidify", type='SOLIDIFY')
        out_mod.thickness = thickness + 0.2 * scale
        out_mod.offset = 0.0

        con2 = out_obj.constraints.new(type='LOCKED_TRACK')
        con2.track_axis = 'TRACK_Y'
        con2.lock_axis  = 'LOCK_Z'
        if cam_obj:
            con2.target = cam_obj


def _add_photo_face(bpy, pin_obj, index: int, photo_path: str,
                    hw: float, body_bot: float, body_top: float,
                    border: float, front_y: float) -> None:
    """Add a textured quad showing the photo thumbnail inside the pin body.

    *front_y* is the local Y coordinate of the front face of the solidified
    pin, so the photo sits just in front of it (more negative Y = toward camera).
    """
    pad = border * 1.5
    x0, x1 = -hw + pad,  hw - pad
    z0, z1 = body_bot + pad, body_top - pad

    photo_verts = [
        (x0, front_y, z0),
        (x1, front_y, z0),
        (x1, front_y, z1),
        (x0, front_y, z1),
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
        tex_node.image = bpy.data.images.load(photo_path)
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


def _build_marker(bpy, track_data: list[dict],
                  height_offset: float = 200.0,
                  fps: float = 30.0, speed_mps: float = 80.0,
                  z_offset: float = 4.0,
                  pause_schedule: dict | None = None) -> None:
    """Create an animated position marker that travels along the track.

    Strategy:
      1. Build a NURBS path object through the ribbon centreline.
      2. Give the marker a Follow Path constraint on that curve.
      3. Keyframe offset_factor 0→1 over the path duration so the marker
         moves in sync with the camera (same speed_mps, same fps).
    """
    n = len(track_data)
    ribbon_spacing_m = 5.0
    frames_per_point = max(1.0, ribbon_spacing_m * fps / speed_mps)
    fly_total        = (pause_schedule or {}).get("fly_total_frames",
                                                   int((n - 1) * frames_per_point))
    total_scene      = (pause_schedule or {}).get("total_scene_frames", fly_total)
    pauses           = (pause_schedule or {}).get("pauses", [])
    total_path_frames = max(2, fly_total)

    scale = height_offset / 200.0
    marker_radius = max(3.0, 8.0 * scale)

    # ------------------------------------------------------------------ #
    # NURBS path through track centreline                                  #
    # ------------------------------------------------------------------ #
    curve_data = bpy.data.curves.new("TrackCurve", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.path_duration = max(2, total_scene)

    spline = curve_data.splines.new('NURBS')
    spline.points.add(n - 1)          # spline starts with 1 point
    spline.use_endpoint_u = True      # curve passes through end points
    spline.order_u = min(4, n)

    for i, pt in enumerate(track_data):
        spline.points[i].co = (pt["x"], pt["y"], pt["z"] + z_offset, 1.0)

    path_obj = bpy.data.objects.new("TrackCurve", curve_data)
    bpy.context.scene.collection.objects.link(path_obj)

    # ------------------------------------------------------------------ #
    # Marker mesh: flat disc                                               #
    # ------------------------------------------------------------------ #
    bpy.ops.mesh.primitive_circle_add(
        vertices=16, radius=marker_radius, fill_type='NGON', location=(0, 0, 0)
    )
    marker_obj = bpy.context.object
    marker_obj.name = "TrackMarker"

    mat = bpy.data.materials.new("TrackMarker")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    emit.inputs["Strength"].default_value = 3.0
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    marker_obj.data.materials.append(mat)

    # ------------------------------------------------------------------ #
    # Follow Path constraint                                               #
    # ------------------------------------------------------------------ #
    con = marker_obj.constraints.new(type='FOLLOW_PATH')
    con.target = path_obj
    con.use_fixed_location = True
    con.use_curve_follow = False

    # Keyframe offset_factor with pause-aware timing:
    #   - LINEAR between pauses (camera moves → marker moves)
    #   - CONSTANT during pauses (camera holds → marker holds)
    last_frame = max(2, total_scene)
    con.offset_factor = 0.0
    con.keyframe_insert("offset_factor", frame=1)

    pause_starts: set[int] = set()
    for pause in pauses:
        ps = pause["scene_start"]
        pd = pause["duration"]
        cb = pause["cumulative_before"]
        # fly frames elapsed just before this pause
        fly_before = ps - cb - 1
        # At pause start: hold at fly_before/fly_total (CONSTANT)
        con.offset_factor = fly_before / fly_total if fly_total > 0 else 0.0
        con.keyframe_insert("offset_factor", frame=ps)
        pause_starts.add(ps)
        # At pause end: resume from (fly_before+1)/fly_total (LINEAR)
        con.offset_factor = (fly_before + 1) / fly_total if fly_total > 0 else 0.0
        con.keyframe_insert("offset_factor", frame=ps + pd)

    con.offset_factor = 1.0
    con.keyframe_insert("offset_factor", frame=last_frame)

    # Apply interpolation: CONSTANT at pause-start KFs, LINEAR everywhere else
    action = marker_obj.animation_data.action
    if action:
        for fcurve in action.fcurves:
            if fcurve.data_path.endswith("offset_factor"):
                for kp in fcurve.keyframe_points:
                    kp.interpolation = (
                        'CONSTANT' if int(round(kp.co.x)) in pause_starts
                        else 'LINEAR'
                    )


def _build_ribbon(bpy, track_data: list[dict],
                  half_width: float = 5.0, z_offset: float = 2.0,
                  fps: float = 30.0, speed_mps: float = 80.0,
                  pause_schedule: dict | None = None) -> None:
    """Build a flat ribbon mesh along the track, colored by slope grade.

    A Build modifier progressively reveals quads so the ribbon unfolds as the
    camera travels.  When pause_schedule is provided, the Build modifier's
    frame_start is keyframed so the ribbon freezes during photo pauses and
    resumes when the camera moves again.
    """
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

    # Build modifier: reveal one quad per "fly frame" so the ribbon unfolds
    # in sync with the camera.  frame_duration covers only fly frames;
    # frame_start is keyframed to advance during pauses so the ribbon freezes.
    ribbon_spacing_m = 5.0  # must match _RIBBON_SAMPLE_SPACING_M in scene_builder.py
    frames_per_face = max(1.0, ribbon_spacing_m * fps / speed_mps)
    fly_total = (pause_schedule or {}).get("fly_total_frames",
                                           int((n - 1) * frames_per_face))

    build_mod = obj.modifiers.new(name="Unfold", type='BUILD')
    build_mod.frame_start = 1
    build_mod.frame_duration = max(1, fly_total)
    build_mod.use_random_order = False

    pauses = (pause_schedule or {}).get("pauses", [])
    if pauses:
        dp = f'modifiers["Unfold"].frame_start'
        if obj.animation_data is None:
            obj.animation_data_create()
        # Initial KF: frame_start=1, CONSTANT (holds until first pause)
        build_mod.frame_start = 1
        obj.keyframe_insert(data_path=dp, frame=1)
        for pause in pauses:
            ps = pause["scene_start"]
            pd = pause["duration"]
            cb = pause["cumulative_before"]
            # At pause start: frame_start = cb+1 (freeze: LINEAR to cb+pd+1 over pd frames)
            build_mod.frame_start = cb + 1
            obj.keyframe_insert(data_path=dp, frame=ps)
            # At pause end: frame_start = cb+pd+1 (CONSTANT until next pause)
            build_mod.frame_start = cb + pd + 1
            obj.keyframe_insert(data_path=dp, frame=ps + pd)
        # Set interpolation: LINEAR on pause-start KFs, CONSTANT elsewhere
        pause_starts = {p["scene_start"] for p in pauses}
        action = obj.animation_data.action
        if action:
            for fc in action.fcurves:
                if fc.data_path == dp:
                    for kp in fc.keyframe_points:
                        kp.interpolation = (
                            'LINEAR' if int(round(kp.co.x)) in pause_starts
                            else 'CONSTANT'
                        )

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
