"""Hitbox serialization - import + export for all three prefab formats.

Converts between the armature's ``vs.hitboxes`` collection entries and:

* **QC text** - ``$hbox`` lines (Source 1 / studiomdl)
* **DME** - ``DmeHitbox`` element attributes (model-DMX / PulseMDL)
* **KV3** - ``HitboxCapsule`` KVNodes (Source 2 / ModelDoc .vmdl)

A hitbox entry stores ``vec_min`` / ``vec_max`` (bone-local box), ``rotation``
(radians) and ``scale`` (capsule radius; ``0`` or negative = oriented box). Each format's
writer (called by ``export.prefab.PrefabExporter``) sits next to its reader so the
two halves stay in sync.
"""

import math

from mathutils import Vector, Euler

from .. import utils, datamodel, keyvalues3
from ..keyvalues3 import KVBool, KVVector3


def _group_id(group_str: str) -> int:
    return int(group_str) if group_str.isdigit() else 0


# -----------------------------------------------------------------------------
# DME (model-DMX / PulseMDL)
# -----------------------------------------------------------------------------

def write_dme_attrs(hb, entry, bone_export: str) -> None:
    """Populate a DmeHitbox element from a hitbox entry. Inverse of
    ``import_hitboxes_from_dmx_root`` below. ``bone_export`` is the resolved
    export bone name (the exporter maps it through ``exportable_boneNames``)."""
    hb["boneName"]   = bone_export
    hb["groupId"]    = _group_id(entry.group)
    hb["minBounds"]  = datamodel.Vector3(entry.vec_min)
    hb["maxBounds"]  = datamodel.Vector3(entry.vec_max)
    # radius: <= 0 = OBB box, > 0 = capsule radius (matches flCapsuleRadius).
    hb["radius"]     = float(entry.scale) if entry.scale >= 0.0 else -1.0
    # Euler degrees (pitch, yaw, roll). Vector3 not Angle, to avoid the
    # "angle"/"qangle" DMX type-name mismatch with PulseMDL.
    hb["orientation"] = datamodel.Vector3((
        math.degrees(entry.rotation[0]),
        math.degrees(entry.rotation[1]),
        math.degrees(entry.rotation[2])))


def import_hitboxes_from_dmx_root(dm_root, armature: 'object') -> 'tuple[int, int, list]':
    import math as _math

    hbox_set_list = dm_root.get("hitboxSetList") if dm_root is not None else None
    if hbox_set_list is None:
        return (0, 0, [])
    sets = hbox_set_list.get("hitboxSetList") or []
    if not sets:
        return (0, 0, [])

    avs = getattr(armature.data, 'vs', None)
    if avs is None:
        return (0, 0, [])

    bone_by_export = {utils.get_bone_exportname(b): b for b in armature.data.bones}
    bone_by_name = {b.name: b for b in armature.data.bones}

    created_count = 0
    skipped_count = 0
    skipped_bones: list = []

    first_set_name = (sets[0].name or "").strip()
    if first_set_name:
        avs.hboxset_name = first_set_name

    for hbox_set in sets:
        for hb in (hbox_set.get("hitboxList") or []):
            bone_export = hb.get("boneName") or hb.name or ""
            bone = bone_by_export.get(bone_export) or bone_by_name.get(bone_export)
            if bone is None:
                skipped_bones.append(bone_export or "<unnamed>")
                skipped_count += 1
                continue

            entry = avs.hitboxes.add()
            entry.bone_name = bone.name
            entry.group = str(min(max(int(hb.get("groupId", 0)), 0), 8))
            entry.vec_min = tuple(hb.get("minBounds", (0.0, 0.0, 0.0)))
            entry.vec_max = tuple(hb.get("maxBounds", (0.0, 0.0, 0.0)))
            orient = hb.get("orientation", (0.0, 0.0, 0.0))
            entry.rotation = (_math.radians(orient[0]), _math.radians(orient[1]), _math.radians(orient[2]))
            entry.scale = float(hb.get("radius", -1.0))
            avs.hitboxes_index = len(avs.hitboxes) - 1
            created_count += 1

    return (created_count, skipped_count, skipped_bones)


# -----------------------------------------------------------------------------
# QC text ($hbox)
# -----------------------------------------------------------------------------

def qc_line(entry, bone_export: str) -> str:
    """Return one ``$hbox`` line. Inverse of ``import_hitboxes_from_content``."""
    grp = _group_id(entry.group)
    base = (
        f'$hbox\t{grp}\t"{bone_export}"\t\t'
        f'{entry.vec_min[0]:.4f}\t{entry.vec_min[1]:.4f}\t{entry.vec_min[2]:.4f}\t'
        f'{entry.vec_max[0]:.4f}\t{entry.vec_max[1]:.4f}\t{entry.vec_max[2]:.4f}'
    )
    rx  = math.degrees(entry.rotation[0])
    ry  = math.degrees(entry.rotation[1])
    rz  = math.degrees(entry.rotation[2])
    scl = entry.scale if entry.scale >= 0.0 else -1.0
    return f'{base}\t{rx:.4f}\t{ry:.4f}\t{rz:.4f}\t{scl:.4f}'


def import_hitboxes_from_content(content: str, armature: 'object', context, create_collection: bool = False, hboxset_name: str = ''):
    """Import hitboxes from $hbox lines into the armature's hitboxes collection.
    Returns (created_count, skipped_count, skipped_bones list)
    """
    import math as _math
    parsed = []
    for line in content.split('\n'):
        if line.strip().lower().startswith('$hbox'):
            data = utils.parse_hitbox_line(line)
            if data:
                parsed.append(data)

    if not parsed:
        return (0, 0, [])

    avs = getattr(armature.data, 'vs', None)
    if avs is None:
        return (0, len(parsed), [d['bone'] for d in parsed])

    if hboxset_name:
        avs.hboxset_name = hboxset_name

    created_count = 0
    skipped_count = 0
    skipped_bones = []

    for hb_data in parsed:
        bone_name = hb_data['bone']
        bone = None
        for b in armature.data.bones:
            if utils.get_bone_exportname(b) == bone_name:
                bone = b
                break
        if not bone:
            skipped_bones.append(bone_name)
            skipped_count += 1
            continue

        entry = avs.hitboxes.add()
        entry.bone_name = bone.name
        entry.group     = str(min(max(hb_data['group'], 0), 8))
        entry.vec_min   = hb_data['min']
        entry.vec_max   = hb_data['max']
        rx, ry, rz      = hb_data['rotation']
        entry.rotation  = (_math.radians(rx), _math.radians(ry), _math.radians(rz))
        entry.scale     = hb_data['scale']
        avs.hitboxes_index = len(avs.hitboxes) - 1
        created_count += 1

    return (created_count, skipped_count, skipped_bones)


# -----------------------------------------------------------------------------
# KV3 (Source 2 / ModelDoc) - capsule only
# -----------------------------------------------------------------------------

def kv3_capsule_kwargs(entry, parent_bone: str) -> dict:
    """Build the ``HitboxCapsule`` KVNode property kwargs (excluding _class).

    Converts the box+rotation representation into the two capsule endpoints,
    mirroring the viewport draw in viewport_draw.py. Inverse of
    ``import_hitboxes_from_kv3``."""
    mn  = Vector(entry.vec_min)
    mx  = Vector(entry.vec_max)
    ctr = (mn + mx) * 0.5
    rot_mat = Euler((entry.rotation[0], entry.rotation[1], entry.rotation[2]), 'XYZ').to_matrix()
    p0 = ctr + rot_mat @ (mn - ctr)
    p1 = ctr + rot_mat @ (mx - ctr)
    grp = int(entry.group) if entry.group.lstrip('-').isdigit() else 0
    return dict(
        parent_bone=parent_bone,
        surface_property="",
        translation_only=KVBool(False),
        group_id=grp,
        radius=entry.scale,
        point0=KVVector3(p0.x, p0.y, p0.z),
        point1=KVVector3(p1.x, p1.y, p1.z),
    )


def import_hitboxes_from_kv3(kv_doc, armature: 'object') -> 'tuple[int, int, list]':
    """Import Source 2 capsule hitboxes from a parsed VMDL KV3 document.

    Source 2 capsules are two bone-local endpoints + radius, stored as
    vec_min/vec_max with identity rotation and scale=radius (round-trips exactly).
    Returns (created_count, skipped_count, skipped_bones list).
    """
    avs = getattr(armature.data, 'vs', None)
    if avs is None:
        return (0, 0, [])

    def find_nodes(node, cls):
        found = []
        if isinstance(node, keyvalues3.KVNode):
            if node.properties.get('_class') == cls:
                found.append(node)
            for child in node.children:
                found.extend(find_nodes(child, cls))
        elif isinstance(node, dict):
            for value in node.values():
                found.extend(find_nodes(value, cls))
        elif isinstance(node, (list, tuple)):
            for item in node:
                found.extend(find_nodes(item, cls))
        return found

    hitbox_sets = []
    for root_node in kv_doc.roots.values():
        hitbox_sets.extend(find_nodes(root_node, 'HitboxSet'))

    if not hitbox_sets:
        return (0, 0, [])

    # Source bone names are case-insensitive; keep a lowercase fallback map.
    bone_map = {utils.get_bone_exportname(b): b for b in armature.data.bones}
    bone_map_lower = {utils.get_bone_exportname(b).lower(): b for b in armature.data.bones}
    for b in armature.data.bones:
        bone_map_lower.setdefault(b.name.lower(), b)

    created = 0
    skipped = 0
    skipped_bones = []
    set_name_applied = False

    for hbset in hitbox_sets:
        set_name = hbset.properties.get('name', '')
        if set_name and not set_name_applied:
            avs.hboxset_name = set_name
            set_name_applied = True

        for cap in hbset.children:
            if cap.properties.get('_class') != 'HitboxCapsule':
                continue
            props = cap.properties

            bone_name = props.get('parent_bone', '')
            bone = (bone_map.get(bone_name)
                    or armature.data.bones.get(bone_name)
                    or bone_map_lower.get(bone_name.lower()))
            if not bone:
                skipped += 1
                skipped_bones.append(bone_name)
                continue

            p0 = props.get('point0', [0.0, 0.0, 0.0])
            p1 = props.get('point1', [0.0, 0.0, 0.0])
            radius = float(props.get('radius', 0.0))
            group_id = int(float(props.get('group_id', 0)))

            entry = avs.hitboxes.add()
            entry.bone_name = bone.name
            entry.group     = str(min(max(group_id, 0), 8))
            entry.vec_min   = (float(p0[0]), float(p0[1]), float(p0[2]))
            entry.vec_max   = (float(p1[0]), float(p1[1]), float(p1[2]))
            entry.rotation  = (0.0, 0.0, 0.0)
            entry.scale     = radius
            avs.hitboxes_index = len(avs.hitboxes) - 1
            created += 1

    return (created, skipped, skipped_bones)
