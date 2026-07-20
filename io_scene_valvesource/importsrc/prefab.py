"""Jigglebone / hitbox / procedural-bone import from a model DMX.

Moved verbatim from readDMX (1756-1802). These are no-ops when the DMX lacks
DmeJiggleBone joints / a hitboxSetList, so they stay safe to attempt on any
skeletal reference import. Whether this should be opt-in is a phase 5 question.
"""

from ..utils import (import_jigglebones_from_dmx_elements, import_hitboxes_from_dmx_root,
                     import_proc_bones_from_dmx_elements)

import bpy

PROC_BONE_TYPES = ("DmeQuatInterpBone", "DmeAimAtBone")


def apply_dmx_prefab_data(ctx, smd, parsed, skel) -> None:
    jiggle_elems = [
        (b.element, smd.boneIDs.get(b.source_id))
        for b in skel.bones if b.element is not None and b.element.type == "DmeJiggleBone"
    ]
    if jiggle_elems:
        jb_count, jb_missing = import_jigglebones_from_dmx_elements(jiggle_elems, smd.a)
        print(f"- Imported {jb_count} jigglebone(s) from DMX")
        if jb_missing:
            ctx.warning(f"DMX jigglebones: {len(jb_missing)} bone(s) not found on "
                        f"'{smd.a.name}': {', '.join(jb_missing)}")

    hb_created, hb_skipped, hb_bones = import_hitboxes_from_dmx_root(parsed.root, smd.a)
    if hb_created or hb_skipped:
        print(f"- Imported {hb_created} hitbox(es) from DMX")
        if hb_skipped:
            ctx.warning(f"DMX hitboxes: {hb_skipped} skipped, bone(s) not found on "
                        f"'{smd.a.name}': {', '.join(hb_bones)}")

    # Procedural (helper) bones: DmeQuatInterpBone (TRIGGER) / DmeAimAtBone (LOOKAT)
    # joints promoted on export. Rebuild each as a vs.proc_bones entry (with a
    # reconstructed slot action for triggers).
    proc_elems = [
        (b.element, smd.boneIDs.get(b.source_id))
        for b in skel.bones if b.element is not None and b.element.type in PROC_BONE_TYPES
    ]
    proc_attachments: dict[str, tuple] = {}
    for att in skel.attachments:
        parent_name = None
        if att.parent is not None:
            parent_name = smd.boneIDs.get(skel.bones[att.parent].source_id)
        proc_attachments[att.name] = (parent_name, att.matrix.to_translation())

    if proc_elems:
        pb_count, pb_missing = import_proc_bones_from_dmx_elements(
            proc_elems, smd.a, bpy.context.scene, proc_attachments)
        print(f"- Imported {pb_count} procedural bone(s) from DMX")
        if pb_missing:
            ctx.warning(f"DMX procedural bones: {len(pb_missing)} entr(y/ies) skipped, "
                        f"bone(s) not found on '{smd.a.name}': {', '.join(pb_missing)}")
