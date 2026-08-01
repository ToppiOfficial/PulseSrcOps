"""Inverse of exports/fbx.py: Blender's importer builds the geometry, this decodes the Source
data out of the FBX user properties and feeds it to the existing prefab_io readers.

Consumed properties are removed, or a re-export emits them twice - once from `.vs`, once
verbatim.
"""

import bpy, json
from mathutils import Vector

from ..utils import *
from .. import keyvalues3
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone

_FLEXGROUPS = {'DEFAULT', 'EYES', 'EYELID', 'BROW', 'MOUTH', 'MISC', 'CHEEK'}


PROPS = ("source_jigglebone", "source_procbone", "source_hitboxes", "source_flex")


class _Doc:
    """The `kv_doc.roots` shape the prefab_io KV3 readers walk."""
    def __init__(self, root):
        self.roots = {"root": root}


class _ProcElem:
    """The datamodel element shape import_proc_bones_from_dmx_elements reads."""
    def __init__(self, elem_type, name, props):
        self.type = elem_type
        self.name = name
        self._props = props

    def get(self, key):
        return self._props.get(key)


def _payload(datablock, key):
    raw = datablock.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def strip_props(objects) -> None:
    for ob in objects:
        for key in PROPS:
            ob.pop(key, None)
        if ob.type == 'ARMATURE':
            for pb in ob.pose.bones:
                for key in PROPS:
                    pb.pop(key, None)


def apply_source_props(reporter, objects, scene, wanted=None) -> dict:
    """Decode source_* properties into `.vs`. ``wanted`` is the prefabData set, None for all.

    Objects with no payload are untouched, so this is safe on any other tool's FBX.
    """
    counts = {"jigglebones": 0, "hitboxes": 0, "procbones": 0, "flex": 0}

    for ob in objects:
        if ob.type == 'MESH' and _apply_flex(ob):
            counts["flex"] += 1

    for arm in [ob for ob in objects if ob.type == 'ARMATURE']:
        if wanted is None or 'JIGGLEBONES' in wanted:
            counts["jigglebones"] += _apply_jigglebones(reporter, arm)
        if wanted is None or 'HITBOXES' in wanted:
            counts["hitboxes"] += _apply_hitboxes(reporter, arm)
        if wanted is None or 'PROCEDURAL' in wanted:
            counts["procbones"] += _apply_procbones(reporter, arm, scene)

    strip_props(objects)
    return counts


# -- flex ------------------------------------------------------------------
def _apply_flex(ob) -> bool:
    data = _payload(ob, "source_flex")
    if not data:
        return False
    vs = ob.vs
    # Only DME mode writes this payload, so a re-export drops it unless the mode goes back.
    vs.flex_controller_mode = 'DME'
    vs.dme_flexcontrollers.clear()
    vs.dme_flex_rules.clear()

    for entry in data.get("controllers", []):
        fc = vs.dme_flexcontrollers.add()
        fc.controller_name = entry.get("name", "")
        fc.shapekey = entry.get("shapekey", "")
        fc.raw_delta_name = entry.get("delta_name", "")
        fc.eyelid = bool(entry.get("eyelid", False))
        fc.stereo = bool(entry.get("stereo", False))
        fc.flex_min = float(entry.get("flex_min", 0.0))
        fc.flex_max = float(entry.get("flex_max", 1.0))
        # Anything that is not one of the fixed groups came from, and goes back to, custom.
        group = (entry.get("group") or "default").upper()
        if group in _FLEXGROUPS:
            fc.flexgroup = group
        else:
            fc.flexgroup = 'CUSTOM'
            fc.flexgroup_custom = entry["group"]

    for entry in data.get("rules", []):
        rule = vs.dme_flex_rules.add()
        rule.rule_type = entry.get("type", 'EXPRESSION')
        rule.name = entry.get("name", "")
        rule.expression = entry.get("expression", "")
        rule.components = entry.get("components", "")
        rule.dominator_names = entry.get("dominators", "")
        rule.suppressed_names = entry.get("suppressed", "")

    return True


# -- prefab data -----------------------------------------------------------
def _apply_jigglebones(reporter, arm) -> int:
    root = None
    for pb in arm.pose.bones:
        props = _payload(pb, "source_jigglebone")
        if not props:
            continue
        # The reader matches on jiggle_root_bone; the pose bone it rode in on is the authority.
        props["jiggle_root_bone"] = pb.name
        if root is None:
            root = keyvalues3.KVNode(_class='JiggleBoneList')
        root.add_child(keyvalues3.KVNode(_class='JiggleBone', **props))

    if root is None:
        return 0
    count, missing = _jigglebone.import_jigglebones_from_kv3(_Doc(root), arm)
    for name in missing:
        reporter.warning(get_id("importer_err_missingbones", True).format("jigglebone", name))
    return count


def _apply_hitboxes(reporter, arm) -> int:
    entries = _payload(arm, "source_hitboxes")
    if not entries:
        return 0
    hbset = keyvalues3.KVNode(_class='HitboxSet', name=getattr(arm.data.vs, 'hboxset_name', ''))
    for entry in entries:
        hbset.add_child(keyvalues3.KVNode(_class='HitboxCapsule', **entry))
    created, _skipped, skipped_bones = _hitbox.import_hitboxes_from_kv3(_Doc(hbset), arm)
    for name in skipped_bones:
        reporter.warning(get_id("importer_err_missingbones", True).format("hitbox", name))
    return created


def _apply_procbones(reporter, arm, scene) -> int:
    elements = []
    attachments = {}
    for pb in arm.pose.bones:
        data = _payload(pb, "source_procbone")
        if not data:
            continue
        if data.get("type") == 'TRIGGER':
            elements.append((_ProcElem("DmeQuatInterpBone", pb.name, {
                "controlBone": data.get("control_bone"),
                "tolerances": data.get("tolerances", []),
                "triggerRotations": data.get("trigger_rotations", []),
                "targetRotations": data.get("target_rotations", []),
                "targetPositions": data.get("target_positions", []),
            }), pb.name))
        else:
            aim_target = data.get("aim_target") or ""
            offset = Vector(data.get("aim_offset") or (0.0, 0.0, 0.0))
            if offset.length_squared:
                # The reader only offsets an aim target that is an attachment, not a bone.
                key = f"{pb.name}_lookat"
                attachments[key] = (aim_target, offset * arm.matrix_world.to_scale())
                aim_target = key
            elements.append((_ProcElem("DmeAimAtBone", pb.name, {
                "aimTarget": aim_target,
                "aimVector": data.get("aim_vector", (0.0, 1.0, 0.0)),
                "upVector": data.get("up_vector", (0.0, 0.0, 1.0)),
            }), pb.name))

    if not elements:
        return 0
    count, missing = _proceduralbone.import_proc_bones_from_dmx_elements(
        elements, arm, scene, attachments)
    for name in missing:
        reporter.warning(get_id("importer_err_missingbones", True).format("procedural bone", name))
    return count
