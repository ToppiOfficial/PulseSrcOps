__all__ = ['ValveSource_Exportable', 'ValveSource_SceneProps', '_on_blend_load_migrate_engine']

import bpy
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       CollectionProperty, FloatProperty, PointerProperty)
from bpy.app.handlers import persistent
from ..utils import (get_id, State, axes, axes_forward, game_presets,
                     get_active_exportable, export_formats_by_engine)
from .. import datamodel, procbones_sim as _procbones_sim
from .items import MaterialPathItem


encodings = [(str(_enc), f"Binary {_enc}", '') for _enc in datamodel.list_support()['binary']]
encodings.append(('kv2', 'ASCII (KeyValues2)', ''))

# Model 22's suffix picks the compiler - see compiler_suffixes in utils.py. This is the
# only place the target Source engine is chosen; scene.vs.engine is GoldSrc-vs-Source.
formats = [
    ('1', "Model 1", "Half-Life 2 / SDK 2013"),
    ('15', "Model 15", "Left 4 Dead 1-2"),
    ('18', "Model 18", "Source Filmmaker / Portal 2 / CS:GO / Alien Swarm"),
    ('22', "Model 22 (Source 1)", "PulseMDL2 - the only compiler that supports Model 22"),
    ('22_resourcecompiler', "Model 22 (ResourceCompiler)", "Source 2 pre-Alyx - Dota 2"),
    ('22_modeldoc', "Model 22 (ModelDoc)", "Source 2 post-Alyx - Half-Life: Alyx / CS2 / Deadlock"),
]

_game_items = tuple((_id, _label, "GoldSrc - SMD" if _engine == 'GOLDSRC'
                     else f"Model {_fmt.split('_')[0]}, Binary {_enc}"
                          + (f" ({_fmt.partition('_')[2]})" if '_' in _fmt else ""))
                    for _id, (_label, _engine, _enc, _fmt) in game_presets.items())
_game_items = (('CUSTOM', "Custom", "Encoding and format set manually"),) + _game_items

# Set while a preset is writing engine/encoding/format, so their update callbacks don't
# bounce `game` back to CUSTOM.
_applying_preset = False

def on_game_changed(self, context):
    global _applying_preset
    preset = game_presets.get(self.game)
    if not preset or _applying_preset:
        return
    _label, engine, encoding, fmt = preset
    _applying_preset = True
    try:
        self.engine = engine
        self.dmx_encoding = encoding
        self.dmx_format = fmt
        if self.export_format == 'SMD' and engine != 'GOLDSRC':
            self.export_format = 'DMX'
    finally:
        _applying_preset = False

# A preset naming an encoding/format Blender's enums don't have would raise on assignment.
for _id, (_l, _eng, _enc, _fmt) in game_presets.items():
    assert _enc in {e[0] for e in encodings} and _fmt in {f[0] for f in formats}, _id
    assert _eng in export_formats_by_engine, _id

# Value 1 is the historical index of the old 'SOURCE1' item, so files saved with it still
# load as 'SOURCE'. Files saved with the removed 'SOURCE2' (value 2) are repaired on load.
_engine_items = (
    ('GOLDSRC', "GoldSrc", "Half-Life 1 - SMD only", 0, 0),
    ('SOURCE', "Source", "Source 1 and 2 - SMD, DMX and FBX. Which Source engine is set by the DMX model format", 0, 1),
)

def on_engine_changed(self, context):
    if not _applying_preset: self.game = 'CUSTOM'
    goldsrc = self.engine == 'GOLDSRC'
    if goldsrc and self.export_format != 'SMD':
        self.export_format = 'SMD'
    want_smd_format = 'GOLDSOURCE' if goldsrc else 'SOURCE'
    if self.smd_format != want_smd_format:
        self.smd_format = want_smd_format

@persistent
def _on_blend_load_migrate_engine(filepath):
    """Infer scene.vs.engine for files saved before the property existed, from the
    smd_format they already had. Runs once per scene - engine_migrated marks that this has
    already happened, since a freshly-added property can't tell an old file's implicit
    default apart from a user's real choice of 'SOURCE'.

    Also repairs scenes saved with the removed 'SOURCE2' engine, whose stored value no
    longer maps to an item and would read back as ''."""
    for scene in bpy.data.scenes:
        vs = scene.vs
        if not vs.engine_migrated:
            vs.engine_migrated = True
            if vs.export_format == 'SMD' and vs.smd_format == 'GOLDSOURCE':
                vs.engine = 'GOLDSRC'
        elif not vs.engine:
            vs.engine = 'SOURCE'


# Identifiers are historical (QCI/DME); the labels describe where the data lands,
# since the file format varies (.qci/.vmdl) and embedding applies to DMX and FBX.
_prefab_export_mode_items = (
    ('QCI', "FILE", get_id("prefab_export_mode_qci_tip"), 0),
    ('DME', "EMBEDDED", get_id("prefab_export_mode_dme_tip"), 1),
)


def on_export_format_changed(self, context):
    allowed = export_formats_by_engine.get(self.engine, export_formats_by_engine['SOURCE'])
    if self.export_format not in allowed:
        self.export_format = allowed[0]


# Encoding and format are deliberately independent - auto-snapping the other dropdown
# fought the user more than it helped. Picking a Game preset sets both at once.
def on_dmx_encoding_changed(self, context):
    if not _applying_preset: self.game = 'CUSTOM'


def on_dmx_format_changed(self, context):
    if not _applying_preset: self.game = 'CUSTOM'


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
    game : EnumProperty(name=get_id("game"), description=get_id("game_tip"), items=_game_items, default='CUSTOM', update=on_game_changed)

    engine : EnumProperty(name=get_id("engine"), description=get_id("engine_tip"), items=_engine_items, default='SOURCE', update=on_engine_changed)
    # Set by _on_blend_load_migrate_engine once a file's engine has been inferred; not for the UI.
    engine_migrated : BoolProperty(default=False, options={'HIDDEN'})

    dmx_encoding : EnumProperty(name=get_id("dmx_encoding"), description=get_id("dmx_encoding_tip"), items=tuple(encodings), default='2', update=on_dmx_encoding_changed)
    dmx_format : EnumProperty(name=get_id("dmx_format"), description=get_id("dmx_format_tip"), items=tuple(formats), default='1', update=on_dmx_format_changed)

    smd_format : EnumProperty(name=get_id("smd_format"), description=get_id("smd_format_tip"), items=(('SOURCE', "Source", "Source Engine (Half-Life 2)"), ("GOLDSOURCE", "GoldSrc", "GoldSrc engine (Half-Life 1)")), default="SOURCE")

    export_format : EnumProperty(name=get_id("export_format"), description=get_id("export_format_tip"), items=[('SMD', "SMD", "Studiomdl Data"), ('DMX', "DMX", "Datamodel Exchange"), ('FBX', "FBX", "Autodesk FBX (Source 2 / external tools)")], default='DMX', update=on_export_format_changed)
    up_axis : EnumProperty(name=get_id("up_axis"), items=axes, default='Z', description=get_id("up_axis_tip"))
    up_axis_offset : FloatProperty(name=get_id("up_axis_offset"), description=get_id("up_axis_tip"), soft_max=30, soft_min=-30, default=0, precision=2)
    forward_axis : EnumProperty(name=get_id("forward_axis"), items=axes_forward, default='-Y', description=get_id("up_axis_tip"))
    world_scale : FloatProperty(name=get_id("world_scale"), description=get_id("world_scale_tip"), default=1.00, precision=4, min=0.0001)
    material_paths : CollectionProperty(type=MaterialPathItem)
    material_paths_index : IntProperty(name=get_id("dmx_mat_path"), default=0, min=0)
    # Legacy single path. Kept registered so pre-collection .blend files still load their
    # value for _on_blend_load_migrate_material_paths to pick up; not drawn in the UI.
    material_path : StringProperty(name=get_id("dmx_mat_path"), description=get_id("dmx_mat_path_tip"))
    export_list_active : IntProperty(name=get_id("active_exportable"), default=0, min=0, update=export_active_changed)
    export_list : CollectionProperty(type=ValveSource_Exportable, options={'SKIP_SAVE', 'HIDDEN'})
    game_path : StringProperty(name=get_id("game_path"), description=get_id("game_path_tip"), subtype='DIR_PATH', update=State.onGamePathChanged)

    weightlink_threshold : FloatProperty(name=get_id("weightlink_threshold"), description=get_id("weightlink_threshold_tip"), max=0.001, min=0.0001, default=0.0001, precision=4)

    vertex_influence_limit_mode : EnumProperty(name=get_id("vertex_influence_limit_mode"), items=[('AUTO', 'AUTO', get_id("vertex_influence_limit_mode_auto_tip")), ('MANUAL', 'MANUAL', get_id("vertex_influence_limit_mode_manual_tip"))], default='AUTO')
    vertex_influence_limit : IntProperty(name=get_id("vertex_influence_limit"), description=get_id("vertex_influence_limit_tip"), default=3, max=32, soft_max=8, min=1)

    force_source2_bone_sanitize : BoolProperty(name=get_id("force_source2_bone_sanitize"), description=get_id("force_source2_bone_sanitize_tip"), default=False)

    prefab_to_clipboard : BoolProperty(name=get_id("prefab_to_clipboard"), description=get_id("prefab_to_clipboard_tip"), default=False)
    prefab_export_mode : EnumProperty(name=get_id("prefab_export_mode"), description=get_id("prefab_export_mode_tip"), items=_prefab_export_mode_items, default='QCI')

    preview_export_pose : BoolProperty(name=get_id('prop_preview_export_pose'), description=get_id('prop_preview_export_pose_tip'), default=True)
    preview_jigglebone_constraints : BoolProperty(name=get_id('prop_preview_jigglebone_constraints'), description=get_id('prop_preview_jigglebone_constraints_tip'), default=True)
    preview_proc_bones : BoolProperty(name=get_id('prop_preview_proc_bones'), description=get_id('prop_preview_proc_bones_tip'), default=True)

    jiggle_sim_enabled : BoolProperty(name=get_id('prop_proc_sim_enabled'), description=get_id('prop_proc_sim_enabled_tip'), default=False, update=lambda self, ctx: _procbones_sim.on_sim_enabled_changed(self, ctx))
    jiggle_sim_engine : EnumProperty(name=get_id('prop_jiggle_sim_engine'), description=get_id('prop_jiggle_sim_engine_tip'), items=[('SOURCE1', "Source 1", ""), ('SOURCE2', "Source 2", "")], default='SOURCE1')
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

    arm_items_view : EnumProperty(name=get_id('prop_arm_items_view'), items=[
        ('JIGGLEBONES', get_id('label_all_jigglebones'), '', 'BONE_DATA',  0),
        ('ATTACHMENTS', get_id('label_all_attachments'), '', 'EMPTY_DATA', 1),
    ], default='JIGGLEBONES')
