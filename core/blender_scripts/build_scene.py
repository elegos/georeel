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

    meta_path, data_path, texture_path, output_path = argv[:4]

    sun_vec = None
    if len(argv) >= 7:
        try:
            sun_vec = (float(argv[4]), float(argv[5]), float(argv[6]))
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

    # Pack the texture so the .blend is self-contained
    bpy.ops.file.pack_all()

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    bpy.ops.wm.save_as_mainfile(filepath=output_path)
    print(f"[georeel] Scene saved: {output_path} "
          f"({rows}×{cols} vertices, {len(faces)} quads)")


main()
