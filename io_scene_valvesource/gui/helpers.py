import bpy, math
from typing import NamedTuple
from ..utils import get_armature, vertex_float_maps, validate_corrective_components, validate_flex_expression, _build_dme_ctrl_names, _build_stereo_delta_names, get_dme_renamed_delta_names
from .. import procbones_sim as _procbones_sim


def _bone_is_hidden(bone) -> bool:
    """True if bone is hidden by its own hide flag or by bone-collection visibility.
    `bone` may be a Bone or PoseBone; context.active_bone can keep pointing at a bone
    hidden this way, so callers must check this before editing its properties."""
    data_bone = getattr(bone, 'bone', bone)
    if data_bone.hide:
        return True
    colls = data_bone.collections
    if not colls:
        return False
    return not any(c.is_visible_effectively for c in colls)


def _mesh_type_allows(ob, feature: str) -> bool:
    mt = getattr(ob.vs, 'mesh_type', 'DEFAULT') if ob and hasattr(ob, 'vs') else 'DEFAULT'
    if mt == 'DEFAULT':
        return True
    if mt == 'CLOTHPROXY':
        return feature == 'vertexmap'
    return False  # COLLISION blocks everything


# Keyword -> flexgroup enum, checked in order so more specific groups win (eyelid before eyes).
_FLEX_GROUP_KEYWORDS = (
    ('EYELID', ('lid', 'blink', 'wink', 'squint')),
    ('EYES',   ('eye', 'pupil', 'iris', 'gaze', 'look')),
    ('BROW',   ('brow', 'forehead')),
    ('CHEEK',  ('cheek', 'puff', 'dimple')),
    ('MOUTH',  ('mouth', 'lip', 'smile', 'frown', 'jaw', 'chin', 'teeth', 'tongue', 'grin', 'pucker', 'kiss', 'sneer')),
)


def _guess_flex_group(name: str) -> str:
    """Guess a FlexControllerItem.flexgroup enum value from a shape key name. Returns 'DEFAULT' if nothing matches."""
    n = (name or '').lower()
    for group, keywords in _FLEX_GROUP_KEYWORDS:
        if any(k in n for k in keywords):
            return group
    return 'DEFAULT'


def _draw_proc_bone_context_menu(self, context):
    if context.mode == 'POSE' and context.selected_pose_bones:
        arm_ob = get_armature(context.object)
        if arm_ob:
            self.layout.operator_context = 'INVOKE_DEFAULT'
            self.layout.operator("smd.copy_bone_export_name", icon='TEXT')
            self.layout.operator("smd.flatten_bone_export_name", icon='SORTALPHA')
            self.layout.operator("smd.proc_bone_add_from_selected", icon='DRIVER')
            self.layout.operator("smd.proc_bone_add_lookat", icon='CON_TRACKTO')


def _ensure_cloth_remaps():
    context = bpy.context
    if context.object and context.object.type == 'MESH':
        existing = {r.group for r in context.object.vs.vertex_map_remaps}
        for map_name in vertex_float_maps:
            if map_name not in existing:
                remap = context.object.vs.vertex_map_remaps.add()
                remap.group = map_name
                remap.min = 0.0
                remap.max = 1.0
    return None


class FlexRuleContext(NamedTuple):
    """Name sets a flex rule is validated against. Build once per object, not per rule."""
    sk: object
    sk_names: set
    ctrl_names: set
    localvar_names: set
    stereo_delta_names: set
    renamed_delta_names: set


def build_flex_rule_context(ob) -> FlexRuleContext:
    vs = ob.vs
    sk = ob.data.shape_keys if (ob.data and hasattr(ob.data, 'shape_keys')) else None
    rules = getattr(vs, 'dme_flex_rules', None) or ()
    return FlexRuleContext(
        sk=sk,
        sk_names=set(sk.key_blocks.keys()) if sk else set(),
        ctrl_names=_build_dme_ctrl_names(vs),
        localvar_names={r.name for r in rules if r.rule_type == 'LOCALVAR' and r.name},
        stereo_delta_names=_build_stereo_delta_names(vs),
        renamed_delta_names=get_dme_renamed_delta_names(ob),
    )


def flex_rule_name_error(rule, ctx: FlexRuleContext) -> bool:
    """True when the rule's name field does not resolve to a valid target."""
    rt = rule.rule_type
    if rt == 'PASSTHROUGH':
        return not rule.name or rule.name not in ctx.ctrl_names
    if rt == 'LOCALVAR':
        return not rule.name
    if rt == 'EXPRESSION':
        if not rule.name:
            return True
        in_shapekeys = ctx.sk is not None and (
            rule.name in ctx.sk.key_blocks or
            any(rule.name in key.name.split('+') for key in ctx.sk.key_blocks)
        )
        return (not in_shapekeys and rule.name not in ctx.localvar_names
                and rule.name not in ctx.stereo_delta_names
                and rule.name not in ctx.renamed_delta_names)
    return False


def flex_rule_has_error(rule, ctx: FlexRuleContext) -> bool:
    """Single source of truth for the UIList row icon and the panel header count."""
    rt = rule.rule_type
    if rt == 'CORRECTIVE':
        comp_str = rule.components.strip()
        return not comp_str or bool(validate_corrective_components(comp_str, ctx.sk_names))
    if rt == 'DOMINATION':
        return not rule.dominator_names or not rule.suppressed_names
    if flex_rule_name_error(rule, ctx):
        return True
    if rt == 'EXPRESSION' and rule.expression:
        d_errs, c_errs = validate_flex_expression(
            rule.expression.strip(), ctx.sk_names, ctx.ctrl_names,
            ctx.localvar_names, ctx.stereo_delta_names, ctx.renamed_delta_names)
        return bool(d_errs or c_errs)
    return False


def _count_flex_rule_errors(ob) -> int:
    if not ob or not hasattr(ob, 'vs'):
        return 0
    rules = getattr(ob.vs, 'dme_flex_rules', None)
    if not rules:
        return 0
    ctx = build_flex_rule_context(ob)
    return sum(flex_rule_has_error(rule, ctx) for rule in rules)


_get_or_create_proc_tol_fcurve = _procbones_sim._get_or_create_proc_tol_fcurve


def _get_entry_proc_tol(entry, frame: float, arm_ob=None) -> float:
    """Return proc_tolerance from entry.action's fcurves at frame.
    Falls back to the bone's static value, then to the 90° default."""
    if not entry.action or not entry.driver_bone:
        if arm_ob:
            eb = arm_ob.data.bones.get(entry.driver_bone)
            if eb:
                return eb.vs.proc_tolerance
        return math.pi / 2
    fcurves = _procbones_sim._get_action_fcurves(entry.action, entry.action_slot_name)
    dp = f'bones["{entry.driver_bone}"].vs.proc_tolerance'
    for fc in fcurves:
        if fc.data_path == dp and fc.array_index == 0:
            return fc.evaluate(frame)
    if arm_ob:
        eb = arm_ob.data.bones.get(entry.driver_bone)
        if eb:
            return eb.vs.proc_tolerance
    return math.pi / 2
