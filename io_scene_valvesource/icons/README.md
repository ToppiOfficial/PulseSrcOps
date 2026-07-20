# Custom icons

Drop a `.png` in this folder and it becomes available as `icon('<filename stem>')`
from `io_scene_valvesource/icons.py` - no code change, no registration list.

```python
from ..icons import icon

layout.label(text="Jigglebones", icon_value=icon('jigglebone'))
row.operator("smd.hitbox_add", text="", icon_value=icon('hitbox'))
```

`icon()` returns `0` for an unknown name, which Blender draws as no icon, so a
typo or a missing file degrades to a plain label rather than raising.

Note it is `icon_value=` (an int), not `icon=` (a built-in enum name). Passing a
custom id to `icon=` will not work.

## Art specs

- **Format** - PNG, RGBA, straight (non-premultiplied) alpha.
- **Size** - 128x128. Blender rasterises to 32x32 or smaller in the UI, so keep
  shapes chunky; thin 1px strokes disappear.
- **Padding** - leave ~6% margin so the glyph doesn't touch the edge.
- **Colour** - solid, flat shapes read best. Blender does not tint custom icons
  to match the theme the way it does built-ins, so a custom icon keeps its own
  colours on both light and dark themes - pick something legible on both.
- **Naming** - lowercase snake_case, matching the feature (`jigglebone.png`,
  `proc_bone.png`, `cloth_proxy.png`).

## Current icons

| Name | Used by | Origin |
|---|---|---|
| `source1` | QC import menu entry | Source engine logo, developer.valvesoftware.com |
| `source2` | VMDL import menu entry | Source 2 logo, developer.valvesoftware.com |

Both were downsampled from the wiki's 2048x2048 originals. Note these are Valve
marks, not original art - fine for a Source modding tool, but they are not
GPL-licensed alongside the rest of the add-on.
