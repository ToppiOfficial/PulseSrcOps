# PulseSrcOps

A heavily modified, character-modding-focused fork of [BlenderSourceTools](https://github.com/Artfunkel/BlenderSourceTools) targeting Blender 4.5+. Far beyond a simple edit, it adds many new features and major code rewrites for DMX-based Source Engine workflows with automated post-processing on export.

> [!IMPORTANT]
> This add-on shares the same property names as Blender Source Tools and its other forks, so it will conflict with them. Disable or uninstall any other Source Tools add-on before installing or enabling this one.

## Requirements

- Blender 4.5 or later

## Installation

1. Get the add-on by either:
   - Cloning or downloading this repository, or
   - Grabbing a packaged zip from [Releases](https://github.com/ToppiOfficial/PulseSrcOps/releases) (or building one yourself by running `make_zip.py`).
2. In Blender, go to *Edit > Preferences > Add-ons > Install*.
3. Select the `io_scene_valvesource` folder or the packaged zip.
4. Enable the add-on.

## Features

### Import

- **SMD** - Mesh, skeleton, and animation import.
- **DMX** - Partial but functional import of Source 2 DMX assets decompiled via [ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat).
- **Prefab data** - Jigglebones, hitboxes, and attachments imported from QC, DME, and KV3 sources.
- **Procedural bones** - Imported from either VRD or DME sources.

### Export

- **Post-Processing** - Automatic mesh processing at export time: toon outline generation, backface generation, mesh splitting, mesh cleanup (face/vertex removal by vertex group or material), and per-vertex weight normalization.
- **Bone Controls** - Per-bone export name, rotation offset, and position override; jigglebone property export directly to QC or VMDL.
- **Prefab data** - Jigglebones, hitboxes, and attachments exported to `.qci` for Source 1, or `.vmdl` / `.vmdl_prefab` for Source 2.
- **DME Prefab mode** - Jigglebones, hitboxes, attachments, and procedural bones embedded directly into the model DMX for Source 1. Works with some later `studiomdl` builds, but primarily targets [PulseMDL](https://github.com/ToppiOfficial/PulseMDL), my own `studiomdl` fork.
- **Axis Orientation** - Configurable up and forward axis on export.
- **Source 2** - Cloth proxy mesh export using `VertexFloatMap` attributes, bone scale animation, and KeyValues3 serialization.

### Tools

- **Jiggle & Procedural Bone Simulation** - Spring physics (flexible, rigid, boing, base spring) for jigglebones and real-time procedural (helper) bone driving run live in the viewport via a timer. Constraint gizmos (cone, yaw/pitch planes, base spring box, custom-length capsule) are drawn as GPU overlays. Simulation suspends automatically during export and resumes after.
- **Export Pose Preview** - Ghost bone overlay for bones with rotation/location offsets, showing the post-export transform alongside the current pose. Includes 2D axis labels and a connector line between current and export tail positions.
- **Hitbox Preview** - GPU overlay of bone hitboxes drawn directly in the viewport, supporting both standard box and capsule shapes for editing and verification before export.
- **Attachment Preview** - GPU overlay of attachments drawn directly in the viewport for editing and verification before export.
- **Flex Authoring** - Create flex controllers, flex rules, and delta maps for highly advanced flex (shape key) setups.
- **Japanese Language Support** - Japanese UI translation (disclosure: AI-assisted translation).

## Credits

Based on [BlenderSourceTools](https://github.com/Artfunkel/BlenderSourceTools) by Artfunkel, with incorporated work from:

- [compucolor/BlenderSourceTools](https://github.com/compucolor/BlenderSourceTools)
- [Rectus/BlenderSourceTools](https://github.com/Rectus/BlenderSourceTools)
- [FellOffFuji/BlenderSource2Tools](https://github.com/FellOffFuji/BlenderSource2Tools) - some export code was adapted from this fork.
- [srcprocbones](https://github.com/NameIsJakob/srcprocbones) by NameIsJakob - the jigglebone physics algorithm in `procbones_sim.py` is adapted from this project.