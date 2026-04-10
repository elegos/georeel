# TO-DO list for GeoReel

## Fade-in and fade-out

Add an option to fade-in from and fade-out to black. The user should choose whether to activate the fade-in and/or the fade-out, specifying for each active effect the black clip's duration (default: 5s) and the fade duration (default: 1s). The feature should be selectable in the main window, under a dedicated tab "Clip effects". The main tab where the existing items already exist should be called "Main".

## Title

Add an option to add a title to the clip to be overlayed on top of the main video, in the "Clip effects" tab of the main window. The user should be able to insert a multi-line title, choose the font from the ones available system-wide (default: "Noto Serif", defaulting to "Sans Serif" or otherwise the first available font), the font size (default: 95), the anchor point (default: bottom-right, with options top-left, top, top-right, center-left, center, etc.), the anchor point's margin (ignored for anchor point center), text alignment (default: right), text color (default: white), text shadow (default: yes), title duration (default: 10s). The user should be able to see a preview of the title in a black preview area with the same selected video resolution's ratio and the text resized according to the preview "screen" resolution. The preview should update as soon as any option or the text change.

## Music

Add an option to add a music to the clip in the main window, under the "Clip effects", selecting the audio clip with a choose file dialog or dropping an audio file in the drop area, with the possibility to delay, fade-in, fade-out, loop and the relative timings (default: delay: no (0s); fade-in: disabled; fade-out: yes, 5s; loop: disabled). The music should be put at the beginning of the clip, possibly delayed by the delay seconds, and in case of loop apply the possible fade-out effect only at the last execution. The clip (or the loop) should be cut at the end of the video's ending, including eventual fade to black effects with the relative black clip.
