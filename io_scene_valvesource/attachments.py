"""Bone-relative attachment editing and shared export resolution."""

import math
from types import SimpleNamespace

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Euler, Matrix, Vector

from .utils import get_armature, get_attachments, get_bone_matrix
from .physics_gizmos import _radius_ring_shape, _frame, _plane_frame, _plane_hit


def local_matrix(entry):
    matrix = Euler(entry.rotation, 'XYZ').to_matrix().to_4x4()
    matrix.translation = entry.location
    return matrix


def attachment_matrix(arm, entry, rest=False):
    bone = arm.pose.bones.get(entry.bone_name)
    parent = get_bone_matrix(bone, rest_space=rest) if bone else Matrix.Identity(4)
    return arm.matrix_world @ parent @ local_matrix(entry)


def legacy_rest_matrix(empty):
    arm = empty.parent
    pose = arm.data.pose_position
    collections = set(empty.users_collection) | set(arm.users_collection)
    excludes = []

    def collection_paths(layer):
        excludes.append((layer, 'exclude', layer.exclude))
        children = [node for child in layer.children for node in collection_paths(child)]
        return [layer, *children] if children or layer.collection in collections else []

    settings = [(owner, prop) for layer in collection_paths(bpy.context.view_layer.layer_collection)
                for owner, prop in ((layer, 'exclude'), (layer.collection, 'hide_viewport'))]
    settings.extend((obj, 'hide_viewport') for obj in (arm, empty))
    restores = excludes + [(owner, prop, getattr(owner, prop))
                           for owner, prop in settings if prop != 'exclude']
    try:
        for owner, prop in settings:
            if getattr(owner, prop):
                setattr(owner, prop, False)
        if pose != 'REST':
            arm.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        return empty.matrix_world.copy()
    finally:
        if pose != 'REST':
            arm.data.pose_position = pose
        for owner, prop, value in restores:
            if getattr(owner, prop) != value:
                setattr(owner, prop, value)
        bpy.context.view_layer.update()


def resolve_attachments(arm, legacy, warning=None):
    """Merge (object, rest-world matrix) pairs with armature entries by name."""
    entries = []
    names = set()
    for entry in arm.data.vs.attachments:
        if not entry.name.strip() or (entry.bone_name and entry.bone_name not in arm.data.bones):
            if warning:
                warning(f"Attachment '{entry.name}' has an invalid name or bone. Skipping.")
            continue
        if entry.name in names:
            if warning:
                warning(f"Duplicate armature attachment '{entry.name}'. Using the first entry.")
            continue
        names.add(entry.name)
        record = SimpleNamespace(name=entry.name, parent_bone=entry.bone_name)
        entries.append((record, attachment_matrix(arm, entry, rest=True)))
    for empty, matrix in legacy:
        if empty.name in names:
            if warning:
                warning(f"Attachment '{empty.name}' conflicts with a deprecated Empty attachment. "
                        "Using the armature attachment.")
        else:
            entries.append((empty, matrix))
    return entries


def add_attachment(arm, name, bone_name, matrix):
    entry = arm.data.vs.attachments.add()
    entry.name = name
    entry.bone_name = bone_name or ''
    entry.location = matrix.translation
    entry.rotation = matrix.to_euler('XYZ')
    entry.preview_scale = matrix.to_scale()
    arm.data.vs.attachments_index = len(arm.data.vs.attachments) - 1
    return entry


class SMD_OT_AttachmentAdd(bpy.types.Operator):
    bl_idname = 'smd.attachment_add'
    bl_label = 'Add Attachment'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(get_armature(context.object)) and context.mode != 'EDIT_ARMATURE'

    def execute(self, context):
        arm = get_armature(context.object)
        names = {e.name for e in arm.data.vs.attachments}
        name, index = 'attachment', 1
        while name in names:
            name = f'attachment.{index:03}'
            index += 1
        bone = arm.data.bones.active
        add_attachment(arm, name, bone.name if bone else '', Matrix.Identity(4))
        return {'FINISHED'}


class SMD_OT_AttachmentRemove(bpy.types.Operator):
    bl_idname = 'smd.attachment_remove'
    bl_label = 'Remove Attachment'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = get_armature(context.object)
        return bool(arm and 0 <= arm.data.vs.attachments_index < len(arm.data.vs.attachments))

    def execute(self, context):
        avs = get_armature(context.object).data.vs
        avs.attachments.remove(avs.attachments_index)
        avs.attachments_index = min(avs.attachments_index, len(avs.attachments) - 1)
        return {'FINISHED'}


class SMD_OT_AttachmentDuplicate(bpy.types.Operator):
    bl_idname = 'smd.attachment_duplicate'
    bl_label = 'Duplicate Attachment'
    bl_description = 'Duplicate the selected attachment with its transform and preview settings'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = get_armature(context.object)
        return bool(arm and context.mode != 'EDIT_ARMATURE' and
                    0 <= arm.data.vs.attachments_index < len(arm.data.vs.attachments))

    def execute(self, context):
        arm = get_armature(context.object)
        avs = arm.data.vs
        source = avs.attachments[avs.attachments_index]
        base, separator, suffix = source.name.rpartition('.')
        if not (separator and len(suffix) == 3 and suffix.isdecimal()):
            base = source.name
        names = {entry.name for entry in avs.attachments}
        names.update(empty.name for empty in get_attachments(arm))
        index = 1
        while f'{base}.{index:03}' in names:
            index += 1
        duplicate = avs.attachments.add()
        duplicate.name = f'{base}.{index:03}'
        for prop in ('bone_name', 'location', 'rotation', 'preview_object',
                     'color', 'preview_scale', 'show_preview'):
            setattr(duplicate, prop, getattr(source, prop))
        avs.attachments_index = len(avs.attachments) - 1
        return {'FINISHED'}


class SMD_OT_ConvertAttachments(bpy.types.Operator):
    bl_idname = 'smd.convert_attachments'
    bl_label = 'Convert Empty Attachments'
    bl_description = 'Convert attachment Empties to armature entries and delete the converted Empties'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return bool(ob and ((ob.type == 'EMPTY' and ob.vs.dmx_attachment and
                            ob.parent and ob.parent.type == 'ARMATURE') or
                           (get_armature(ob) and get_attachments(get_armature(ob)))))

    def execute(self, context):
        ob = context.object
        arm = ob.parent if ob.type == 'EMPTY' else get_armature(ob)
        empties = [ob] if ob.type == 'EMPTY' else get_attachments(arm)
        count = 0
        for empty in empties:
            bone = arm.pose.bones.get(empty.parent_bone) if empty.parent_type == 'BONE' else None
            if empty.parent_type == 'BONE' and bone is None:
                self.report({'WARNING'}, f"Attachment '{empty.name}' has no valid bone. Skipping.")
                continue
            if any(e.name == empty.name for e in arm.data.vs.attachments):
                self.report({'WARNING'}, f"Attachment '{empty.name}' already exists. Skipping conversion.")
                continue
            world = legacy_rest_matrix(empty)
            parent = arm.matrix_world @ (get_bone_matrix(bone, rest_space=True) if bone else Matrix.Identity(4))
            matrix = parent.inverted_safe() @ world
            entry = add_attachment(arm, empty.name, bone.name if bone else '', matrix)
            slots = empty.vs.attachment_display_meshes
            index = empty.vs.attachment_display_mesh_render_index
            if 0 <= index < len(slots):
                entry.preview_object = slots[index].mesh
                entry.color = slots[index].color
            bpy.data.objects.remove(empty, do_unlink=True)
            count += 1
        self.report({'INFO'}, f'Converted {count} attachment(s)')
        return {'FINISHED'}


class SMD_UL_Attachments(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'name', text='', emboss=False, icon='EMPTY_ARROWS')
        row.label(text=item.bone_name or 'Model Root', icon='BONE_DATA')


class SMD_PT_Attachments(bpy.types.Panel):
    bl_label = ''
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PulseSrcOps'
    bl_parent_id = 'SMD_PT_Armature'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return bool(get_armature(context.object))

    def draw_header(self, context):
        arm = get_armature(context.object)
        self.layout.label(text=f'Attachments ({len(arm.data.vs.attachments)})', icon='EMPTY_ARROWS')

    def draw(self, context):
        arm = get_armature(context.object)
        avs, layout = arm.data.vs, self.layout
        row = layout.row()
        row.template_list('SMD_UL_Attachments', '', avs, 'attachments', avs, 'attachments_index', rows=3)
        buttons = row.column(align=True)
        buttons.operator('smd.attachment_add', text='', icon='ADD')
        buttons.operator('smd.attachment_remove', text='', icon='REMOVE')
        buttons.separator()
        buttons.operator('smd.attachment_duplicate', text='', icon='DUPLICATE')
        layout.operator('smd.convert_attachments', icon='EMPTY_DATA')
        if 0 <= avs.attachments_index < len(avs.attachments):
            entry = avs.attachments[avs.attachments_index]
            box = layout.box()
            box.prop(entry, 'name')
            box.prop_search(entry, 'bone_name', arm.data, 'bones')
            if entry.bone_name and entry.bone_name not in arm.data.bones:
                box.label(text='Bone not found', icon='ERROR')
            box.prop(entry, 'location')
            box.prop(entry, 'rotation')
            box.prop(entry, 'preview_object')
            box.prop(entry, 'show_preview')
            box.prop(entry, 'color')
            box.prop(entry, 'preview_scale')
            box.operator('smd.refresh_attachment_mesh', icon='FILE_REFRESH')


def attachment_pose_selected(context, arm, bone_name):
    return context.mode == 'POSE' and any(
        bone.id_data == arm and bone.name == bone_name
        for bone in (context.selected_pose_bones or []))


def active_attachment(context):
    arm = context.object
    if not arm or arm.type != 'ARMATURE' or not arm.select_get() or context.mode not in {'OBJECT', 'POSE'}:
        return None
    avs = arm.data.vs
    if not 0 <= avs.attachments_index < len(avs.attachments):
        return None
    entry = avs.attachments[avs.attachments_index]
    bone = arm.pose.bones.get(entry.bone_name)
    if entry.bone_name and bone is None:
        return None
    if context.scene.vs.preview_attachment_mesh == 'NONE':
        return None
    if (context.scene.vs.preview_attachment_mesh == 'POSE' and
            not attachment_pose_selected(context, arm, entry.bone_name)):
        return None
    matrix = arm.matrix_world @ (get_bone_matrix(bone) if bone else Matrix.Identity(4))
    return (arm, entry, matrix) if abs(matrix.determinant()) > 1e-12 else None


class SMD_OT_AttachmentDrag(bpy.types.Operator):
    bl_idname = 'smd.attachment_drag'
    bl_label = 'Edit Attachment'
    bl_description = 'Move or rotate along the attachment local axes; Shift for precision, Esc to cancel'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    action: EnumProperty(items=[('MOVE', 'Move', ''), ('PLANE_MOVE', 'Move in Plane', ''),
                                ('ROTATE', 'Rotate', '')])
    axis: IntProperty(default=0, min=0, max=2)

    def invoke(self, context, event):
        active = active_attachment(context)
        if active is None:
            return {'CANCELLED'}
        self._arm, self._entry, self._matrix = active
        self._index = self._arm.data.vs.attachments_index
        self._location = Vector(self._entry.location)
        self._rotation = Euler(self._entry.rotation, 'XYZ')
        self._local_basis = self._rotation.to_matrix()
        self._basis = self._matrix.to_3x3() @ self._local_basis
        self._inverse = self._basis.inverted()
        self._origin = self._matrix @ self._location
        self._unit = Vector((0, 0, 0))
        self._unit[self.axis] = 1
        self._mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self._normal = (self._inverse.transposed() @ self._unit).normalized()
        self._amount = 0.0
        self._movement = Vector((0, 0, 0))
        if self.action == 'MOVE':
            step = self._basis @ self._unit
            start = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, self._origin)
            end = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, self._origin + step)
            if start is None or end is None or (end - start).length < 2:
                return {'CANCELLED'}
            self._screen_axis = (end - start).normalized()
            self._pixels = (end - start).length
        else:
            self._previous = self._hit(context, self._mouse)
            if self._previous is None:
                return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set('Attachment: drag to edit | Shift: precision | Esc: cancel')
        return {'RUNNING_MODAL'}

    def _hit(self, context, mouse):
        hit = _plane_hit(context, mouse, self._origin, self._normal)
        if hit is None:
            return None
        local = self._inverse @ (hit - self._origin)
        local[self.axis] = 0
        if self.action == 'ROTATE':
            return local.normalized() if local.length > 1e-6 else None
        return local

    def modal(self, context, event):
        active = active_attachment(context)
        cancel = (active is None or active[0] != self._arm or
                  self._arm.data.vs.attachments_index != self._index or event.type in {'ESC', 'RIGHTMOUSE'})
        if cancel:
            self._entry.location, self._entry.rotation = self._location, self._rotation
            context.area.header_text_set(None)
            context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            context.area.header_text_set(None)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            precision = 0.1 if event.shift else 1
            if self.action == 'MOVE':
                self._amount += (mouse - self._mouse).dot(self._screen_axis) / self._pixels * precision
                self._mouse = mouse
                self._entry.location = self._location + self._local_basis @ (self._unit * self._amount)
            else:
                hit = self._hit(context, mouse)
                if hit is None:
                    return {'RUNNING_MODAL'}
                if self.action == 'ROTATE':
                    self._amount += math.atan2(self._previous.cross(hit)[self.axis],
                                               self._previous.dot(hit)) * precision
                    rotation = self._local_basis @ Matrix.Rotation(self._amount, 3, 'XYZ'[self.axis])
                    self._entry.rotation = rotation.to_euler('XYZ', self._rotation)
                else:
                    self._movement += (hit - self._previous) * precision
                    self._entry.location = self._location + self._local_basis @ self._movement
                self._previous = hit
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class SMD_GT_AttachmentRotationRing(bpy.types.Gizmo):
    bl_idname = 'SMD_GT_AttachmentRotationRing'

    def setup(self):
        self._ring = _radius_ring_shape(self, 0.018)

    def draw(self, context):
        self.draw_custom_shape(self._ring)

    def draw_select(self, context, select_id):
        self.draw_custom_shape(self._ring, select_id=select_id)


class SMD_GGT_Attachment(bpy.types.GizmoGroup):
    bl_idname = 'SMD_GGT_Attachment'
    bl_label = 'Attachment Editing'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        return active_attachment(context) is not None

    def setup(self, context):
        self._handles = []
        for axis, color in enumerate(((0.9, 0.15, 0.15), (0.2, 0.85, 0.2), (0.2, 0.4, 1))):
            for action, shape, size in (('MOVE', 'SMD_GT_PhysicsMoveArrow', 1.2),
                                         ('PLANE_MOVE', 'SMD_GT_PhysicsMovePlane', 0.8),
                                         ('ROTATE', 'SMD_GT_AttachmentRotationRing', 0.7)):
                gizmo = self.gizmos.new(shape)
                gizmo.color, gizmo.alpha = color, 0.8
                gizmo.color_highlight, gizmo.alpha_highlight = (1, 1, 1), 1
                gizmo.scale_basis = size
                gizmo.use_draw_modal = True
                props = gizmo.target_set_operator('smd.attachment_drag')
                props.action, props.axis = action, axis
                self._handles.append((gizmo, action, axis))

    def draw_prepare(self, context):
        active = active_attachment(context)
        if active is None:
            return
        arm, entry, matrix = active
        position = matrix @ Vector(entry.location)
        basis = matrix.to_3x3() @ Euler(entry.rotation, 'XYZ').to_matrix()
        for gizmo, action, axis in self._handles:
            gizmo.matrix_basis = (_frame(position, basis.col[axis]) if action == 'MOVE'
                                  else _plane_frame(position, basis, axis))
            if action != 'MOVE':
                view = context.region_data.view_rotation @ Vector((0, 0, 1))
                gizmo.hide = abs(gizmo.matrix_basis.to_3x3().col[2].dot(view)) < 0.15


CLASSES = (SMD_OT_AttachmentAdd, SMD_OT_AttachmentRemove, SMD_OT_AttachmentDuplicate, SMD_OT_ConvertAttachments,
           SMD_UL_Attachments, SMD_PT_Attachments, SMD_OT_AttachmentDrag,
           SMD_GT_AttachmentRotationRing, SMD_GGT_Attachment)
