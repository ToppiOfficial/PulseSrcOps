__all__ = [
    'ValveSource_FloatMapRemap',
    'PrefabItem',
    'FlexControllerItem',
    'DmeFlexRuleItem',
    'DmeDeltaNameOverride',
    'VertexAnimation',
    'ArmatureItemEntry',
    'HitboxEntry',
    'ProcBoneEntry',
    'AttachmentDisplayMeshItem',
]

import bpy, re, math as _math
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       FloatProperty, FloatVectorProperty, PointerProperty)
from ..utils import get_id, hitbox_group
from .. import procbones_sim as _procbones_sim


def update_sanitize_name(self, context):
    legal_name = re.sub(r'[^a-z0-9]', '_', self.controller_name.lower())
    if self.controller_name != legal_name:
        self.controller_name = legal_name


def _proc_entry_invalidate_cache(self, context):
    arm_ob = context.object
    if arm_ob and arm_ob.type == 'ARMATURE':
        _procbones_sim.invalidate_proc_cache(arm_ob.name)
    else:
        _procbones_sim._proc_trigger_cache.clear()


class ValveSource_FloatMapRemap(bpy.types.PropertyGroup):
    group : StringProperty(name="Group name", description=get_id("prop_float_map_group_tip"), default="")
    min : FloatProperty(name="Min", description="Maps to 0.0", default=0.0)
    max : FloatProperty(name="Max", description="Maps to 1.0", default=1.0)


class AttachmentDisplayMeshItem(bpy.types.PropertyGroup):
    mesh : PointerProperty(
        type=bpy.types.Object,
        name=get_id('prop_attachment_display_mesh'),
        description=get_id('prop_attachment_display_mesh_tip'),
        poll=lambda self, ob: ob.type == 'MESH',
    )
    color : FloatVectorProperty(
        name=get_id('prop_attachment_display_mesh_color'),
        description=get_id('prop_attachment_display_mesh_color_tip'),
        subtype='COLOR_GAMMA', size=4,
        default=(0.3, 0.9, 1.0, 0.45), min=0.0, max=1.0,
    )


# Prefab types that can be auto-exported alongside an armature. The order here is
# the order they appear in the exportables list.
prefab_type_items = [
    ('JIGGLEBONES',   "Jigglebones",   ""),
    ('ATTACHMENTS',   "Attachments",   ""),
    ('HITBOXES',      "Hitboxes",      ""),
    ('PROCEDURAL',    "Procedural",    ""),
]


class PrefabItem(bpy.types.PropertyGroup):
    prefab_type : EnumProperty(items=prefab_type_items, default='JIGGLEBONES')
    export : BoolProperty(name="Export", description=get_id("prop_prefab_export_tip"), default=True)
    filepath: StringProperty(name="Filepath", description=get_id("prop_prefab_filepath_tip"), subtype='FILE_PATH', options={'PATH_SUPPORTS_BLEND_RELATIVE'})


class FlexControllerItem(bpy.types.PropertyGroup):
    controller_name: StringProperty(name='Controller Name', description=get_id("prop_controller_name_tip"), update=update_sanitize_name)
    raw_delta_name : StringProperty(name='Delta Name', description=get_id("prop_delta_name_tip"))
    shapekey : StringProperty(name='ShapeKey', description=get_id("prop_flexctrl_shapekey_tip"))
    eyelid : BoolProperty(name='Eyelid', description=get_id("prop_eyelid_tip"))
    stereo : BoolProperty(name='Stereo', description=get_id("prop_stereo_tip"))
    flexgroup : EnumProperty(name='Flex Type', description=get_id("prop_flex_type_tip"), items=[
        ('DEFAULT', 'DEFAULT', ''),
        ('EYES', 'EYES', ''),
        ('EYELID', 'EYELID', ''),
        ('BROW', 'BROW', ''),
        ('MOUTH', 'MOUTH', ''),
        ('MISC', 'MISC', ''),
        ('CHEEK', 'CHEEK', ''),
        ('CUSTOM', 'CUSTOM', ''),
    ], default='DEFAULT')
    flexgroup_custom : StringProperty(name='Custom Flex Group', description=get_id("prop_flex_group_custom_tip"))
    flex_min : FloatProperty(name='Flex Min', description=get_id("prop_flex_min_tip"), default=0.0, soft_min=-1.0, soft_max=1.0, precision=3)
    flex_max : FloatProperty(name='Flex Max', description=get_id("prop_flex_max_tip"), default=1.0, soft_min=0.0, soft_max=2.0, precision=3)

    def resolved_flexgroup(self) -> str:
        """Return the flexgroup string to export. Always non-empty: DEFAULT -> 'default',
        CUSTOM -> the custom string (falling back to 'default' if blank), else the lowercased enum."""
        if self.flexgroup == 'CUSTOM':
            custom = self.flexgroup_custom.strip().lower()
            return custom if custom else 'default'
        if self.flexgroup == 'DEFAULT':
            return 'default'
        return self.flexgroup.lower()


class DmeDeltaNameOverride(bpy.types.PropertyGroup):
    shapekey   : StringProperty(name='Shape Key', description=get_id("prop_delta_override_shapekey_tip"))
    delta_name : StringProperty(name='Delta Name', description=get_id("prop_delta_override_name_tip"))
    split_lr   : BoolProperty(name='Split to L/R', description=get_id("prop_delta_override_split_tip"), default=False)


class DmeFlexRuleItem(bpy.types.PropertyGroup):
    rule_type: EnumProperty(
        name="Rule Type",
        description=get_id("prop_dme_flex_rule_type_tip"),
        items=[
            ('EXPRESSION',  "Expression",  get_id("prop_dme_flex_rule_expression_tip")),
            ('PASSTHROUGH', "Pass Through", get_id("prop_dme_flex_rule_passthrough_tip")),
            ('LOCALVAR',    "Local Var",    get_id("prop_dme_flex_rule_localvar_tip")),
            ('DOMINATION',  "Domination",   get_id("prop_dme_flex_rule_domination_tip")),
            ('CORRECTIVE',  "Corrective",   get_id("prop_dme_flex_rule_corrective_tip")),
        ],
        default='EXPRESSION',
    )
    name: StringProperty(name="Name", description=get_id("prop_dme_flex_rule_name_tip"))
    expression: StringProperty(name="Expression", description=get_id("prop_dme_flex_rule_expr_tip"))
    components: StringProperty(name="Components", description=get_id("prop_dme_corrective_components_tip"))
    dominator_names: StringProperty(name="Dominators", description=get_id("prop_dme_dominator_names_tip"))
    suppressed_names: StringProperty(name="Suppressed", description=get_id("prop_dme_suppressed_names_tip"))


class VertexAnimation(bpy.types.PropertyGroup):
    name : StringProperty(name="Name", description=get_id("prop_vertex_anim_name_tip"), default="VertexAnim")
    start : IntProperty(name="Start", description=get_id("vca_start_tip"), default=0)
    end : IntProperty(name="End", description=get_id("vca_end_tip"), default=250)
    export_sequence : BoolProperty(name=get_id("vca_sequence"), description=get_id("vca_sequence_tip"), default=True)


class ArmatureItemEntry(bpy.types.PropertyGroup):
    obj : PointerProperty(type=bpy.types.Object)
    bone_name : StringProperty()


# ---- Hitbox propagation state -----------------------------------------------

_hb_snapshot_key : tuple | None = None   # (arm_data_ptr, entry_index)
_hb_snapshot     : dict         = {}     # field -> value | tuple
_hb_propagating  : bool         = False


def refresh_hitbox_snapshot(arm_vs) -> None:
    global _hb_snapshot_key, _hb_snapshot
    arm_data = arm_vs.id_data
    idx      = arm_vs.hitboxes_index
    _hb_snapshot_key = (arm_data.as_pointer(), idx)
    if 0 <= idx < len(arm_vs.hitboxes):
        e = arm_vs.hitboxes[idx]
        _hb_snapshot = {
            'vec_min':  tuple(e.vec_min),
            'vec_max':  tuple(e.vec_max),
            'rotation': tuple(e.rotation),
            'scale':    e.scale,
        }
    else:
        _hb_snapshot = {}


def _hb_arm_vs_and_idx(self):
    arm_data = getattr(self, 'id_data', None)
    if arm_data is None:
        return None, -1
    arm_vs = getattr(arm_data, 'vs', None)
    if arm_vs is None:
        return None, -1
    ptr = self.as_pointer()
    for i, e in enumerate(arm_vs.hitboxes):
        if e.as_pointer() == ptr:
            return arm_vs, i
    return arm_vs, -1


def _hb_propagate(self, context, field: str, is_vec: bool) -> None:
    global _hb_propagating, _hb_snapshot_key, _hb_snapshot
    if _hb_propagating:
        return

    arm_vs, my_idx = _hb_arm_vs_and_idx(self)
    if arm_vs is None or my_idx != arm_vs.hitboxes_index:
        return

    key     = (self.id_data.as_pointer(), my_idx)
    new_val = tuple(getattr(self, field)) if is_vec else getattr(self, field)

    if _hb_snapshot_key != key:
        refresh_hitbox_snapshot(arm_vs)
        return

    old_val = _hb_snapshot.get(field)
    if old_val is None:
        _hb_snapshot[field] = new_val
        return

    scvs = getattr(getattr(context, 'scene', None), 'vs', None)
    if context.mode == 'POSE' and getattr(scvs, 'hitbox_sync_propagate', True):
        if is_vec:
            delta = tuple(n - o for n, o in zip(new_val, old_val))
            has_delta = any(abs(d) >= 1e-9 for d in delta)
        else:
            delta = new_val - old_val
            has_delta = abs(delta) >= 1e-9

        if has_delta:
            sel_names = {pb.name for pb in (context.selected_pose_bones or [])}
            if sel_names:
                _hb_propagating = True
                try:
                    for i, e in enumerate(arm_vs.hitboxes):
                        if i == my_idx or e.bone_name not in sel_names:
                            continue
                        if is_vec:
                            cur = list(getattr(e, field))
                            for k, d in enumerate(delta):
                                cur[k] += d
                            setattr(e, field, cur)
                        else:
                            new_e = getattr(e, field) + delta
                            if field == 'scale':
                                new_e = max(-1.0, new_e)
                            setattr(e, field, new_e)
                finally:
                    _hb_propagating = False

    _hb_snapshot[field] = new_val


# ---- HitboxEntry ------------------------------------------------------------

class HitboxEntry(bpy.types.PropertyGroup):
    bone_name : StringProperty(name=get_id('prop_hitbox_bone'), description=get_id('prop_hitbox_bone_tip'))
    group     : EnumProperty(name="Group", description="Hitbox hit group", items=hitbox_group, default='0')
    vec_min   : FloatVectorProperty(name=get_id('prop_hitbox_vec_min'), size=3, default=(0.0, 0.0, 0.0), subtype='XYZ', precision=4,
                                    update=lambda s, c: _hb_propagate(s, c, 'vec_min',  True))
    vec_max   : FloatVectorProperty(name=get_id('prop_hitbox_vec_max'), size=3, default=(0.0, 0.0, 0.0), subtype='XYZ', precision=4,
                                    update=lambda s, c: _hb_propagate(s, c, 'vec_max',  True))
    rotation  : FloatVectorProperty(name=get_id('prop_hitbox_rotation'), description=get_id('prop_hitbox_rotation_tip'), size=3, default=(0.0, 0.0, 0.0), subtype='EULER', unit='ROTATION', precision=4,
                                    update=lambda s, c: _hb_propagate(s, c, 'rotation', True))
    scale     : FloatProperty(name=get_id('prop_hitbox_scale'), description=get_id('prop_hitbox_scale_tip'), default=-1.0, min=-1.0, precision=4,
                              update=lambda s, c: _hb_propagate(s, c, 'scale',    False))


def _get_preview_tol(self) -> float:
    frame = self.trigger_preview_frame
    if self.action and self.driver_bone:
        fcurves = _procbones_sim._get_action_fcurves(self.action, self.action_slot_name)
        dp = f'bones["{self.driver_bone}"].vs.proc_tolerance'
        for fc in fcurves:
            if fc.data_path == dp and fc.array_index == 0:
                return fc.evaluate(frame)
    arm_ob = bpy.context.object
    if arm_ob and arm_ob.type != 'ARMATURE':
        arm_ob = arm_ob.find_armature()
    if arm_ob and arm_ob.type == 'ARMATURE' and self.driver_bone:
        eb = arm_ob.data.bones.get(self.driver_bone)
        if eb:
            return eb.vs.proc_tolerance
    return _math.pi / 2


def _set_preview_tol(self, value: float) -> None:
    if not self.driver_bone or not self.action:
        return
    dp = f'bones["{self.driver_bone}"].vs.proc_tolerance'
    fc = _procbones_sim._get_or_create_proc_tol_fcurve(self, dp)
    if fc is not None:
        fc.keyframe_points.insert(self.trigger_preview_frame, value, options={'NEEDED', 'FAST'})
        fc.update()
    arm_ob = bpy.context.object
    if arm_ob and arm_ob.type != 'ARMATURE':
        arm_ob = arm_ob.find_armature()
    if arm_ob:
        _procbones_sim.invalidate_proc_cache(arm_ob.name)


class ProcBoneEntry(bpy.types.PropertyGroup):
    proc_type : EnumProperty(
        name=get_id('prop_proc_bone_type'),
        description=get_id('prop_proc_bone_type_tip'),
        items=[
            ('TRIGGER', "Trigger", "Action-driven pose blending",  'ACTION',      0),
            ('LOOKAT',  "LookAt",  "Aim toward a target bone",     'CON_TRACKTO', 1),
        ],
        default='TRIGGER',
    )
    helper_bone : StringProperty(name=get_id('prop_proc_bone_helper'), description=get_id('prop_proc_bone_helper_tip'))
    driver_bone : StringProperty(name=get_id('prop_proc_bone_driver'), description=get_id('prop_proc_bone_driver_tip'))
    action : PointerProperty(name=get_id('prop_proc_bone_action'), description=get_id('prop_proc_bone_action_tip'), type=bpy.types.Action, update=_proc_entry_invalidate_cache)
    action_slot_name : StringProperty(name=get_id('prop_proc_bone_slot'), description=get_id('prop_proc_bone_slot_tip'), update=_proc_entry_invalidate_cache)
    use_manual_frame_range : BoolProperty(
        name=get_id('prop_proc_bone_use_manual_range'),
        description=get_id('prop_proc_bone_use_manual_range_tip'),
        default=False,
        update=_proc_entry_invalidate_cache,
    )
    trigger_frame_start : IntProperty(
        name=get_id('prop_proc_bone_frame_start'),
        description=get_id('prop_proc_bone_frame_start_tip'),
        default=1,
        update=_proc_entry_invalidate_cache,
    )
    trigger_frame_end : IntProperty(
        name=get_id('prop_proc_bone_frame_end'),
        description=get_id('prop_proc_bone_frame_end_tip'),
        default=1,
        update=_proc_entry_invalidate_cache,
    )
    trigger_preview_frame : IntProperty(
        name=get_id('prop_proc_bone_preview_frame'),
        description=get_id('prop_proc_bone_preview_frame_tip'),
        default=0,
    )
    trigger_preview_tol : FloatProperty(
        name=get_id('prop_pose_bone_proc_tolerance'),
        description=get_id('prop_pose_bone_proc_tolerance_tip'),
        default=_math.pi / 2, min=0.01, max=_math.pi, subtype='ANGLE', precision=2,
        get=_get_preview_tol,
        set=_set_preview_tol,
    )
    _lookat_axes = [
        ('+X', "+X", "Positive X",  1),
        ('+Y', "+Y", "Positive Y",  2),
        ('+Z', "+Z", "Positive Z",  4),
        ('-X', "-X", "Negative X",  8),
        ('-Y', "-Y", "Negative Y", 16),
        ('-Z', "-Z", "Negative Z", 32),
    ]
    lookat_aim_axis : EnumProperty(
        name=get_id('prop_proc_bone_lookat_aim_axis'),
        description=get_id('prop_proc_bone_lookat_aim_axis_tip'),
        items=_lookat_axes, default={'+X'}, options={'ENUM_FLAG'},
    )
    lookat_up_axis : EnumProperty(
        name=get_id('prop_proc_bone_lookat_up_axis'),
        description=get_id('prop_proc_bone_lookat_up_axis_tip'),
        items=_lookat_axes, default={'+Z'}, options={'ENUM_FLAG'},
    )
    lookat_offset : FloatVectorProperty(
        name=get_id('prop_proc_bone_lookat_offset'),
        description=get_id('prop_proc_bone_lookat_offset_tip'),
        size=3, default=(0.0, 0.0, 0.0),
        subtype='XYZ',
    )
