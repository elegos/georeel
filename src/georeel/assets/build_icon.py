#!/usr/bin/env python3
"""
Embed an Earth photograph into assets/icon.svg as a base64 data URI so the
SVG is fully self-contained (no external file dependency).

Usage:
    uv run assets/build_icon.py path/to/earth.jpg
"""
import base64
import sys
from pathlib import Path

_MIME = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}

_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <!--
      Circular clip removes the black space background from the photo.
      Earth sphere: centre (64, 72) radius 52.
    -->
    <clipPath id="sc">
      <circle cx="64" cy="72" r="52"/>
    </clipPath>
  </defs>

  <!-- Earth photograph embedded as base64 — no external dependency -->
  <image href="{data_uri}"
         x="12" y="20" width="104" height="104"
         clip-path="url(#sc)"
         preserveAspectRatio="xMidYMid slice"/>

  <!--
    Map pin: ¼ outside the sphere, ¾ inside.

    Geometry — sphere centre (64, 72) radius 52:
      Sphere surface at x = 90:
          y = 72 − √(52²−26²) = 72 − 45 = 27

      ¼-outside rule → head centre y = 27 + 5 = 32
      Head radius r = 15,  centre→tip distance d = 25
      (3-4-5 Pythagorean triple: sin θ = 3/5, cos θ = 4/5)

      Tangent points : (78, 41) and (102, 41)   — on circle, exact integers
      Tip            : (90, 57)  dist from sphere centre ≈ 30 < 52 ✓
      Head top       : (90, 17)  10 px above sphere surface = ¼ of L=40 ✓
      Hole circle    : centre (90, 32), radius 6
                       (84, 32) ↔ (96, 32) diametrically opposite
  -->
  <path
    fill="#27AE60"
    fill-rule="evenodd"
    d="M 90,57 L 78,41 A 15,15 0 1 1 102,41 Z
       M 84,32 A 6,6 0 1 0 96,32 A 6,6 0 1 0 84,32 Z"
  />
</svg>
"""


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: build_icon.py <earth-image>", file=sys.stderr)
        sys.exit(1)

    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"File not found: {img_path}", file=sys.stderr)
        sys.exit(1)

    ext  = img_path.suffix.lower()
    mime = _MIME.get(ext, "image/jpeg")
    b64  = base64.b64encode(img_path.read_bytes()).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"

    out = Path(__file__).parent / "icon.svg"
    out.write_text(_SVG_TEMPLATE.format(data_uri=data_uri))
    print(f"Written: {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
