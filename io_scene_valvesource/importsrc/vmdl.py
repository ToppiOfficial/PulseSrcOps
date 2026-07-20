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


def resolve_content_ref(vmdl_path: str, dmx_ref: str, content_path: str = "") -> str | None:
    """Find the DMX a VMDL references.

    Source 2 paths are relative to the *content* root - for a CS2 addon that is
    `.../content/csgo_addons/<addon>/`, several levels above the VMDL - so neither the
    VMDL's own directory nor the compiled game path resolves them. The content root is
    found by walking up from the VMDL until the reference resolves, which needs no
    configuration because the VMDL always lives inside its own content tree.
    `content_path` (the importer's Content Path field) overrides that for references into
    a different addon, or a VMDL that has been moved out of its tree.
    """
    vmdl_dir = os.path.dirname(vmdl_path)
    normalized = dmx_ref.replace("\\", os.sep).replace("/", os.sep)
    basename = os.path.basename(normalized)

    candidates = []
    if content_path:
        candidates.append(os.path.normpath(
            os.path.join(bpy.path.abspath(content_path), normalized)))
    candidates.append(os.path.join(vmdl_dir, basename))
    candidates.append(os.path.normpath(os.path.join(vmdl_dir, normalized)))
    if State.gamePath:
        candidates.append(os.path.normpath(os.path.join(State.gamePath, normalized)))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    # Walk up for the content root the reference is relative to.
    current = vmdl_dir
    while True:
        candidate = os.path.normpath(os.path.join(current, normalized))
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _content_path(ctx) -> str:
    """Only ImportVMDL offers the field; other entry points fall back to the walk."""
    return getattr(ctx.properties, "contentPath", "") or ""


def read_vmdl(ctx, filepath: str, qc, rot_mode: str) -> int:
    filename = os.path.basename(filepath)
    print(f"\nVMDL IMPORTER: now working on {filename}")

    # smd exists so create_armature can read smd.isDMX and the collection helper works
    smd = ctx.smd = SmdInfo(qc.jobName)
    smd.isDMX = 1
    smd.jobType = REF
    smd.upAxis = qc.upAxis
    smd.rotMode = rot_mode
    ctx.createCollection()

    if not _read_document(ctx, smd, qc, filepath, rot_mode, set()):
        return 0

    printTimeMessage(qc.startTime, filename, "import", "VMDL")
    # Count referenced meshes when present, otherwise the VMDL itself.
    return ctx.num_files_imported or 1


def _read_document(ctx, smd, qc, filepath: str, rot_mode: str, seen: set) -> bool:
    """Process one .vmdl / .vmdl_prefab, prefabs first.

    A PrefabList/Prefab target_file is Source 2's $include: the prefab holds the shared
    base (skeleton, constraints, sometimes meshes) that the model extends, so it has to
    be read before the model's own content. `seen` breaks cycles and stops a prefab
    shared by two branches being imported twice.
    """
    key = os.path.normcase(os.path.abspath(filepath))
    if key in seen:
        return True
    seen.add(key)

    filename = os.path.basename(filepath)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
    except IOError as e:
        ctx.error(f"Could not read {filepath}: {e}")
        return False

    try:
        kv_doc = keyvalues3.KVParser(text).parse()
    except Exception as e:
        ctx.error(f"Failed to parse {filename}: {e}")
        return False

    root_node = kv_doc.roots.get("rootNode")
    if not root_node:
        ctx.error(f"{filename}: no rootNode")
        return False

    _read_prefabs(ctx, smd, qc, root_node, filepath, filename, rot_mode, seen)

    # Skeleton. Either inline Bone children, or a SkeletonFile referencing a DMX.
    skeleton_node = root_node.get(recursive=True, _class="Skeleton")
    if skeleton_node:
        if extract_bones(skeleton_node):
            qc.a = _build_skeleton(ctx, smd, qc, skeleton_node)
        else:
            found = _read_skeleton_file(ctx, qc, skeleton_node, filepath, filename, rot_mode)
            if found:
                qc.a = found
            else:
                ctx.warning(f"{filename}: Skeleton has no Bone children and no usable "
                            f"SkeletonFile")

    # Meshes do not depend on a skeleton - an arms/mesh-only VMDL has no Skeleton node
    # at all, and skipping its RenderMeshList would import nothing.
    _read_render_meshes(ctx, qc, root_node, filepath, filename, rot_mode)

    arm = qc.a or (ctx.smd.a if ctx.smd else None) or find_armature()
    if not arm:
        print(f"- {filename}: no armature, skipping attachments/jigglebones/hitboxes")
        return True
    qc.a = smd.a = arm

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

    return True


def _read_prefabs(ctx, smd, qc, root_node, filepath, filename, rot_mode, seen) -> None:
    for prefab in root_node.find_all(recursive=True, _class="Prefab"):
        target = prefab.properties.get("target_file", "")
        if not target:
            continue
        path = resolve_content_ref(filepath, target, _content_path(ctx))
        if not path:
            ctx.warning(f"{filename}: could not find prefab '{target}'")
            continue
        print(f"- {filename}: including prefab {os.path.basename(path)}")
        _read_document(ctx, smd, qc, path, rot_mode, seen)


def _read_skeleton_file(ctx, qc, skeleton_node, filepath, filename, rot_mode):
    """Import the DMX a SkeletonFile points at and return the armature it built."""
    for child in skeleton_node.children:
        if child.properties.get("_class") != "SkeletonFile":
            continue
        ref = child.properties.get("filename", "")
        if not ref:
            continue
        path = resolve_content_ref(filepath, ref, _content_path(ctx))
        if not path:
            ctx.warning(f"{filename}: could not find skeleton DMX '{ref}'")
            continue
        if path not in qc.imported_smds:
            qc.imported_smds.append(path)
            prev_append = ctx.append
            ctx.append = 'VALIDATE' if qc.a else 'NEW_ARMATURE'
            ctx.num_files_imported += ctx.readDMX(path, qc.upAxis, rot_mode, False, REF)
            ctx.append = prev_append
        # readDMX replaced ctx.smd with its own; the armature it built is on that.
        if ctx.smd and ctx.smd.a:
            qc.a = ctx.smd.a
            return qc.a
    return qc.a or find_armature()


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
        dmx_path = resolve_content_ref(filepath, dmx_ref, _content_path(ctx))
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
        anim_path = resolve_content_ref(filepath, src, _content_path(ctx))
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
