"""
Blender startup script: passed via --python when opening the preview .blend
interactively.  Uses a timer so it runs after the UI event loop is ready.

Sets up every 3D viewport to:
  • Material Preview shading
  • Camera perspective view (look through FlyCamera)

Also activates and selects FlyCamera in the scene collection.
"""

import bpy


def _setup() -> None:
    scene = bpy.context.scene

    cam = bpy.data.objects.get("FlyCamera")
    if cam:
        # Activate and select camera in the outliner / scene collection
        for obj in bpy.context.view_layer.objects:
            obj.select_set(False)
        cam.select_set(True)
        bpy.context.view_layer.objects.active = cam
        scene.camera = cam

    # Configure every 3D viewport
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = next((s for s in area.spaces if s.type == 'VIEW_3D'), None)
            if space is None:
                continue
            space.shading.type = 'MATERIAL'
            space.region_3d.view_perspective = 'CAMERA'
            if cam:
                space.camera = cam


# Delay slightly so the window system is fully initialised before we touch it
bpy.app.timers.register(_setup, first_interval=0.05)
