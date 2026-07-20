"""IR -> Blender. The half of the DMX importer that creates data-blocks.

Extracted from SmdImporter.readDMX. `ctx` is the importer operator, duck-typed for
`warning`/`error` (Logger), `append`, `existingBones`, `appliedReferencePose`, `qc`
and `properties`.
"""

import os
import random

import bpy
import bmesh
from bpy import ops
from bpy.app.translations import pgettext
from mathutils import Matrix, Vector

from .. import flex, ordered_set
from ..utils import (REF, ANIM, PHYS, KeyFrame, get_id, getUpAxisMat,
                     channelBagForNewActionSlot)


# ---------------------------------------------------------------------------
# Shared helpers - used by every format, not just DMX
# ---------------------------------------------------------------------------

def truncate_id_name(ctx, name: str, id_type) -> str:
    truncated = bytes(name, 'utf8')
    if len(truncated) < 64:
        return name
    truncated = truncated[:63]
    while truncated:
        try:
            truncated = truncated.decode('utf8')
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    ctx.error(get_id("importer_err_namelength", True).format(
        pgettext(id_type if isinstance(id_type, str) else id_type.__name__),
        name, truncated))
    return str(truncated)


def find_armature() -> bpy.types.Object | None:
    if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
        return bpy.context.active_object

    def isArmIn(lst):
        for ob in lst:
            if ob.type == 'ARMATURE':
                return ob

    a = isArmIn(bpy.context.selected_objects)
    if a:
        return a
    for ob in bpy.context.selected_objects:
        if ob.type == 'MESH':
            a = ob.find_armature()
            if a:
                return a
    return isArmIn(bpy.context.scene.objects)


def create_armature(smd, armature_name: str) -> bpy.types.Object:
    if bpy.context.active_object:
        ops.object.mode_set(mode='OBJECT', toggle=False)
    a = bpy.data.objects.new(armature_name, bpy.data.armatures.new(armature_name))
    a.show_in_front = True
    a.data.display_type = 'STICK'
    bpy.context.scene.collection.objects.link(a)
    for i in bpy.context.selected_objects:
        i.select_set(False)
    a.select_set(True)
    bpy.context.view_layer.objects.active = a
    if not smd.isDMX:
        ops.object.mode_set(mode='OBJECT')
    return a


def get_mesh_material(ctx, smd, mat_name: str) -> tuple[bpy.types.Material, int]:
    if mat_name:
        mat_name = truncate_id_name(ctx, mat_name, bpy.types.Material)
    else:
        mat_name = "Material"

    md = smd.m.data
    mat = None
    for candidate in bpy.data.materials:
        if candidate.name == mat_name:
            mat = candidate
    if mat:
        if md.materials.get(mat.name):
            for i in range(len(md.materials)):
                if md.materials[i].name == mat.name:
                    mat_ind = i
                    break
        else:
            md.materials.append(mat)
            mat_ind = len(md.materials) - 1
    else:
        print(f"- New material: {mat_name}")
        mat = bpy.data.materials.new(mat_name)
        md.materials.append(mat)
        randCol = [random.uniform(.4, 1) for _ in range(3)] + [1]
        mat.diffuse_color = randCol
        if smd.jobType == PHYS:
            smd.m.display_type = 'SOLID'
        mat_ind = len(md.materials) - 1

    return mat, mat_ind


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def apply_frames(ctx, smd, keyframes: dict, num_frames: int):
    assert smd.a
    ops.object.mode_set(mode='POSE')

    if ctx.append != 'VALIDATE' and smd.jobType in [REF, ANIM] and not ctx.appliedReferencePose:
        ctx.appliedReferencePose = True

        for bone in smd.a.pose.bones:
            bone.matrix_basis.identity()
        for bone, kf in keyframes.items():
            if bone.name in ctx.existingBones:
                continue
            elif bone.parent and not keyframes.get(bone.parent):
                bone.matrix = bone.parent.matrix @ kf[0].matrix
            else:
                bone.matrix = kf[0].matrix
        ops.pose.armature_apply()

        bone_vis = None if ctx.properties.boneMode == 'NONE' else bpy.data.objects.get("smd_bone_vis")

        if ctx.properties.boneMode == 'SPHERE' and (not bone_vis or bone_vis.type != 'MESH'):
            ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=2)
            bone_vis = bpy.context.active_object
            bone_vis.data.name = bone_vis.name = "smd_bone_vis"
            bone_vis.use_fake_user = True
            for collection in bone_vis.users_collection:
                collection.objects.unlink(bone_vis)
            bpy.context.view_layer.objects.active = smd.a
        elif ctx.properties.boneMode == 'ARROWS' and (not bone_vis or bone_vis.type != 'EMPTY'):
            bone_vis = bpy.data.objects.new("smd_bone_vis", None)
            bone_vis.use_fake_user = True
            bone_vis.empty_display_type = 'ARROWS'
            bone_vis.empty_display_size = 5

        maxs = Vector()
        mins = Vector()
        for bone in smd.a.data.bones:
            for i in range(3):
                maxs[i] = max(maxs[i], bone.head_local[i])
                mins[i] = min(mins[i], bone.head_local[i])

        dimensions = []
        if ctx.qc:
            ctx.qc.dimensions = dimensions
        for i in range(3):
            dimensions.append(maxs[i] - mins[i])

        length = max(0.001, (dimensions[0] + dimensions[1] + dimensions[2]) / 600)

        ops.object.mode_set(mode='EDIT')
        for bone in [smd.a.data.edit_bones[b.name] for b in keyframes.keys()]:
            bone.tail = bone.head + (bone.tail - bone.head).normalized() * length
            smd.a.pose.bones[bone.name].custom_shape = bone_vis

    if smd.jobType == ANIM:
        if not smd.a.animation_data:
            smd.a.animation_data_create()

        channelbag = channelBagForNewActionSlot(smd.a, smd.jobName)
        fcurves = channelbag.fcurves
        groups = channelbag.groups

        ops.object.mode_set(mode='POSE')

        bpy.context.scene.frame_start = 0
        bpy.context.scene.frame_end = num_frames - 1

        for bone in smd.a.pose.bones:
            bone.rotation_mode = smd.rotMode

        for bone, frames in list(keyframes.items()):
            if not frames:
                del keyframes[bone]

        if not smd.isDMX:
            still_bones = list(keyframes.keys())
            for bone in keyframes.keys():
                bone_keyframes = keyframes[bone]
                for keyframe in bone_keyframes[1:]:
                    diff = keyframe.matrix.inverted() @ bone_keyframes[0].matrix
                    if diff.to_translation().length > 0.00001 or abs(diff.to_quaternion().w) > 0.0001:
                        still_bones.remove(bone)
                        break
            for bone in still_bones:
                keyframes[bone] = [keyframes[bone][0]]

        def ApplyRecursive(bone: bpy.types.PoseBone):
            keys = keyframes.get(bone)
            if keys:
                curvesLoc = None
                curvesRot = None
                curvesScale = None
                bone_string = f"pose.bones[\"{bone.name}\"]."
                group = groups.new(name=bone.name)

                for keyframe in keys:
                    if bone.parent:
                        parentMat = bone.parent.matrix
                        bone.matrix = parentMat @ keyframe.matrix
                    else:
                        bone.matrix = getUpAxisMat(smd.upAxis) @ keyframe.matrix

                    if keyframe.pos:
                        if curvesLoc is None:
                            curvesLoc = []
                            for i in range(3):
                                curve = fcurves.new(data_path=bone_string + "location", index=i)
                                curve.group = group
                                curvesLoc.append(curve)
                        for i in range(3):
                            curvesLoc[i].keyframe_points.add(1)
                            curvesLoc[i].keyframe_points[-1].co = [keyframe.frame, bone.location[i]]

                    if keyframe.rot:
                        if curvesRot is None:
                            curvesRot = []
                            for i in range(3 if smd.rotMode == 'XYZ' else 4):
                                curve = fcurves.new(
                                    data_path=bone_string + ("rotation_euler" if smd.rotMode == 'XYZ' else "rotation_quaternion"),
                                    index=i,
                                )
                                curve.group = group
                                curvesRot.append(curve)
                        if smd.rotMode == 'XYZ':
                            for i in range(3):
                                curvesRot[i].keyframe_points.add(1)
                                curvesRot[i].keyframe_points[-1].co = [keyframe.frame, bone.rotation_euler[i]]
                        else:
                            for i in range(4):
                                curvesRot[i].keyframe_points.add(1)
                                curvesRot[i].keyframe_points[-1].co = [keyframe.frame, bone.rotation_quaternion[i]]

                    if keyframe.scale:
                        if curvesScale is None:
                            curvesScale = []
                            for i in range(3):
                                curve = fcurves.new(data_path=bone_string + "scale", index=i)
                                curve.group = group
                                curvesScale.append(curve)
                        for i in range(3):
                            curvesScale[i].keyframe_points.add(1)
                            curvesScale[i].keyframe_points[-1].co = [keyframe.frame, bone.scale[i]]

            for child in bone.children:
                ApplyRecursive(child)

        for bone in smd.a.pose.bones:
            if not bone.parent:
                ApplyRecursive(bone)

        for fc in fcurves:
            fc.update()

    for bone in smd.a.pose.bones:
        bone.location.zero()
        if smd.rotMode == 'XYZ':
            bone.rotation_euler.zero()
        else:
            bone.rotation_quaternion.identity()
        bone.scale = (1.0, 1.0, 1.0)

    scn = bpy.context.scene
    if scn.frame_current == 1:
        scn.frame_set(0)
    else:
        scn.frame_set(scn.frame_current)
    ops.object.mode_set(mode='OBJECT')
    print(f"- Imported {num_frames} frames of animation")


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

def build_skeleton(ctx, smd, skel, target_arm, model_name: str) -> dict:
    """Creates or validates bones and attachments. Returns bone name -> rest matrix
    for the bones that need a rest pose applied.

    Populates smd.a, smd.boneIDs and smd.boneTransformIDs.
    """
    bone_matrices: dict[str, Matrix] = {}
    # Parallel to skel.bones: the Blender bone name each IR bone resolved to
    bone_names: list[str | None] = [None] * len(skel.bones)

    def parent_edit_bone(index):
        if index is None:
            return None
        name = bone_names[index]
        return smd.a.data.edit_bones[name] if name else None

    if target_arm:
        smd.a = target_arm
        missing_bones: list[str] = []
        bpy.context.view_layer.objects.active = smd.a
        smd.a.hide_set(False)
        ops.object.mode_set(mode='EDIT')

        for att in skel.attachments:
            ctx.warning(
                f"DMX attachment '{att.name}' encountered while validating against "
                f"existing armature - attachments are skipped in validate/append mode")

        for i, ibone in enumerate(skel.bones):
            bone = smd.a.data.edit_bones.get(truncate_id_name(ctx, ibone.name, bpy.types.Bone))
            if not bone:
                if ctx.append == 'APPEND' and smd.jobType in [REF, ANIM]:
                    bone = smd.a.data.edit_bones.new(truncate_id_name(ctx, ibone.name, bpy.types.Bone))
                    bone.parent = parent_edit_bone(ibone.parent)
                    bone.tail = (0, 5, 0)
                    bone_matrices[bone.name] = ibone.matrix
                    bone_names[i] = bone.name
                    smd.boneIDs[ibone.source_id] = bone.name
                    smd.boneTransformIDs[ibone.transform_id] = bone.name
                else:
                    missing_bones.append(ibone.name)
            else:
                scene_parent = bone.parent.name if bone.parent else "<None>"
                dmx_parent = skel.bones[ibone.parent].name if ibone.parent is not None else "<None>"
                if scene_parent != dmx_parent:
                    ctx.warning(get_id('importer_bone_parent_miss', True).format(
                        ibone.name, scene_parent, dmx_parent, smd.jobName))
                bone_names[i] = bone.name
                smd.boneIDs[ibone.source_id] = bone.name
                smd.boneTransformIDs[ibone.transform_id] = bone.name

        if missing_bones and smd.jobType != ANIM:
            ctx.warning(get_id("importer_err_missingbones", True).format(
                smd.jobName, len(missing_bones), smd.a.name))
            print("\n".join(missing_bones))

    elif skel.has_bones:
        ctx.append = 'NEW_ARMATURE'
        smd.a = create_armature(smd, truncate_id_name(ctx, model_name, bpy.types.Armature))
        if ctx.qc:
            ctx.qc.a = smd.a
        bpy.context.view_layer.objects.active = smd.a
        ops.object.mode_set(mode='EDIT')

        smd.a.matrix_world = getUpAxisMat(smd.upAxis)

        for i, ibone in enumerate(skel.bones):
            bone = smd.a.data.edit_bones.new(truncate_id_name(ctx, ibone.name, bpy.types.Bone))
            bone.parent = parent_edit_bone(ibone.parent)
            bone.tail = (0, 5, 0)
            bone_matrices[bone.name] = ibone.matrix
            bone_names[i] = bone.name
            smd.boneIDs[ibone.source_id] = bone.name
            smd.boneTransformIDs[ibone.transform_id] = bone.name

        build_attachments(ctx, smd, skel, bone_names)

    elif skel.attachments:
        # Attachments with no skeleton - nothing to parent them to
        ctx.warning(
            f"DMX '{smd.jobName}' contains {len(skel.attachments)} attachment(s) "
            f"but no skeleton - attachments will not be imported")

    return bone_matrices


def build_attachments(ctx, smd, skel, bone_names) -> None:
    for att in skel.attachments:
        parent_name = bone_names[att.parent] if att.parent is not None else None
        if parent_name is None:
            ctx.warning(f"Attachment '{att.name}' has no parent bone - skipped")
            continue
        atch = smd.atch = bpy.data.objects.new(
            name=truncate_id_name(ctx, att.name, "Attachment"), object_data=None)
        (smd.g if smd.g else bpy.context.scene.collection).objects.link(atch)
        atch.show_in_front = True
        atch.empty_display_type = 'ARROWS'
        atch.parent = smd.a
        atch.parent_type = 'BONE'
        atch.parent_bone = parent_name
        atch.vs.dmx_attachment = True
        atch.matrix_local = att.matrix


def build_smd_skeleton(ctx, smd, nodes) -> None:
    """SMD node block -> bones on smd.a, creating the armature if there is none.

    Separate from build_skeleton because SMD has no rest matrices here - they come
    from frame 0 of the skeleton block, applied later via apply_frames.
    """
    bone_parents: dict[str, int] = {}

    def add_bone(node):
        bone = smd.a.data.edit_bones.new(truncate_id_name(ctx, node.name, bpy.types.Bone))
        bone.tail = 0, 5, 0
        smd.boneIDs[node.id] = bone.name
        bone_parents[bone.name] = node.parent
        return bone

    if ctx.append != 'NEW_ARMATURE':
        smd.a = smd.a or find_armature()
        if smd.a:
            append = ctx.append == 'APPEND' and smd.jobType in [REF, ANIM]
            if append:
                bpy.context.view_layer.objects.active = smd.a
                smd.a.hide_set(False)
                ops.object.mode_set(mode='EDIT', toggle=False)
                ctx.existingBones.extend([b.name for b in smd.a.data.bones])

            missing = validated = 0
            for node in nodes:
                target_bone = smd.a.data.bones.get(node.name)
                if target_bone:
                    validated += 1
                elif append:
                    target_bone = add_bone(node)
                else:
                    missing += 1
                if not smd.boneIDs.get(node.parent):
                    smd.phantomParentIDs[node.id] = node.parent
                smd.boneIDs[node.id] = target_bone.name if target_bone else node.name

            print("- Validated {} bones against armature \"{}\"{}".format(
                validated, smd.a.name,
                " (could not find {})".format(missing) if missing > 0 else ""))

    if not smd.a:
        smd.a = create_armature(smd, truncate_id_name(
            ctx, (ctx.qc.jobName if ctx.qc else smd.jobName) + "_skeleton", bpy.types.Armature))
        if ctx.qc:
            ctx.qc.a = smd.a
        smd.a.data.vs.implicit_zero_bone = False

        ops.object.mode_set(mode='EDIT', toggle=False)
        for node in nodes:
            add_bone(node)

    for bone_name, parent_id in bone_parents.items():
        if parent_id != -1:
            smd.a.data.edit_bones[bone_name].parent = smd.a.data.edit_bones[smd.boneIDs[parent_id]]

    ops.object.mode_set(mode='OBJECT')
    if bone_parents:
        print(f"- Imported {len(bone_parents)} new bones")

    if len(smd.a.data.bones) > 128:
        ctx.warning(get_id("importer_err_bonelimit_smd"))


def apply_rest_pose(ctx, smd, bone_matrices: dict) -> None:
    if not smd.a:
        return
    ops.object.mode_set(mode='POSE')
    if smd.jobType == ANIM:
        return
    restData: dict = {}
    for bone in smd.a.pose.bones:
        mat = bone_matrices.get(bone.name)
        if mat:
            keyframe = KeyFrame()
            keyframe.matrix = mat
            restData[bone] = [keyframe]
    if restData:
        apply_frames(ctx, smd, restData, 1)


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

_BM_LAYER_FOR_KIND = {
    'FLOAT':  lambda bm: bm.loops.layers.float,
    'INT':    lambda bm: bm.loops.layers.int,
    'STRING': lambda bm: bm.loops.layers.string,
    'UV':     lambda bm: bm.loops.layers.uv,
    'COLOR':  lambda bm: bm.loops.layers.color,
}


class _LayerBinding:
    __slots__ = ('layer', 'indices', 'values', 'is_uv')

    def __init__(self, layer, indices, values, is_uv):
        self.layer = layer
        self.indices = indices
        self.values = values
        self.is_uv = is_uv

    def get_loop_value(self, loop_index):
        return self.values[self.indices[loop_index]]


def build_mesh(ctx, smd, imesh, corrective_separator: str = '_'):
    """Creates one mesh object from an ImportedMesh. Returns the object."""
    if bpy.context.active_object:
        ops.object.mode_set(mode='OBJECT')

    mesh_name = truncate_id_name(ctx, imesh.name, bpy.types.Mesh)
    ob = smd.m = bpy.data.objects.new(name=mesh_name,
                                      object_data=bpy.data.meshes.new(name=mesh_name))
    smd.g.objects.link(ob)
    ob.show_wire = smd.jobType == PHYS

    if smd.a:
        ob.parent = smd.a
        if imesh.has_weightmap:
            amod = ob.modifiers.new(name="Armature", type='ARMATURE')
            amod.object = smd.a
            amod.use_bone_envelopes = False
    else:
        ob.matrix_local = getUpAxisMat(smd.upAxis)

    print(f"Importing mesh \"{imesh.name}\"")

    bm = bmesh.new()
    bm.from_mesh(ob.data)

    for pos in imesh.positions:
        bm.verts.new(Vector(pos))
    bm.verts.ensure_lookup_table()

    bindings: list[_LayerBinding] = []
    normals_layer_name = None
    for ilayer in imesh.loop_layers:
        if ilayer.kind == 'NORMAL':
            # Held as a float_vector attribute, applied via normals_split_custom_set
            # once the bmesh has been converted.
            layer = bm.loops.layers.float_vector.new(ilayer.name)
            normals_layer_name = layer.name
            bindings.append(_LayerBinding(layer, ilayer.indices, ilayer.values, False))
            continue
        if ilayer.uneditable:
            ctx.warning(f"Vertex data '{ilayer.name}' was imported but cannot be edited in Blender")
        layer = _BM_LAYER_FOR_KIND[ilayer.kind](bm).new(ilayer.name)
        bindings.append(_LayerBinding(layer, ilayer.indices, ilayer.values,
                                      ilayer.kind == 'UV'))

    deform_group_names = ordered_set.OrderedSet()
    if imesh.has_weightmap:
        deformLayer = bm.verts.layers.deform.new()
        for vert, weights in zip(bm.verts, imesh.weights):
            for vg_index, weight in weights:
                vert[deformLayer][vg_index] = weight
        for name in imesh.group_names:
            deform_group_names.add(name)

    # Face sets resolve to material slots by name, so two sets sharing a material
    # land in one slot.
    slot_for_face_set: list[int] = []
    for mat_path in imesh.materials:
        if imesh.materials_are_paths:
            bpy.context.scene.vs.material_path = os.path.dirname(mat_path).replace("\\", "/")
            mat_name = os.path.basename(mat_path)
        else:
            mat_name = mat_path
        _mat, mat_ind = get_mesh_material(ctx, smd, mat_name)
        slot_for_face_set.append(mat_ind)

    deform_layer = bm.verts.layers.deform.active
    for iface in imesh.faces:
        verts = [bm.verts[imesh.position_indices[loop]] for loop in iface.loops]
        try:
            face = bm.faces.new(verts)
        except ValueError:
            if not imesh.split_duplicate_faces:
                continue  # degenerate / duplicate face
            # Give the face its own copies so a repeated vertex set still builds
            copies = []
            for v in verts:
                nv = bm.verts.new(v.co)
                if deform_layer is not None:
                    for gi, w in v[deform_layer].items():
                        nv[deform_layer][gi] = w
                copies.append(nv)
            bm.verts.ensure_lookup_table()
            try:
                face = bm.faces.new(copies)
            except ValueError:
                continue
        face.smooth = True
        face.material_index = slot_for_face_set[iface.face_set]
        for binding in bindings:
            for i, loop in enumerate(face.loops):
                value = binding.get_loop_value(iface.loops[i])
                if binding.is_uv:
                    loop[binding.layer].uv = value
                else:
                    loop[binding.layer] = value

    if imesh.cloth_groups:
        deformLayer = bm.verts.layers.deform.verify()
        for cloth_name, cloth_data, cloth_indices in imesh.cloth_groups:
            vg_index = deform_group_names.add(cloth_name)
            if cloth_data is None or cloth_indices is None:
                ctx.warning(f"Cloth group '{cloth_name}' has no data - skipped")
                continue
            loop_i = 0
            for face in bm.faces:
                for loop in face.loops:
                    w = cloth_data[cloth_indices[loop_i]]
                    loop.vert[deformLayer][vg_index] = w
                    loop_i += 1
        print(f"- Imported {len(imesh.cloth_groups)} cloth-enable vertex group(s)")

    for groupName in deform_group_names:
        ob.vertex_groups.new(name=groupName)

    if imesh.parent_bone:
        ob.parent_type = 'BONE'
        ob.parent_bone = imesh.parent_bone

    bm.to_mesh(ob.data)
    del bm
    ob.data.update()
    ob.matrix_world @= imesh.matrix
    if ob.parent_bone:
        ob.matrix_world = (ob.parent.matrix_world
                           @ ob.parent.data.bones[ob.parent_bone].matrix_local
                           @ ob.matrix_world)
    elif ob.parent:
        ob.matrix_world = ob.parent.matrix_world @ ob.matrix_world
    if smd.jobType == PHYS:
        ob.display_type = 'SOLID'

    if normals_layer_name:
        normalsAttr = ob.data.attributes[normals_layer_name]
        ob.data.normals_split_custom_set([v.vector for v in normalsAttr.data])
        ob.data.attributes.remove(ob.data.attributes[normals_layer_name])

    if imesh.data_transform is not None:
        ob.data.transform(imesh.data_transform)
        ob.data.update()

    if imesh.balance:
        _build_balance_group(ob, *imesh.balance)

    if imesh.shapes:
        build_shape_keys(ob, imesh, corrective_separator)

    return ob


def _build_balance_group(ob, balance, balanceIndices) -> None:
    vg = ob.vertex_groups.new(name=get_id("importer_balance_group", data=True))
    ones: list[int] = []
    for i in balanceIndices:
        val = balance[i]
        if val == 0:
            continue
        elif val == 1:
            ones.append(i)
        else:
            vg.add([i], val, 'REPLACE')
    vg.add(ones, 1, 'REPLACE')
    ob.data.vs.flex_stereo_mode = 'VGROUP'
    ob.data.vs.flex_stereo_vg = vg.name


def build_shape_keys(ob, imesh, corrective_separator: str) -> None:
    for shape in imesh.shapes:
        if not ob.data.shape_keys:
            ob.shape_key_add(name="Basis")
            ob.show_only_shape_key = True
            ob.data.shape_keys.name = imesh.name
        shape_key = ob.shape_key_add(name=shape.name)
        shape_key.value = 0.0
        for i, posIndex in enumerate(shape.indices):
            shape_key.data[posIndex].co += Vector(shape.offsets[i])
        if corrective_separator in shape.name:
            flex.AddCorrectiveShapeDrivers.addDrivers(
                shape_key, shape.name.split(corrective_separator))
