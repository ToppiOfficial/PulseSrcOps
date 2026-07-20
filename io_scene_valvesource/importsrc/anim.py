"""ImportedAnim -> pose bone keyframes.

KeyFrame assembly lives here rather than in the parser because it depends on the
resolved pose bone's parent (see the no-parent up-axis correction below).
"""

import collections
from math import ceil

import bpy
from bpy import ops
from mathutils import Matrix

from .. import datamodel
from ..utils import REF, KeyFrame, get_id, getUpAxisMat
from .dmx import blender_quat
from .build import apply_frames


def build_smd_anim(ctx, smd, parsed) -> None:
    """ParsedFrames (bone id -> matrices) -> pose keyframes."""
    if parsed is None or not smd.a:
        return
    bpy.context.view_layer.objects.active = smd.a
    ops.object.mode_set(mode='POSE')

    keyframes: dict = collections.defaultdict(list)
    for bone_id, entries in parsed.frames.items():
        bone_name = smd.boneIDs.get(bone_id)
        bone = smd.a.pose.bones.get(bone_name) if bone_name else None
        is_phantom = bone is None

        for frame, matrix in entries:
            keyframe = KeyFrame()
            keyframe.frame = frame
            keyframe.matrix = matrix
            keyframe.pos = keyframe.rot = True

            if smd.jobType == REF:
                # Root bones carry the up-axis correction. A phantom id (one the
                # armature has no bone for) counts as a root unless the file gave it
                # a parent.
                is_root = (not bone.parent) if bone else (not smd.phantomParentIDs.get(bone_id))
                if is_root:
                    keyframe.matrix = getUpAxisMat(smd.upAxis) @ keyframe.matrix

            if not is_phantom:
                keyframes[bone].append(keyframe)

    apply_frames(ctx, smd, keyframes, parsed.num_frames)


def build_anim(ctx, smd, ianim) -> None:
    if ianim is None:
        ctx.warning(f"DMX file \"{smd.jobName}\" has no animation data - skipping")
        return

    print(f"Importing DMX animation \"{smd.jobName}\"")

    frameRate = ianim.frame_rate
    start = ianim.start

    lastFrameIndex = 0
    keyframes: dict = collections.defaultdict(list)
    unknown_bones: list[str] = []

    for channel in ianim.channels:
        bone_name = smd.boneTransformIDs.get(channel.transform_id)
        bone = smd.a.pose.bones.get(bone_name) if bone_name else None
        if not bone:
            if ctx.append != 'NEW_ARMATURE' and channel.name_hint not in unknown_bones:
                unknown_bones.append(channel.name_hint)
                print(f"- Animation refers to unrecognised bone \"{channel.name_hint}\"")
            continue

        is_position_channel = channel.attribute == "position"
        is_rotation_channel = channel.attribute == "orientation"
        is_scale_channel = channel.attribute == "scale"

        for i in range(len(channel.times)):
            frame_time = channel.times[i] + start
            if type(frame_time) == int:
                frame_time = datamodel.Time.from_int(frame_time)
            frame_value = channel.values[i]

            keyframe = KeyFrame()
            keyframes[bone].append(keyframe)
            keyframe.frame = frame_time * frameRate
            lastFrameIndex = max(lastFrameIndex, keyframe.frame)

            if not (bone.parent or keyframe.pos or keyframe.rot or keyframe.scale):
                keyframe.matrix = getUpAxisMat(smd.upAxis).inverted()

            if is_position_channel and not keyframe.pos:
                keyframe.matrix @= Matrix.Translation(frame_value)
                keyframe.pos = True
            elif is_rotation_channel and not keyframe.rot:
                keyframe.matrix @= blender_quat(frame_value).to_matrix().to_4x4()
                keyframe.rot = True
            elif is_scale_channel and not keyframe.scale:
                # Source 2 stores a single uniform scale float per bone transform
                keyframe.matrix @= Matrix.Scale(float(frame_value), 4)
                keyframe.scale = True

    if smd.a is None:
        ctx.warning(get_id("importer_err_noanimationbones", True).format(smd.jobName))
        return

    smd.a.hide_set(False)
    bpy.context.view_layer.objects.active = smd.a
    if unknown_bones:
        ctx.warning(get_id("importer_err_missingbones", True).format(
            smd.jobName, len(unknown_bones), smd.a.name))

    duration = ianim.duration
    total_frames = ceil((duration * frameRate) if duration else lastFrameIndex) + 1
    apply_frames(ctx, smd, keyframes, total_frames)
    bpy.context.scene.frame_end += int(round(start * 2 * frameRate, 0))
