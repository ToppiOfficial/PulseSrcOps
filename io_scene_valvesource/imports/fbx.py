"""Inverse of exports/fbx.py: Blender's importer builds the geometry, and the companion
.dmx written beside the .fbx carries everything FBX has no representation for - flex
controllers and rules, jigglebones, hitboxes, attachments and procedural bones.

The companion is a mesh-less skeleton DMX, so it merges onto the rig the FBX importer
just built rather than being imported as a model in its own right.
"""

import os

from ..utils import *
from .dmx import load_dmx
from .flexdata import populate_dme_flex_from_dmx
from .prefab import read_dmx_prefab


def companion_path(fbx_path: str) -> str | None:
    """The .dmx our exporter writes beside the .fbx, if it is there."""
    path = os.path.splitext(fbx_path)[0] + ".dmx"
    return path if os.path.isfile(path) else None


def apply_companion_dmx(reporter, filepath: str, objects) -> tuple[int, int, int, int]:
    """Merge the companion DMX onto the objects the FBX importer just produced.

    Returns (jigglebones, hitboxes, procbones, attachments).
    """
    try:
        parsed = load_dmx(filepath)
    except (IOError, ValueError) as err:
        reporter.warning(get_id("importer_warn_fbx_companion", True).format(
            os.path.basename(filepath), err))
        return (0, 0, 0, 0)

    combo_op = parsed.root.get("combinationOperator")
    if combo_op:
        # Flex controllers are global model data - every mesh with shapes gets them.
        for ob in objects:
            if ob.type == 'MESH' and hasShapes(ob):
                populate_dme_flex_from_dmx(ob, combo_op)

    arm = next((ob for ob in objects if ob.type == 'ARMATURE'), None)
    if not arm:
        return (0, 0, 0, 0)
    # Bones are resolved by name against the FBX rig; nothing here rebuilds the skeleton.
    return read_dmx_prefab(reporter, filepath, arm, parsed)
