import bpy, math
from ..utils import get_armature, vertex_float_maps, validate_corrective_components, validate_flex_expression, _build_dme_ctrl_names, _build_stereo_delta_names, get_dme_renamed_delta_names
from .. import procbones_sim as _procbones_sim


def _mesh_type_allows(ob, feature: str) -> bool:
    mt = getattr(ob.vs, 'mesh_type', 'DEFAULT') if ob and hasattr(ob, 'vs') else 'DEFAULT'
    if mt == 'DEFAULT':
        return True
    if mt == 'CLOTHPROXY':
        return feature in ('vertexmap', 'vertexfloatmap')
    return False  # COLLISION blocks everything


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


def _count_flex_rule_errors(ob) -> int:
    if not ob or not hasattr(ob, 'vs'):
        return 0
    vs = ob.vs
    rules = getattr(vs, 'dme_flex_rules', None)
    if not rules:
        return 0
    sk = ob.data.shape_keys if (ob.data and hasattr(ob.data, 'shape_keys')) else None
    sk_names = set(sk.key_blocks.keys()) if sk else set()
    ctrl_names = _build_dme_ctrl_names(vs)
    localvar_names = {r.name for r in rules if r.rule_type == 'LOCALVAR' and r.name}
    stereo_delta_names = _build_stereo_delta_names(vs)
    renamed_delta_names = get_dme_renamed_delta_names(ob)
    count = 0
    for rule in rules:
        rt = rule.rule_type
        if rt == 'CORRECTIVE':
            comp_str = rule.components.strip()
            if not comp_str or validate_corrective_components(comp_str, sk_names):
                count += 1
        elif rt == 'DOMINATION':
            if not rule.dominator_names or not rule.suppressed_names:
                count += 1
        elif rt == 'PASSTHROUGH':
            if not rule.name or rule.name not in ctrl_names:
                count += 1
        elif rt == 'EXPRESSION':
            if not rule.name:
                count += 1
            elif rule.expression:
                d_errs, c_errs = validate_flex_expression(rule.expression.strip(), sk_names, ctrl_names, localvar_names, stereo_delta_names, renamed_delta_names)
                if d_errs or c_errs:
                    count += 1
        elif rt == 'LOCALVAR':
            if not rule.name:
                count += 1
    return count


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
