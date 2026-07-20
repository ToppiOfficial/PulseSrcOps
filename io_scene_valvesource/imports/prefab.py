"""Jigglebone / hitbox / procedural-bone import from a model DMX.

`apply_dmx_prefab_data` moved verbatim from readDMX (1756-1802). It is a no-op when the
DMX lacks DmeJiggleBone joints / a hitboxSetList, so it stays safe to attempt on any
skeletal reference import.

`read_dmx_prefab` is the standalone form used by ImportPrefab: same readers, but against
an armature that already exists, so it resolves bones by their DMX names instead of the
boneIDs map that only exists while a skeleton is being built.
"""

from ..utils import (import_jigglebones_from_dmx_elements, import_hitboxes_from_dmx_root,
                     import_proc_bones_from_dmx_elements)

import bpy

PROC_BONE_TYPES = ("DmeQuatInterpBone", "DmeAimAtBone")


PREFAB_KINDS = ('JIGGLEBONES', 'HITBOXES', 'PROCEDURAL', 'ATTACHMENTS')


def wants_prefab(ctx, kind: str) -> bool:
    """Whether the importer's Prefab Data selection includes this kind. Importers that
    cannot produce prefab data do not declare the property, so absent means yes."""
    selected = getattr(ctx.properties, 'prefabData', None)
    if selected is None:
        return True
    return kind in selected


def read_dmx_prefab(ctx, filepath: str, arm) -> tuple[int, int, int, int]:
    """Attach prefab data from a model DMX onto an existing armature.

    Returns (jigglebones, hitboxes, procbones, attachments).
    """
    from .dmx import load_dmx, read_skeleton

    parsed = load_dmx(filepath)
    skel = read_skeleton(parsed)

    jiggle_elems = [(b.element, b.name) for b in skel.bones
                    if b.element is not None and b.element.type == "DmeJiggleBone"
                    ] if wants_prefab(ctx, 'JIGGLEBONES') else []
    jb_count = 0
    if jiggle_elems:
        jb_count, jb_missing = import_jigglebones_from_dmx_elements(jiggle_elems, arm)
        if jb_missing:
            ctx.warning(f"DMX jigglebones: {len(jb_missing)} bone(s) not found on "
                        f"'{arm.name}': {', '.join(jb_missing)}")

    hb_created, hb_skipped, hb_bones = (
        import_hitboxes_from_dmx_root(parsed.root, arm)
        if wants_prefab(ctx, 'HITBOXES') else (0, 0, []))
    if hb_skipped:
        ctx.warning(f"DMX hitboxes: {hb_skipped} skipped, bone(s) not found on "
                    f"'{arm.name}': {', '.join(hb_bones)}")

    proc_elems = [(b.element, b.name) for b in skel.bones
                  if b.element is not None and b.element.type in PROC_BONE_TYPES
                  ] if wants_prefab(ctx, 'PROCEDURAL') else []
    proc_attachments: dict[str, tuple] = {}
    for att in skel.attachments:
        parent_name = skel.bones[att.parent].name if att.parent is not None else None
        proc_attachments[att.name] = (parent_name, att.matrix.to_translation())

    pb_count = 0
    if proc_elems:
        pb_count, pb_missing = import_proc_bones_from_dmx_elements(
            proc_elems, arm, bpy.context.scene, proc_attachments)
        if pb_missing:
            ctx.warning(f"DMX procedural bones: {len(pb_missing)} entr(y/ies) skipped, "
                        f"bone(s) not found on '{arm.name}': {', '.join(pb_missing)}")

    at_count = (_build_dmx_attachments(ctx, skel, arm)
                if wants_prefab(ctx, 'ATTACHMENTS') else 0)

    return jb_count, hb_created, pb_count, at_count


def _build_dmx_attachments(ctx, skel, arm) -> int:
    """Unlike the model-import path, bones are resolved by name - there is no boneIDs map
    when the armature was not built from this file."""
    from .build import build_attachment_empty

    bone_lower = {b.name.lower(): b.name for b in arm.data.bones}
    coll = bpy.context.scene.collection
    created = 0
    missing: list[str] = []
    for att in skel.attachments:
        dmx_bone = skel.bones[att.parent].name if att.parent is not None else None
        if not dmx_bone:
            ctx.warning(f"Attachment '{att.name}' has no parent bone - skipped")
            continue
        resolved = (arm.data.bones[dmx_bone].name if dmx_bone in arm.data.bones
                    else bone_lower.get(dmx_bone.lower()))
        if not resolved:
            missing.append(dmx_bone)
            continue
        build_attachment_empty(ctx, coll, arm, att.name, resolved, att.matrix)
        created += 1
    if missing:
        ctx.warning(f"DMX attachments: {len(missing)} skipped, bone(s) not found on "
                    f"'{arm.name}': {', '.join(sorted(set(missing)))}")
    return created


def apply_dmx_prefab_data(ctx, smd, parsed, skel) -> None:
    jiggle_elems = [
        (b.element, smd.boneIDs.get(b.source_id))
        for b in skel.bones if b.element is not None and b.element.type == "DmeJiggleBone"
    ] if wants_prefab(ctx, 'JIGGLEBONES') else []
    if jiggle_elems:
        jb_count, jb_missing = import_jigglebones_from_dmx_elements(jiggle_elems, smd.a)
        print(f"- Imported {jb_count} jigglebone(s) from DMX")
        if jb_missing:
            ctx.warning(f"DMX jigglebones: {len(jb_missing)} bone(s) not found on "
                        f"'{smd.a.name}': {', '.join(jb_missing)}")

    hb_created, hb_skipped, hb_bones = (
        import_hitboxes_from_dmx_root(parsed.root, smd.a)
        if wants_prefab(ctx, 'HITBOXES') else (0, 0, []))
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
    ] if wants_prefab(ctx, 'PROCEDURAL') else []
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
