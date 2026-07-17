"""Procedural (helper) bone serialization - DME export for the model DMX.

Converts the armature's ``vs.proc_bones`` entries (``ProcBoneEntry``) into the
two DME skeleton-joint element types PulseMDL reads out of the model ``.dmx``:

* **DmeQuatInterpBone** - a ``$driverbone`` / VRD ``<helper>`` (proc_type TRIGGER)
* **DmeAimAtBone**       - a ``$driverlookat`` / VRD ``<aimconstraint>`` (proc_type LOOKAT)

The helper bone's own joint element is promoted to one of these types, so no
separate ``.vrd`` is needed in DME prefab mode.

The per-trigger transform math is shared with the text/VRD writer
(``export.prefab.PrefabExporter._write_proc_vrd``) via ``build_trigger_transforms``
and the helpers below, so the two paths can't drift: the VRD path converts the
matrices to Euler degrees, this module to DMX quaternions.

Source 1 only - the caller guards on ``dme_mode and not source2``.
"""

import re as _re
from math import degrees, radians

from mathutils import Matrix, Vector, Quaternion, Euler

from .. import utils, datamodel


# -----------------------------------------------------------------------------
# Shared transform helpers (also used by export.prefab.PrefabExporter._write_proc_vrd)
# -----------------------------------------------------------------------------

_AXIS_VEC = {
    '+X': (1, 0, 0), '-X': (-1, 0, 0),
    '+Y': (0, 1, 0), '-Y': (0, -1, 0),
    '+Z': (0, 0, 1), '-Z': (0, 0, -1),
}


def axes_to_vec(axes):
    """Sum the selected axis enum flags into a normalized vector. Falls back to
    ``(1, 0, 0)`` for a zero-length result (mirrors the VRD aim/up handling)."""
    x = y = z = 0.0
    for a in (axes if isinstance(axes, set) else {axes}):
        v = _AXIS_VEC.get(a, (0, 0, 0))
        x += v[0]; y += v[1]; z += v[2]
    L = (x * x + y * y + z * z) ** 0.5
    return (x / L, y / L, z / L) if L > 1e-9 else (1.0, 0.0, 0.0)


def export_off_mat_rot_only(pb):
    """Rotation-only export offset (no translation component)."""
    bvs = pb.bone.vs
    if bvs.ignore_rotation_offset:
        return Matrix.Identity(4)
    return (Matrix.Rotation(bvs.export_rotation_offset_z, 4, 'Z') @
            Matrix.Rotation(bvs.export_rotation_offset_y, 4, 'Y') @
            Matrix.Rotation(bvs.export_rotation_offset_x, 4, 'X'))


def export_off_mat(pb):
    """Full (translation + rotation) export offset for a pose bone."""
    bvs = pb.bone.vs
    loc_x = 0.0 if bvs.ignore_location_offset else bvs.export_location_offset_x
    loc_y = 0.0 if bvs.ignore_location_offset else bvs.export_location_offset_y
    loc_z = 0.0 if bvs.ignore_location_offset else bvs.export_location_offset_z
    rot_x = 0.0 if bvs.ignore_rotation_offset else bvs.export_rotation_offset_x
    rot_y = 0.0 if bvs.ignore_rotation_offset else bvs.export_rotation_offset_y
    rot_z = 0.0 if bvs.ignore_rotation_offset else bvs.export_rotation_offset_z
    rot_mat = (Matrix.Rotation(rot_z, 4, 'Z') @
               Matrix.Rotation(rot_y, 4, 'Y') @
               Matrix.Rotation(rot_x, 4, 'X'))
    return Matrix.Translation((loc_x, loc_y, loc_z)) @ rot_mat


def bone_rest_rot(arm, bone_name):
    """Parent-relative rest orientation of ``bone_name`` (world-relative if it has
    no parent). ``_build_proc_triggers`` returns rest-relative deltas, but the
    compiler wants absolute local rotations - baking this in converts one to the
    other."""
    b = arm.data.bones.get(bone_name)
    if not b:
        return Matrix.Identity(4)
    if b.parent:
        return (b.parent.matrix_local.to_3x3().normalized().inverted() @
                b.matrix_local.to_3x3().normalized()).to_4x4()
    return b.matrix_local.to_3x3().normalized().to_4x4()


# Backwards-compatible alias (the driver was the first user of this helper).
driver_rest_rot = bone_rest_rot


def basepos_local(arm, helper_name, parent_name):
    """Rest-space translation of the helper relative to ``parent_name``
    (unscaled). The VRD path scales this by its scalar; the DME path scales it
    per-axis by ``armature_scale`` to match bone DmeTransform positions."""
    h_pb = arm.pose.bones.get(helper_name)
    p_pb = arm.pose.bones.get(parent_name)
    if not h_pb or not p_pb:
        return Vector((0.0, 0.0, 0.0))
    return (utils.get_bone_matrix(p_pb, rest_space=True).inverted() @
            utils.get_bone_matrix(h_pb, rest_space=True)).to_translation()


def build_trigger_transforms(arm, entry, entry_idx, scene):
    """Compute the per-trigger driver + helper export matrices for a TRIGGER entry.

    Returns a list of ``(d_mat, h_export, tol, dq, dloc)`` (empty if no triggers).
    ``d_mat`` / ``h_export`` are absolute local matrices; ``dq`` / ``dloc`` are the
    raw driver delta kept for the near-duplicate warning. Shared verbatim by the
    VRD writer - offset convention ``parent_off.inv @ rest_local @ delta @ own_off``."""
    from .. import procbones_sim as _pbsim

    driver_name = entry.driver_bone
    helper_name = entry.helper_bone
    d_pb = arm.pose.bones.get(driver_name)
    h_pb = arm.pose.bones.get(helper_name)

    d_off        = export_off_mat(d_pb)                   if d_pb                  else Matrix.Identity(4)
    h_off        = export_off_mat(h_pb)                   if h_pb                  else Matrix.Identity(4)
    d_parent_off = export_off_mat_rot_only(d_pb.parent)   if d_pb and d_pb.parent  else Matrix.Identity(4)
    h_parent_off = export_off_mat_rot_only(h_pb.parent)   if h_pb and h_pb.parent  else Matrix.Identity(4)
    d_rest_rot   = bone_rest_rot(arm, driver_name)
    h_rest_rot   = bone_rest_rot(arm, helper_name)

    out = []
    for dq, dloc, hloc, hq, tol in _pbsim._build_proc_triggers(arm, entry, entry_idx, scene, export_print=True):
        d_mat = d_parent_off.inverted() @ d_rest_rot @ dq.to_matrix().to_4x4() @ d_off
        h_mat = hq.to_matrix().to_4x4()
        h_mat.translation = hloc
        # Bake in the helper's rest orientation like the driver above: `hq` is a
        # rest-relative delta but targetRotations must be absolute local.
        h_export = h_parent_off.inverted() @ h_rest_rot @ h_mat @ h_off
        out.append((d_mat, h_export, tol, dq, dloc))
    return out


# -----------------------------------------------------------------------------
# DME (model-DMX / PulseMDL)
# -----------------------------------------------------------------------------

def write_dme_quatinterp_attrs(elem, arm, entry, entry_idx, scene, control_bone,
                               armature_scale, warn, parent_name=None) -> bool:
    """Populate a DmeQuatInterpBone element (proc_type TRIGGER). ``control_bone``
    is the driver's DMX joint name. Returns ``False`` (leaving the caller to keep a
    plain DmeJoint) and warns when the entry can't produce valid data.

    Position encoding: the compiler computes
    ``pos[t] = (basePos + targetPositions[t]) * $scale``. ``basePos`` is the helper
    rest position relative to its parent; ``targetPositions[t]`` is the per-trigger
    local delta from rest. Both are scaled per-axis by ``armature_scale``; world_scale
    is not pre-multiplied because the compiler applies ``$scale`` on top."""
    helper_name = entry.helper_bone

    if not entry.action:
        warn(utils.get_id('exporter_warn_procbone_no_action', True).format(helper_name))
        return False
    if not control_bone:
        warn(utils.get_id('exporter_warn_procbone_no_driver', True).format(helper_name))
        return False

    transforms = build_trigger_transforms(arm, entry, entry_idx, scene)
    if not transforms:
        warn(utils.get_id('exporter_warn_procbone_no_triggers', True).format(helper_name))
        return False
    if len(transforms) > 32:
        warn(utils.get_id('exporter_warn_procbone_too_many', True).format(helper_name, len(transforms)))

    # basePos = helper rest position relative to its DMX skeleton parent.
    if parent_name is None:
        helper_bone = arm.data.bones.get(helper_name)
        parent_name = (helper_bone.parent.name if helper_bone and helper_bone.parent
                       else entry.driver_bone)
    bp = basepos_local(arm, helper_name, parent_name)

    tolerances, trig_rots, tgt_pos, tgt_rots = [], [], [], []
    for d_mat, h_export, tol, _dq, _dloc in transforms:
        tolerances.append(degrees(tol))
        trig_rots.append(utils.getDatamodelQuat(d_mat.to_quaternion()))
        tgt_rots.append(utils.getDatamodelQuat(h_export.to_quaternion()))
        p = h_export.to_translation()
        tgt_pos.append(datamodel.Vector3([p.x * armature_scale[0],
                                          p.y * armature_scale[1],
                                          p.z * armature_scale[2]]))

    elem["controlBone"]      = control_bone
    elem["basePos"]          = datamodel.Vector3([bp.x * armature_scale[0],
                                                  bp.y * armature_scale[1],
                                                  bp.z * armature_scale[2]])
    elem["unlockBones"]      = False
    elem["tolerances"]       = datamodel.make_array(tolerances, float)
    elem["triggerRotations"] = datamodel.make_array(trig_rots, datamodel.Quaternion)
    elem["targetPositions"]  = datamodel.make_array(tgt_pos, datamodel.Vector3)
    elem["targetRotations"]  = datamodel.make_array(tgt_rots, datamodel.Quaternion)
    return True


def write_dme_aimat_attrs(elem, arm, entry, aim_target_name, armature_scale, warn,
                          parent_control="", parent_name=None) -> bool:
    """Populate a DmeAimAtBone element (proc_type LOOKAT). ``aim_target_name`` is the
    ``{base}_lookat[idx]`` DmeAttachment for a non-zero ``lookat_offset``, else the
    driver joint name (aimed at directly).
    ``parent_control`` is the DMX joint name of the helper's skeleton parent and
    ``parent_name`` its data-bone name - the compiler places ``basePos`` by this
    bone's matrix, so both must reference the same bone (an empty ``parentBone``
    collapses every aim bone onto the origin). Returns ``False`` and warns when
    there is no aim target."""
    helper_name = entry.helper_bone

    if not aim_target_name:
        warn(utils.get_id('exporter_warn_procbone_no_target', True).format(helper_name))
        return False

    if parent_name is None:
        helper_bone = arm.data.bones.get(helper_name)
        parent_name = (helper_bone.parent.name if helper_bone and helper_bone.parent
                       else entry.driver_bone)
    bp = basepos_local(arm, helper_name, parent_name)

    aim = axes_to_vec(entry.lookat_aim_axis)
    up  = axes_to_vec(entry.lookat_up_axis)

    elem["aimTarget"]  = aim_target_name
    elem["parentBone"] = parent_control or ""  # must match the bone basePos was measured against
    elem["aimVector"]  = datamodel.Vector3(list(aim))
    elem["upVector"]   = datamodel.Vector3(list(up))
    elem["basePos"]    = datamodel.Vector3([bp.x * armature_scale[0],
                                            bp.y * armature_scale[1],
                                            bp.z * armature_scale[2]])
    return True


# -----------------------------------------------------------------------------
# Import (reader) - reconstructs vs.proc_bones entries + a slot action from a
# DME model-DMX (DmeQuatInterpBone / DmeAimAtBone) or a VRD text block.
#
# This is the inverse of the writers above. On a fresh import every bone's
# export offset is identity, so the writer collapses to
#     d_mat    = d_rest_rot @ dq          (driver, rotation only)
#     h_export = h_rest_rot @ h_mat       (helper, rotation + translation)
# which we invert here to recover the per-trigger local pose (dq / h_mat) that
# the action must keyframe, so a round-trip re-export reproduces the same DMX.
# -----------------------------------------------------------------------------


def _blender_quat(dq):
    """datamodel.Quaternion [x, y, z, w] -> mathutils.Quaternion (w, x, y, z)."""
    return Quaternion((dq[3], dq[0], dq[1], dq[2]))


def _axes_from_vec(vec):
    """Reverse of ``axes_to_vec``: pick the axis enum flags a (near-)unit vector
    points along. Handles single axes and diagonal combinations."""
    if vec is None:
        return {'+X'}
    x, y, z = vec[0], vec[1], vec[2]
    out = set()
    if x >  0.5: out.add('+X')
    elif x < -0.5: out.add('-X')
    if y >  0.5: out.add('+Y')
    elif y < -0.5: out.add('-Y')
    if z >  0.5: out.add('+Z')
    elif z < -0.5: out.add('-Z')
    return out or {'+X'}


def _bone_resolver(armature):
    """Map an exported/raw bone-name string back to a data-bone (name-only)."""
    by_name = {b.name: b for b in armature.data.bones}
    by_export = {utils.get_bone_exportname(b): b for b in armature.data.bones}
    by_lower = {b.name.lower(): b for b in armature.data.bones}

    def resolve(name):
        if not name:
            return None
        return by_name.get(name) or by_export.get(name) or by_lower.get(name.lower())
    return resolve


def _ensure_proc_action(armature):
    """Create the single per-armature proc action (one layer + strip). Each
    TRIGGER helper then gets its own slot/channelbag inside it."""
    import bpy
    action = bpy.data.actions.new(armature.name)
    action.use_fake_user = True
    layer = action.layers.new("Layer")
    layer.strips.new(type='KEYFRAME')
    return action


def _add_trigger_slot(action, armature, helper_name, driver_name, triggers):
    """Add a slot (named after the helper) to ``action`` whose N frames pose the
    driver at each trigger rotation and the helper at the matching target pose,
    plus a per-frame proc_tolerance curve. ``triggers`` is a list of
    ``(tol_rad, d_mat_quat, h_export_quat, h_export_trans)`` in absolute-local
    space. Returns the slot name to store in ``entry.action_slot_name``."""
    d_rest_inv = bone_rest_rot(armature, driver_name).to_quaternion().inverted()
    h_rest_inv = bone_rest_rot(armature, helper_name).inverted()

    slot = action.slots.new(id_type='OBJECT', name=helper_name)
    bag = action.layers[0].strips[0].channelbag(slot, ensure=True)

    def curve(dp, idx):
        return bag.fcurves.new(dp, index=idx)

    d_rot = [curve(f'pose.bones["{driver_name}"].rotation_quaternion', i) for i in range(4)]
    h_rot = [curve(f'pose.bones["{helper_name}"].rotation_quaternion', i) for i in range(4)]
    h_loc = [curve(f'pose.bones["{helper_name}"].location', i) for i in range(3)]
    tol_fc = curve(f'bones["{driver_name}"].vs.proc_tolerance', 0)

    for t, (tol, d_mat_q, h_exp_q, h_exp_t) in enumerate(triggers):
        frame = t + 1
        dq = d_rest_inv @ d_mat_q
        h_export = h_exp_q.to_matrix().to_4x4()
        h_export.translation = h_exp_t
        h_mat = h_rest_inv @ h_export
        hq = h_mat.to_quaternion()
        hloc = h_mat.to_translation()
        for i in range(4):
            d_rot[i].keyframe_points.insert(frame, dq[i], options={'FAST'})
            h_rot[i].keyframe_points.insert(frame, hq[i], options={'FAST'})
        for i in range(3):
            h_loc[i].keyframe_points.insert(frame, hloc[i], options={'FAST'})
        tol_fc.keyframe_points.insert(frame, tol, options={'FAST'})

    for fc in (*d_rot, *h_rot, *h_loc, tol_fc):
        fc.update()

    return slot.name_display


def _add_trigger_entry(action, armature, helper_name, driver_name, triggers):
    """Append a TRIGGER proc-bone entry bound to a new slot in the shared action."""
    slot_name = _add_trigger_slot(action, armature, helper_name, driver_name, triggers)
    entry = armature.data.vs.proc_bones.add()
    entry.proc_type = 'TRIGGER'
    entry.helper_bone = helper_name
    entry.driver_bone = driver_name
    entry.action = action
    entry.action_slot_name = slot_name
    entry.use_manual_frame_range = True
    entry.trigger_frame_start = 1
    entry.trigger_frame_end = max(1, len(triggers))
    return entry


def _add_lookat_entry(armature, helper_name, driver_name, aim_vec, up_vec, offset):
    """Append a LOOKAT proc-bone entry."""
    entry = armature.data.vs.proc_bones.add()
    entry.proc_type = 'LOOKAT'
    entry.helper_bone = helper_name
    entry.driver_bone = driver_name
    entry.lookat_aim_axis = _axes_from_vec(aim_vec)
    entry.lookat_up_axis = _axes_from_vec(up_vec)
    entry.lookat_offset = offset
    return entry


def import_proc_bones_from_dmx_elements(elements, armature, scene, attachments=None):
    """Reconstruct proc-bone entries from DmeQuatInterpBone / DmeAimAtBone joints.

    ``elements`` is a list of ``(elem, bone_name)`` where ``bone_name`` is the
    helper's resolved data-bone name (from ``smd.boneIDs``). ``attachments`` maps
    a ``{name: (parent_bone_name, raw_translation)}`` for ``*_lookat`` aim targets.
    Returns ``(imported_count, missing_names)``."""
    resolve = _bone_resolver(armature)
    by_name = {b.name: b for b in armature.data.bones}
    arm_scale = armature.matrix_world.to_scale()

    proc_coll = armature.data.vs.proc_bones
    existing = {e.helper_bone for e in proc_coll}

    count = 0
    missing: list = []
    proc_action = None  # single per-armature action, created on first TRIGGER

    for elem, helper_bone_name in elements:
        helper = by_name.get(helper_bone_name) if helper_bone_name else None
        if helper is None:
            helper = resolve(elem.name)
        if helper is None:
            missing.append(elem.name or "<unnamed>")
            continue
        helper_name = helper.name
        if helper_name in existing:
            continue

        if elem.type == "DmeQuatInterpBone":
            driver = resolve(elem.get("controlBone"))
            if driver is None:
                missing.append(elem.get("controlBone") or f"<{helper_name}: no controlBone>")
                continue
            tolerances = list(elem.get("tolerances") or [])
            trig_rots  = list(elem.get("triggerRotations") or [])
            tgt_rots   = list(elem.get("targetRotations") or [])
            tgt_pos    = list(elem.get("targetPositions") or [])
            n = min(len(tolerances), len(trig_rots), len(tgt_rots))
            if n == 0:
                continue
            triggers = []
            for i in range(n):
                p = tgt_pos[i] if i < len(tgt_pos) else (0.0, 0.0, 0.0)
                h_t = Vector((p[0] / arm_scale[0], p[1] / arm_scale[1], p[2] / arm_scale[2]))
                triggers.append((radians(float(tolerances[i])),
                                 _blender_quat(trig_rots[i]),
                                 _blender_quat(tgt_rots[i]), h_t))
            if proc_action is None:
                proc_action = _ensure_proc_action(armature)
            _add_trigger_entry(proc_action, armature, helper_name, driver.name, triggers)
            existing.add(helper_name)
            count += 1

        elif elem.type == "DmeAimAtBone":
            aim_target = elem.get("aimTarget")
            driver = resolve(aim_target)
            offset = Vector((0.0, 0.0, 0.0))
            if driver is None and attachments and aim_target in attachments:
                parent_name, raw = attachments[aim_target]
                driver = resolve(parent_name)
                offset = Vector((raw[0] / arm_scale[0], raw[1] / arm_scale[1],
                                 raw[2] / arm_scale[2]))
            if driver is None:
                missing.append(aim_target or f"<{helper_name}: no aimTarget>")
                continue
            _add_lookat_entry(armature, helper_name, driver.name,
                              elem.get("aimVector"), elem.get("upVector"), offset)
            existing.add(helper_name)
            count += 1

    if count:
        proc_coll_len = len(proc_coll)
        armature.data.vs.proc_bones_index = proc_coll_len - 1
    return count, missing


def import_proc_bones_from_vrd_content(content, armature, scene):
    """Reconstruct proc-bone entries from a VRD text block (``<helper>`` /
    ``<trigger>`` / ``<aimconstraint>`` lines, as written by
    ``PrefabExporter._write_proc_vrd``). Returns ``(imported_count, missing_names)``."""
    resolve = _bone_resolver(armature)
    scale = scene.vs.world_scale * armature.matrix_world.to_scale().x
    if abs(scale) < 1e-9:
        scale = 1.0

    proc_coll = armature.data.vs.proc_bones
    existing = {e.helper_bone for e in proc_coll}

    # First pass: parse text into normalized blocks.
    blocks: list = []
    cur = None
    for raw in content.splitlines():
        parts = raw.split()
        if not parts:
            continue
        tag = parts[0].lower()
        rest = parts[1:]
        if tag == '<helper>' and len(rest) >= 4:
            cur = {'type': 'TRIGGER', 'helper': rest[0], 'driver': rest[3], 'triggers': []}
            blocks.append(cur)
        elif tag == '<aimconstraint>' and len(rest) >= 3:
            cur = {'type': 'LOOKAT', 'helper': rest[0], 'target': rest[2],
                   'aim': None, 'up': None}
            blocks.append(cur)
        elif tag == '<trigger>' and cur and cur['type'] == 'TRIGGER' and len(rest) >= 10:
            v = [float(x) for x in rest[:10]]
            d_q = Euler((radians(v[1]), radians(v[2]), radians(v[3])), 'XYZ').to_quaternion()
            h_q = Euler((radians(v[4]), radians(v[5]), radians(v[6])), 'XYZ').to_quaternion()
            h_t = Vector((v[7] / scale, v[8] / scale, v[9] / scale))
            cur['triggers'].append((radians(v[0]), d_q, h_q, h_t))
        elif tag == '<aimvector>' and cur and len(rest) >= 3:
            cur['aim'] = Vector((float(rest[0]), float(rest[1]), float(rest[2])))
        elif tag == '<upvector>' and cur and len(rest) >= 3:
            cur['up'] = Vector((float(rest[0]), float(rest[1]), float(rest[2])))

    count = 0
    missing: list = []
    proc_action = None  # single per-armature action, created on first TRIGGER
    for blk in blocks:
        helper = resolve(blk['helper'])
        if helper is None:
            missing.append(blk['helper'])
            continue
        if helper.name in existing:
            continue
        if blk['type'] == 'TRIGGER':
            driver = resolve(blk['driver'])
            if driver is None:
                missing.append(blk['driver'])
                continue
            if not blk['triggers']:
                continue
            if proc_action is None:
                proc_action = _ensure_proc_action(armature)
            _add_trigger_entry(proc_action, armature, helper.name, driver.name, blk['triggers'])
        else:
            # VRD aim targets are `{driver}_lookat[idx]` attachment names (defined
            # by a QC $attachment we don't parse); strip that suffix to recover the
            # driver bone. The per-target offset lives in the attachment, so it is
            # lost here - default to a zero offset (aim directly at the driver).
            target = blk['target']
            driver = resolve(target)
            if driver is None:
                base = _re.sub(r'_lookat\d*$', '', target)
                driver = resolve(base)
            if driver is None:
                missing.append(target)
                continue
            _add_lookat_entry(armature, helper.name, driver.name,
                              blk['aim'], blk['up'], Vector((0.0, 0.0, 0.0)))
        existing.add(helper.name)
        count += 1

    if count:
        armature.data.vs.proc_bones_index = len(proc_coll) - 1
    return count, missing
