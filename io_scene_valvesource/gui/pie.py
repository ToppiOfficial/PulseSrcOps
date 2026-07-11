import bpy
from ..utils import get_id
from .operators import SMD_OT_AssignBoneRotExportOffset, SMD_OT_CopySourceBoneProps, SMD_OT_CopyJigglebonesFromArmature


class SMD_MT_BoneToolsPie(bpy.types.Menu):
    bl_label = "SMD Tools"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' or not context.mode.startswith('EDIT_')

    def draw(self, context):
        pie = self.layout.menu_pie()

        # W - Copy box
        box = pie.box().column()
        box.operator(SMD_OT_AssignBoneRotExportOffset.bl_idname, icon='EMPTY_AXIS')
        box.operator(SMD_OT_CopySourceBoneProps.bl_idname, text="Copy Source Bone Props", icon='BONE_DATA')
        op = box.operator(SMD_OT_CopySourceBoneProps.bl_idname, text="Copy Jigglebone", icon='OUTLINER_OB_ARMATURE')
        op.copy_jigglebone = True
        op.copy_name = False
        op.copy_rotation = False
        op.copy_location = False
        op.to_invoke = False
        box.operator(SMD_OT_CopyJigglebonesFromArmature.bl_idname, icon='ARMATURE_DATA')

        # E - Preview box
        box = pie.box().column()
        box.operator("wm.context_toggle", text="Simulate Jigglebones", icon='PHYSICS').data_path = "scene.vs.jiggle_sim_enabled"
        box.operator("wm.context_toggle", text=get_id('prop_preview_export_pose'), icon='AXIS_SIDE').data_path = "scene.vs.preview_export_pose"
        box.operator("wm.context_toggle", text=get_id('prop_preview_proc_bones'), icon='AXIS_SIDE').data_path = "scene.vs.sim_proc_bones"
        box.operator("wm.context_toggle", text=get_id('prop_preview_jigglebone_constraints'), icon='AXIS_SIDE').data_path = "scene.vs.preview_jigglebone_constraints"
        box.label(text=get_id('prop_preview_hitboxes'))
        row = box.row(align=True)
        row.prop(context.scene.vs, 'preview_hitboxes', expand=True)
        box.label(text=get_id('prop_preview_attachment_mesh'))
        row = box.row(align=True)
        row.prop(context.scene.vs, 'preview_attachment_mesh', expand=True)
