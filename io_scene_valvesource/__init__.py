#  Copyright (c) 2014 Tom Edwards contact@steamreview.org
#
# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy, math, os
from bpy.props import PointerProperty, BoolProperty, CollectionProperty, IntProperty, StringProperty, EnumProperty

# Python doesn't reload package sub-modules at the same time as __init__.py!
import importlib, sys

pkg_name = __name__
# -------------------------------------------------------------------------------------
# Reload all modules that belong to this package
# -------------------------------------------------------------------------------------

for modname, module in list(sys.modules.items()):
    if modname.startswith(pkg_name + ".") and module:
        importlib.reload(module)


# -------------------------------------------------------------------------------------
# Clear out any scene update funcs hanging around, e.g. after a script reload
# -------------------------------------------------------------------------------------

for collection in [bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.load_post, bpy.app.handlers.frame_change_post]:
    for func in collection[:]:
        if func.__module__.startswith(pkg_name):
            collection.remove(func)

from . import datamodel, import_smd, export, flex, procbones_sim, updater
from . import gui as GUI
from .utils import *
from .props import *

def menu_func_import(self, context):
    self.layout.menu("SMD_MT_ImportChoice", text=get_id("importmenu_title"))

def menu_func_export(self, context):
    self.layout.menu("SMD_MT_ExportChoice", text=get_id("export_menuitem"))

def menu_func_shapekeys(self,context):
    self.layout.operator(flex.ActiveDependencyShapes.bl_idname, text=get_id("activate_dependency_shapes",True), icon='SHAPEKEY_DATA')

def menu_func_textedit(self,context):
    self.layout.operator(flex.InsertUUID.bl_idname)

def draw_copy_bone_props(self, context):
    self.layout.operator(GUI.SMD_OT_CopyBoneExportName.bl_idname)
    copyop = self.layout.operator(GUI.SMD_OT_CopySourceBoneProps.bl_idname, text='Copy Jigglebone Properties')
    copyop.to_invoke = False
    copyop.copy_name = False
    copyop.copy_rotation = False
    copyop.copy_location = False
    copyop.copy_jigglebone = True

class ValveSource_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    bone_name_prefixes : CollectionProperty(type=BoneNamePrefixItem)
    bone_name_prefixes_index : IntProperty(default=0)
    valvebiped_shortcut : StringProperty(
        name=get_id("bone_name_shortcut"),
        description=get_id("bone_name_shortcut_tip"),
        default="vbip")
    show_bone_name_prefixes : BoolProperty(
        name=get_id("bone_name_prefixes_title"),
        description=get_id("bone_name_prefixes_title_tip"),
        default=False)

    update_channel : EnumProperty(
        name=get_id("updater_channel"),
        description=get_id("updater_channel_tip"),
        items=[
            ('STABLE', get_id("updater_channel_stable"), get_id("updater_channel_stable_tip")),
            ('DEV', get_id("updater_channel_dev"), get_id("updater_channel_dev_tip")),
        ],
        default='STABLE',
        update=lambda self, context: updater.reset_state())
    update_auto_check : BoolProperty(
        name=get_id("updater_auto_check"),
        description=get_id("updater_auto_check_tip"),
        default=True)
    show_updater : BoolProperty(
        name=get_id("updater_title"),
        default=False)
    dev_build_date : StringProperty(options={'HIDDEN'})

    def draw(self, context):
        layout = self.layout

        updater.draw_prefs(layout, self)

        header = layout.row(align=True)
        header.prop(self, "show_bone_name_prefixes",
            text=get_id("bone_name_prefixes_title"),
            icon='DISCLOSURE_TRI_DOWN' if self.show_bone_name_prefixes else 'DISCLOSURE_TRI_RIGHT',
            emboss=False)

        if not self.show_bone_name_prefixes:
            return

        head = layout.split(factor=0.6, align=True)
        head.label(text=get_id("bone_name_prefix"))
        head.label(text=get_id("bone_name_shortcut"))

        # ValveBiped is always preserved; its name is locked but its shortcut is editable.
        vb = layout.split(factor=0.6, align=True)
        name = vb.row()
        name.enabled = False
        name.label(text="ValveBiped.Bip01", icon='LOCKED')
        vb.prop(self, "valvebiped_shortcut", text="", icon='SYNTAX_OFF')

        row = layout.row()
        row.template_list("SMD_UL_BoneNamePrefixes", "", self, "bone_name_prefixes", self, "bone_name_prefixes_index", rows=3)
        col = row.column(align=True)
        col.operator(GUI.SMD_OT_BoneNamePrefixAdd.bl_idname, text="", icon='ADD')
        col.operator(GUI.SMD_OT_BoneNamePrefixRemove.bl_idname, text="", icon='REMOVE')

        info = layout.column(align=True)
        info.label(text=get_id("bone_name_prefixes_desc"), icon='INFO')
        info.label(text=get_id("bone_name_prefixes_desc2"), icon='BLANK1')

# -------------------------------------------------------------------------------------
# Register
# -------------------------------------------------------------------------------------

_addon_keymaps: list = []

_classes = (
    # Base/Utility Classes
    ValveSource_FloatMapRemap,

    # Simple Item Classes
    FlexControllerItem,
    DmeFlexRuleItem,
    DmeDeltaNameOverride,
    VertexAnimation,
    ProcBoneEntry,
    HitboxEntry,
    ArmatureItemEntry,
    PrefabItem,
    AttachmentDisplayMeshItem,
    BoneNamePrefixItem,

    # Material Classes
    ValveSource_MaterialProps,

    # Geometry Property Classes
    ValveSource_MeshProps,
    ValveSource_SurfaceProps,
    ValveSource_CurveProps,
    ValveSource_TextProps,

    # Object/Bone Property Classes
    ValveSource_BoneProps,
    ValveSource_ObjectProps,
    ValveSource_ArmatureProps,

    # Collection/Group Classes
    ValveSource_CollectionProps,

    # Exportable and Scene Classes
    ValveSource_Exportable,
    ValveSource_SceneProps,

    # GUI - Scene
    GUI.SMD_MT_ImportChoice,
    GUI.SMD_MT_ExportChoice,
    GUI.SMD_PT_Scene,
    GUI.SMD_MT_ConfigureScene,
    GUI.SMD_UL_ExportItems,
    GUI.SMD_UL_GroupItems,
    GUI.SMD_UL_ActionExport,
    GUI.SMD_PT_Exportables,
    GUI.SMD_PT_ViewportSimulation,

    # Properties
    GUI.SMD_PT_Armature,
    GUI.SMD_PT_ArmatureData,
    GUI.SMD_PT_Bone,
    GUI.SMD_PT_BoneData,
    GUI.SMD_PT_Mesh,
    GUI.SMD_PT_Material,
    GUI.SMD_PT_Shapekey,
    GUI.SMD_PT_Vertexmap,
    GUI.SMD_PT_Vertexfloatmap,
    GUI.SMD_PT_Vertexanimations,
    GUI.SMD_PT_ToonEdgeline,
    GUI.SMD_PT_MeshBackface,
    GUI.SMD_PT_MeshSplit,
    GUI.SMD_PT_LOD,
    GUI.SMD_PT_Empty,
    GUI.SMD_PT_Curve,
    GUI.SMD_UL_ArmatureItems,
    GUI.SMD_UL_Hitboxes,
    GUI.SMD_UL_ProcBones,
    GUI.SMD_MT_HitboxSpecials,
    GUI.SMD_MT_ProcBoneSpecials,
    GUI.SMD_OT_HitboxAdd,
    GUI.SMD_OT_HitboxRemove,
    GUI.SMD_OT_HitboxFromBone,
    GUI.SMD_OT_HitboxDuplicate,
    GUI.SMD_OT_HitboxCopyEntry,
    GUI.SMD_OT_HitboxCopyAll,
    GUI.SMD_OT_HitboxPasteEntries,
    GUI.SMD_OT_HitboxPasteValues,
    GUI.SMD_OT_HitboxCopyToArmature,
    GUI.SMD_OT_HitboxMirror,
    GUI.SMD_OT_ProcBoneAdd,
    GUI.SMD_OT_ProcBoneAddFromSelected,
    GUI.SMD_OT_ProcBoneAddLookAt,
    GUI.SMD_OT_ProcBoneDuplicate,
    GUI.SMD_OT_ProcBoneRemove,
    GUI.SMD_OT_ProcBoneSetTolerance,
    GUI.SMD_OT_ProcBoneNavigateFrame,
    GUI.SMD_OT_ProcBoneCopyTolerance,
    GUI.SMD_OT_ProcBonePasteTolerance,
    GUI.SMD_OT_ProcBoneCopyActive,
    GUI.SMD_OT_ProcBoneCopyByDriverBone,
    GUI.SMD_OT_ProcBoneCopyAll,
    GUI.SMD_OT_ProcBonePasteEntries,
    GUI.SMD_PT_Hitboxes,
    GUI.SMD_PT_ProcBones,
GUI.SMD_PT_Jigglebones,

    # Properties Operators
    GUI.SMD_UL_DmeFlexControllers,
    GUI.SMD_UL_DmeFlexRules,
    GUI.SMD_MT_FlexControllerSpecials,
    GUI.SMD_OT_AutoAssignFlexGroups,
    GUI.SMD_OT_AddFlexController,
    GUI.SMD_OT_AddAllFlexControllers,
    GUI.SMD_OT_ImportFlexControllersFromText,
    GUI.SMD_OT_CombineStereoFlexControllers,
    GUI.SMD_OT_RemoveFlexController,
    GUI.SMD_OT_MoveFlexController,
    GUI.SMD_OT_SortFlexControllers,
    GUI.SMD_OT_CopyFlexControllers,
    GUI.SMD_OT_ClearFlexControllers,
    GUI.SMD_OT_PreviewFlexController,
    GUI.SMD_OT_MigrateQCDeltasToOverrides,
    GUI.SMD_OT_AddFlexRule,
    GUI.SMD_OT_RemoveFlexRule,
    GUI.SMD_OT_ClearFlexRules,
    GUI.SMD_OT_MoveFlexRule,
    GUI.SMD_OT_FlexRuleRegexReplace,
    GUI.SMD_UL_DmeDeltaOverrides,
    GUI.SMD_OT_AddDeltaOverride,
    GUI.SMD_OT_RemoveDeltaOverride,
    GUI.SMD_OT_ClearDeltaOverrides,
    GUI.SMD_OT_AddVertexMapRemap,
    GUI.SMD_UL_VertexAnimationItem,
    GUI.SMD_OT_AddVertexAnimation,
    GUI.SMD_OT_RemoveVertexAnimation,
    GUI.SMD_OT_PreviewVertexAnimation,
    GUI.SMD_OT_GenerateVertexAnimationQCSnippet,
    GUI.SMD_OT_CopyBoneExportName,
    GUI.SMD_OT_FlattenBoneExportName,
    GUI.SMD_OT_AssignBoneRotExportOffset,
    GUI.SMD_OT_CopySourceBoneProps,
    GUI.SMD_OT_CopyJigglebonesFromArmature,
    GUI.SMD_OT_ResetJiggleSimulation,
    GUI.SMD_UL_AttachmentDisplayMeshes,
    GUI.SMD_OT_AddAttachmentDisplayMesh,
    GUI.SMD_OT_RemoveAttachmentDisplayMesh,
    GUI.SMD_OT_SetAttachmentMeshRender,
    GUI.SMD_UL_BoneNamePrefixes,
    GUI.SMD_OT_BoneNamePrefixAdd,
    GUI.SMD_OT_BoneNamePrefixRemove,
    GUI.SMD_MT_BoneToolsPie,

    # Flex
    flex.DmxWriteFlexControllers,
    flex.AddCorrectiveShapeDrivers,
    flex.RenameShapesToMatchCorrectiveDrivers,
    flex.ActiveDependencyShapes,
    flex.InsertUUID,

    # Export and Import
    export.SmdExporter,
    export.PrefabExporter,
    import_smd.SmdImporter,
    import_smd.ImportDMX,
    import_smd.ImportSMD,
    import_smd.ImportQC,
    import_smd.ImportVMDL,

    # Updater
    updater.SMD_OT_CheckForUpdates,
    updater.SMD_OT_InstallUpdate,

    # Add-on preferences
    ValveSource_AddonPreferences,
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    from . import translations
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass
    bpy.app.translations.register(__name__, translations.translations)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.MESH_MT_shape_key_context_menu.append(menu_func_shapekeys)
    bpy.types.TEXT_MT_edit.append(menu_func_textedit)
    bpy.types.VIEW3D_MT_bone_options_toggle.append(draw_copy_bone_props)
    bpy.types.VIEW3D_MT_pose_context_menu.append(GUI._draw_proc_bone_context_menu)

    try: bpy.ops.wm.addon_disable('EXEC_SCREEN',module="io_smd_tools")
    except: pass

    def make_pointer(prop_type):
        return PointerProperty(name=get_id("settings_prop"),type=prop_type)

    bpy.types.Scene.vs = make_pointer(ValveSource_SceneProps)
    bpy.types.Object.vs = make_pointer(ValveSource_ObjectProps)
    bpy.types.Armature.vs = make_pointer(ValveSource_ArmatureProps)
    bpy.types.Collection.vs = make_pointer(ValveSource_CollectionProps)
    bpy.types.Mesh.vs = make_pointer(ValveSource_MeshProps)
    bpy.types.SurfaceCurve.vs = make_pointer(ValveSource_SurfaceProps)
    bpy.types.Curve.vs = make_pointer(ValveSource_CurveProps)
    bpy.types.Text.vs = make_pointer(ValveSource_TextProps)
    bpy.types.Bone.vs = make_pointer(ValveSource_BoneProps)
    bpy.types.Material.vs = make_pointer(ValveSource_MaterialProps)

    State.hook_events()
    bpy.app.handlers.depsgraph_update_post.append(_on_armature_data_updated)
    bpy.app.handlers.load_post.append(_on_blend_load_refresh_hitbox_snapshot)

    procbones_sim.register()

    from . import viewport_draw as _vd
    _vd.register_draw_handler()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("wm.call_menu_pie", type="V", value="PRESS")
        kmi.properties.name = "SMD_MT_BoneToolsPie"
        _addon_keymaps.append((km, kmi))

    bpy.app.timers.register(updater._startup_check, first_interval=5.0)

def unregister():
    if bpy.app.timers.is_registered(updater._startup_check):
        bpy.app.timers.unregister(updater._startup_check)

    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()

    from . import viewport_draw as _vd
    _vd.unregister_draw_handler()

    procbones_sim.unregister()

    if _on_armature_data_updated in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_armature_data_updated)
    if _on_blend_load_refresh_hitbox_snapshot in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_blend_load_refresh_hitbox_snapshot)
    State.unhook_events()

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.MESH_MT_shape_key_context_menu.remove(menu_func_shapekeys)
    bpy.types.TEXT_MT_edit.remove(menu_func_textedit)
    bpy.types.VIEW3D_MT_bone_options_toggle.remove(draw_copy_bone_props)
    bpy.types.VIEW3D_MT_pose_context_menu.remove(GUI._draw_proc_bone_context_menu)

    bpy.app.translations.unregister(__name__)

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.vs
    del bpy.types.Object.vs
    del bpy.types.Armature.vs
    del bpy.types.Collection.vs
    del bpy.types.Mesh.vs
    del bpy.types.SurfaceCurve.vs
    del bpy.types.Curve.vs
    del bpy.types.Text.vs
    del bpy.types.Bone.vs
    del bpy.types.Material.vs

if __name__ == "__main__":
    register()
