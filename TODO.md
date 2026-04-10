# TO-DO list for GeoReel

## Export as Kdenlive project

Add an optional "Export as Kdenlive project" action (e.g. a button in the main window or a menu item) that, given a completed pipeline run, generates a `.kdenlive` XML project file containing:
- The rendered terrain fly-through video as the main clip
- Photo overlays placed at the correct timestamps as image clips on a separate track
- Any clip effects already configured (fade-in/out, title, music) pre-applied as MLT effects/transitions in the project
- The correct project framerate and resolution matching the render settings

The feature is purely additive: the normal MP4 export path is unchanged. Kdenlive must not become a runtime dependency; the project file is generated using Python's XML facilities and validated against a known-good Kdenlive version's format.
