__all__ = ['ValveSource_BoneProps', 'ValveSource_ArmatureProps', '_on_armature_data_updated', '_on_blend_load_refresh_hitbox_snapshot']

import bpy, math
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       FloatProperty, CollectionProperty)
from bpy.app.handlers import persistent
from mathutils import Vector, Matrix
from ..utils import get_id
from .items import ProcBoneEntry, ArmatureItemEntry, HitboxEntry, PrefabItem, _proc_entry_invalidate_cache, refresh_hitbox_snapshot
from .mixins import JiggleBoneProps

_propagation_active: set = set()
_arm_was_in_edit: set = set()  # armature object names that were in EDIT mode last handler call


def _on_hitboxes_index_changed(self, context):
    refresh_hitbox_snapshot(self)


def _find_owning_bone(self):
    """Return the bpy.types.Bone whose .vs matches self, using pointer comparison."""
    self_ptr = self.as_pointer()
    for b in self.id_data.bones:
        if b.vs.as_pointer() == self_ptr:
            return b
    return None


def _sync_arm_to_local(self, context):
    bone = _find_owning_bone(self)
    if bone is None:
        return
    arm_vec = Vector((self.export_location_offset_arm_x,
                      self.export_location_offset_arm_y,
                      self.export_location_offset_arm_z))
    rot = bone.matrix_local.to_3x3()
    local_vec = rot.inverted() @ arm_vec
    self.export_location_offset_x = local_vec.x
    self.export_location_offset_y = local_vec.y
    self.export_location_offset_z = local_vec.z


def _sync_local_to_arm(self, context):
    if not self.location_offset_in_armature_space:
        return
    bone = _find_owning_bone(self)
    if bone is None:
        return
    local_vec = Vector((self.export_location_offset_x,
                        self.export_location_offset_y,
                        self.export_location_offset_z))
    rot = bone.matrix_local.to_3x3()
    arm_vec = rot @ local_vec
    self.export_location_offset_arm_x = arm_vec.x
    self.export_location_offset_arm_y = arm_vec.y
    self.export_location_offset_arm_z = arm_vec.z


def _compute_rotation_sync(cur_pb, target_pb):
    """Compute the adjusted export rotation offset for cur_pb to match target_pb's orientation.
    Returns an Euler. Accounts for rest-pose differences via R_cur^-1 @ R_tgt @ EX_tgt."""
    target_vs = target_pb.bone.vs
    tx = 0.0 if target_vs.ignore_rotation_offset else target_vs.export_rotation_offset_x
    ty = 0.0 if target_vs.ignore_rotation_offset else target_vs.export_rotation_offset_y
    tz = 0.0 if target_vs.ignore_rotation_offset else target_vs.export_rotation_offset_z
    target_off = (Matrix.Rotation(tz, 3, 'Z') @
                  Matrix.Rotation(ty, 3, 'Y') @
                  Matrix.Rotation(tx, 3, 'X'))
    R_cur = cur_pb.bone.matrix_local.to_3x3()
    R_tgt = target_pb.bone.matrix_local.to_3x3()
    return (R_cur.inverted() @ R_tgt @ target_off).to_4x4().to_euler('XYZ')


def _apply_rotation_sync(cur_pb, target_pb):
    """Set export rotation offset via attribute access (triggers update callbacks for chain propagation)."""
    euler = _compute_rotation_sync(cur_pb, target_pb)
    bvs = cur_pb.bone.vs
    bvs.export_rotation_offset_x = euler.x
    bvs.export_rotation_offset_y = euler.y
    bvs.export_rotation_offset_z = euler.z


def _sync_rotation_from_target(self, context):
    """Update callback for rotation_copy_target: sync this bone from its target."""
    if not self.rotation_copy_target:
        return
    arm_ob = context.active_object
    if arm_ob is None:
        return
    cur_pb = context.active_pose_bone
    if cur_pb is None:
        return
    target_pb = arm_ob.pose.bones.get(self.rotation_copy_target)
    if target_pb is None:
        return
    _apply_rotation_sync(cur_pb, target_pb)


def _propagate_rotation_to_dependents(self, context):
    """Update callback for export_rotation_offset_x/y/z: push changes to bones copying this one."""
    arm_ob = context.active_object
    if arm_ob is None or arm_ob.type != 'ARMATURE':
        return
    # Use as_pointer() because Blender may create a new Python wrapper on each .vs access,
    # making identity comparison (is) unreliable.
    self_ptr = self.as_pointer()
    src_name = None
    for b in arm_ob.data.bones:
        if b.vs.as_pointer() == self_ptr:
            src_name = b.name
            break
    if src_name is None or src_name in _propagation_active:
        return
    src_pb = arm_ob.pose.bones.get(src_name)
    if src_pb is None:
        return
    _propagation_active.add(src_name)
    try:
        for pb in arm_ob.pose.bones:
            if pb.bone.vs.rotation_copy_target == src_name:
                _apply_rotation_sync(pb, src_pb)
    finally:
        _propagation_active.discard(src_name)


@persistent
def _on_blend_load_refresh_hitbox_snapshot(filepath):
    """Refresh the hitbox propagation snapshot for every armature after a blend file loads.
    The snapshot is module-level Python state and is cleared on load, so without this the
    delta-propagation system would be working from a stale baseline on first edit."""
    for arm_data in bpy.data.armatures:
        avs = getattr(arm_data, 'vs', None)
        if avs is None:
            continue
        if avs.hitboxes and 0 <= avs.hitboxes_index < len(avs.hitboxes):
            refresh_hitbox_snapshot(avs)


@persistent
def _on_armature_data_updated(scene, depsgraph):
    """Re-sync rotation copy targets when an armature transitions out of edit mode."""
    for arm_ob in bpy.data.objects:
        if arm_ob.type != 'ARMATURE':
            continue
        in_edit = (arm_ob.mode == 'EDIT')
        was_edit = arm_ob.name in _arm_was_in_edit
        if in_edit:
            _arm_was_in_edit.add(arm_ob.name)
            continue
        if not was_edit:
            continue
        # Transitioned out of edit mode - re-sync all copy targets.
        _arm_was_in_edit.discard(arm_ob.name)
        for _ in range(4):
            changed = False
            for pb in arm_ob.pose.bones:
                bvs = pb.bone.vs
                if not bvs.rotation_copy_target:
                    continue
                target_pb = arm_ob.pose.bones.get(bvs.rotation_copy_target)
                if target_pb is None:
                    continue
                euler = _compute_rotation_sync(pb, target_pb)
                if (abs(bvs.export_rotation_offset_x - euler.x) > 1e-7 or
                        abs(bvs.export_rotation_offset_y - euler.y) > 1e-7 or
                        abs(bvs.export_rotation_offset_z - euler.z) > 1e-7):
                    bvs['export_rotation_offset_x'] = euler.x
                    bvs['export_rotation_offset_y'] = euler.y
                    bvs['export_rotation_offset_z'] = euler.z
                    changed = True
            if not changed:
                break


class ValveSource_BoneProps(JiggleBoneProps, bpy.types.PropertyGroup):
    export_name : StringProperty(name=get_id("exportname"), description=get_id("exportname_tip"), maxlen=256)

    ignore_rotation_offset : BoolProperty(name=get_id('prop_ignore_rotation_offset'), description=get_id('prop_ignore_rotation_offset_tip'), default=False)
    export_rotation_offset_x : FloatProperty(name=get_id('prop_rotation_x'), description=get_id('prop_rotation_x_tip'), unit='ROTATION', default=math.radians(0), precision=4, min=-360, max=360, update=_propagate_rotation_to_dependents)
    export_rotation_offset_y : FloatProperty(name=get_id('prop_rotation_y'), description=get_id('prop_rotation_y_tip'), unit='ROTATION', default=math.radians(0), precision=4, min=-360, max=360, update=_propagate_rotation_to_dependents)
    export_rotation_offset_z : FloatProperty(name=get_id('prop_rotation_z'), description=get_id('prop_rotation_z_tip'), unit='ROTATION', default=math.radians(0), precision=4, min=-360, max=360, update=_propagate_rotation_to_dependents)
    rotation_copy_target : StringProperty(name=get_id('prop_rotation_copy_target'), description=get_id('prop_rotation_copy_target_tip'), default="", update=_sync_rotation_from_target)

    ignore_location_offset : BoolProperty(name=get_id('prop_ignore_location_offset'), description=get_id('prop_ignore_location_offset_tip'), default=True)
    export_location_offset_x : FloatProperty(name=get_id('prop_location_x'), description=get_id('prop_location_x_tip'), default=0, precision=4)
    export_location_offset_y : FloatProperty(name=get_id('prop_location_y'), description=get_id('prop_location_y_tip'), default=0, precision=4)
    export_location_offset_z : FloatProperty(name=get_id('prop_location_z'), description=get_id('prop_location_z_tip'), default=0, precision=4)

    location_offset_in_armature_space : BoolProperty(name=get_id('prop_location_offset_space'), description=get_id('prop_location_offset_space_tip'), default=False, update=_sync_local_to_arm)
    export_location_offset_arm_x : FloatProperty(name=get_id('prop_location_arm_x'), description=get_id('prop_location_arm_x_tip'), default=0, precision=4, update=_sync_arm_to_local)
    export_location_offset_arm_y : FloatProperty(name=get_id('prop_location_arm_y'), description=get_id('prop_location_arm_y_tip'), default=0, precision=4, update=_sync_arm_to_local)
    export_location_offset_arm_z : FloatProperty(name=get_id('prop_location_arm_z'), description=get_id('prop_location_arm_z_tip'), default=0, precision=4, update=_sync_arm_to_local)

    proc_tolerance : FloatProperty(
        name=get_id('prop_pose_bone_proc_tolerance'),
        description=get_id('prop_pose_bone_proc_tolerance_tip'),
        default=math.pi / 2, min=0.01, max=math.pi, subtype='ANGLE', precision=2,
        update=_proc_entry_invalidate_cache,
    )


class ValveSource_ArmatureProps(bpy.types.PropertyGroup):
    implicit_zero_bone : BoolProperty(name=get_id("dummy_bone"), default=False, description=get_id("dummy_bone_tip"))
    arm_modes = (
        ('CURRENT', get_id("action_slot_current"), get_id("action_slot_selection_current_tip")),
        ('FILTERED', get_id("slot_filter"), get_id("slot_filter_tip")),
        ('FILTERED_ACTIONS', get_id("action_filter"), get_id("action_selection_filter_tip")),
    )

    reset_pose_per_anim : BoolProperty(name=get_id('prop_reset_pose_per_anim'), description=get_id('prop_reset_pose_per_anim_tip'), default=True)

    action_selection : EnumProperty(name=get_id("action_selection_mode"), items=arm_modes, description=get_id("action_selection_mode_tip"), default='FILTERED')

    hitboxes       : CollectionProperty(type=HitboxEntry)
    hitboxes_index : IntProperty(default=-1, update=_on_hitboxes_index_changed)
    hboxset_name          : StringProperty(name=get_id('prop_hitbox_hboxset'), description=get_id('prop_hitbox_hboxset_tip'), default='')
    arm_attachment_entries : CollectionProperty(type=ArmatureItemEntry)
    arm_attachment_index : IntProperty(default=-1)
    arm_jigglebone_entries : CollectionProperty(type=ArmatureItemEntry)
    arm_jigglebone_index : IntProperty(default=-1)
    ignore_bone_exportnames : BoolProperty(name=get_id("ignore_bone_exportnames"), description=get_id("ignore_bone_exportnames_tip"))
    bone_direction_naming_left : StringProperty(name=get_id('prop_bone_dir_left'), description=get_id('prop_bone_dir_left_tip'), default='L')
    bone_direction_naming_right : StringProperty(name=get_id('prop_bone_dir_right'), description=get_id('prop_bone_dir_right_tip'), default='R')
    bone_name_startcount : IntProperty(name=get_id('prop_bone_name_startcount'), description=get_id('prop_bone_name_startcount_tip'), default=1, min=0, soft_max=10)

    proc_bones       : CollectionProperty(type=ProcBoneEntry)
    proc_bones_index : IntProperty(default=-1)

    prefab_items : CollectionProperty(type=PrefabItem)
