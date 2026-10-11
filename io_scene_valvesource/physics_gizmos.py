import math

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Euler, Matrix, Vector

from .utils import get_bone_matrix


def _active_shape(context, kind='PHYSICS'):
    ob = context.object
    if ob is None or ob.type != 'ARMATURE' or not ob.select_get() or context.mode not in {'OBJECT', 'POSE'}:
        return None
    avs = ob.data.vs
    entries = avs.hitboxes if kind == 'HITBOX' else avs.physics_shapes
    index = avs.hitboxes_index if kind == 'HITBOX' else avs.physics_shapes_index
    if not 0 <= index < len(entries):
        return None
    entry = entries[index]
    bone = ob.pose.bones.get(entry.bone_name)
    mode = context.scene.vs.preview_hitboxes if kind == 'HITBOX' else context.scene.vs.preview_physics_shapes
    if bone is None or mode == 'NONE':
        return None
    if mode == 'POSE' and (context.mode != 'POSE' or
                           bone.name not in {pb.name for pb in (context.selected_pose_bones or [])}):
        return None
    matrix = ob.matrix_world @ get_bone_matrix(bone)
    if abs(matrix.determinant()) < 1e-12:
        return None
    return ob, entry, matrix


def _shape_radii(entry, kind):
    return (entry.scale, entry.scale) if kind == 'HITBOX' else (entry.radius0, entry.radius1)


def _shape_index(ob, kind):
    return ob.data.vs.hitboxes_index if kind == 'HITBOX' else ob.data.vs.physics_shapes_index


def _frame(position, direction):
    matrix = direction.normalized().to_track_quat('Z', 'Y').to_matrix().to_4x4()
    matrix.translation = position
    return matrix


class SMD_GT_PhysicsRadiusRing(bpy.types.Gizmo):
    bl_idname = 'SMD_GT_PhysicsRadiusRing'

    def setup(self):
        vertices = []

        def point(angle, tube_angle):
            radius = 1 + 0.05 * math.cos(tube_angle)
            return (radius * math.cos(angle), radius * math.sin(angle),
                    0.05 * math.sin(tube_angle))

        for i in range(96):
            a, b = i * math.tau / 96, (i + 1) * math.tau / 96
            for j in range(12):
                c, d = j * math.tau / 12, (j + 1) * math.tau / 12
                ac, bc, bd, ad = point(a, c), point(b, c), point(b, d), point(a, d)
                vertices.extend((ac, bc, bd, ac, bd, ad))
        self._ring = self.new_custom_shape('TRIS', vertices)

    def draw(self, context):
        self.draw_custom_shape(self._ring)

    def draw_select(self, context, select_id):
        self.draw_custom_shape(self._ring, select_id=select_id)


class SMD_GT_PhysicsEndpointLink(bpy.types.Gizmo):
    bl_idname = 'SMD_GT_PhysicsEndpointLink'

    def setup(self):
        self._line = self.new_custom_shape('LINES', ((0, 0, 0), (0, 0, 1)))

    def draw(self, context):
        self.draw_custom_shape(self._line)


class SMD_GT_PhysicsMovePlane(bpy.types.Gizmo):
    bl_idname = 'SMD_GT_PhysicsMovePlane'

    def setup(self):
        a, b, c, d = (0.28, 0.28, 0), (0.55, 0.28, 0), (0.55, 0.55, 0), (0.28, 0.55, 0)
        self._square = self.new_custom_shape('TRIS', (a, b, c, a, c, d))

    def draw(self, context):
        self.draw_custom_shape(self._square)

    def draw_select(self, context, select_id):
        self.draw_custom_shape(self._square, select_id=select_id)


class SMD_GT_PhysicsMoveArrow(bpy.types.Gizmo):
    bl_idname = 'SMD_GT_PhysicsMoveArrow'

    def setup(self):
        vertices = []
        for i in range(12):
            a, b = i * math.tau / 12, (i + 1) * math.tau / 12
            bottom_a = (0.025 * math.cos(a), 0.025 * math.sin(a), 0)
            bottom_b = (0.025 * math.cos(b), 0.025 * math.sin(b), 0)
            top_a = (*bottom_a[:2], 0.78)
            top_b = (*bottom_b[:2], 0.78)
            head_a = (0.075 * math.cos(a), 0.075 * math.sin(a), 0.78)
            head_b = (0.075 * math.cos(b), 0.075 * math.sin(b), 0.78)
            vertices.extend((bottom_a, bottom_b, top_b, bottom_a, top_b, top_a,
                             head_a, head_b, (0, 0, 1), head_b, head_a, (0, 0, 0.78)))
        self._arrow = self.new_custom_shape('TRIS', vertices)

    def draw(self, context):
        self.draw_custom_shape(self._arrow)

    def draw_select(self, context, select_id):
        self.draw_custom_shape(self._arrow, select_id=select_id)


def _plane_frame(position, basis, axis):
    axes = [i for i in range(3) if i != axis]
    x, y = (basis.col[i].normalized() for i in axes)
    matrix = Matrix((x, y, x.cross(y).normalized())).transposed().to_4x4()
    matrix.translation = position
    return matrix


def _plane_hit(context, mouse, position, normal):
    ray = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, mouse)
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, mouse)
    denominator = ray.dot(normal)
    if abs(denominator) < 1e-5:
        return None
    return origin + ray * ((position - origin).dot(normal) / denominator)


def _endpoint_handle_position(context, position, outward, radius):
    region, view = context.region, context.region_data
    start = view3d_utils.location_3d_to_region_2d(region, view, position)
    tip = view3d_utils.location_3d_to_region_2d(region, view, position + outward)
    anchor = position + outward * radius
    screen = view3d_utils.location_3d_to_region_2d(region, view, anchor)
    if start is None or tip is None or screen is None:
        return anchor
    projected = tip - start
    if projected.length < 2:
        projected = Vector((1 if outward.dot(view.view_rotation @ Vector((0, 0, 1))) >= 0 else -1, 0))
    screen += projected.normalized() * 20
    return view3d_utils.region_2d_to_location_3d(region, view, screen, anchor)


class SMD_OT_PhysicsShapeDrag(bpy.types.Operator):
    bl_idname = 'smd.physics_shape_drag'
    bl_label = 'Edit Collision Shape'
    bl_description = 'Drag to move, rotate or resize the active shape; Shift for precision, Esc to cancel'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    action: EnumProperty(items=[('MOVE', 'Move', ''), ('BOUND', 'Bound', ''),
                                ('POINT', 'Endpoint', ''), ('RADIUS', 'Radius', ''),
                                ('PLANE_MOVE', 'Move in Plane', ''),
                                ('PLANE_POINT', 'Move Endpoint in Plane', ''),
                                ('ROTATE', 'Rotate Box', '')])
    axis: IntProperty(default=0, min=0, max=2)
    end: IntProperty(default=0, min=0, max=1)
    shape_kind: EnumProperty(items=[('PHYSICS', 'Physics Shape', ''), ('HITBOX', 'Hitbox', '')],
                             default='PHYSICS')

    def invoke(self, context, event):
        active = _active_shape(context, self.shape_kind)
        if active is None:
            return {'CANCELLED'}
        self._ob, self._entry, self._matrix = active
        self._index = _shape_index(self._ob, self.shape_kind)
        self._mn = Vector(self._entry.vec_min)
        self._mx = Vector(self._entry.vec_max)
        if self.shape_kind == 'PHYSICS' and self._entry.shape_type == 'SPHERE':
            self._sphere_max = self._mx.copy()
            self._mx = self._mn.copy()
        self._radii = _shape_radii(self._entry, self.shape_kind)
        self._rotation_start = Euler(self._entry.rotation, 'XYZ')
        self._rot = self._rotation_start.to_matrix()
        self._center = (self._mn + self._mx) * 0.5
        unit = Vector((0, 0, 0))
        unit[self.axis] = 1
        local_direction = self._rot @ unit if self.action == 'BOUND' else unit
        self._mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        if self.action == 'ROTATE':
            self._plane_position = self._matrix @ self._center
            self._inverse_basis = self._matrix.to_3x3().inverted()
            self._plane_normal = (self._inverse_basis.transposed() @ self._rot @ unit).normalized()
            self._rotation_previous = self._rotation_vector(context, self._mouse)
            if self._rotation_previous is None:
                self.report({'INFO'}, 'Orbit the view to drag this rotation ring')
                return {'CANCELLED'}
            self._rotation_angle = 0.0
        elif self.action in {'PLANE_MOVE', 'PLANE_POINT'}:
            point = self._center
            if self.action == 'PLANE_POINT':
                point = self._center + self._rot @ ((self._mx if self.end else self._mn) - self._center)
            self._plane_position = self._matrix @ point
            self._inverse_basis = self._matrix.to_3x3().inverted()
            self._plane_normal = (self._inverse_basis.transposed() @ unit).normalized()
            self._plane_previous = _plane_hit(context, self._mouse, self._plane_position, self._plane_normal)
            if self._plane_previous is None:
                self.report({'INFO'}, 'Orbit the view to drag this plane')
                return {'CANCELLED'}
            self._plane_delta = Vector((0, 0, 0))
        elif self.action == 'RADIUS':
            point = self._mx if self.end else self._mn
            origin = self._matrix @ (self._center + self._rot @ (point - self._center))
            screen0 = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, origin)
            if screen0 is None or (self._mouse - screen0).length < 2:
                self.report({'INFO'}, 'Orbit the view to drag this ring')
                return {'CANCELLED'}
            radial = self._mouse - screen0
            self._screen_axis = radial.normalized()
            self._pixels_per_unit = radial.length / max(self._radii[self.end], 0.02)
        else:
            self._world_step = self._matrix.to_3x3() @ local_direction
            origin = self._matrix @ self._center
            screen0 = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, origin)
            screen1 = view3d_utils.location_3d_to_region_2d(context.region, context.region_data,
                                                         origin + self._world_step.normalized())
            if screen0 is None or screen1 is None or (screen1 - screen0).length < 2:
                self.report({'INFO'}, 'Orbit the view to drag this axis')
                return {'CANCELLED'}
            self._screen_axis = (screen1 - screen0).normalized()
            self._pixels_per_unit = (screen1 - screen0).length * self._world_step.length
        self._amount = 0.0
        context.window_manager.modal_handler_add(self)
        label = 'Hitbox' if self.shape_kind == 'HITBOX' else 'Physics Shape'
        context.area.header_text_set(label + ': drag to edit | Shift: precision | Esc: cancel')
        return {'RUNNING_MODAL'}

    def _restore(self):
        self._entry.vec_min = self._mn
        self._entry.vec_max = self._mx
        if hasattr(self, '_sphere_max'):
            self._entry.vec_max = self._sphere_max
        if self.shape_kind == 'HITBOX':
            self._entry.scale = self._radii[0]
        else:
            self._entry.radius0, self._entry.radius1 = self._radii
        self._entry.rotation = self._rotation_start

    def _rotation_vector(self, context, mouse):
        hit = _plane_hit(context, mouse, self._plane_position, self._plane_normal)
        if hit is None:
            return None
        local = self._rot.transposed() @ (self._inverse_basis @ (hit - self._plane_position))
        local[self.axis] = 0
        return local.normalized() if local.length > 1e-6 else None

    def modal(self, context, event):
        active = _active_shape(context, self.shape_kind)
        if active is None or active[0] != self._ob or _shape_index(self._ob, self.shape_kind) != self._index:
            self._restore()
            context.area.header_text_set(None)
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._restore()
            context.area.header_text_set(None)
            context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            context.area.header_text_set(None)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            if self.action == 'ROTATE':
                vector = self._rotation_vector(context, mouse)
                if vector is None:
                    return {'RUNNING_MODAL'}
                previous = self._rotation_previous
                angle = math.atan2(previous.cross(vector)[self.axis], previous.dot(vector))
                self._rotation_angle += angle * (0.1 if event.shift else 1)
                self._rotation_previous = vector
                rotation = self._rot @ Matrix.Rotation(self._rotation_angle, 3, 'XYZ'[self.axis])
                self._entry.rotation = rotation.to_euler('XYZ', self._rotation_start)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            plane = self.action in {'PLANE_MOVE', 'PLANE_POINT'}
            movement = Vector((0, 0, 0))
            if plane:
                hit = _plane_hit(context, mouse, self._plane_position, self._plane_normal)
                if hit is None:
                    return {'RUNNING_MODAL'}
                delta = self._inverse_basis @ (hit - self._plane_previous)
                delta[self.axis] = 0
                self._plane_delta += delta * (0.1 if event.shift else 1)
                self._plane_previous = hit
                movement = self._plane_delta
            else:
                amount = (mouse - self._mouse).dot(self._screen_axis) / self._pixels_per_unit
                if event.shift:
                    amount *= 0.1
                self._mouse = mouse
                self._amount += amount
                amount = self._amount
                movement[self.axis] = amount
            mn, mx = self._mn.copy(), self._mx.copy()
            if self.action == 'RADIUS':
                value = self._radii[self.end] + amount
                if self.shape_kind == 'HITBOX':
                    # A positive scale keeps the hitbox in capsule mode.
                    self._entry.scale = max(1e-5, value)
                else:
                    setattr(self._entry, 'radius' + str(self.end), max(0.0, value))
            elif self.action in {'MOVE', 'PLANE_MOVE'}:
                mn += movement
                mx += movement
            elif self.action in {'POINT', 'PLANE_POINT'}:
                p0 = self._center + self._rot @ (mn - self._center)
                p1 = self._center + self._rot @ (mx - self._center)
                if self.end:
                    p1 += movement
                else:
                    p0 += movement
                center = (p0 + p1) * 0.5
                mn = center + self._rot.transposed() @ (p0 - center)
                mx = center + self._rot.transposed() @ (p1 - center)
            else:
                bound = mx if self.end else mn
                bound[self.axis] += amount
                bound[self.axis] = max(mn[self.axis], bound[self.axis]) if self.end else min(mx[self.axis], bound[self.axis])
                shift = (mn + mx) * 0.5 - self._center
                correction = self._rot @ shift - shift
                mn += correction
                mx += correction
            if self.action != 'RADIUS':
                self._entry.vec_min = mn
                if not hasattr(self, '_sphere_max'):
                    self._entry.vec_max = mx
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class _ShapeGizmoGroup:
    @classmethod
    def poll(cls, context):
        return _active_shape(context, cls._shape_kind) is not None

    def _arrow(self, action, axis, end, color, size):
        gizmo = self.gizmos.new('SMD_GT_PhysicsMoveArrow')
        gizmo.color = color
        gizmo.alpha = 0.8
        gizmo.color_highlight = (1, 1, 1)
        gizmo.alpha_highlight = 1
        gizmo.scale_basis = size
        gizmo.line_width = 5
        gizmo.use_draw_modal = True
        props = gizmo.target_set_operator('smd.physics_shape_drag')
        props.shape_kind = self._shape_kind
        props.action, props.axis, props.end = action, axis, end
        return gizmo

    def setup(self, context):
        colors = ((0.9, 0.15, 0.15), (0.2, 0.85, 0.2), (0.2, 0.4, 1))
        self._move = [self._arrow('MOVE', i, 0, colors[i], 0.8) for i in range(3)]
        self._bounds = [self._arrow('BOUND', i, end, colors[i], 0.5)
                        for end in range(2) for i in range(3)]
        self._points = [self._arrow('POINT', i, end, colors[i], 0.5)
                        for end in range(2) for i in range(3)]
        self._move_planes = [self._plane('PLANE_MOVE', i, 0, colors[i], 0.8) for i in range(3)]
        self._point_planes = [self._plane('PLANE_POINT', i, end, colors[i], 0.5)
                              for end in range(2) for i in range(3)]
        self._rotation_rings = []
        for axis, color in enumerate(colors):
            ring = self.gizmos.new('SMD_GT_PhysicsRadiusRing')
            ring.color, ring.alpha = color, 0.8
            ring.color_highlight, ring.alpha_highlight = (1, 1, 1), 1
            ring.scale_basis = 0.7
            ring.use_draw_modal = True
            props = ring.target_set_operator('smd.physics_shape_drag')
            props.shape_kind = self._shape_kind
            props.action, props.axis = 'ROTATE', axis
            self._rotation_rings.append(ring)
        self._links = []
        for end in range(2):
            link = self.gizmos.new('SMD_GT_PhysicsEndpointLink')
            link.color, link.alpha = (0.75, 0.85, 1), 0.7
            link.line_width = 2
            link.use_draw_scale = False
            link.hide_select = True
            self._links.append(link)
        self._radii = []
        for end, color in enumerate(((1, 0.65, 0.1), (1, 0.35, 0.15))):
            ring = self.gizmos.new('SMD_GT_PhysicsRadiusRing')
            ring.color, ring.alpha = color, 0.85
            ring.color_highlight, ring.alpha_highlight = (1, 1, 1), 1
            ring.line_width = 6
            ring.use_draw_scale = False
            ring.use_draw_modal = True
            props = ring.target_set_operator('smd.physics_shape_drag')
            props.shape_kind = self._shape_kind
            props.action, props.end = 'RADIUS', end
            self._radii.append(ring)

    def _plane(self, action, axis, end, color, size):
        gizmo = self.gizmos.new('SMD_GT_PhysicsMovePlane')
        gizmo.color, gizmo.alpha = color, 0.65
        gizmo.color_highlight, gizmo.alpha_highlight = (1, 1, 1), 0.95
        gizmo.scale_basis = size
        gizmo.use_draw_modal = True
        props = gizmo.target_set_operator('smd.physics_shape_drag')
        props.shape_kind = self._shape_kind
        props.action, props.axis, props.end = action, axis, end
        return gizmo

    def _position_plane(self, context, gizmo, position, basis, axis, hidden=False):
        gizmo.matrix_basis = _plane_frame(position, basis, axis)
        view_direction = context.region_data.view_rotation @ Vector((0, 0, 1))
        gizmo.hide = hidden or abs(gizmo.matrix_basis.to_3x3().col[2].dot(view_direction)) < 0.15

    def draw_prepare(self, context):
        active = _active_shape(context, self._shape_kind)
        if active is None:
            return
        ob, entry, matrix = active
        mn, mx = Vector(entry.vec_min), Vector(entry.vec_max)
        sphere = self._shape_kind == 'PHYSICS' and entry.shape_type == 'SPHERE'
        if sphere:
            mx = mn.copy()
        center = (mn + mx) * 0.5
        rotation = Euler(entry.rotation, 'XYZ').to_matrix()
        basis = matrix.to_3x3()
        capsule = entry.scale > 0 if self._shape_kind == 'HITBOX' else entry.shape_type == 'CAPSULE'
        for i, gizmo in enumerate(self._move):
            gizmo.hide = not (capsule or sphere)
            gizmo.matrix_basis = _frame(matrix @ center, basis.col[i])
            self._position_plane(context, self._move_planes[i], matrix @ center, basis, i,
                                 hidden=not (capsule or sphere))
            self._position_plane(context, self._rotation_rings[i], matrix @ center,
                                 basis @ rotation, i, hidden=capsule or sphere)
        for end in range(2):
            point = center + rotation @ ((mx if end else mn) - center)
            direction = basis @ rotation @ (mx - mn)
            direction = direction.normalized() if direction.length > 1e-6 else basis.col[1].normalized()
            radius_value = _shape_radii(entry, self._shape_kind)[end]
            position = matrix @ point
            handle_position = position
            if capsule:
                outward = direction if end else -direction
                handle_position = _endpoint_handle_position(context, position, outward,
                                                            radius_value * basis.col[0].length)
            link = self._links[end]
            link.hide = not capsule
            delta = handle_position - position
            if delta.length > 1e-6:
                link.matrix_basis = _frame(position, delta) @ Matrix.Diagonal((1, 1, delta.length, 1))
            else:
                link.hide = True
            for i in range(3):
                face = Vector((0, 0, 0))
                face[i] = (mx[i] - mn[i]) * (0.5 if end else -0.5)
                bound = self._bounds[end * 3 + i]
                bound.hide = capsule or sphere
                bound.matrix_basis = _frame(matrix @ (center + rotation @ face),
                                             (basis @ rotation.col[i]) * (1 if end else -1))
                endpoint = self._points[end * 3 + i]
                endpoint.hide = not capsule
                endpoint.matrix_basis = _frame(handle_position, basis.col[i])
                self._position_plane(context, self._point_planes[end * 3 + i], handle_position,
                                     basis, i, hidden=not capsule)
            radius = self._radii[end]
            radius.hide = not capsule and not (sphere and end == 0)
            # Keep a collapsed radius selectable for dragging outward.
            size = max(radius_value, 0.02) * basis.col[0].length
            if sphere:
                direction = context.region_data.view_rotation @ Vector((0, 0, 1))
            radius.matrix_basis = _frame(matrix @ point, direction) @ Matrix.Diagonal((size, size, size, 1))


class SMD_GGT_PhysicsShape(_ShapeGizmoGroup, bpy.types.GizmoGroup):
    bl_idname = 'SMD_GGT_PhysicsShape'
    bl_label = 'Physics Shape Editing'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}
    _shape_kind = 'PHYSICS'


class SMD_GGT_Hitbox(_ShapeGizmoGroup, bpy.types.GizmoGroup):
    bl_idname = 'SMD_GGT_Hitbox'
    bl_label = 'Hitbox Editing'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}
    _shape_kind = 'HITBOX'
