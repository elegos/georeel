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
    marker_color  = argv[11]       if len(argv) > 11 else "#ADD8E6"

    pause_schedule: dict = {}
    if pauses_path and os.path.isfile(pauses_path):
        with open(pauses_path) as _f:
            pause_schedule = json.load(_f)

    sun_vec = None
    if len(argv) >= 15:
        try:
            sun_vec = (float(argv[12]), float(argv[13]), float(argv[14]))
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
    # Placeholder FlyCamera                                                #
    # Create it now so LOCKED_TRACK constraints in _build_marker /        #
    # _build_pins can target it.  inject_camera.py will reuse this same   #
    # object (by name) and add the fly-through keyframes to it.           #
    # ------------------------------------------------------------------ #
    cam_data_placeholder = bpy.data.cameras.new("FlyCamera")
    cam_data_placeholder.lens       = 35
    cam_data_placeholder.clip_start = 1.0
    cam_data_placeholder.clip_end   = 100_000.0
    cam_placeholder = bpy.data.objects.new("FlyCamera", cam_data_placeholder)
    bpy.context.scene.collection.objects.link(cam_placeholder)
    bpy.context.scene.camera = cam_placeholder

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
                          pause_schedule=pause_schedule,
                          marker_color=marker_color)

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
    """Build one map-pin marker per photo waypoint.

    Shape mirrors _build_marker: teardrop outer body (in the local XZ plane) +
    inner disc (photo thumbnail or dark hole), parented to the body so all
    children inherit its LOCKED_TRACK constraint and track the camera every frame.

    Geometry sits in the local XZ plane (Y = 0).  LOCKED_TRACK (TRACK_Y, LOCK_Z)
    rotates the body so local +Y always points toward the FlyCamera, making the
    front face visible throughout the animation.  The inner disc is offset to
    local Y = +solidify_th/2 + margin so it sits just in front of the solidified
    outer face.
    """
    scale       = height_offset / 200.0
    marker_r    = max(1.5, 4.0 * scale)
    r_head      = marker_r * 0.8
    z_c         = r_head * 1.7
    solidify_th = max(1.0, marker_r * 0.3)
    z_offset    = 3.0 * scale
    r_inner     = r_head * 0.52
    n_inner     = 20
    n_arc       = 24

    # Teardrop: tip at origin, CCW arc from right-tangent to left-tangent
    theta_r = -math.asin(r_head / z_c)
    theta_l =  math.pi + math.asin(r_head / z_c)
    arc_verts = []
    for k in range(n_arc + 1):
        t = theta_r + (theta_l - theta_r) * k / n_arc
        arc_verts.append((r_head * math.cos(t), 0.0, z_c + r_head * math.sin(t)))
    teardrop_verts = [(0.0, 0.0, 0.0)] + arc_verts
    teardrop_face  = list(range(len(teardrop_verts)))

    # Inner disc vertices template (local XZ plane, centred on head circle)
    inner_verts_tmpl = [
        (r_inner * math.cos(2 * math.pi * k / n_inner),
         0.0,
         z_c + r_inner * math.sin(2 * math.pi * k / n_inner))
        for k in range(n_inner)
    ]

    pin_r, pin_g, pin_b = _hex_to_rgb(pin_color_hex)
    cam_obj = next((o for o in bpy.data.objects if o.type == 'CAMERA'), None)

    for i, pin in enumerate(pins_data):
        base_x = pin["x"]
        base_y = pin["y"]
        base_z = pin["z"] + z_offset

        # ---------------------------------------------------------------- #
        # Outer teardrop body                                               #
        # ---------------------------------------------------------------- #
        body_mesh = bpy.data.meshes.new(f"Pin_{i}")
        body_mesh.from_pydata(teardrop_verts, [], [teardrop_face])
        body_mesh.update()

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
        body_mesh.materials.append(body_mat)

        pin_obj = bpy.data.objects.new(f"Pin_{i}", body_mesh)
        pin_obj.location = (base_x, base_y, base_z)
        bpy.context.scene.collection.objects.link(pin_obj)

        sol = pin_obj.modifiers.new("Solidify", 'SOLIDIFY')
        sol.thickness = solidify_th
        sol.offset    = 0.0   # symmetric: front face at local Y = +solidify_th/2

        # LOCKED_TRACK on the body — the single source of camera-facing rotation
        locked = pin_obj.constraints.new(type='LOCKED_TRACK')
        locked.track_axis = 'TRACK_Y'
        locked.lock_axis  = 'LOCK_Z'
        if cam_obj:
            locked.target = cam_obj

        # ---------------------------------------------------------------- #
        # Inner disc: photo thumbnail or dark hole                          #
        # Parented to body → inherits camera-facing rotation automatically  #
        # Local Y offset places it just in front of the solidified face     #
        # ---------------------------------------------------------------- #
        inner_mesh = bpy.data.meshes.new(f"PinInner_{i}")
        inner_mesh.from_pydata(inner_verts_tmpl, [], [list(range(n_inner))])
        inner_mesh.update()

        photo_path = pin.get("photo_path", "")
        if photo_path and os.path.isfile(photo_path):
            # UV: map each loop vertex's angle to UV space
            uv_layer = inner_mesh.uv_layers.new(name="UVMap")
            for loop_idx in range(n_inner):
                u = 0.5 + 0.5 * math.cos(2 * math.pi * loop_idx / n_inner)
                v = 0.5 + 0.5 * math.sin(2 * math.pi * loop_idx / n_inner)
                uv_layer.data[loop_idx].uv = (u, v)

            inner_mat = bpy.data.materials.new(f"PinPhoto_{i}")
            inner_mat.use_nodes = True
            inodes = inner_mat.node_tree.nodes
            ilinks = inner_mat.node_tree.links
            inodes.clear()
            tex_nd = inodes.new("ShaderNodeTexImage")
            try:
                tex_nd.image = bpy.data.images.load(photo_path)
            except Exception:
                pass
            tex_nd.location = (-300, 0)
            iemit = inodes.new("ShaderNodeEmission")
            iemit.inputs["Strength"].default_value = 2.0
            iemit.location = (0, 0)
            iout = inodes.new("ShaderNodeOutputMaterial")
            iout.location = (300, 0)
            ilinks.new(tex_nd.outputs["Color"], iemit.inputs["Color"])
            ilinks.new(iemit.outputs["Emission"], iout.inputs["Surface"])
        else:
            inner_mat = bpy.data.materials.new(f"PinHole_{i}")
            inner_mat.use_nodes = True
            inodes = inner_mat.node_tree.nodes
            ilinks = inner_mat.node_tree.links
            inodes.clear()
            iemit = inodes.new("ShaderNodeEmission")
            iemit.inputs["Color"].default_value = (0.02, 0.02, 0.02, 1.0)
            iemit.inputs["Strength"].default_value = 3.0
            iout = inodes.new("ShaderNodeOutputMaterial")
            ilinks.new(iemit.outputs["Emission"], iout.inputs["Surface"])

        inner_mesh.materials.append(inner_mat)

        inner_obj = bpy.data.objects.new(f"PinInner_{i}", inner_mesh)
        bpy.context.scene.collection.objects.link(inner_obj)
        inner_obj.parent = pin_obj  # inherits LOCKED_TRACK via parent world transform
        inner_obj.matrix_parent_inverse.identity()
        # Local +Y offset: sit just in front of the solidified outer face
        inner_obj.location = (0.0, solidify_th / 2.0 + 0.05 * scale, 0.0)


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
                  pause_schedule: dict | None = None,
                  marker_color: str = "#ADD8E6") -> None:
    """Create an animated position marker that travels along the track.

    Strategy: keyframe the marker's world *location* at each track point,
    using the identical timing formula as the ribbon Build modifier.  Between
    keyframes Blender's LINEAR interpolation moves the marker in a straight
    line from one track point to the next — exactly matching the piecewise-
    linear ribbon segment that was just revealed.  This avoids the NURBS
    curve parameterisation warp that caused the marker to run ahead/behind on
    bends when using a FOLLOW_PATH constraint.
    """
    n = len(track_data)
    if n < 1:
        return

    ribbon_spacing_m  = 5.0
    frames_per_point  = max(1.0, ribbon_spacing_m * fps / speed_mps)
    pauses            = (pause_schedule or {}).get("pauses", [])
    pre_total         = (pause_schedule or {}).get("pre_total_frames", 0)

    scale        = height_offset / 200.0
    marker_radius = max(1.5, 4.0 * scale)

    # ------------------------------------------------------------------ #
    # Marker mesh: map-pin teardrop                                        #
    # ------------------------------------------------------------------ #
    r_head      = marker_radius * 0.8
    z_c         = r_head * 1.7
    solidify_th = max(1.0, marker_radius * 0.3)

    theta_r = -math.asin(r_head / z_c)
    theta_l =  math.pi + math.asin(r_head / z_c)
    n_arc   = 24
    arc_verts = []
    for i in range(n_arc + 1):
        t = theta_r + (theta_l - theta_r) * i / n_arc
        arc_verts.append((r_head * math.cos(t), 0.0, z_c + r_head * math.sin(t)))

    outer_verts = [(0.0, 0.0, 0.0)] + arc_verts
    outer_face  = list(range(len(outer_verts)))

    outer_mesh = bpy.data.meshes.new("TrackMarker")
    outer_mesh.from_pydata(outer_verts, [], [outer_face])
    outer_mesh.update()

    m_r, m_g, m_b = _hex_to_rgb(marker_color)
    mat_outer = bpy.data.materials.new("TrackMarker")
    mat_outer.use_nodes = True
    nodes = mat_outer.node_tree.nodes
    links = mat_outer.node_tree.links
    nodes.clear()
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (m_r, m_g, m_b, 1.0)
    emit.inputs["Strength"].default_value = 3.0
    out  = nodes.new("ShaderNodeOutputMaterial")
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    outer_mesh.materials.append(mat_outer)

    x0 = track_data[0]["x"]
    y0 = track_data[0]["y"]
    z0 = track_data[0]["z"] + z_offset

    marker_obj = bpy.data.objects.new("TrackMarker", outer_mesh)
    marker_obj.location = (x0, y0, z0)
    bpy.context.scene.collection.objects.link(marker_obj)

    sol = marker_obj.modifiers.new("Solidify", 'SOLIDIFY')
    sol.thickness = solidify_th
    sol.offset    = 0.0

    # -- Inner "hole" disc, parented so it inherits LOCKED_TRACK rotation --
    n_inner = 20
    r_inner = r_head * 0.52
    inner_verts = [
        (r_inner * math.cos(2 * math.pi * k / n_inner),
         0.0,
         z_c + r_inner * math.sin(2 * math.pi * k / n_inner))
        for k in range(n_inner)
    ]
    inner_mesh = bpy.data.meshes.new("TrackMarkerHole")
    inner_mesh.from_pydata(inner_verts, [], [list(range(n_inner))])
    inner_mesh.update()

    mat_inner = bpy.data.materials.new("TrackMarkerHole")
    mat_inner.use_nodes = True
    inodes = mat_inner.node_tree.nodes
    ilinks = mat_inner.node_tree.links
    inodes.clear()
    iemit = inodes.new("ShaderNodeEmission")
    iemit.inputs["Color"].default_value = (0.02, 0.02, 0.02, 1.0)
    iemit.inputs["Strength"].default_value = 3.0
    iout  = inodes.new("ShaderNodeOutputMaterial")
    ilinks.new(iemit.outputs["Emission"], iout.inputs["Surface"])
    inner_mesh.materials.append(mat_inner)

    hole_obj = bpy.data.objects.new("TrackMarkerHole", inner_mesh)
    bpy.context.scene.collection.objects.link(hole_obj)
    hole_obj.parent = marker_obj
    hole_obj.matrix_parent_inverse.identity()
    hole_obj.location = (0.0, solidify_th / 2.0 + 0.05 * scale, 0.0)

    # LOCKED_TRACK: keep face toward camera, Z stays world-up
    cam_obj = next((o for o in bpy.data.objects if o.type == 'CAMERA'), None)
    locked = marker_obj.constraints.new(type='LOCKED_TRACK')
    locked.track_axis = 'TRACK_Y'
    locked.lock_axis  = 'LOCK_Z'
    if cam_obj:
        locked.target = cam_obj

    # ------------------------------------------------------------------ #
    # Keyframe world location directly, mirroring the ribbon timing        #
    #                                                                      #
    # The ribbon Build modifier reveals one face (= one track segment) per #
    # frames_per_point fly-frames.  We insert a LINEAR keyframe at the     #
    # exact scene frame when the ribbon head reaches each track point.      #
    # CONSTANT keyframes bracket each photo pause so the marker holds.     #
    # ------------------------------------------------------------------ #

    # Build a set of pause scene-start frames and a dict for fast lookup:
    #   fly_frame (= scene_start - cumulative_before - pre_total - 1) → pause
    pauses_by_fly: dict[int, dict] = {}
    for p in pauses:
        fly_f = p["scene_start"] - p["cumulative_before"] - pre_total - 1
        pauses_by_fly[fly_f] = p

    pause_starts: set[int] = set()
    cumulative_pause_frames = 0

    # Pre-track hold: marker sits at track start during pre-photo slideshow
    if pre_total > 0:
        pause_starts.add(1)
        marker_obj.location = (x0, y0, z0)
        marker_obj.keyframe_insert("location", frame=1)
        marker_obj.keyframe_insert("location", frame=pre_total + 1)

    for i in range(n):
        fly_frame_i = round(i * frames_per_point)
        xi = track_data[i]["x"]
        yi = track_data[i]["y"]
        zi = track_data[i]["z"] + z_offset

        if fly_frame_i in pauses_by_fly:
            # A photo pause begins when the ribbon head reaches this track point.
            # Insert CONSTANT (hold) at pause start, LINEAR (resume) at pause end.
            p   = pauses_by_fly[fly_frame_i]
            ps  = p["scene_start"]
            pd  = p["duration"]
            marker_obj.location = (xi, yi, zi)
            marker_obj.keyframe_insert("location", frame=ps)
            pause_starts.add(ps)
            marker_obj.keyframe_insert("location", frame=ps + pd)
            cumulative_pause_frames += pd
        else:
            scene_frame_i = pre_total + fly_frame_i + cumulative_pause_frames + 1
            marker_obj.location = (xi, yi, zi)
            marker_obj.keyframe_insert("location", frame=scene_frame_i)

    # Apply interpolation: CONSTANT at pause-start frames, LINEAR everywhere else
    action = marker_obj.animation_data.action if marker_obj.animation_data else None
    if action:
        for fcurve in action.fcurves:
            if fcurve.data_path == "location":
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
    # Pre-photo frames are handled by setting frame_start > 1 so the ribbon
    # shows 0 faces while pre-track photos are displayed.
    ribbon_spacing_m = 5.0  # must match _RIBBON_SAMPLE_SPACING_M in scene_builder.py
    frames_per_face = max(1.0, ribbon_spacing_m * fps / speed_mps)
    sched      = pause_schedule or {}
    pre_total  = sched.get("pre_total_frames", 0)
    fly_total  = sched.get("fly_total_frames", int((n - 1) * frames_per_face))

    build_mod = obj.modifiers.new(name="Unfold", type='BUILD')
    build_mod.frame_start    = pre_total + 1
    build_mod.frame_duration = max(1, fly_total)
    build_mod.use_random_order = False

    pauses = sched.get("pauses", [])
    if pauses or pre_total:
        dp = f'modifiers["Unfold"].frame_start'
        if obj.animation_data is None:
            obj.animation_data_create()
        # Initial KF at frame 1: frame_start = pre_total+1 → 0 faces during pre-photos
        build_mod.frame_start = pre_total + 1
        obj.keyframe_insert(data_path=dp, frame=1)
        for pause in pauses:
            ps = pause["scene_start"]   # already offset by pre_total
            pd = pause["duration"]
            cb = pause["cumulative_before"]
            # At pause start: freeze ribbon (frame_start advances with time)
            build_mod.frame_start = pre_total + cb + 1
            obj.keyframe_insert(data_path=dp, frame=ps)
            # At pause end: resume (CONSTANT until next pause)
            build_mod.frame_start = pre_total + cb + pd + 1
            obj.keyframe_insert(data_path=dp, frame=ps + pd)
        # LINEAR interpolation on pause-start KFs so frame_start tracks current_frame;
        # CONSTANT everywhere else so the ribbon holds its position.
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
