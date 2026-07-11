# PulseSrcOps

A character-modding-focused fork of [BlenderSourceTools](https://github.com/Artfunkel/BlenderSourceTools) targeting Blender 4.5+. Designed for DMX-based Source Engine workflows with automated post-processing on export.

## Requirements

- Blender 4.5 or later

## Installation

1. Go to *Edit > Preferences > Add-ons > Install*.
2. Select the `io_scene_valvesource` folder (or a zip of it).
3. Enable the add-on.

## Features

### Export Formats

DMX is the primary format. SMD and VTA are supported for compatibility but receive minimal updates.

### Post-Processing

Performed automatically at export time:

- **Toon Outline** - Solidify-based outline mesh generation for stylized models.
- **Mesh Cleanup** - Face and vertex removal driven by vertex groups or materials.
- **Weight Normalization** - Per-vertex influence limiting and normalization with per-bone priority via `Bone Sort Order`.

### Viewport Simulation & Previews

Real-time overlays in the 3D viewport driven by the **Simulation** panel in the sidebar:

- **Jiggle Bone Simulation** - Spring physics (flexible, rigid, boing, base spring) run live in the viewport via a timer. Constraint gizmos (cone, yaw/pitch planes, base spring box, custom-length capsule) are drawn as GPU overlays. Simulation suspends automatically during export and resumes after.
- **Export Pose Preview** - Ghost bone overlay for bones with rotation/location offsets, showing the post-export transform alongside the current pose. Includes 2D axis labels and a connector line between current and export tail positions.
- **Hitbox Preview** - GPU overlay of bone hitboxes drawn directly in the viewport, supporting both standard box and capsule shapes for editing and verification before export.

### Bone Controls

- Per-bone export name, rotation offset, and position override.
- Jigglebone property export directly to QC or VMDL.

### Source 2

- Cloth proxy mesh export using `VertexFloatMap` attributes.
- KeyValues3 serialization support.

## References

- [Valve Developer Wiki - DMX / Source 2 Vertex Attributes](http://developer.valvesoftware.com/wiki/DMX/Source_2_Vertex_attributes)

## Credits

Based on [BlenderSourceTools](https://github.com/Artfunkel/BlenderSourceTools) by Artfunkel, with incorporated work from:

- [compucolor/BlenderSourceTools](https://github.com/compucolor/BlenderSourceTools)
- [Rectus/BlenderSourceTools](https://github.com/Rectus/BlenderSourceTools)
- [FellOffFuji/BlenderSource2Tools](https://github.com/FellOffFuji/BlenderSource2Tools) - some export code was adapted from this fork.
- The jigglebone physics algorithm in `procbones_sim.py` is adapted from [srcprocbones](https://github.com/NameIsJakob/srcprocbones) by NameIsJakob.