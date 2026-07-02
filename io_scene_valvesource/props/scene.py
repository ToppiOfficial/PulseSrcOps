__all__ = ['ValveSource_Exportable', 'ValveSource_SceneProps']

import bpy
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       CollectionProperty, FloatProperty, PointerProperty)
from ..utils import (get_id, State, axes, axes_forward, dmx_versions_source1,
                     dmx_versions_source2, get_active_exportable)
from .. import datamodel, procbones_sim as _procbones_sim


encodings = []
for _enc in datamodel.list_support()['binary']:
    encodings.append((str(_enc), f"Binary {_enc}", ''))
encodings.append(('kv2', 'ASCII (KeyValues2)', ''))

formats = []
for _version in set(x for x in [*dmx_versions_source1.values(), *dmx_versions_source2.values()] if x.format != 0):
    formats.append((_version.format_enum, _version.format_title, ''))
formats.sort(key=lambda f: f[0])


def export_active_changed(self, context):
    if not context.scene.vs.export_list_active < len(context.scene.vs.export_list):
        context.scene.vs.export_list_active = len(context.scene.vs.export_list) - 1
        return

    item = get_active_exportable(context).item

    if type(item) == bpy.types.Collection and item.vs.mute: return
    for ob in context.scene.objects: ob.select_set(False)

    if type(item) == bpy.types.Collection:
        visible = [ob for ob in item.objects if ob.visible_get()]
        if not visible: return
        context.view_layer.objects.active = visible[0]
        for ob in visible: ob.select_set(True)
    else:
        if not item.visible_get(): return
        item.select_set(True)
        context.view_layer.objects.active = item


def on_flexcontroller_index_changed(self, context):
    ob = context.active_object
    if not ob:
        return

    mesh : bpy.types.Object = ob if ob.type == 'MESH' else next(
        (child for child in ob.children if child.type == 'MESH'), None
    )
    if not mesh or not mesh.data.shape_keys:
        return

    items = ob.vs.dme_flexcontrollers
    idx = ob.vs.dme_flexcontrollers_index
    if idx < 0 or idx >= len(items):
        return

    shapekey_name = items[idx].shapekey
    if not shapekey_name:
        return

    key_blocks = mesh.data.shape_keys.key_blocks
    sk_idx = key_blocks.find(shapekey_name)
    if sk_idx != -1:
        mesh.active_shape_key_index = sk_idx


class ValveSource_Exportable(bpy.types.PropertyGroup):
    ob_type : StringProperty()
    icon : StringProperty()
    obj : PointerProperty(type=bpy.types.Object)
    collection : PointerProperty(type=bpy.types.Collection)
    # Non-empty for synthetic "prefab" rows (jigglebones / attachments / hitboxes /
    # procedural). For those rows `obj` points at the owning armature.
    prefab_type : StringProperty(default='')
    prefab_count : IntProperty(default=0)

    @property
    def item(self) -> bpy.types.Object | bpy.types.Collection: return self.obj or self.collection

    @property
    def session_uid(self): return self.item.session_uid

    @property
    def is_prefab(self) -> bool: return bool(self.prefab_type)

    @property
    def prefab_item(self):
        """The PrefabItem on the owning armature that this row represents, or None."""
        if not self.prefab_type or not self.obj or self.obj.type != 'ARMATURE':
            return None
        for p in self.obj.data.vs.prefab_items:
            if p.prefab_type == self.prefab_type:
                return p
        return None


class ValveSource_SceneProps(bpy.types.PropertyGroup):
    export_path : StringProperty(name=get_id("exportroot"), description=get_id("exportroot_tip"), subtype='DIR_PATH', options={'PATH_SUPPORTS_BLEND_RELATIVE'})
    engine_path : StringProperty(name=get_id("engine_path"), description=get_id("engine_path_tip"), subtype='DIR_PATH', update=State.onEnginePathChanged)

    dmx_encoding : EnumProperty(name=get_id("dmx_encoding"), description=get_id("dmx_encoding_tip"), items=tuple(encodings), default='2')
    dmx_format : EnumProperty(name=get_id("dmx_format"), description=get_id("dmx_format_tip"), items=tuple(formats), default='1')

    smd_format : EnumProperty(name=get_id("smd_format"), description=get_id("smd_format_tip"), items=(('SOURCE', "Source", "Source Engine (Half-Life 2)"), ("GOLDSOURCE", "GoldSrc", "GoldSrc engine (Half-Life 1)")), default="SOURCE")

    export_format : EnumProperty(name=get_id("export_format"), description=get_id("export_format_tip"), items=[('SMD', "SMD", "Studiomdl Data"), ('DMX', "DMX", "Datamodel Exchange")], default='DMX')
    up_axis : EnumProperty(name=get_id("up_axis"), items=axes, default='Z', description=get_id("up_axis_tip"))
    up_axis_offset : FloatProperty(name=get_id("up_axis_offset"), description=get_id("up_axis_tip"), soft_max=30, soft_min=-30, default=0, precision=2)
    forward_axis : EnumProperty(name=get_id("forward_axis"), items=axes_forward, default='-Y', description=get_id("up_axis_tip"))
    world_scale : FloatProperty(name=get_id("world_scale"), description=get_id("world_scale_tip"), default=1.00, precision=3, min=0.0001)
    material_path : StringProperty(name=get_id("dmx_mat_path"), description=get_id("dmx_mat_path_tip"))
    export_list_active : IntProperty(name=get_id("active_exportable"), default=0, min=0, update=export_active_changed)
    export_list : CollectionProperty(type=ValveSource_Exportable, options={'SKIP_SAVE', 'HIDDEN'})
    game_path : StringProperty(name=get_id("game_path"), description=get_id("game_path_tip"), subtype='DIR_PATH', update=State.onGamePathChanged)

    weightlink_threshold : FloatProperty(name=get_id("weightlink_threshold"), description=get_id("weightlink_threshold_tip"), max=0.001, min=0.0001, default=0.0001, precision=4)

    vertex_influence_limit_mode : EnumProperty(name=get_id("vertex_influence_limit_mode"), items=[('AUTO', 'AUTO', get_id("vertex_influence_limit_mode_auto_tip")), ('MANUAL', 'MANUAL', get_id("vertex_influence_limit_mode_manual_tip"))], default='AUTO')
    vertex_influence_limit : IntProperty(name=get_id("vertex_influence_limit"), description=get_id("vertex_influence_limit_tip"), default=3, max=32, soft_max=8, min=1)

    prefab_to_clipboard : BoolProperty(name=get_id("prefab_to_clipboard"), description=get_id("prefab_to_clipboard_tip"), default=False)
    prefab_export_mode : EnumProperty(name=get_id("prefab_export_mode"), description=get_id("prefab_export_mode_tip"), items=[('QCI', "FILE", get_id("prefab_export_mode_qci_tip")), ('DME', "DME", get_id("prefab_export_mode_dme_tip"))], default='QCI')

    preview_export_pose : BoolProperty(name=get_id('prop_preview_export_pose'), description=get_id('prop_preview_export_pose_tip'), default=True)
    preview_jigglebone_constraints : BoolProperty(name=get_id('prop_preview_jigglebone_constraints'), description=get_id('prop_preview_jigglebone_constraints_tip'), default=True)
    preview_proc_bones : BoolProperty(name=get_id('prop_preview_proc_bones'), description=get_id('prop_preview_proc_bones_tip'), default=True)

    jiggle_sim_enabled : BoolProperty(name=get_id('prop_proc_sim_enabled'), description=get_id('prop_proc_sim_enabled_tip'), default=False, update=lambda self, ctx: _procbones_sim.on_sim_enabled_changed(self, ctx))
    jiggle_sim_rate : IntProperty(name=get_id('prop_jiggle_sim_rate'), description=get_id('prop_jiggle_sim_rate_tip'), default=60, min=12, max=240)
    sim_jiggle_bones : BoolProperty(name=get_id('prop_sim_jiggle_bones'), description=get_id('prop_sim_jiggle_bones_tip'), default=True)
    sim_proc_bones   : BoolProperty(name=get_id('prop_sim_proc_bones'), description=get_id('prop_sim_proc_bones_tip'), default=True)
    preview_edgeline : BoolProperty(name=get_id('prop_preview_edgeline'), description=get_id('prop_preview_edgeline_tip'), default=False)
    preview_attachment_mesh : EnumProperty(
        name=get_id('prop_preview_attachment_mesh'),
        description=get_id('prop_preview_attachment_mesh_tip'),
        items=[
            ('ALL',      'All',      'Show ghost mesh for all attachment empties in the scene'),
            ('SELECTED', 'Selected', 'Show ghost mesh only for selected attachment empties'),
            ('NONE',     'None',     'Hide attachment mesh preview'),
        ],
        default='SELECTED',
    )
    hitbox_sync_pose : BoolProperty(name=get_id('prop_hitbox_sync_pose'), description=get_id('prop_hitbox_sync_pose_tip'), default=True)
    hitbox_sync_propagate : BoolProperty(name=get_id('prop_hitbox_sync_propagate'), description=get_id('prop_hitbox_sync_propagate_tip'), default=False)

    preview_hitboxes : EnumProperty(
        name=get_id('prop_preview_hitboxes'),
        description=get_id('prop_preview_hitboxes_tip'),
        items=[
            ('ALL',      'All',      'Show all hitboxes in the viewport'),
            ('SELECTED', 'Selected', 'Show only the hitbox entry selected in the list'),
            ('POSE',     'Pose',     'Show hitboxes for all selected pose bones (Pose mode only)'),
            ('NONE',     'None',     'Hide hitbox preview'),
        ],
        default='POSE',
    )

    show_flex_items : BoolProperty(default=False)
    show_flex_rules_items : BoolProperty(default=True)
    show_flex_delta_overrides : BoolProperty(default=False)

    arm_items_view : EnumProperty(name=get_id('prop_arm_items_view'), items=[
        ('JIGGLEBONES', get_id('label_all_jigglebones'), '', 'BONE_DATA',  0),
        ('ATTACHMENTS', get_id('label_all_attachments'), '', 'EMPTY_DATA', 1),
    ], default='JIGGLEBONES')
