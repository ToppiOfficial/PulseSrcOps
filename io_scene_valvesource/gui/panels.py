import bpy, math
from bpy.types import Panel, UILayout, Collection, PoseBone, Bone, EditBone
from .. import procbones_sim as _procbones_sim
from bpy.app.translations import pgettext
from ..utils import (get_id, State, Compiler, ExportFormat, is_armature, is_mesh, is_empty,
                     is_curve, is_mesh_compatible, modifier_compatible, vertex_maps, vertex_float_maps,
                     cloth_map_groups, hasFlexControllerSource, get_armature, countShapes,
                     MakeObjectIcon, get_active_exportable, get_valid_vertexanimation_object,
                     get_bone_exportname,
                     sanitize_string_for_delta, _build_dme_ctrl_names, _build_stereo_delta_names,
                     get_dme_renamed_delta_names, get_dme_delta_override_conflicts,
                     get_dme_split_delta_conflicts, get_collection_parent_collection)
from ..export_smd import SmdExporter, PrefabExporter
from ..import_smd import SmdImporter
from ..flex import AddCorrectiveShapeDrivers, RenameShapesToMatchCorrectiveDrivers, DmxWriteFlexControllers
from .helpers import _mesh_type_allows, _ensure_cloth_remaps, validate_flex_expression, validate_corrective_components, _count_flex_rule_errors
from .operators import (
    SMD_OT_AssignBoneRotExportOffset,
    SMD_OT_AddFlexController,
    SMD_OT_RemoveFlexController,
    SMD_OT_MoveFlexController,
    SMD_OT_PreviewFlexController,
    SMD_OT_AddFlexRule,
    SMD_OT_RemoveFlexRule,
    SMD_OT_ClearFlexRules,
    SMD_OT_MoveFlexRule,
    SMD_OT_FlexRuleRegexReplace,
    SMD_OT_AddDeltaOverride,
    SMD_OT_RemoveDeltaOverride,
    SMD_OT_ClearDeltaOverrides,
    SMD_OT_AddVertexAnimation,
    SMD_OT_RemoveVertexAnimation,
    SMD_OT_GenerateVertexAnimationQCSnippet,
    SMD_OT_CreateVertexMap_idname,
    SMD_OT_RemoveVertexMap_idname,
    SMD_OT_SelectVertexMap_idname,
    SMD_OT_CreateVertexFloatMap_idname,
    SMD_OT_RemoveVertexFloatMap_idname,
    SMD_OT_SelectVertexFloatMap_idname,
)


class Properties_Panel(Panel):
    bl_label = 'sample_propertiessub'
    bl_category = 'PulseSrcOps'
    bl_region_type = 'UI'
    bl_space_type = 'VIEW_3D'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout


class SMD_PT_ViewportSimulation(Panel):
    bl_label = get_id('panel_viewport_simulation')
    bl_category = 'PulseSrcOps'
    bl_region_type = 'UI'
    bl_space_type = 'VIEW_3D'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        vs = context.scene.vs

        box.label(text=get_id('label_simulate_jigglebones', format_string=True))
        box.prop(context.scene.vs, 'jiggle_sim_enabled', toggle=True)
        sub = box.column(align=True)
        sub.prop(vs, 'sim_jiggle_bones')
        sub.prop(vs, 'sim_proc_bones')
        col = box.column(align=True)
        col.enabled = vs.jiggle_sim_enabled
        col.prop(vs, 'jiggle_sim_rate', slider=True)
        col.operator('smd.reset_simulation', icon='FILE_REFRESH')

        box2 = layout.box().column(align=True)
        box2.label(text='Preview')
        box2.prop(vs, 'preview_export_pose')
        box2.prop(vs, 'preview_jigglebone_constraints')
        box2.prop(vs, 'preview_proc_bones')
        box2.prop(vs, 'preview_hitboxes')
        box2.prop(vs, 'preview_edgeline')
        box2.prop(vs, 'preview_attachment_mesh')
        if vs.preview_edgeline:
            if vs.jiggle_sim_enabled:
                row = box2.row()
                row.alert = True
                row.label(text=get_id('warn_edgeline_jiggle_sim'), icon='PAUSE')
            else:
                row = box2.row()
                row.alert = True
                row.label(text=get_id('warn_edgeline_expensive'), icon='ERROR')
                box2.label(text=get_id('warn_edgeline_approximate'), icon='INFO')
                box2.label(text=get_id('warn_edgeline_smudging'))


class SMD_PT_Scene(Panel):
    bl_label = get_id("exportpanel_title")
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'
    bl_order = 0

    def draw(self, context) -> None:
        l = self.layout
        scene = context.scene

        # Export
        row = l.row(align=True)
        row.scale_y = 1.5
        row.operator(SmdImporter.bl_idname, text="Import", icon='IMPORT')
        row.operator(SmdExporter.bl_idname, text="Export", icon='EXPORT')

        box = l.box()
        row = box.row()
        row.alert = len(scene.vs.export_path) == 0
        row.prop(scene.vs, "export_path")

        row = box.row()
        row.alert = len(scene.vs.engine_path) > 0 and State.compiler == Compiler.UNKNOWN
        row.prop(scene.vs, "engine_path")

        # Format

        if State.datamodelEncoding != 0:
            row = box.row().split(factor=0.33)
            row.label(text=get_id("export_format", True) + ":")
            row.row().prop(scene.vs, "export_format", expand=True)

        if scene.vs.export_format == 'DMX':
            if State.engineBranch is None:
                row = box.split(factor=0.33)
                row.label(text=get_id("exportpanel_dmxver"))
                sub = row.row(align=True)
                sub.prop(scene.vs, "dmx_encoding", text="")
                sub.prop(scene.vs, "dmx_format", text="")
                sub.enabled = not sub.alert
            if State.exportFormat == ExportFormat.DMX:
                col1 = box.column()
                col1.scale_y = 1.2
                col1.prop(scene.vs, "material_path")

                if State.compiler != Compiler.MODELDOC:
                    row = box.row().split(factor=0.33)
                    row.label(text=get_id("prefab_export_mode", True) + ":")
                    row.row().prop(scene.vs, "prefab_export_mode", expand=True)
        else:
            row = box.split(factor=0.33)
            row.label(text=get_id("smd_format", True) + ":")
            row.row().prop(scene.vs, "smd_format", expand=True)

        #Scene

        row = box.row().split(factor=0.33)
        row.label(text=get_id("up_axis", True) + ":")
        row.row().prop(scene.vs, "up_axis", expand=True)

        row = box.row().split(factor=0.33)
        row.label(text=get_id("up_axis_offset", True) + ":")
        row.row().prop(scene.vs, "up_axis_offset", expand=True)

        row = box.row().split(factor=0.33)
        row.label(text=get_id("forward_axis", True) + ":")
        row.row().prop(scene.vs, "forward_axis", expand=True)

        row = box.row().split(factor=0.33)
        row.label(text=get_id("world_scale", True) + ":")
        row.row().prop(scene.vs, "world_scale")

        # Mesh
        row = box.row().split(factor=0.33)
        row.label(text=get_id("weightlink_threshold", True) + ":")
        row.row().prop(scene.vs, "weightlink_threshold", slider=True)

        row = box.row().split(factor=0.33)
        row.label(text=get_id("vertex_influence_limit_mode", True) + ":")
        row.row().prop(scene.vs, "vertex_influence_limit_mode", expand=True)

        if scene.vs.vertex_influence_limit_mode == 'MANUAL':
            row = box.row().split(factor=0.33)
            row.label(text=get_id("vertex_influence_limit", True) + ":")
            row.row().prop(scene.vs, "vertex_influence_limit", slider=True)


class SMD_PT_Exportables(Panel):
    bl_label = get_id('exportables_title')
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'
    bl_order = 1

    @classmethod
    def get_item(cls, context):
        active_exportable = get_active_exportable(context)
        if not active_exportable:
            return None
        return active_exportable.item

    @classmethod
    def is_collection(cls, item):
        return isinstance(item, Collection)

    def draw(self, context) -> None:
        layout = self.layout
        active_object = context.object
        active_exportable = get_active_exportable(context)
        item = active_exportable.item if active_exportable else None
        scene = context.scene

        if active_exportable and active_exportable.is_prefab:
            pitem = active_exportable.prefab_item
            if pitem is not None:
                col = layout.column()
                col.prop(pitem, "filepath", text=get_id("prop_prefab_filepath"), icon='FILE_FOLDER')
        elif item is not None:
            layout.column().prop(item.vs, "subdir", icon='FILE_FOLDER')

        layout.template_list("SMD_UL_ExportItems","",scene.vs,"export_list",scene.vs,"export_list_active",rows=3,maxrows=8)

        if active_exportable and active_exportable.is_prefab:
            arm_ob = active_exportable.obj
            if arm_ob and arm_ob.type == 'ARMATURE':
                armvs = arm_ob.data.vs
                ptype = active_exportable.prefab_type
                if ptype == 'JIGGLEBONES':
                    layout.template_list("SMD_UL_ArmatureItems", "", armvs, "arm_jigglebone_entries",
                                         armvs, "arm_jigglebone_index", rows=3)
                elif ptype == 'ATTACHMENTS':
                    layout.template_list("SMD_UL_ArmatureItems", "", armvs, "arm_attachment_entries",
                                         armvs, "arm_attachment_index", rows=3)
                elif ptype == 'HITBOXES':
                    layout.template_list("SMD_UL_Hitboxes", "", armvs, "hitboxes",
                                         armvs, "hitboxes_index", rows=3)
                elif ptype == 'PROCEDURAL':
                    layout.template_list("SMD_UL_ProcBones", "", armvs, "proc_bones",
                                         armvs, "proc_bones_index", rows=3)
            return

        if not item or not self.is_collection(item): return

        vs = item.vs
        if vs:
            r = layout.row()
            r.alignment = 'CENTER'
            r.prop(vs, "mute")
            # Bypass only applies to nested groups; folds them into the parent group.
            if not vs.mute and get_collection_parent_collection(item) is not None:
                r.prop(vs, "bypass")
            if vs.mute:
                return
            elif State.exportFormat == ExportFormat.DMX:
                r.prop(vs, "automerge")

            if not vs.mute:
                layout.template_list("SMD_UL_GroupItems", item.name, item, "objects", vs, "selected_item", columns=2, rows=2, maxrows=10)


class SMD_PT_Armature(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_armature(get_armature(context.object))

    def draw_header(self, context):
        active_object = get_armature(context.object)
        label = '{} ({})'.format(pgettext("Armature"), active_object.name) if active_object else pgettext("Armature")
        self.layout.label(text=label, icon='ARMATURE_DATA')

    def draw(self, context):
        pass


class SMD_PT_ArmatureData(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Armature'

    def draw_header(self, context):
        self.layout.label(text='Armature Data', icon='ARMATURE_DATA')

    @classmethod
    def poll(cls, context):
        return is_armature(get_armature(context.object))

    def draw(self, context):
        layout = self.layout
        active_armature = get_armature(context.object)

        box = layout.box()
        col = box.column()
        col.enabled = bool(State.exportFormat == ExportFormat.SMD)
        col.prop(active_armature.data.vs,"implicit_zero_bone")

        box = layout.box()
        col = box.column(align=True)
        col.prop(active_armature.data.vs, "ignore_bone_exportnames")
        col.label(text=get_id('label_direction_naming', format_string=True))

        row = col.row()
        row.prop(active_armature.data.vs, 'bone_direction_naming_left', text='Left')
        row.prop(active_armature.data.vs, 'bone_direction_naming_right', text='Right')

        box.prop(active_armature.data.vs, 'bone_name_startcount', slider=True)


class SMD_PT_Action(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Armature'

    @classmethod
    def poll(cls, context):
        return is_armature(get_armature(context.object))

    def draw_header(self, context):
        active_object = get_armature(context.object)
        label = '{} ({})'.format(pgettext("Action"), active_object.name) if active_object else pgettext("Action")
        self.layout.label(text=label, icon='ACTION')

    def draw(self, context):
        layout = self.layout
        active_object = get_armature(context.object)

        box = layout.box()
        col = box.column()
        col.row().prop(active_object.data.vs, "action_selection", expand=True)
        if active_object.data.vs.action_selection != 'CURRENT':
            is_slot_filter = active_object.data.vs.action_selection == 'FILTERED'
            col.prop(active_object.vs, "action_filter", text=get_id("slot_filter") if is_slot_filter else get_id("action_filter"))
            col.prop(active_object.data.vs, "reset_pose_per_anim")


class SMD_PT_Hitboxes(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Armature'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(get_armature(context.object))

    def draw_header(self, context):
        arm_ob = get_armature(context.object)
        count = len(arm_ob.data.vs.hitboxes) if arm_ob else 0
        self.layout.label(text='{} ({})'.format(get_id('panel_hitboxes', True), count), icon='MESH_CUBE')

    def draw(self, context):
        layout = self.layout
        arm_ob = get_armature(context.object)
        arm_data = arm_ob.data
        avs = arm_data.vs
        scvs = context.scene.vs

        row = layout.row(align=True)
        row.prop(avs, 'hboxset_name')
        row.prop(avs, 'hbox_capsule_support', toggle=True, icon='META_CAPSULE', text='')

        row = layout.row()
        row.template_list("SMD_UL_Hitboxes", "", avs, "hitboxes",
                          avs, "hitboxes_index", rows=3)
        col = row.column(align=True)
        col.operator("smd.hitbox_add",    icon='ADD',    text='')
        col.operator("smd.hitbox_remove", icon='REMOVE', text='')
        col.separator()
        col.operator("smd.hitbox_from_bone", icon='BONE_DATA', text='')
        col.separator()
        col.menu("SMD_MT_HitboxSpecials", icon='DOWNARROW_HLT', text='')

        idx = avs.hitboxes_index
        if 0 <= idx < len(avs.hitboxes):
            entry = avs.hitboxes[idx]
            box = layout.box()
            is_capsule = entry.scale > 0

            box.prop_search(entry, 'bone_name', arm_data, 'bones',
                            text=get_id('prop_hitbox_bone'))
            box.prop(entry, 'group')

            split = box.split(factor=0.22, align=True)
            split.label(text=get_id('prop_hitbox_vec_min') + ":")
            split.row(align=True).prop(entry, 'vec_min', text='')

            split = box.split(factor=0.22, align=True)
            split.label(text=get_id('prop_hitbox_vec_max') + ":")
            split.row(align=True).prop(entry, 'vec_max', text='')

            if not is_capsule and any(entry.vec_min[i] > entry.vec_max[i] for i in range(3)):
                box.label(text="Min > Max : inverted box, swap Min and Max", icon='ERROR')

            box.prop(entry, 'rotation', text=get_id('prop_hitbox_rotation'))

            split = box.split(factor=0.7)
            split.prop(entry, 'scale', text=get_id('prop_hitbox_scale'))
            split.label(text="Capsule" if is_capsule else "Box",
                      icon='META_CAPSULE' if is_capsule else 'MESH_CUBE')

        row = layout.row(align=True)
        scvs = context.scene.vs
        row.prop(scvs, 'hitbox_sync_pose',      toggle=True, icon='BONE_DATA')
        row.prop(scvs, 'hitbox_sync_propagate', toggle=True, icon='CONSTRAINT_BONE')


class SMD_PT_ProcBones(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Armature'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(get_armature(context.object))

    def draw_header(self, context):
        arm_ob = get_armature(context.object)
        count = len(arm_ob.data.vs.proc_bones) if arm_ob else 0
        label = '{} ({})'.format(get_id('panel_proc_bones', True), count)
        self.layout.label(text=label, icon='CONSTRAINT_BONE')

    def draw(self, context):
        layout = self.layout
        arm_ob = get_armature(context.object)
        arm_data = arm_ob.data
        avs = arm_data.vs

        row = layout.row()
        row.template_list("SMD_UL_ProcBones", "", avs, "proc_bones",
                          avs, "proc_bones_index", rows=3)
        col = row.column(align=True)
        col.operator("smd.proc_bone_add", icon='ADD', text='')
        col.operator("smd.proc_bone_remove", icon='REMOVE', text='')
        col.separator()
        col.menu("SMD_MT_ProcBoneSpecials", icon='DOWNARROW_HLT', text='')

        idx = avs.proc_bones_index
        if 0 <= idx < len(avs.proc_bones):
            entry = avs.proc_bones[idx]
            box = layout.box()
            row = box.row(align=True)
            row.prop(entry, 'proc_type', expand=True)
            box.prop_search(entry, 'helper_bone', arm_data, 'bones')
            if entry.proc_type == 'TRIGGER':
                box.prop_search(entry, 'driver_bone', arm_data, 'bones')
                box.prop(entry, 'reference_armature')
                box.prop(entry, 'action')
                if entry.action and not getattr(entry.action, 'is_action_legacy', True):
                    box.prop_search(entry, 'action_slot_name', entry.action, 'slots',
                                    text=get_id('prop_proc_bone_slot'))
                if entry.action:
                    fs, fe, valid = _procbones_sim._get_proc_trigger_frame_range(entry, arm_ob)
                    if entry.use_manual_frame_range:
                        row = box.row(align=True)
                        row.prop(entry, 'trigger_frame_start', text=get_id('prop_proc_bone_frame_start'))
                        row.prop(entry, 'trigger_frame_end',   text=get_id('prop_proc_bone_frame_end'))
                    else:
                        row = box.row(align=True)
                        if valid:
                            row.label(text=f"{fs}", icon='KEYFRAME')
                            row.label(text=f"–  {fe}", icon='KEYFRAME')
                        else:
                            row.label(text=get_id('warn_no_trigger_frames'), icon='ERROR')
                    box.prop(entry, 'use_manual_frame_range', toggle=True)
                    nav = box.row(align=True)
                    nav.operator("smd.proc_bone_navigate_frame", text="", icon='REW').direction    = 'FIRST'
                    nav.operator("smd.proc_bone_navigate_frame", text="", icon='PREV_KEYFRAME').direction = 'PREV'
                    nav.prop(entry, 'trigger_preview_frame', text="")
                    nav.operator("smd.proc_bone_navigate_frame", text="", icon='NEXT_KEYFRAME').direction = 'NEXT'
                    nav.operator("smd.proc_bone_navigate_frame", text="", icon='FF').direction     = 'LAST'
                    nav.enabled = valid
                    box.prop(entry, 'trigger_preview_tol')
            elif entry.proc_type == 'LOOKAT':
                box.prop_search(entry, 'driver_bone', arm_data, 'bones',
                                text=get_id('prop_proc_bone_lookat_target'))
                col = box.column(align=True)

                split = col.split(factor=0.22)
                split.label(text=get_id('prop_proc_bone_lookat_aim_axis'))
                split.row().prop(entry, 'lookat_aim_axis', expand=True)

                split = col.split(factor=0.22)
                split.label(text=get_id('prop_proc_bone_lookat_up_axis'))
                split.row().prop(entry, 'lookat_up_axis', expand=True)

                col.separator()

                split = col.split(factor=0.30)
                split.label(text=get_id('prop_proc_bone_lookat_offset'))
                split.prop(entry, 'lookat_offset', text='')


class SMD_PT_Bone(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_armature(context.object) and isinstance(context.active_bone, (PoseBone, Bone))

    def draw_header(self, context):
        active_bone = context.active_bone
        label = '{} ({})'.format(pgettext("Bone"), active_bone.name) if active_bone else pgettext("Bone")
        self.layout.label(text=label, icon='BONE_DATA')

    def draw(self, context):
        layout = self.layout


class SMD_PT_BoneData(Properties_Panel):
    bl_label = 'Bone Data'
    bl_parent_id = 'SMD_PT_Bone'

    @classmethod
    def poll(cls, context):
        return is_armature(context.object) and context.active_bone is not None and not isinstance(context.active_bone, EditBone)

    def draw(self, context):
        layout = self.layout
        active_object = context.object
        active_bone = context.active_bone

        box = layout.box()
        col = box.column(align=True)

        if isinstance(active_bone, PoseBone):
            active_bone_vs = active_bone.bone.vs
        else:
            active_bone_vs = active_bone.vs

        active_bone_exportname = get_bone_exportname(active_bone)
        col.prop(active_bone.vs, 'export_name', placeholder=active_bone_exportname, text='')
        col.separator()
        col.prop(active_bone.vs, 'bone_sort_order', slider=True)
        col.label(text='{}: {}'.format(get_id('label_export_name_format', True), active_bone_exportname))

        split = box.split(factor=0.5)

        col_left = split.column(align=True)
        loc_icon = 'ORIENTATION_GLOBAL' if active_bone_vs.location_offset_in_armature_space else 'ORIENTATION_LOCAL'
        col_left.label(text=get_id('label_location_offset', format_string=True), icon=loc_icon)
        col_left.prop(active_bone_vs, 'ignore_location_offset', text='Ignore', toggle=True)

        row_space = col_left.row(align=True)
        row_space.active = not active_bone_vs.ignore_location_offset
        row_space.prop(active_bone_vs, 'location_offset_in_armature_space', toggle=True, text='ARM', icon='ORIENTATION_GLOBAL')

        sub1 = col_left.column(align=True)
        sub1.active = not active_bone_vs.ignore_location_offset
        if active_bone_vs.location_offset_in_armature_space:
            sub1.prop(active_bone_vs, 'export_location_offset_arm_x')
            sub1.prop(active_bone_vs, 'export_location_offset_arm_y')
            sub1.prop(active_bone_vs, 'export_location_offset_arm_z')
        else:
            sub1.prop(active_bone_vs, 'export_location_offset_x')
            sub1.prop(active_bone_vs, 'export_location_offset_y')
            sub1.prop(active_bone_vs, 'export_location_offset_z')

        col_right = split.column(align=True)
        col_right.label(text=get_id('label_rotation_offset', format_string=True), icon='ORIENTATION_GIMBAL')
        col_right.prop(active_bone_vs, 'ignore_rotation_offset', text='Ignore', toggle=True)

        row_copy_target = col_right.row(align=True)
        row_copy_target.active = not active_bone_vs.ignore_rotation_offset
        row_copy_target.prop_search(active_bone_vs, 'rotation_copy_target', active_object.data, 'bones', text='', icon='BONE_DATA')

        sub2 = col_right.column(align=True)
        sub2.active = not active_bone_vs.ignore_rotation_offset and not active_bone_vs.rotation_copy_target
        sub2.prop(active_bone_vs, 'export_rotation_offset_x')
        sub2.prop(active_bone_vs, 'export_rotation_offset_y')
        sub2.prop(active_bone_vs, 'export_rotation_offset_z')

        box.operator(SMD_OT_AssignBoneRotExportOffset.bl_idname)


class SMD_PT_Jigglebones(Properties_Panel):
    bl_label = get_id('panel_jigglebones')
    bl_parent_id = 'SMD_PT_Bone'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return is_armature(context.object) and context.active_bone is not None and not isinstance(context.active_bone, EditBone)

    def draw(self, context):
        layout = self.layout
        active_object = context.object
        active_armature = get_armature(active_object)
        active_bone = context.active_bone

        box = layout.box()
        if active_bone and active_bone.select:
            self.draw_jigglebone_properties(box, active_bone)
        else:
            box = box.box()
            box.label(text=get_id('label_select_valid_bone', format_string=True), icon='ERROR')

    def _draw_export_buttons(self, layout: UILayout, operator: str, scale_y: float = 1.25,
                            clipboard_text= 'Write to Clipboard',
                            file_text= 'Write to File',
                            clipboard_icon= 'FILE_TEXT',
                            file_icon= 'EXPORT') -> None:
        """Draw standard export button pair (clipboard/file)."""
        row = layout.row(align=True)
        row.scale_y = scale_y
        row.operator(operator, text=clipboard_text, icon=clipboard_icon).to_clipboard = True
        row.operator(operator, text=file_text, icon=file_icon).to_clipboard = False

    def draw_jigglebone_properties(self, layout: UILayout, bone: Bone) -> None:
        vs_bone = bone.vs

        box = layout
        row = box.row()
        row.prop(
            vs_bone, 'bone_is_jigglebone',
            toggle=True,
            icon='DOWNARROW_HLT' if vs_bone.bone_is_jigglebone else 'RIGHTARROW',
            text=f'{bone.name}',
            emboss=True
        )

        if not vs_bone.bone_is_jigglebone:
            return

        box = layout
        col = box.column(align=False)

        col.label(text=get_id('label_jiggle_type', format_string=True), icon='DRIVER')
        subcol = col.column(align=True)
        subcol.prop(vs_bone, 'jiggle_flex_type', text=get_id('label_jiggle_flexibility', format_string=True))
        subcol.prop(vs_bone, 'jiggle_base_type')

        col.separator(factor=0.5)

        self._draw_flexible_rigid_props(col, vs_bone)

        if vs_bone.jiggle_base_type == 'BASESPRING':
            self._draw_basespring_props(col, vs_bone)
        elif vs_bone.jiggle_base_type == 'BOING':
            self._draw_boing_props(col, vs_bone)

        self._draw_collision_props(col, vs_bone)

    def _draw_collision_props(self, layout: UILayout, vs_bone) -> None:
        box = layout.box()
        col = box.column(align=False)
        col.prop(
            vs_bone, 'jiggle_has_collision',
            toggle=True,
            icon='DOWNARROW_HLT' if vs_bone.jiggle_has_collision else 'RIGHTARROW',
            text=get_id('label_jiggle_collision', format_string=True),
        )

        if not vs_bone.jiggle_has_collision:
            return

        subcol = col.column(align=True)
        subcol.prop(vs_bone, 'jiggle_collision_radius0')
        subcol.prop(vs_bone, 'jiggle_collision_radius1')

        col.separator(factor=0.5)
        col.prop(vs_bone, 'jiggle_collision_point0')
        col.prop(vs_bone, 'jiggle_collision_point1')

    def _draw_flexible_rigid_props(self, layout: UILayout, vs_bone) -> None:
        if vs_bone.jiggle_flex_type not in ['FLEXIBLE', 'RIGID']:
            return

        box = layout.box()
        col = box.column(align=False)

        col.label(text=get_id('label_physical_properties', format_string=True), icon='PHYSICS')
        subcol = col.column(align=True)
        subcol.prop(vs_bone, 'use_bone_length_for_jigglebone_length', toggle=True, text=get_id('label_use_bone_length', format_string=True))
        if not vs_bone.use_bone_length_for_jigglebone_length:
            subcol.prop(vs_bone, 'jiggle_length', text=get_id('label_jiggle_length', format_string=True))
        subcol.prop(vs_bone, 'jiggle_tip_mass')

        if vs_bone.jiggle_flex_type == 'FLEXIBLE':
            col.separator(factor=0.5)
            col.label(text=get_id('label_stiffness_damping', format_string=True), icon='FORCE_TURBULENCE')

            subcol = col.column(align=True)
            subcol.prop(vs_bone, 'jiggle_yaw_stiffness', slider=True)
            subcol.prop(vs_bone, 'jiggle_yaw_damping', slider=True)

            subcol = col.column(align=True)
            subcol.prop(vs_bone, 'jiggle_pitch_stiffness', slider=True)
            subcol.prop(vs_bone, 'jiggle_pitch_damping', slider=True)

            col.separator(factor=0.5)
            subcol = col.column(align=True)
            subcol.prop(vs_bone, 'jiggle_allow_length_flex', toggle=True)

            if vs_bone.jiggle_allow_length_flex:
                subcol.prop(vs_bone, 'jiggle_along_stiffness', slider=True)
                subcol.prop(vs_bone, 'jiggle_along_damping', slider=True)

        layout.separator(factor=0.5)
        self._draw_angle_constraints(layout, vs_bone)

    def _draw_angle_constraints(self, layout: UILayout, vs_bone) -> None:
        box = layout.box()
        col = box.column(align=False)

        col.label(text=get_id('label_angle_constraints', format_string=True), icon='CON_ROTLIMIT')
        row = col.row(align=True)
        row.prop(vs_bone, 'jiggle_has_angle_constraint', toggle=True, text=get_id('label_angle', format_string=True))
        row.prop(vs_bone, 'jiggle_has_yaw_constraint', toggle=True, text=get_id('label_yaw', format_string=True))
        row.prop(vs_bone, 'jiggle_has_pitch_constraint', toggle=True, text=get_id('label_pitch', format_string=True))

        has_any = any([
            vs_bone.jiggle_has_angle_constraint,
            vs_bone.jiggle_has_yaw_constraint,
            vs_bone.jiggle_has_pitch_constraint])

        if not has_any:
            return

        col.separator(factor=0.3)

        if vs_bone.jiggle_has_angle_constraint:
            subcol = col.column(align=True)
            subcol.prop(vs_bone, 'jiggle_angle_constraint')
            col.separator(factor=0.3)

        if vs_bone.jiggle_has_yaw_constraint:
            subcol = col.column(align=False)
            subcol.label(text=get_id('label_yaw_limits', format_string=True), icon='EMPTY_SINGLE_ARROW')
            row = subcol.row(align=True)
            row.prop(vs_bone, 'jiggle_yaw_constraint_min', slider=True, text=get_id('label_min', format_string=True))
            row.prop(vs_bone, 'jiggle_yaw_constraint_max', slider=True, text=get_id('label_max', format_string=True))
            subcol.prop(vs_bone, 'jiggle_yaw_friction', slider=True, text=get_id('label_friction', format_string=True))
            col.separator(factor=0.3)

        if vs_bone.jiggle_has_pitch_constraint:
            subcol = col.column(align=False)
            subcol.label(text=get_id('label_pitch_limits', format_string=True), icon='EMPTY_SINGLE_ARROW')
            row = subcol.row(align=True)
            row.prop(vs_bone, 'jiggle_pitch_constraint_min', slider=True, text=get_id('label_min', format_string=True))
            row.prop(vs_bone, 'jiggle_pitch_constraint_max', slider=True, text=get_id('label_max', format_string=True))
            subcol.prop(vs_bone, 'jiggle_pitch_friction', slider=True, text=get_id('label_friction', format_string=True))

    def _draw_basespring_props(self, layout: UILayout, vs_bone) -> None:
        box = layout.box()
        col = box.column(align=False)

        col.label(text=get_id('label_base_spring_properties', format_string=True), icon='FORCE_HARMONIC')
        subcol = col.column(align=True)
        subcol.prop(vs_bone, 'jiggle_base_stiffness', slider=True, text=get_id('label_stiffness', format_string=True))
        subcol.prop(vs_bone, 'jiggle_base_damping', slider=True, text=get_id('label_damping', format_string=True))
        subcol.prop(vs_bone, 'jiggle_base_mass', slider=True, text=get_id('label_mass', format_string=True))

        col.separator(factor=0.5)
        col.label(text=get_id('label_side_constraints', format_string=True), icon='CON_LOCLIMIT')
        row = col.row(align=True)
        row.prop(vs_bone, 'jiggle_has_left_constraint', toggle=True, text=get_id('label_side', format_string=True))
        row.prop(vs_bone, 'jiggle_has_up_constraint', toggle=True, text=get_id('label_up', format_string=True))
        row.prop(vs_bone, 'jiggle_has_forward_constraint', toggle=True, text=get_id('label_forward', format_string=True))

        has_any = any([
            vs_bone.jiggle_has_left_constraint,
            vs_bone.jiggle_has_up_constraint,
            vs_bone.jiggle_has_forward_constraint
        ])

        if not has_any:
            return

        col.separator(factor=0.3)

        constraint_props = [
            (vs_bone.jiggle_has_left_constraint,    'left',    'label_side_limits'),
            (vs_bone.jiggle_has_up_constraint,      'up',      'label_up_limits'),
            (vs_bone.jiggle_has_forward_constraint, 'forward', 'label_forward_limits'),
        ]

        for has_constraint, direction, limits_key in constraint_props:
            if has_constraint:
                subcol = col.column(align=False)
                subcol.label(text=get_id(limits_key, format_string=True), icon='EMPTY_SINGLE_ARROW')
                row = subcol.row(align=True)
                row.prop(vs_bone, f'jiggle_{direction}_constraint_min', slider=True, text=get_id('label_min', format_string=True))
                row.prop(vs_bone, f'jiggle_{direction}_constraint_max', slider=True, text=get_id('label_max', format_string=True))
                subcol.prop(vs_bone, f'jiggle_{direction}_friction', slider=True, text=get_id('label_friction', format_string=True))
                col.separator(factor=0.3)

    def _draw_boing_props(self, layout: UILayout, vs_bone) -> None:
        box = layout.box()
        col = box.column(align=False)

        col.label(text=get_id('label_boing_properties', format_string=True), icon='FORCE_FORCE')
        subcol = col.column(align=True)
        subcol.prop(vs_bone, 'jiggle_impact_speed', slider=True)
        subcol.prop(vs_bone, 'jiggle_impact_angle', slider=True)
        subcol.prop(vs_bone, 'jiggle_damping_rate', slider=True)
        subcol.prop(vs_bone, 'jiggle_frequency', slider=True)
        subcol.prop(vs_bone, 'jiggle_amplitude', slider=True)


class SMD_PT_Mesh(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object)

    def draw_header(self, context):
        layout = self.layout
        active_object = context.object
        label = '{} ({})'.format(pgettext("Mesh"), active_object.name) if is_mesh_compatible(active_object) else pgettext("Mesh")
        layout.label(text=label, icon='MESH_DATA')

    def draw(self, context):
        active_object = context.object
        vs = active_object.vs

        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False

        box = layout.box().column(align=True)
        box.label(text='Mesh Type')
        box.row(align=True).prop(vs, 'mesh_type',expand=True)
        if vs.mesh_type == 'CLOTHPROXY' and context.scene.vs.export_format != 'DMX':
            box.label(text="Cloth Proxy requires DMX export format", icon='ERROR')

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        if vs.mesh_type == 'DEFAULT':
            box = layout.box().column(align=True)
            box.prop_search(vs, 'non_exportable_vgroup', active_object, 'vertex_groups')
            box.separator(factor=0.5)
            box.prop(vs, 'non_exportable_vgroup_tolerance')


class SMD_PT_Shapekey(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'shapekey')

    def draw_header(self, context):
        active_object = context.object
        val1, val2 = countShapes(active_object)
        label = '{} ({} Shapes, {} Correctives)'.format(pgettext("Shape keys"), val1, val2) if is_mesh_compatible(active_object) else pgettext("Shape Keys")
        self.layout.label(text=label, icon='SHAPEKEY_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        if not is_mesh_compatible(active_object):
            layout.label(text=get_id("panel_select_mesh"), icon='ERROR')
            return

        num_shapes, num_correctives = countShapes(active_object)

        box = layout.box()
        col = box.column()
        col.prop(active_object.data.vs, "bake_shapekey_as_basis_normals")
        col.prop(active_object.data.vs, "normalize_shapekeys")

        col = box.column()
        col.scale_y = 1.2
        row = col.row(align=True)
        row.prop(active_object.vs,"flex_controller_mode",expand=True)

        def insertCorrectiveUi(parent):
            col = parent.column(align=True)
            col.operator(AddCorrectiveShapeDrivers.bl_idname, icon='DRIVER',text=get_id("gen_drivers",True))
            col.operator(RenameShapesToMatchCorrectiveDrivers.bl_idname, icon='SYNTAX_OFF',text=get_id("apply_drivers",True))

        def insertStereoSplitUi(parent):
            col = parent.column()
            subbx = col.box()

            subbx.label(text=get_id("exportables_flex_split"))
            sharpness_col = subbx.column(align=True)

            r = sharpness_col.split(factor=0.33,align=True)
            r.label(text=active_object.data.name + ":",icon=MakeObjectIcon(active_object,suffix='_DATA'),translate=False) # type: ignore
            r2 = r.split(factor=0.7,align=True)

            if active_object.data.vs.flex_stereo_mode == 'VGROUP':
                r2.alert = active_object.vertex_groups.get(active_object.data.vs.flex_stereo_vg) is None
                r2.prop_search(active_object.data.vs,"flex_stereo_vg",active_object,"vertex_groups",text="")
            else:
                r2.prop(active_object.data.vs,"flex_stereo_sharpness",text="Sharpness")

            r2.prop(active_object.data.vs,"flex_stereo_mode",text="")

        if active_object.vs.flex_controller_mode == 'ADVANCED':
            controller_source = col.row()
            controller_source.alert = hasFlexControllerSource(active_object.vs.flex_controller_source) == False
            controller_source.prop(active_object.vs,"flex_controller_source",text=get_id("exportables_flex_src"),icon = 'TEXT' if active_object.vs.flex_controller_source in bpy.data.texts else 'NONE')

            row = col.row(align=True)
            row.operator(DmxWriteFlexControllers.bl_idname,icon='TEXT',text=get_id("exportables_flex_generate", True))
            row.operator("wm.url_open",text=get_id("exportables_flex_help", True),icon='HELP').url = "http://developer.valvesoftware.com/wiki/Blender_SMD_Tools_Help#Flex_properties"

            insertCorrectiveUi(col)

            insertStereoSplitUi(col)

        elif active_object.vs.flex_controller_mode == 'DME':
            if State.exportFormat != ExportFormat.DMX:
                info_row = box.row()
                info_row.label(text=get_id("warn_dme_dmx_only_panel"), icon='INFO')

            # --- Flex Controllers ---
            ctrl_header = box.row()
            ctrl_header.prop(context.scene.vs, "show_flex_items",
                             icon='TRIA_DOWN' if context.scene.vs.show_flex_items else 'TRIA_RIGHT',
                             icon_only=True, emboss=False)
            ctrl_header.label(text=get_id("label_dme_flex_controllers"), icon='SHAPEKEY_DATA')

            if context.scene.vs.show_flex_items:
                ctrl_col = box.column()
                ctrl_row = ctrl_col.row()
                ctrl_list_col = ctrl_row.column()
                ctrl_list_col.template_list(
                    "SMD_UL_DmeFlexControllers", "",
                    active_object.vs, "dme_flexcontrollers",
                    active_object.vs, "dme_flexcontrollers_index",
                )
                ctrl_btn_col = ctrl_row.column(align=True)
                ctrl_btn_col.operator(SMD_OT_AddFlexController.bl_idname, icon='ADD', text='')
                ctrl_btn_col.operator(SMD_OT_RemoveFlexController.bl_idname, icon='REMOVE', text='')
                ctrl_btn_col.separator()
                ctrl_btn_col.menu('SMD_MT_FlexControllerSpecials', icon='DOWNARROW_HLT', text='')
                ctrl_btn_col.separator()
                up = ctrl_btn_col.operator(SMD_OT_MoveFlexController.bl_idname, icon='TRIA_UP', text='')
                up.direction = 'UP'
                down = ctrl_btn_col.operator(SMD_OT_MoveFlexController.bl_idname, icon='TRIA_DOWN', text='')
                down.direction = 'DOWN'

                idx = active_object.vs.dme_flexcontrollers_index
                if len(active_object.vs.dme_flexcontrollers) > 0 and idx != -1:
                    item = active_object.vs.dme_flexcontrollers[idx]
                    ctrl_col.separator(factor=0.5)
                    item_col = ctrl_col.column(align=True)

                    r = item_col.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Controller Name')
                    name_r = r.row()
                    name_r.alert = not bool(item.controller_name and item.controller_name.strip())
                    name_r.prop(item, 'controller_name', text='')

                    r = item_col.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Shape Key')
                    if active_object.data.shape_keys:
                        r.prop_search(item, 'shapekey', active_object.data.shape_keys, 'key_blocks', text='')
                    else:
                        r.prop(item, 'shapekey', text='')

                    r = item_col.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='')
                    flags = r.row(align=True)
                    flags.prop(item, 'stereo', text='Stereo', toggle=True)
                    flags.prop(item, 'eyelid', text='Eyelid', toggle=True)

                    r = item_col.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Range')
                    range_row = r.row(align=True)
                    range_row.prop(item, 'flex_min', text='Min')
                    range_row.prop(item, 'flex_max', text='Max')

                    r = item_col.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Flex Group')
                    r.prop(item, 'flexgroup', text='')

                    if item.flexgroup == 'CUSTOM':
                        r = item_col.split(factor=0.33, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Custom Group')
                        cr = r.row()
                        cr.alert = not bool(item.flexgroup_custom and item.flexgroup_custom.strip())
                        cr.prop(item, 'flexgroup_custom', text='')

            # --- Flex Rules & Domination ---
            rules_header = box.row()
            rules_header.prop(context.scene.vs, "show_flex_rules_items",
                              icon='TRIA_DOWN' if context.scene.vs.show_flex_rules_items else 'TRIA_RIGHT',
                              icon_only=True, emboss=False)
            rules_header.label(text=get_id("label_dme_flex_rules"), icon='DRIVER')
            rule_err_count = _count_flex_rule_errors(active_object)
            if rule_err_count:
                err_label = rules_header.row()
                err_label.alert = True
                err_label.label(text=str(rule_err_count), icon='ERROR')

            if context.scene.vs.show_flex_rules_items:
                rules_col = box.column()
                rules_row = rules_col.row()
                rules_list_col = rules_row.column()
                rules_list_col.template_list(
                    "SMD_UL_DmeFlexRules", "",
                    active_object.vs, "dme_flex_rules",
                    active_object.vs, "dme_flex_rules_index",
                )
                rules_btn_col = rules_row.column(align=True)
                rules_btn_col.operator(SMD_OT_AddFlexRule.bl_idname, icon='ADD', text='')
                rules_btn_col.operator(SMD_OT_RemoveFlexRule.bl_idname, icon='REMOVE', text='')
                rules_btn_col.separator()
                up = rules_btn_col.operator(SMD_OT_MoveFlexRule.bl_idname, icon='TRIA_UP', text='')
                up.direction = 'UP'
                dn = rules_btn_col.operator(SMD_OT_MoveFlexRule.bl_idname, icon='TRIA_DOWN', text='')
                dn.direction = 'DOWN'
                rules_btn_col.separator()
                rules_btn_col.operator(SMD_OT_FlexRuleRegexReplace.bl_idname, icon='VIEWZOOM', text='')
                rules_btn_col.separator()
                rules_btn_col.operator(SMD_OT_ClearFlexRules.bl_idname, icon='TRASH', text='')

                ridx = active_object.vs.dme_flex_rules_index
                if len(active_object.vs.dme_flex_rules) > 0 and ridx != -1:
                    rule = active_object.vs.dme_flex_rules[ridx]
                    rules_col.separator(factor=0.5)
                    rule_col = rules_col.column(align=True)

                    rule_col.row().prop(rule, 'rule_type', expand=True)
                    rule_col.separator(factor=0.5)

                    if rule.rule_type == 'PASSTHROUGH':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Controller')
                        r.prop(rule, 'name', text='')
                    elif rule.rule_type == 'EXPRESSION':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Local Var')
                        r.prop(rule, 'name', text='')
                    elif rule.rule_type == 'LOCALVAR':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Variable Name')
                        r.prop(rule, 'name', text='')
                    elif rule.rule_type == 'CORRECTIVE':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Components')
                        r.prop(rule, 'components', text='')
                        rule_col.label(text=get_id("label_dme_corrective_hint"), icon='INFO')

                        comp_str = rule.components.strip()
                        if comp_str:
                            sk_names = set(active_object.data.shape_keys.key_blocks.keys()) if active_object.data.shape_keys else set()
                            comp_errs = validate_corrective_components(comp_str, sk_names)
                            if not comp_errs:
                                rule_col.label(text=get_id("label_dme_components_valid"), icon='CHECKMARK')
                            else:
                                for name in comp_errs:
                                    err_row = rule_col.row()
                                    err_row.alert = True
                                    err_row.label(text=get_id("label_dme_unknown_shapekey", True).format(name), icon='ERROR')

                    elif rule.rule_type == 'DOMINATION':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Dominators')
                        r.prop(rule, 'dominator_names', text='')

                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Suppressed')
                        r.prop(rule, 'suppressed_names', text='')

                        rule_col.label(text=get_id("label_dme_dominator_hint"), icon='INFO')
                        rule_col.label(text=get_id("label_dme_suppressed_hint"), icon='BLANK1')

                    if rule.rule_type == 'EXPRESSION':
                        r = rule_col.split(factor=0.25, align=True)
                        r.alignment = 'RIGHT'
                        r.label(text='Expression')
                        r.prop(rule, 'expression', text='')
                        rule_col.label(text=get_id("label_dme_expression_hint"), icon='INFO')

                        expr = rule.expression.strip()
                        if expr:
                            sk_names = set(active_object.data.shape_keys.key_blocks.keys()) if active_object.data.shape_keys else set()
                            ctrl_names = _build_dme_ctrl_names(active_object.vs)
                            localvar_names = set(
                                r.name for r in active_object.vs.dme_flex_rules
                                if r.rule_type == 'LOCALVAR' and r.name
                            )
                            stereo_delta_names = _build_stereo_delta_names(active_object.vs)
                            renamed_delta_names = get_dme_renamed_delta_names(active_object)
                            delta_errs, ctrl_errs = validate_flex_expression(expr, sk_names, ctrl_names, localvar_names, stereo_delta_names, renamed_delta_names)
                            if not delta_errs and not ctrl_errs:
                                rule_col.label(text=get_id("label_dme_expression_valid"), icon='CHECKMARK')
                            else:
                                for name in delta_errs:
                                    err_row = rule_col.row()
                                    err_row.alert = True
                                    err_row.label(text=get_id("label_dme_unknown_delta", True).format(name), icon='ERROR')
                                for name in ctrl_errs:
                                    err_row = rule_col.row()
                                    err_row.alert = True
                                    err_row.label(text=get_id("label_dme_unknown_controller", True).format(name), icon='ERROR')

            # --- Delta Name Overrides ---
            ov_header = box.row()
            ov_header.prop(context.scene.vs, "show_flex_delta_overrides",
                           icon='TRIA_DOWN' if context.scene.vs.show_flex_delta_overrides else 'TRIA_RIGHT',
                           icon_only=True, emboss=False)
            ov_header.label(text="Delta Map", icon='SORTALPHA')
            ov_conflicts = get_dme_delta_override_conflicts(active_object)
            if ov_conflicts:
                ov_err = ov_header.row()
                ov_err.alert = True
                ov_err.label(text=str(len(ov_conflicts)), icon='ERROR')

            if context.scene.vs.show_flex_delta_overrides:
                ov_col = box.column()
                ov_row = ov_col.row()
                ov_list_col = ov_row.column()
                ov_list_col.template_list(
                    "SMD_UL_DmeDeltaOverrides", "",
                    active_object.vs, "dme_delta_overrides",
                    active_object.vs, "dme_delta_overrides_index",
                )
                ov_btn_col = ov_row.column(align=True)
                ov_btn_col.operator(SMD_OT_AddDeltaOverride.bl_idname, icon='ADD', text='')
                ov_btn_col.operator(SMD_OT_RemoveDeltaOverride.bl_idname, icon='REMOVE', text='')
                ov_btn_col.separator()
                ov_btn_col.operator(SMD_OT_ClearDeltaOverrides.bl_idname, icon='TRASH', text='')

                ovidx = active_object.vs.dme_delta_overrides_index
                if len(active_object.vs.dme_delta_overrides) > 0 and ovidx != -1:
                    ov_item = active_object.vs.dme_delta_overrides[ovidx]
                    ov_col.separator(factor=0.5)
                    ov_detail = ov_col.column(align=True)

                    r = ov_detail.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Shape Key')
                    if active_object.data.shape_keys:
                        r.prop_search(ov_item, 'shapekey', active_object.data.shape_keys, 'key_blocks', text='')
                    else:
                        r.prop(ov_item, 'shapekey', text='')

                    r = ov_detail.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='Delta Name')
                    r.prop(ov_item, 'delta_name', text='')

                    r = ov_detail.split(factor=0.33, align=True)
                    r.alignment = 'RIGHT'
                    r.label(text='')
                    r.prop(ov_item, 'split_lr', text='Split to L/R', toggle=True)

                    if ov_item.split_lr and ov_item.delta_name.strip():
                        base = sanitize_string_for_delta(ov_item.delta_name.strip())
                        if base:
                            hint = ov_detail.row()
                            hint.label(text=get_id("label_dme_split_hint", True).format(base), icon='MOD_MIRROR')

                    if ovidx in ov_conflicts:
                        err_row = ov_detail.row()
                        err_row.alert = True
                        err_row.label(text=get_id("label_dme_override_conflict"), icon='ERROR')

                    if ovidx in get_dme_split_delta_conflicts(active_object):
                        err_row = ov_detail.row()
                        err_row.alert = True
                        err_row.label(text=get_id("label_dme_split_on_controller"), icon='ERROR')

            insertStereoSplitUi(box.column())
        else:
            insertCorrectiveUi(col)


class SMD_PT_Vertexmap(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'vertexmap')

    def draw_header(self, context):
        layout = self.layout
        layout.label(text='Vertex Maps', icon='MOD_VERTEX_WEIGHT')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        box : UILayout = layout.box()
        col = box.column(align=True)

        if State.exportFormat != ExportFormat.DMX:
            box.label(text=get_id('label_dmx_only', format_string=True), icon='ERROR')

        col.label(text=get_id('label_vertex_maps', format_string=True))
        for map_name in vertex_maps:
            r = col.row()
            r.label(text=get_id(map_name),icon='GROUP_VCOL')

            add_remove = r.row(align=True)
            add_remove.operator(SMD_OT_CreateVertexMap_idname + map_name,icon='ADD',text="")
            add_remove.operator(SMD_OT_RemoveVertexMap_idname + map_name,icon='REMOVE',text="")
            add_remove.operator(SMD_OT_SelectVertexMap_idname + map_name,text="Activate")


class SMD_PT_Vertexfloatmap(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'vertexfloatmap')

    def draw_header(self, context):
        layout = self.layout
        layout.label(text='Vertex Float Maps', icon='MOD_VERTEX_WEIGHT')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        layout.operator("wm.url_open", text=get_id("help", True), icon='INTERNET').url = "http://developer.valvesoftware.com/wiki/DMX/Source_2_Vertex_attributes"

        box : UILayout = layout.box()

        col = box.column()
        col.label(text=get_id('label_vertex_float_maps', format_string=True))

        col.scale_y = 1.15

        existing_remaps = {r.group: r for r in active_object.vs.vertex_map_remaps}

        # Defer remap initialization to avoid drawing data mutation
        if len(existing_remaps) < len(vertex_float_maps):
            bpy.app.timers.register(_ensure_cloth_remaps)

        # Draw cloth maps grouped by category
        for group_name, group_maps in cloth_map_groups.items():
            group_box = col.box()
            group_col = group_box.column(align=True)

            # Group header with category icon
            group_header = group_col.row(align=True)
            group_header.scale_y = 0.9
            group_header.label(text=group_name, icon='FOLDER_REDIRECT')

            # Render each map in the group
            for map_name in group_maps:
                display_name = map_name.replace("cloth_", "").replace("_", " ").title()
                remap = existing_remaps.get(map_name)

                split = group_col.split(align=True, factor=0.5)

                # Left: activate / add / remove
                left = split.row(align=True)
                left.operator(SMD_OT_SelectVertexFloatMap_idname + map_name, text=display_name, icon='GROUP_VERTEX')
                left.operator(SMD_OT_CreateVertexFloatMap_idname + map_name, icon='ADD', text="")
                left.operator(SMD_OT_RemoveVertexFloatMap_idname + map_name, icon='REMOVE', text="")

                # Right: remap range (always show, as timer will ensure entries exist)
                right = split.row(align=True)
                if remap is not None:
                    right.prop(remap, "min", text="Min")
                    right.prop(remap, "max", text="Max")
                else:
                    right.label(text="0.0 -> 1.0", icon='LOCKED')


class SMD_PT_Vertexanimations(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'vertexanimation')

    def draw_header(self, context):
        layout = self.layout
        layout.label(text='Vertex Animations', icon='ANIM_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = get_valid_vertexanimation_object(context.object)

        op3 = layout.operator("wm.url_open", text='Vertex Animations Help', icon='INTERNET')
        op3.url = "http://developer.valvesoftware.com/wiki/Vertex_animation"

        if active_object is None:
            layout.label(text=get_id("panel_select_mesh"))
            return

        box = layout.box()

        box.label(text='{}: {}'.format(get_id('label_target_object', True), active_object.name), icon='MESH_DATA' if is_mesh_compatible(active_object) else "OUTLINER_COLLECTION")
        row = box.row(align=True)
        row.operator(SMD_OT_AddVertexAnimation.bl_idname, icon="ADD", text="Add")

        remove_op = row.operator(SMD_OT_RemoveVertexAnimation.bl_idname, icon="REMOVE", text="Remove")
        remove_op.vertexindex = active_object.vs.active_vertex_animation

        if active_object.vs.vertex_animations:
            box.template_list("SMD_UL_VertexAnimationItem", "", active_object.vs, "vertex_animations", active_object.vs, "active_vertex_animation", rows=2, maxrows=4)
            box.operator(SMD_OT_GenerateVertexAnimationQCSnippet.bl_idname, icon='FILE_TEXT')


class SMD_PT_ToonEdgeline(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'toonedgeline')

    def draw_header(self, context):
        active_object = context.object
        is_outline = active_object.vs.use_toon_edgeline
        label = '{} ({})'.format(get_id("panel_toon_outline_edgeline", True), str(is_outline)) if is_mesh_compatible(active_object) else get_id("panel_toon_outline_edgeline", True)
        self.layout.label(text=label, icon='MOD_SOLIDIFY')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        active_object = context.object

        if not is_mesh_compatible(active_object) or active_object.type not in modifier_compatible:
            layout.label(text=get_id("panel_select_mesh"), icon='ERROR')
            return

        vs = active_object.vs

        box = layout.box().column(align=True)
        box.prop(vs, 'use_toon_edgeline')

        col = box.column(align=True)
        col.enabled = vs.use_toon_edgeline
        col.prop(vs, 'edgeline_per_material')
        col.prop(vs, 'export_edgeline_separately', text="Export Edgeline Separately")
        col.prop(vs, 'base_toon_edgeline_thickness', text='Thickness')
        col.prop_search(vs, 'toon_edgeline_vertexgroup', active_object, 'vertex_groups', text="Outline Width VertexGroup", icon='GROUP_VERTEX')


class SMD_PT_LOD(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'lod')

    def draw_header(self, context):
        active_object = context.object
        is_lod = active_object.vs.generate_lods
        label = '{} ({})'.format(get_id("panel_level_of_detail", True), str(is_lod)) if is_mesh_compatible(active_object) else get_id("panel_level_of_detail", True)
        self.layout.label(text=label, icon='MOD_DECIM')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        if not is_mesh_compatible(active_object) or active_object.type not in modifier_compatible:
            layout.label(text=get_id("panel_select_mesh"), icon='ERROR')
            return

        vs = active_object.vs

        box = layout.box()
        box.prop(vs, 'generate_lods', text="Generate LODs on export", toggle=True)

        col = box.column(align=True)
        col.enabled = vs.generate_lods

        col.prop(vs, 'lod_count', slider=True)
        col.prop(vs, 'decimate_factor', slider=True)


class SMD_PT_MeshSplit(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'meshsplit')

    def draw_header(self, context):
        active_object = context.object
        is_meshsplited = active_object.vs.use_mesh_split
        label = '{} ({})'.format(get_id("panel_mesh_split", True), str(is_meshsplited)) if is_mesh_compatible(active_object) else get_id("panel_mesh_split", True)
        self.layout.label(text=label, icon='TEXTURE_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        if not is_mesh_compatible(active_object) or active_object.type not in modifier_compatible:
            layout.label(text=get_id("panel_select_mesh"), icon='ERROR')
            return

        vs = active_object.vs

        box = layout.box()
        box.prop(vs, 'use_mesh_split', toggle=True)

        col = box.column(align=True)
        col.enabled = vs.use_mesh_split

        col.prop(vs, 'export_mesh_split_separately')
        col.prop(vs, 'max_mesh_split', slider=True)
        col.prop(vs, 'mesh_split_threshold', slider=True)


class SMD_PT_MeshBackface(Properties_Panel):
    bl_label = ''
    bl_parent_id = 'SMD_PT_Mesh'

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object) and _mesh_type_allows(context.object, 'backface')

    def draw_header(self, context):
        active_object = context.object
        generate_backface = active_object.vs.generate_backface
        label = '{} ({})'.format(get_id("panel_backface", True), str(generate_backface)) if is_mesh_compatible(active_object) else get_id("panel_backface", True)
        self.layout.label(text=label, icon='NORMALS_FACE')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        if not is_mesh_compatible(active_object) or active_object.type not in modifier_compatible:
            layout.label(text=get_id("panel_select_mesh"), icon='ERROR')
            return

        vs = active_object.vs

        box = layout.box()
        col = box.column(align=True)
        col.prop(vs, 'generate_backface', toggle=True)

        col = box.column(align=True)
        col.enabled = vs.generate_backface
        col.prop_search(vs, 'backface_vgroup', active_object, 'vertex_groups')
        col.separator(factor=0.5)
        col.prop(vs, 'backface_vgroup_tolerance')


class SMD_PT_Material(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_mesh_compatible(context.object)

    def draw_header(self, context):
        active_object = context.object
        active_material = active_object.active_material if is_mesh(active_object) else None
        label = '{} ({})'.format(pgettext("Material"), active_material.name) if active_material else pgettext("Material")
        self.layout.label(text=label, icon='MATERIAL_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = context.object
        active_material = active_object.active_material

        if not active_material:
            layout.label(text=get_id("panel_select_mesh_mat"), icon='ERROR')
            return

        box = layout.box()

        if State.exportFormat == ExportFormat.DMX:
            box.prop(active_material.vs, 'override_dmx_export_path', placeholder=context.scene.vs.material_path)


class SMD_PT_Empty(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_empty(context.object)

    def draw_header(self, context):
        active_object = context.object
        label = '{} ({})'.format(pgettext("Empty"), active_object.name) if is_empty(active_object) else pgettext("Empty")
        self.layout.label(text=label, icon='EMPTY_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        box = layout.box()

        col = box.column()
        vs_ob = active_object.vs
        col.prop(vs_ob, 'dmx_attachment', toggle=False)

        if vs_ob.dmx_attachment and active_object.children:
            col.alert = True
            col.box().label(text="Attachment cannot be a parent", icon='WARNING_LARGE')
            col.alert = False

        if vs_ob.dmx_attachment:
            col.separator()
            col.label(text="Display Meshes", icon='MESH_DATA')
            row = col.row()
            row.template_list(
                "SMD_UL_AttachmentDisplayMeshes", "",
                vs_ob, "attachment_display_meshes",
                vs_ob, "attachment_display_meshes_index",
                rows=3,
            )
            btn_col = row.column(align=True)
            btn_col.operator('smd.add_attachment_display_mesh',    icon='ADD',    text='')
            btn_col.operator('smd.remove_attachment_display_mesh', icon='REMOVE', text='')

            idx = vs_ob.attachment_display_meshes_index
            if 0 <= idx < len(vs_ob.attachment_display_meshes):
                item = vs_ob.attachment_display_meshes[idx]
                box = col.box()
                box.prop(item, 'mesh', text="")
                box.prop(item, 'color')


class SMD_PT_Curve(Properties_Panel):
    bl_label = ''

    @classmethod
    def poll(cls, context):
        return is_curve(context.object)

    def draw_header(self, context):
        active_object = context.object
        label = '{} ({})'.format(pgettext("Curve"), active_object.name) if is_curve(active_object) else pgettext("Curve")
        self.layout.label(text=label, icon='CURVE_DATA')

    def draw(self, context):
        layout = self.layout
        active_object = context.object

        box = layout.box()

        done = set()

        row = box.split(factor=0.33)
        row.label(text=context.object.data.name + ":",icon=MakeObjectIcon(context.object,suffix='_DATA'),translate=False) # type: ignore
        row.prop(context.object.data.vs,"faces",text="")
        done.add(context.object.data)
