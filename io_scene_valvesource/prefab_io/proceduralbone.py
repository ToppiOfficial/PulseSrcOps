"""Procedural (helper) bone serialization - DME export for the model DMX.

Converts the armature's ``vs.proc_bones`` entries (``ProcBoneEntry``) into the
two DME skeleton-joint element types PulseMDL reads directly out of the model
``.dmx``:

* **DmeQuatInterpBone** - a ``$driverbone`` / VRD ``<helper>`` (proc_type TRIGGER)
* **DmeAimAtBone**       - a ``$driverlookat`` / VRD ``<aimconstraint>`` (proc_type LOOKAT)

Both are ``DmeJoint`` subclasses: the helper bone's own joint element is promoted
to one of these types and given the extra attributes, so no separate ``.vrd`` is
needed in DME prefab mode. This mirrors ``jigglebone.write_dme_attrs`` /
``hitbox.write_dme_attrs``.

The per-trigger transform math is **shared** with the text/VRD writer
(``export_smd.PrefabExporter._write_proc_vrd``) via ``build_trigger_transforms``
and the ``*_off_mat`` / ``bone_rest_rot`` helpers below, so the two export paths
can never drift. The VRD path converts the returned matrices to Euler degrees;
this module converts them to DMX quaternions.

Source 1 only - the caller guards on ``dme_mode and not source2``.
"""

from math import degrees

from mathutils import Matrix, Vector

from .. import utils, datamodel


# -----------------------------------------------------------------------------
# Shared transform helpers (also used by export_smd._write_proc_vrd)
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
    no parent).

    ``_build_proc_triggers`` returns ``matrix_basis`` deltas (identity at rest) for
    both the driver and the helper, but the compiler's ``triggerRotations`` /
    ``targetRotations`` are absolute parent-relative local rotations. The rest
    orientation is baked in here so a delta becomes an absolute local rotation."""
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

    Returns a list of ``(d_mat, h_export, tol, dq, dloc)`` (empty if the entry has
    no triggers). ``d_mat`` / ``h_export`` are absolute local matrices; ``dq`` /
    ``dloc`` are the raw driver delta quat / loc kept for the near-duplicate
    warning. Shared verbatim by the VRD text writer so the two paths agree on the
    offset convention ``parent_off.inv @ rest_local @ delta @ own_off``."""
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
        # Bake the helper's parent-relative rest orientation in, exactly like the
        # driver above: `hq` is a rest-relative delta, but targetRotations must be
        # absolute local. Without this, helpers whose rest orientation differs from
        # their parent (e.g. any using an export rotation offset / bone-target
        # forward) export rotated by the missing rest rotation.
        h_export = h_parent_off.inverted() @ h_rest_rot @ h_mat @ h_off
        out.append((d_mat, h_export, tol, dq, dloc))
    return out


# -----------------------------------------------------------------------------
# DME (model-DMX / PulseMDL)
# -----------------------------------------------------------------------------

def write_dme_quatinterp_attrs(elem, arm, entry, entry_idx, scene, control_bone,
                               armature_scale, warn, parent_name=None) -> bool:
    """Populate a DmeQuatInterpBone element (proc_type TRIGGER). ``control_bone``
    is the driver's *DMX joint name* (resolved via ``exportable_boneNames`` -
    Decision D). Returns ``False`` (leaving the caller to keep a plain DmeJoint)
    and warns when the entry can't produce valid data.

    Position encoding: the compiler computes ``pos[t] = (basePos + targetPositions[t]) * $scale``.
    ``basePos`` carries the helper's rest position relative to its parent (the
    bulk offset, same as the VRD ``<basepos>`` line); ``targetPositions[t]`` carries
    only the per-trigger *local delta from rest* (near-zero for rotation-only
    helpers, same as the VRD per-trigger position). Both are scaled per-axis by
    ``armature_scale`` to match the bone DmeTransform positions; world_scale is
    *not* pre-multiplied because the compiler applies ``$scale`` on top."""
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

    # basePos = helper rest position relative to its DMX skeleton parent (the
    # nearest exportable ancestor, resolved by the caller). Matches VRD <basepos>.
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
    """Populate a DmeAimAtBone element (proc_type LOOKAT). ``aim_target_name`` is
    the ``{base}_lookat[idx]`` DmeAttachment name the caller embeds at a non-zero
    ``lookat_offset`` in the driver bone's local space; for a zero offset (or a
    non-exportable driver) it is the driver joint name and the bone is aimed at
    directly.
    ``parent_control`` is the *DMX joint name* of the helper's skeleton parent
    (the nearest exportable ancestor) and ``parent_name`` its data-bone name - the
    compiler transforms ``basePos`` by this bone's matrix to place the aim bone, so
    both must reference the same bone (an empty ``parentBone`` resolves to the root
    and collapses every aim bone onto the model origin). Returns ``False`` and
    warns when there is no aim target."""
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
    # basePos is parent-relative, so parentBone must name the same bone it was
    # measured against (mirrors the VRD <aimconstraint> parent field).
    elem["parentBone"] = parent_control or ""
    elem["aimVector"]  = datamodel.Vector3(list(aim))
    elem["upVector"]   = datamodel.Vector3(list(up))
    elem["basePos"]    = datamodel.Vector3([bp.x * armature_scale[0],
                                            bp.y * armature_scale[1],
                                            bp.z * armature_scale[2]])
    return True
