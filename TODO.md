# TO-DO list for GeoReel

## Export as Kdenlive project

Add an optional "Export as Kdenlive project" action (e.g. a button in the main window or a menu item) that, given a completed pipeline run, generates a `.kdenlive` XML project file containing:
- The rendered terrain fly-through video as the main clip
- Photo overlays placed at the correct timestamps as image clips on a separate track
- Any clip effects already configured (fade-in/out, title, music) pre-applied as MLT effects/transitions in the project
- The correct project framerate and resolution matching the render settings

The feature is purely additive: the normal MP4 export path is unchanged. Kdenlive must not become a runtime dependency; the project file is generated using Python's XML facilities and validated against a known-good Kdenlive version's format.

## Music

Add an option to add a music to the clip in the main window, under the "Clip effects", selecting the audio clip with a choose file dialog or dropping an audio file in the drop area, with the possibility to delay, fade-in, fade-out, loop and the relative timings (default: delay: no (0s); fade-in: disabled; fade-out: yes, 5s; loop: disabled). The music should be put at the beginning of the clip, possibly delayed by the delay seconds, and in case of loop apply the possible fade-out effect only at the last execution. The clip (or the loop) should be cut at the end of the video's ending, including eventual fade to black effects with the relative black clip.
