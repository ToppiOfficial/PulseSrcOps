"""VMDL / VMDL_PREFAB -> Blender.

Source 2's model definition is KeyValues3, so keyvalues3.py does the parsing and this
module is only extraction plus orchestration: build the skeleton from the Skeleton node,
then pull in the DMX files the RenderMeshList and AnimationList reference.

Like qc.py it needs a QcInfo, which is where the job name, up axis and the
already-imported list live - it is reached through readQC, which creates one.
"""

import os
from math import radians

import bpy
from bpy import ops
from mathutils import Matrix, Euler, Vector

from .. import keyvalues3
from ..utils import (REF, ANIM, KeyFrame, SmdInfo, State, printTimeMessage,
                     import_jigglebones_from_kv3, import_hitboxes_from_kv3)
from .build import truncate_id_name, find_armature, create_armature, apply_frames


def local_matrix(origin, angles_deg) -> Matrix:
    # Source stores rotations as a QAngle [pitch, yaw, roll] (degrees) where
    # pitch is about Y, yaw about Z and roll about X. Source builds the matrix
    # as Rz(yaw) @ Ry(pitch) @ Rx(roll), which is a Blender 'XYZ' Euler of
    # (roll, pitch, yaw).
    pitch, yaw, roll = angles_deg[0], angles_deg[1], angles_deg[2]
    rot = Euler((radians(roll), radians(pitch), radians(yaw)), 'XYZ').to_matrix().to_4x4()
    return Matrix.Translation(Vector(origin)) @ rot


def extract_bones(skeleton_node) -> list[tuple[str, object]]:
    result = []

    def _dfs(node):
        name = node.properties.get("name")
        if name:
            result.append((name, node))
        for child in node.children:
            if child.properties.get("_class") == "Bone":
                _dfs(child)

    for child in skeleton_node.children:
        if child.properties.get("_class") == "Bone":
            _dfs(child)
    return result


def resolve_dmx_ref(vmdl_path: str, dmx_ref: str) -> str | None:
    vmdl_dir = os.path.dirname(vmdl_path)
    normalized = dmx_ref.replace("\\", os.sep).replace("/", os.sep)
    basename = os.path.basename(normalized)
    candidates = [
        os.path.join(vmdl_dir, basename),
        os.path.normpath(os.path.join(vmdl_dir, normalized)),
    ]
    if State.gamePath:
        candidates.append(os.path.normpath(os.path.join(State.gamePath, normalized)))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def read_vmdl(ctx, filepath: str, qc, rot_mode: str) -> int:
    filename = os.path.basename(filepath)
    print(f"\nVMDL IMPORTER: now working on {filename}")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            vmdl_text = f.read()
    except IOError as e:
        ctx.error(f"Could not read {filepath}: {e}")
        return 0

    try:
        kv_doc = keyvalues3.KVParser(vmdl_text).parse()
    except Exception as e:
        ctx.error(f"Failed to parse {filename}: {e}")
        return 0

    root_node = kv_doc.roots.get("rootNode")
    if not root_node:
        ctx.error(f"{filename}: no rootNode")
        return 0

    # smd exists so create_armature can read smd.isDMX and the collection helper works
    smd = ctx.smd = SmdInfo(qc.jobName)
    smd.isDMX = 1
    smd.jobType = REF
    smd.upAxis = qc.upAxis
    smd.rotMode = rot_mode
    ctx.createCollection()

    skeleton_node = root_node.get(recursive=True, _class="Skeleton")
    if not skeleton_node:
        ctx.warning(f"{filename}: no Skeleton - only jigglebones imported")
        arm = qc.a or find_armature()
        if arm:
            cnt, _missing = import_jigglebones_from_kv3(kv_doc, arm)
            ctx.imported_jigglebones += cnt
        return 1

    if not extract_bones(skeleton_node):
        ctx.warning(f"{filename}: Skeleton has no Bone children")
        return 0

    arm = _build_skeleton(ctx, smd, qc, skeleton_node)

    _read_render_meshes(ctx, qc, root_node, filepath, filename, rot_mode)
    _read_attachments(ctx, smd, arm, root_node, filename)

    cnt, missing = import_jigglebones_from_kv3(kv_doc, arm)
    if cnt:
        ctx.imported_jigglebones += cnt
        print(f"- Imported {cnt} jigglebone(s) from {filename}")
    if missing:
        ctx.warning(f"Could not find bones for {len(missing)} jigglebone(s): {', '.join(missing)}")

    hb_created, hb_skipped, hb_bones = import_hitboxes_from_kv3(kv_doc, arm)
    if hb_created:
        ctx.imported_hitboxes += hb_created
        print(f"- Imported {hb_created} hitbox(es) from {filename}")
    if hb_skipped:
        missing_names = ', '.join(sorted({b for b in hb_bones if b}))
        ctx.warning(f"Skipped {hb_skipped} hitbox(es) with missing bones: {missing_names}")

    if getattr(ctx.properties, 'doAnim', True):
        _read_animations(ctx, qc, arm, root_node, filepath, filename, rot_mode)

    printTimeMessage(qc.startTime, filename, "import", "VMDL")
    # Count referenced meshes when present, otherwise the VMDL itself.
    return ctx.num_files_imported or 1


def _build_skeleton(ctx, smd, qc, skeleton_node):
    arm_name = truncate_id_name(ctx, qc.jobName + "_skeleton", bpy.types.Armature)
    arm = create_armature(smd, arm_name)
    qc.a = smd.a = arm

    bpy.context.view_layer.objects.active = arm
    ops.object.mode_set(mode='EDIT')

    edit_bone_map: dict[str, bpy.types.EditBone] = {}
    bone_matrices: dict[str, Matrix] = {}

    def _create(name: str, node, parent_name: str | None):
        eb = arm.data.edit_bones.new(truncate_id_name(ctx, name, bpy.types.Bone))
        eb.tail = (0, 5, 0)
        edit_bone_map[name] = eb
        if parent_name and parent_name in edit_bone_map:
            eb.parent = edit_bone_map[parent_name]
        origin = node.properties.get("origin", [0.0, 0.0, 0.0])
        angles_deg = node.properties.get("angles", [0.0, 0.0, 0.0])
        bone_matrices[eb.name] = local_matrix(origin, angles_deg)

    def _dfs(node, parent_name: str | None):
        name = node.properties.get("name")
        if name:
            _create(name, node, parent_name)
            for child in node.children:
                if child.properties.get("_class") == "Bone":
                    _dfs(child, name)

    for child in skeleton_node.children:
        if child.properties.get("_class") == "Bone":
            _dfs(child, None)

    ops.object.mode_set(mode='OBJECT')
    print(f"- Created {len(edit_bone_map)} bones from VMDL Skeleton")

    ctx.appliedReferencePose = False
    rest_data: dict = {}
    for pbone in arm.pose.bones:
        mat = bone_matrices.get(pbone.name)
        if mat:
            kf = KeyFrame()
            kf.matrix = mat
            rest_data[pbone] = [kf]
    if rest_data:
        apply_frames(ctx, smd, rest_data, 1)

    return arm


def _read_render_meshes(ctx, qc, root_node, filepath, filename, rot_mode) -> None:
    render_mesh_list = root_node.get(recursive=False, _class="RenderMeshList")
    if not render_mesh_list:
        return
    for rmf in render_mesh_list.children:
        if rmf.properties.get("_class") != "RenderMeshFile":
            continue
        dmx_ref = rmf.properties.get("filename", "")
        if not dmx_ref:
            continue
        dmx_path = resolve_dmx_ref(filepath, dmx_ref)
        if not dmx_path:
            ctx.warning(f"{filename}: could not find DMX '{dmx_ref}'")
            continue
        if dmx_path in qc.imported_smds:
            continue
        qc.imported_smds.append(dmx_path)
        prev_append = ctx.append
        ctx.append = 'VALIDATE'
        ctx.num_files_imported += ctx.readDMX(dmx_path, qc.upAxis, rot_mode, False, REF)
        ctx.append = prev_append


def _read_attachments(ctx, smd, arm, root_node, filename) -> None:
    att_list = root_node.get(recursive=False, _class="AttachmentList")
    if not att_list:
        return
    coll = smd.g if smd.g else bpy.context.scene.collection
    # Source bone names are case-insensitive; map them to the real bones.
    bone_lower = {b.name.lower(): b.name for b in arm.data.bones}
    imported_att = 0
    for att in att_list.children:
        if att.properties.get("_class") != "Attachment":
            continue
        att_name = att.properties.get("name", "")
        parent_bone = att.properties.get("parent_bone", "")
        if not att_name:
            continue
        resolved_bone = ""
        if parent_bone:
            resolved_bone = (arm.data.bones[parent_bone].name
                             if parent_bone in arm.data.bones
                             else bone_lower.get(parent_bone.lower(), ""))
            if not resolved_bone:
                ctx.warning(f"Attachment '{att_name}': bone '{parent_bone}' not found - skipped")
                continue
        origin = att.properties.get("relative_origin", [0.0, 0.0, 0.0])
        angles_deg = att.properties.get("relative_angles", [0.0, 0.0, 0.0])
        atch = bpy.data.objects.new(
            name=truncate_id_name(ctx, att_name, "Attachment"), object_data=None)
        coll.objects.link(atch)
        atch.show_in_front = True
        atch.empty_display_type = 'ARROWS'
        atch.parent = arm
        if resolved_bone:
            atch.parent_type = 'BONE'
            atch.parent_bone = resolved_bone
        atch.vs.dmx_attachment = True
        atch.matrix_local = local_matrix(origin, angles_deg)
        imported_att += 1
    if imported_att:
        print(f"- Imported {imported_att} attachment(s)")


def _read_animations(ctx, qc, arm, root_node, filepath, filename, rot_mode) -> None:
    """Every AnimFile, including those nested in Folder nodes, becomes a separate action
    slot on one action named after the VMDL."""
    anim_files = root_node.find_all(recursive=True, _class="AnimFile")
    if not anim_files:
        return

    action_name = truncate_id_name(ctx, os.path.splitext(qc.jobName)[0], bpy.types.Action)
    arm.animation_data_create()
    if not arm.animation_data.action:
        act = bpy.data.actions.new(action_name)
        act.use_fake_user = True
        arm.animation_data.action = act

    bpy.context.view_layer.objects.active = arm
    imported_anims = 0
    for af in anim_files:
        src = af.properties.get("source_filename", "")
        if not src:
            continue
        anim_path = resolve_dmx_ref(filepath, src)
        if not anim_path:
            ctx.warning(f"{filename}: could not find animation DMX '{src}'")
            continue
        if anim_path in qc.imported_smds:
            continue
        qc.imported_smds.append(anim_path)
        prev_append = ctx.append
        ctx.append = 'VALIDATE'
        ctx.num_files_imported += ctx.readDMX(anim_path, qc.upAxis, rot_mode, False, ANIM)
        ctx.append = prev_append
        imported_anims += 1
    if imported_anims:
        print(f"- Imported {imported_anims} animation(s) into action '{action_name}'")
