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

import bpy, bmesh, random, collections, os
from bpy import ops
from bpy.app.translations import pgettext
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from mathutils import Quaternion, Euler
from math import ceil
from typing import cast
from .utils import *
from . import datamodel, ordered_set, flex, keyvalues3, importsrc

from .utils import PULSE_ATTACHMENT_COLL as _PULSE_ATTACHMENT_COLL, ensure_pulse_collection_at_top as _ensure_pulse_collection_at_top
from .importsrc.flexdata import apply_flex_text_to_object, populate_dme_flex_from_dmx


class ImporterBase(bpy.types.Operator, Logger):
    """Shared file-browser plumbing and the format readers.

    Subclasses declare bl_idname/bl_label, filter_glob, any format-specific
    properties, and implement read_file().
    """
    bl_options = {'UNDO', 'PRESET'}

    qc: QcInfo | None = None
    smd: SmdInfo

    # Properties used by the file browser
    filepath: StringProperty(name="File Path", description="File filepath used for importing the SMD/VTA/DMX/QC file", maxlen=1024, default="", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN'})
    directory: StringProperty(maxlen=1024, default="", subtype='FILE_PATH', options={'HIDDEN'})
    filter_folder: BoolProperty(name="Filter Folders", description="", default=True, options={'HIDDEN'})

    # Options every format honours
    createCollections: BoolProperty(name=get_id("importer_use_collections"), description=get_id("importer_use_collections_tip"), default=True)
    append: EnumProperty(
        name=get_id("importer_bones_mode"),
        description=get_id("importer_bones_mode_desc"),
        items=(
            ('VALIDATE', get_id("importer_bones_validate"), get_id("importer_bones_validate_desc")),
            ('APPEND',   get_id("importer_bones_append"),   get_id("importer_bones_append_desc")),
            ('NEW_ARMATURE', get_id("importer_bones_newarm"), get_id("importer_bones_newarm_desc")),
        ),
        default='APPEND',
    )
    upAxis: EnumProperty(name="Up Axis", items=axes, default='Z', description=get_id("importer_up_tip"))
    rotMode: EnumProperty(
        name=get_id("importer_rotmode"),
        items=(('XYZ', "Euler", ''), ('QUATERNION', "Quaternion", "")),
        default='XYZ',
        description=get_id("importer_rotmode_tip"),
    )
    boneMode: EnumProperty(
        name=get_id("importer_bonemode"),
        items=(('NONE', 'Default', ''), ('ARROWS', 'Arrows', ''), ('SPHERE', 'Sphere', '')),
        default='SPHERE',
        description=get_id("importer_bonemode_tip"),
    )
    def __init__(self, *args, **kwargs):
        bpy.types.Operator.__init__(self, *args, **kwargs)
        Logger.__init__(self)

    def execute(self, context):
        pre_obs = set(bpy.context.scene.objects)
        pre_eem = context.preferences.edit.use_enter_edit_mode
        pre_append = self.append
        context.preferences.edit.use_enter_edit_mode = False

        self.existingBones: list[str] = []
        self.num_files_imported = 0
        self.imported_jigglebones = 0
        self.imported_hitboxes = 0
        self.imported_procbones = 0

        for filepath in [os.path.join(self.directory, file.name) for file in self.files] if self.files else [self.filepath]:
            # read_file returns None for an unreadable path, leaving the running
            # count from any earlier file in a multi-file selection untouched
            count = self.read_file(filepath)
            if count is not None:
                self.num_files_imported = count

            self.append = pre_append

        report_message = get_id("importer_complete", True).format(self.num_files_imported, self.elapsed_time())
        details = []
        if self.imported_hitboxes > 0:
            details.append(f"{self.imported_hitboxes} hitboxes")
        if self.imported_jigglebones > 0:
            details.append(f"{self.imported_jigglebones} jigglebones")
        if self.imported_procbones > 0:
            details.append(f"{self.imported_procbones} procedural bones")
        if details:
            report_message += f" ({', '.join(details)})"

        self.errorReport(report_message)
        if self.num_files_imported:
            if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
                ops.object.mode_set(mode='OBJECT')
            ops.object.select_all(action='DESELECT')
            new_obs = set(bpy.context.scene.objects).difference(pre_obs)
            xy = xyz = 0
            for ob in new_obs:
                ob.select_set(True)
                xy  = max(xy,  int(max(ob.dimensions[0], ob.dimensions[1])))
                xyz = max(xyz, max(xy, int(ob.dimensions[2])))
            bpy.context.view_layer.objects.active = self.qc.a if self.qc else self.smd.a
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.clip_end = max(area.spaces.active.clip_end, xyz * 2)
        if bpy.context.area and bpy.context.area.type == 'VIEW_3D' and bpy.context.region:
            ops.view3d.view_selected()

        context.preferences.edit.use_enter_edit_mode = pre_eem
        self.append = pre_append

        State.update_scene(context.scene)
        if bpy.data.collections.get(_PULSE_ATTACHMENT_COLL):
            _ensure_pulse_collection_at_top(context.scene, context.view_layer)
        return {'FINISHED'}

    def invoke(self, context, event):
        self.properties.upAxis = context.scene.vs.up_axis
        bpy.context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        """Defining this replaces Blender's automatic property layout, so a new property
        will NOT appear in the file browser until it is drawn here or in draw_options.
        Worth the trade: auto-layout renders an ENUM_FLAG as a horizontal row, which
        truncates the prefab data labels to 'Jiggl.../Hitb.../Proc...'."""
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        for prop in ("createCollections", "append", "upAxis", "rotMode", "boneMode"):
            layout.prop(self.properties, prop)
        self.draw_options(layout)

    def draw_options(self, layout) -> None:
        """Format-specific controls, drawn under the shared ones. Subclasses that add
        properties must draw them here."""

    def draw_prefab_data(self, layout) -> None:
        """Stacked, not the default expanded row - three horizontal toggles get their
        labels truncated to 'Jiggl.../Hitb.../Proc...' in the file browser sidebar."""
        col = layout.column(align=True)
        col.use_property_split = False
        col.label(text=get_id("importer_prefabdata"))
        col.prop(self.properties, "prefabData", expand=True)

    def read_file(self, filepath: str) -> int | None:
        raise NotImplementedError

    def report_unreadable(self, filepath: str) -> None:
        if len(filepath) == 0:
            self.report({'ERROR'}, get_id("importer_err_nofile"))
        else:
            self.report({'ERROR'}, get_id("importer_err_badfile", True).format(os.path.basename(filepath)))

    def ensureAnimationBonesValidated(self):
        if self.smd.jobType == ANIM and self.append == 'APPEND' and (hasattr(self.smd, "a") or self.findArmature()):
            print("- Appending bones from animations is destructive; switching Bone Append Mode to \"Validate\"")
            self.append = 'VALIDATE'

    def truncate_id_name(self, name: str, id_type) -> str:
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
        self.error(get_id("importer_err_namelength", True).format(pgettext(id_type if isinstance(id_type, str) else id_type.__name__), name, truncated))
        return str(truncated)

    def parseQuoteBlockedLine(self, line, lower=True):
        if len(line) == 0:
            return ["\n"]

        qc = self.qc
        words = []
        last_word_start = 0
        in_quote = False

        if line[-1] != "\n":
            line += "\n"

        for i in range(len(line)):
            char = line[i]
            nchar = line[i + 1] if i < len(line) - 1 else None
            pchar = line[i - 1] if i > 0 else None

            if not in_quote and ((char == "/" and nchar == "/") or char in ['#', ';']):
                if i > 0:
                    i = i - 1
                break

            if qc:
                if qc.in_block_comment:
                    if char == "/" and pchar == "*":
                        qc.in_block_comment = False
                    continue
                elif char == "/" and nchar == "*":
                    qc.in_block_comment = True
                    continue

            if char == "\"" and pchar != "\\":
                in_quote = not in_quote
            if not in_quote:
                if char in [" ", "\t"]:
                    cur_word = line[last_word_start:i].strip("\"")
                    if len(cur_word) > 0:
                        if (lower and os.name == 'nt') or cur_word[0] == "$":
                            cur_word = cur_word.lower()
                        words.append(cur_word)
                    last_word_start = i + 1

        needBracket = False
        cur_word = line[last_word_start:i]
        if cur_word.endswith("{"):
            needBracket = True
        cur_word = cur_word.strip("\"{")
        if len(cur_word) > 0:
            words.append(cur_word)
        if needBracket:
            words.append("{")
        if line.endswith("\\\\\n") and (len(words) == 0 or words[-1] != "\\\\"):
            words.append("\\\\")
        return words

    # -------------------------------------------------------------------------
    # Bones
    # -------------------------------------------------------------------------

    @classmethod
    def findArmature(cls) -> bpy.types.Object | None:
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

    def createArmature(self, armature_name: str) -> bpy.types.Object:
        smd = self.smd
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

    # -------------------------------------------------------------------------
    # Frames / animation
    # -------------------------------------------------------------------------

    def applyFrames(self, keyframes: dict[bpy.types.PoseBone, list[KeyFrame]], num_frames: int):
        smd = self.smd
        assert smd.a
        ops.object.mode_set(mode='POSE')

        if self.append != 'VALIDATE' and smd.jobType in [REF, ANIM] and not self.appliedReferencePose:
            self.appliedReferencePose = True

            for bone in smd.a.pose.bones:
                bone.matrix_basis.identity()
            for bone, kf in keyframes.items():
                if bone.name in self.existingBones:
                    continue
                elif bone.parent and not keyframes.get(bone.parent):
                    bone.matrix = bone.parent.matrix @ kf[0].matrix
                else:
                    bone.matrix = kf[0].matrix
            ops.pose.armature_apply()

            bone_vis = None if self.properties.boneMode == 'NONE' else bpy.data.objects.get("smd_bone_vis")

            if self.properties.boneMode == 'SPHERE' and (not bone_vis or bone_vis.type != 'MESH'):
                ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=2)
                bone_vis = bpy.context.active_object
                bone_vis.data.name = bone_vis.name = "smd_bone_vis"
                bone_vis.use_fake_user = True
                for collection in bone_vis.users_collection:
                    collection.objects.unlink(bone_vis)
                bpy.context.view_layer.objects.active = smd.a
            elif self.properties.boneMode == 'ARROWS' and (not bone_vis or bone_vis.type != 'EMPTY'):
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
            if self.qc:
                self.qc.dimensions = dimensions
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

    # -------------------------------------------------------------------------
    # Mesh / materials
    # -------------------------------------------------------------------------

    def getMeshMaterial(self, mat_name: str) -> tuple[bpy.types.Material, int]:
        smd = self.smd
        if mat_name:
            mat_name = self.truncate_id_name(mat_name, bpy.types.Material)
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

    def readQC(self, filepath: str, newscene: bool, doAnim: bool, makeCamera: bool, rotMode: str, outer_qc: bool = False) -> int:
        # KST_OLD_QC falls back to the pre-importsrc implementation below.
        if not os.getenv("KST_OLD_QC"):
            return importsrc.read_qc(self, filepath, newscene, doAnim, makeCamera, rotMode, outer_qc)
        return self._readQC_legacy(filepath, newscene, doAnim, makeCamera, rotMode, outer_qc)

    def _readQC_legacy(self, filepath: str, newscene: bool, doAnim: bool, makeCamera: bool, rotMode: str, outer_qc: bool = False) -> int:
        filename = os.path.basename(filepath)
        filedir = os.path.dirname(filepath)

        def normalisePath(path: str) -> str:
            if os.path.sep == '/':
                path = path.replace('\\', '/')
            return os.path.normpath(path)

        if outer_qc:
            print(f"\nQC IMPORTER: now working on {filename}")
            qc = self.qc = QcInfo()
            qc.startTime = time.time()
            qc.jobName = filename
            qc.root_filedir = filedir
            qc.makeCamera = makeCamera
            qc.animation_names = []
            qc.flex_controllers_pending = []
            qc.localvars_pending = []
            qc.expressions_pending = []
            qc.stereo_flex_names_pending = set()
            qc.flex_target_mesh = None
            qc.flex_target_combo_op = None
            if newscene:
                bpy.context.screen.scene = bpy.data.scenes.new(filename)
            elif filename.lower().endswith('.qc'):
                bpy.context.scene.name = filename
        else:
            qc = self.qc

        filepath_lc = filepath.lower()
        if filepath_lc.endswith(('.vmdl', '.vmdl_prefab')):
            return importsrc.read_vmdl(self, filepath, qc, rotMode)

        try:
            with open(filepath, 'r') as f:
                qc_content = f.read()
        except IOError:
            qc_content = ""

        if qc_content and '$jigglebone' in qc_content.lower():
            if not qc.a:
                qc.a = self.findArmature()
            if qc.a:
                imported_count, missing_bones = import_jigglebones_from_content(qc_content, qc.a)
                if imported_count > 0:
                    self.imported_jigglebones += imported_count
                    print(f"- Imported {imported_count} jigglebone(s) from {filename}")
                if missing_bones:
                    self.warning(f"Could not find bones for {len(missing_bones)} jigglebone(s) in {filename}: {', '.join(missing_bones)}")

        file = open(filepath, 'r')
        in_bodygroup = in_lod = in_sequence = False
        lod = 0
        def _pin_flex_target():
            if qc.flex_target_mesh is None and qc.ref_mesh:
                qc.flex_target_mesh     = qc.ref_mesh
                qc.flex_target_combo_op = qc.pending_combo_op

        for line_str in file:
            line = self.parseQuoteBlockedLine(line_str)
            if len(line) == 0:
                continue

            i = 0
            for word in line:
                for var in qc.vars.keys():
                    kw = f"${var}$"
                    pos = word.lower().find(kw)
                    if pos != -1:
                        word = word.replace(word[pos:pos + len(kw)], qc.vars[var])
                line[i] = word.replace("/", "\\")
                i += 1

            if line[0] == "$definemacro":
                self.warning(get_id("importer_qc_macroskip", True).format(filename))
                while True:
                    line = self.parseQuoteBlockedLine(file.readline())
                    if not line or (line and line[-1] != "\\\\"):
                        break
                continue

            if line[0] == "$definevariable":
                qc.vars[line[1]] = line[2].lower()
                continue

            if line[0] == "$pushd":
                if line[1][-1] != "\\":
                    line[1] += "\\"
                qc.dir_stack.append(line[1])
                continue
            if line[0] == "$popd":
                try:
                    qc.dir_stack.pop()
                except IndexError:
                    pass
                continue

            if line[0] == "$upaxis":
                qc.upAxis = bpy.context.scene.vs.up_axis = line[1].upper()
                qc.upAxisMat = getUpAxisMat(line[1])
                continue

            if line[0] == "$definebone":
                pass

            if line[0] == "$hboxset":
                if len(line) >= 2:
                    new_set = line[1].strip('"')
                    if not qc.hboxset_name:
                        qc.hboxset_name = new_set
                    elif qc.hboxset_name != new_set:
                        self.warning(f"Multiple $hboxset values found; using first (\"{qc.hboxset_name}\"), ignoring \"{new_set}\"")
                continue

            if line[0] == "$hbox":
                if not qc.a:
                    qc.a = self.findArmature()
                    if not qc.a:
                        self.warning(get_id("qc_warn_noarmature_hbox", True).format(filename))
                        continue

                prev_pose_position = qc.a.data.pose_position
                qc.a.data.pose_position = 'REST'
                bpy.context.view_layer.update()

                created, skipped, bones = import_hitboxes_from_content(line_str, qc.a, bpy.context, self.createCollections, hboxset_name=qc.hboxset_name)

                qc.a.data.pose_position = prev_pose_position
                bpy.context.view_layer.update()

                if created > 0:
                    self.imported_hitboxes += created
                    print(f"- Imported {created} hitbox(es) from QC")
                if skipped > 0:
                    print(f"  Warning: Skipped {skipped} hitbox(es) with missing bones: {', '.join(bones)}")
                continue

            if line[0] in ["$proceduralbones", "$procbones"]:
                if len(line) < 2:
                    continue
                if not qc.a:
                    qc.a = self.findArmature()
                if not qc.a:
                    self.warning(f"$proceduralbones in {filename} but no armature to bind to")
                    continue
                vrd_path = os.path.join(qc.cd(), normalisePath(line[1]))
                if not os.path.splitext(vrd_path)[1]:
                    vrd_path = appendExt(vrd_path, "vrd")
                try:
                    with open(vrd_path, 'r') as vf:
                        vrd_content = vf.read()
                except IOError:
                    self.warning(f"Could not read procedural bone file '{vrd_path}'")
                    continue
                prev_pose_position = qc.a.data.pose_position
                qc.a.data.pose_position = 'REST'
                bpy.context.view_layer.update()
                pb_count, pb_missing = import_proc_bones_from_vrd_content(
                    vrd_content, qc.a, bpy.context.scene)
                qc.a.data.pose_position = prev_pose_position
                bpy.context.view_layer.update()
                if pb_count > 0:
                    self.imported_procbones += pb_count
                    print(f"- Imported {pb_count} procedural bone(s) from {os.path.basename(vrd_path)}")
                if pb_missing:
                    self.warning(f"Could not find bones for {len(pb_missing)} procedural entr(y/ies) "
                                 f"in {os.path.basename(vrd_path)}: {', '.join(pb_missing)}")
                continue

            def import_file(word_index, default_ext, smd_type, append='APPEND', layer=0, in_file_recursion=False):
                path = os.path.join(qc.cd(), appendExt(normalisePath(line[word_index]), default_ext))
                if not in_file_recursion and not os.path.exists(path):
                    return import_file(word_index, "dmx", smd_type, append, layer, True)
                if path not in qc.imported_smds:
                    qc.imported_smds.append(path)
                    self.append = append if qc.a else 'NEW_ARMATURE'
                    self.num_files_imported += (self.readDMX if path.endswith("dmx") else self.readSMD)(path, qc.upAxis, rotMode, False, smd_type, target_layer=layer)
                return True

            if line[0] in ["$body", "$model"]:
                import_file(2, "smd", REF)
                continue
            if line[0] == "$lod":
                in_lod = True
                lod += 1
                continue
            if in_lod:
                if line[0] == "replacemodel":
                    import_file(2, "smd", REF, 'VALIDATE', layer=lod)
                    continue
                if "}" in line:
                    in_lod = False
                    continue
            if line[0] == "$bodygroup":
                in_bodygroup = True
                continue
            if in_bodygroup:
                if line[0] == "studio":
                    import_file(1, "smd", REF)
                    continue
                if "}" in line:
                    in_bodygroup = False
                    continue

            if in_sequence or (doAnim and line[0] in ["$sequence", "$animation"]):
                num_words_to_skip = 2 if not in_sequence else 0
                for i in range(len(line)):
                    if num_words_to_skip:
                        num_words_to_skip -= 1
                        continue
                    if line[i] == "{":
                        in_sequence = True
                        continue
                    if line[i] == "}":
                        in_sequence = False
                        continue
                    if line[i] in ["hidden", "autolay", "realtime", "snap", "spline", "xfade", "delta", "predelta"]:
                        continue
                    if line[i] in ["fadein", "fadeout", "addlayer", "blendwidth", "node"]:
                        num_words_to_skip = 1
                        continue
                    if line[i] in ["activity", "transision", "rtransition"]:
                        num_words_to_skip = 2
                        continue
                    if line[i] in ["blend"]:
                        num_words_to_skip = 3
                        continue
                    if line[i] in ["blendlayer"]:
                        num_words_to_skip = 5
                        continue
                    if not qc.a:
                        qc.a = self.findArmature()
                    if not qc.a:
                        self.warning(get_id("qc_warn_noarmature", True).format(line_str.strip()))
                        continue
                    if line[i].lower() not in qc.animation_names:
                        if not qc.a.animation_data:
                            qc.a.animation_data_create()
                        last_action = qc.a.animation_data.action
                        import_file(i, "smd", ANIM, 'VALIDATE')
                        if line[0] == "$animation":
                            qc.animation_names.append(line[1].lower())
                        while i < len(line) - 1:
                            i += 1
                    break
                continue

            if line[0] == "flexfile":
                import_file(1, "vta", FLEX, 'VALIDATE')
                continue

            if qc.ref_mesh and line[0] in ["flex", "flexpair"]:
                for i in range(1, len(line)):
                    if line[i] == "frame":
                        shape = qc.ref_mesh.data.shape_keys.key_blocks.get(line[i + 1])
                        if shape and shape.name.startswith("Key"):
                            shape.name = line[1]
                        break
                if line[0] == "flexpair":
                    qc.stereo_flex_names_pending.add(line[1])
                continue

            if line[0] == "flexcontroller" and qc.ref_mesh and len(line) >= 3:
                try:
                    fc_type = line[1]
                    if len(line) >= 5 and line[2] == "range":
                        flex_min, flex_max = float(line[3]), float(line[4])
                        names = line[5:]
                    else:
                        flex_min, flex_max = 0.0, 1.0
                        names = line[2:]
                except (ValueError, IndexError):
                    continue
                if not names:
                    continue
                _pin_flex_target()
                for name in names:
                    qc.flex_controllers_pending.append((name, fc_type, flex_min, flex_max))
                continue

            if line[0] == "localvar" and qc.ref_mesh and len(line) >= 2:
                _pin_flex_target()
                _lv_m = re.match(r'(?i)localvar\s+(.+?)(?:\s*//.*)?$', line_str.strip())
                if _lv_m:
                    for _lv in _lv_m.group(1).split():
                        qc.localvars_pending.append(_lv)
                continue

            if line[0].startswith('%') and qc.ref_mesh:
                m = re.match(r'^\s*%(\w+)\s*=\s*(.+?)(?:\s*//.*)?$', line_str.rstrip())
                if m:
                    _pin_flex_target()
                    qc.expressions_pending.append((m.group(1), m.group(2).strip()))
                continue

            if line[0] == "noautodmxrules":
                _pin_flex_target()
                qc.no_auto_dmx_rules = True
                continue

            if line[0] in ["$collisionmodel", "$collisionjoints"]:
                import_file(1, "smd", PHYS, 'VALIDATE', layer=10)
                continue

            if line[0] == "$origin":
                if qc.makeCamera:
                    data = bpy.data.cameras.new(qc.jobName + "_origin")
                    name = "camera"
                else:
                    data = None
                    name = "empty object"
                print(f"QC IMPORTER: created {name} at $origin\n")

                origin = bpy.data.objects.new(qc.jobName + "_origin", data)
                bpy.context.scene.collection.objects.link(origin)
                origin.rotation_euler = Vector([pi / 2, 0, pi]) + Vector(getUpAxisMat(qc.upAxis).inverted().to_euler())
                ops.object.select_all(action="DESELECT")
                origin.select_set(True)
                ops.object.transform_apply(rotation=True)

                for i in range(3):
                    origin.location[i] = float(line[i + 1])
                origin.matrix_world = getUpAxisMat(qc.upAxis) @ origin.matrix_world

                if qc.makeCamera:
                    bpy.context.scene.camera = origin
                    origin.data.lens_unit = 'DEGREES'
                    origin.data.lens = 31.401752
                    origin.data.shift_y = -0.27
                    origin.data.shift_x = 0.36
                    origin.data.passepartout_alpha = 1
                else:
                    origin.empty_display_type = 'PLAIN_AXES'

                qc.origin = origin

            if line[0] == "$include":
                path = os.path.join(qc.root_filedir, normalisePath(line[1]))
                if not path.endswith(".qc") and not path.endswith(".qci"):
                    if os.path.exists(appendExt(path, ".qci")):
                        path = appendExt(path, ".qci")
                    elif os.path.exists(appendExt(path, ".qc")):
                        path = appendExt(path, ".qc")
                try:
                    self._readQC_legacy(path, False, doAnim, makeCamera, rotMode)
                except IOError:
                    self.warning(get_id("importer_err_qci", True).format(path))

        file.close()

        if qc.origin:
            qc.origin.parent = qc.a
            if qc.ref_mesh:
                size = min(qc.ref_mesh.dimensions) / 15
                if qc.makeCamera:
                    qc.origin.data.display_size = size
                else:
                    qc.origin.empty_display_size = size

        if outer_qc:
            # Apply all accumulated flex data from this file and any $include children.
            # Flex controllers/rules are global model data, so they are applied to every
            # imported mesh that has shape keys - not just a single target mesh.
            target_meshes = [m for m in qc.flex_meshes if m and hasShapes(m)]
            if qc.flex_target_mesh and qc.flex_target_mesh not in target_meshes:
                if hasShapes(qc.flex_target_mesh) or not target_meshes:
                    target_meshes.append(qc.flex_target_mesh)

            if target_meshes and (qc.flex_controllers_pending or qc.localvars_pending
                                  or qc.expressions_pending or qc.no_auto_dmx_rules
                                  or qc.flex_target_combo_op):
                for ob in target_meshes:
                    if qc.no_auto_dmx_rules:
                        ob.vs.dme_flexcontrollers.clear()
                        ob.vs.dme_flex_rules.clear()
                        # Rename stereo shape keys from the DMX combo op: a controller with stereo=True
                        # and exactly one rawControlName means a single shape key that should use the
                        # compound L+R naming convention (e.g. "AU15" = "AU15L+AU15R").
                        # Controllers with two rawControlNames already have separate L/R shape keys.
                        if (qc.flex_target_combo_op
                                and ob.data and hasattr(ob.data, 'shape_keys') and ob.data.shape_keys):
                            _key_blocks = ob.data.shape_keys.key_blocks
                            for _ctrl in qc.flex_target_combo_op.get("controls", []):
                                _raw = _ctrl.get("rawControlNames", [])
                                if bool(_ctrl.get("stereo", False)) and len(_raw) == 1:
                                    _sk = _key_blocks.get(_raw[0])
                                    if _sk:
                                        _sk.name = f"{_raw[0]}L+{_raw[0]}R"
                    elif qc.flex_target_combo_op:
                        self._populate_dme_flex_from_dmx(ob, qc.flex_target_combo_op)

                    apply_flex_text_to_object(ob, {
                        'controllers': qc.flex_controllers_pending,
                        'localvars': qc.localvars_pending,
                        'expressions': qc.expressions_pending,
                        'stereo_names': qc.stereo_flex_names_pending,
                    })

                    if ob.vs.dme_flexcontrollers:
                        print(f"- Imported {len(ob.vs.dme_flexcontrollers)} flex controllers and "
                              f"{len(ob.vs.dme_flex_rules)} flex rules from QC/DMX into '{ob.name}'")

            printTimeMessage(qc.startTime, filename, "import", "QC")
        return self.num_files_imported

    # -------------------------------------------------------------------------
    # SMD init helpers
    # -------------------------------------------------------------------------

    def initSMD(self, filepath: str, smd_type, upAxis: str, rotMode: str, target_layer: int) -> SmdInfo:
        smd = self.smd = SmdInfo(os.path.splitext(os.path.basename(filepath))[0])
        smd.jobType = smd_type
        smd.startTime = time.time()
        smd.layer = target_layer
        smd.rotMode = rotMode
        if self.qc:
            smd.upAxis = self.qc.upAxis
            smd.a = self.qc.a
        if upAxis:
            smd.upAxis = upAxis
        return smd

    def createCollection(self):
        if self.smd.jobType and self.smd.jobType != ANIM:
            if self.createCollections:
                self.smd.g = bpy.data.collections.new(self.smd.jobName)
                bpy.context.scene.collection.children.link(self.smd.g)
            else:
                self.smd.g = bpy.context.scene.collection

    # -------------------------------------------------------------------------
    # SMD file reader
    # -------------------------------------------------------------------------

    def readSMD(self, filepath: str, upAxis: str, rotMode: str, newscene: bool = False, smd_type=None, target_layer: int = 0) -> int:
        smd = self.initSMD(filepath, smd_type, upAxis, rotMode, target_layer)
        self.appliedReferencePose = False

        try:
            smd.file = file = open(filepath, 'r')
        except IOError as err:
            self.error(get_id("importer_err_smd", True).format(smd.jobName, err))
            return 0

        if newscene:
            bpy.context.screen.scene = bpy.data.scenes.new(smd.jobName)
        elif bpy.context.scene.name == pgettext("Scene"):
            bpy.context.scene.name = smd.jobName

        print(f"\nSMD IMPORTER: now working on {smd.jobName}")

        while True:
            header = self.parseQuoteBlockedLine(file.readline())
            if header:
                break

        if header != ["version", "1"]:
            self.warning(get_id("importer_err_smd_ver"))

        if smd.jobType is None:
            importsrc.scan_smd(smd)
        self.createCollection()

        # Order is forced by the format: the node block must be built into an armature
        # before triangle weights can resolve, so this stays a single pass over the file.
        for line in file:
            if line == "nodes\n":
                importsrc.build_smd_skeleton(self, smd, importsrc.read_nodes(smd, self.qc))
            if line == "skeleton\n":
                importsrc.build_smd_anim(self, smd, importsrc.read_frames(self, smd, self.qc))
            if line == "triangles\n":
                group_names = [b.name for b in smd.a.data.bones] if smd.a else []
                imesh = importsrc.read_polys(self, smd, group_names, self.qc)
                if imesh:
                    ob = importsrc.build_mesh(self, smd, imesh)
                    if smd.jobType == REF and self.qc:
                        self.qc.ref_mesh = ob
                        self.qc.ref_meshes.append(ob)
                    # Leave the mesh active and selected. A VTA imported in the same
                    # batch gets a fresh SmdInfo, so read_shapes finds its target by
                    # scanning the selection - readPolys did this for the same reason.
                    ops.object.select_all(action="DESELECT")
                    ob.select_set(True)
                    bpy.context.view_layer.objects.active = ob
                    for poly in ob.data.polygons:
                        poly.select = True
            if line == "vertexanimation\n":
                importsrc.read_shapes(self, smd)

        file.close()
        printTimeMessage(smd.startTime, smd.jobName, "import")
        return 1

    # -------------------------------------------------------------------------
    # DMX file reader
    # -------------------------------------------------------------------------

    def readDMX(self, filepath: str, upAxis: str, rotMode: str, newscene: bool = False, smd_type=None, target_layer: int = 0) -> int:
        # KST_OLD_DMX_IMPORT falls back to the pre-importsrc implementation below.
        # Delete _readDMX_legacy once the new path is verified (mirrors KST_OLD_DMX
        # on the export rewrite).
        if os.getenv("KST_OLD_DMX_IMPORT"):
            return self._readDMX_legacy(filepath, upAxis, rotMode, newscene, smd_type, target_layer)

        smd = self.initSMD(filepath, smd_type, upAxis, rotMode, target_layer)
        smd.isDMX = 1

        bench = BenchMarker(1, "DMX")

        target_arm = self.findArmature() if self.append != 'NEW_ARMATURE' else None
        if target_arm:
            smd.a = target_arm

        smd.atch = None
        smd.layer = target_layer
        if bpy.context.active_object:
            ops.object.mode_set(mode='OBJECT')
        self.appliedReferencePose = False

        print(f"\nDMX IMPORTER: now working on {os.path.basename(filepath)}")

        try:
            print("- Loading DMX...")
            try:
                parsed = importsrc.load_dmx(filepath, smd_type, smd.upAxis)
            except IOError as e:
                self.error(e)
                return 0
            bench.report("Load DMX")

            if bpy.context.scene.name.startswith("Scene"):
                bpy.context.scene.name = smd.jobName

            smd.upAxis = parsed.upAxis
            smd.jobType = parsed.jobType
            self.createCollection()
            self.ensureAnimationBonesValidated()

            ifile = importsrc.read_file(parsed)
            for version in parsed.version_bumps:
                self._ensureSceneDmxVersion(version)
            for message in parsed.warnings:
                self.warning(message)

            bone_matrices = importsrc.build_skeleton(
                self, smd, ifile.skeleton, target_arm,
                parsed.DmeModel.name or smd.jobName)
            importsrc.apply_rest_pose(self, smd, bone_matrices)

            if smd.a and smd.jobType != ANIM:
                importsrc.apply_dmx_prefab_data(self, smd, parsed, ifile.skeleton)

            imported_meshes = [
                importsrc.build_mesh(self, smd, imesh, parsed.corrective_separator)
                for imesh in ifile.meshes
            ]

            # Flex controllers are global model data: apply them to every imported mesh
            # with shape keys, not just the last one parsed. When called from readQC,
            # defer so readQC can merge QC data on top.
            if smd.jobType == REF and smd.m:
                if self.qc:
                    self.qc.ref_mesh = smd.m
                    self.qc.ref_meshes.extend(m for m in imported_meshes
                                              if m not in self.qc.ref_meshes)
                flex_meshes = [m for m in imported_meshes if hasShapes(m)]
                _combo_op = parsed.root.get("combinationOperator")
                if self.qc:
                    for m in flex_meshes:
                        if m not in self.qc.flex_meshes:
                            self.qc.flex_meshes.append(m)
                    if _combo_op:
                        self.qc.pending_combo_op = _combo_op
                elif _combo_op:
                    for m in (flex_meshes or [smd.m]):
                        self._populate_dme_flex_from_dmx(m, _combo_op)

            if smd.jobType == ANIM:
                importsrc.build_anim(self, smd, ifile.anim)

        except datamodel.AttributeError as e:
            e.args = [f"Invalid DMX file: {e.args[0] if e.args else 'Unknown error'}"]
            raise

        bench.report("DMX imported in")
        return 1

    def _readDMX_legacy(self, filepath: str, upAxis: str, rotMode: str, newscene: bool = False, smd_type=None, target_layer: int = 0) -> int:
        smd = self.initSMD(filepath, smd_type, upAxis, rotMode, target_layer)
        smd.isDMX = 1

        bench = BenchMarker(1, "DMX")

        target_arm = self.findArmature() if self.append != 'NEW_ARMATURE' else None
        if target_arm:
            smd.a = target_arm

        ob = bone = smd.atch = None
        smd.layer = target_layer
        if bpy.context.active_object:
            ops.object.mode_set(mode='OBJECT')
        self.appliedReferencePose = False

        print(f"\nDMX IMPORTER: now working on {os.path.basename(filepath)}")

        try:
            print("- Loading DMX...")
            try:
                dm = datamodel.load(filepath)
            except IOError as e:
                self.error(e)
                return 0
            bench.report("Load DMX")

            if bpy.context.scene.name.startswith("Scene"):
                bpy.context.scene.name = smd.jobName

            keywords = getDmxKeywords(dm.format_ver)

            correctiveSeparator = '_'
            if dm.format_ver >= 22 and any([elem for elem in dm.elements if elem.type == "DmeVertexDeltaData" and '__' in elem.name]):
                correctiveSeparator = '__'
                self._ensureSceneDmxVersion(dmx_version(9, 22, compiler=Compiler.MODELDOC))

            if not smd_type:
                if dm.root.get("model"):
                    smd.jobType = REF
                elif dm.root.get("animationList") or dm.root.get("channels"):
                    smd.jobType = ANIM
                else:
                    smd.jobType = REF
            self.createCollection()
            self.ensureAnimationBonesValidated()

            DmeModel = dm.root["skeleton"]
            transforms = (
                DmeModel["baseStates"][0]["transforms"]
                if DmeModel.get("baseStates") and len(DmeModel["baseStates"]) > 0
                else None
            )

            DmeAxisSystem = DmeModel.get("axisSystem")
            if DmeAxisSystem:
                for axis in axes_lookup.items():
                    if axis[1] == DmeAxisSystem["upAxis"] - 1:
                        upAxis = smd.upAxis = axis[0]
                        break

            def getBlenderQuat(datamodel_quat):
                return Quaternion([datamodel_quat[3], datamodel_quat[0], datamodel_quat[1], datamodel_quat[2]])

            def get_transform_matrix(elem):
                out = Matrix()
                if not elem:
                    return out
                trfm = elem.get("transform")
                if transforms:
                    for e in transforms:
                        if e.name == elem.name:
                            trfm = e
                if not trfm:
                    return out
                out @= Matrix.Translation(Vector(trfm["position"]))
                out @= getBlenderQuat(trfm["orientation"]).to_matrix().to_4x4()
                return out

            def isBone(elem) -> bool:
                # DmeQuatInterpBone (TRIGGER) and DmeAimAtBone (LOOKAT) are the
                # procedural-bone joint types the DME exporter promotes helper
                # joints to; they are DmeJoint subclasses, so treat them as bones
                # or their joints (and any children) are skipped on import.
                return elem.type in ["DmeDag", "DmeJoint", "DmeJiggleBone",
                                     "DmeQuatInterpBone", "DmeAimAtBone"]

            def getBoneForElement(elem) -> bpy.types.EditBone:
                return smd.a.data.edit_bones[smd.boneIDs[elem.id]]

            def enumerateBonesAndAttachments(elem: datamodel.Element):
                parent = elem if isBone(elem) else None
                for child in cast(list[datamodel.Element], elem.get("children") or []):
                    if child.type == "DmeDag" and child.get("shape") and child["shape"].type == "DmeAttachment":
                        yield (cast(datamodel.Element, child["shape"]), parent)
                    elif isBone(child) and child.name != implicit_bone_name:
                        boneShape = child.get("shape")
                        if not boneShape or boneShape.get("currentState") is None:
                            yield (child, parent)
                        yield from enumerateBonesAndAttachments(child)
                    elif child.type == "DmeModel":
                        yield from enumerateBonesAndAttachments(child)

            # -----------------------------------------------------------------
            # Skeleton setup
            # -----------------------------------------------------------------
            bone_matrices: dict[str, Matrix] = {}

            if target_arm:
                # Validate / append against existing armature
                missing_bones: list[str] = []
                bpy.context.view_layer.objects.active = smd.a
                smd.a.hide_set(False)
                ops.object.mode_set(mode='EDIT')

                for (elem, parent) in enumerateBonesAndAttachments(DmeModel):
                    # Attachments are only valid when we have an armature
                    if elem.type == "DmeAttachment":
                        self.warning(
                            f"DMX attachment '{elem.name}' encountered while validating against "
                            f"existing armature - attachments are skipped in validate/append mode"
                        )
                        continue
                    if elem.name is None:
                        continue

                    bone = smd.a.data.edit_bones.get(self.truncate_id_name(elem.name, bpy.types.Bone))
                    if not bone:
                        if self.append == 'APPEND' and smd.jobType in [REF, ANIM]:
                            bone = smd.a.data.edit_bones.new(self.truncate_id_name(elem.name, bpy.types.Bone))
                            bone.parent = getBoneForElement(parent) if parent else None
                            bone.tail = (0, 5, 0)
                            bone_matrices[bone.name] = get_transform_matrix(elem)
                            smd.boneIDs[elem.id] = bone.name
                            smd.boneTransformIDs[elem["transform"].id] = bone.name
                        else:
                            missing_bones.append(elem.name)
                    else:
                        scene_parent = bone.parent.name if bone.parent else "<None>"
                        dmx_parent = parent.name if parent else "<None>"
                        if scene_parent != dmx_parent:
                            self.warning(get_id('importer_bone_parent_miss', True).format(
                                elem.name, scene_parent, dmx_parent, smd.jobName))
                        smd.boneIDs[elem.id] = bone.name
                        smd.boneTransformIDs[elem["transform"].id] = bone.name

                if missing_bones and smd.jobType != ANIM:
                    self.warning(get_id("importer_err_missingbones", True).format(smd.jobName, len(missing_bones), smd.a.name))
                    print("\n".join(missing_bones))

            else:
                # No existing armature - inspect what the DMX contains
                skeleton_items = list(enumerateBonesAndAttachments(DmeModel))
                has_actual_bones = any(isBone(e) for e, _ in skeleton_items)
                has_attachments  = any(e.type == "DmeAttachment" for e, _ in skeleton_items)

                if skeleton_items and not has_actual_bones:
                    # DMX contains only attachments with no skeleton
                    if has_attachments:
                        att_count = sum(1 for e, _ in skeleton_items if e.type == "DmeAttachment")
                        self.warning(
                            f"DMX '{os.path.basename(filepath)}' contains {att_count} attachment(s) "
                            f"but no skeleton - attachments will not be imported"
                        )
                    # smd.a remains None; mesh (if any) is still imported below

                elif has_actual_bones:
                    # Create a new armature from the DMX skeleton
                    self.append = 'NEW_ARMATURE'
                    ob = smd.a = self.createArmature(
                        self.truncate_id_name(DmeModel.name or smd.jobName, bpy.types.Armature))
                    if self.qc:
                        self.qc.a = ob
                    bpy.context.view_layer.objects.active = smd.a
                    ops.object.mode_set(mode='EDIT')

                    smd.a.matrix_world = getUpAxisMat(smd.upAxis)

                    for (elem, parent) in skeleton_items:
                        if elem.name is None:
                            continue

                        parent_bone = getBoneForElement(parent) if parent else None

                        if elem.type == "DmeAttachment":
                            # Attachments require a parent bone - warn if none
                            if parent_bone is None:
                                self.warning(
                                    f"Attachment '{elem.name}' has no parent bone - skipped"
                                )
                                continue
                            atch = smd.atch = bpy.data.objects.new(
                                name=self.truncate_id_name(elem.name, "Attachment"),
                                object_data=None,
                            )
                            (smd.g if smd.g else bpy.context.scene.collection).objects.link(atch)
                            atch.show_in_front = True
                            atch.empty_display_type = 'ARROWS'
                            atch.parent = smd.a
                            atch.parent_type = 'BONE'
                            atch.parent_bone = parent_bone.name
                            atch.vs.dmx_attachment = True
                            atch.matrix_local = get_transform_matrix(elem)
                        else:
                            bone = smd.a.data.edit_bones.new(
                                self.truncate_id_name(elem.name, bpy.types.Bone))
                            bone.parent = parent_bone
                            bone.tail = (0, 5, 0)
                            bone_matrices[bone.name] = get_transform_matrix(elem)
                            smd.boneIDs[elem.id] = bone.name
                            smd.boneTransformIDs[elem["transform"].id] = bone.name

            # Apply rest-pose transforms for any bones we just created
            if smd.a:
                ops.object.mode_set(mode='POSE')
                if smd.jobType != ANIM:
                    restData: dict[bpy.types.PoseBone, list[KeyFrame]] = {}
                    for bone in smd.a.pose.bones:
                        mat = bone_matrices.get(bone.name)
                        if mat:
                            keyframe = KeyFrame()
                            keyframe.matrix = mat
                            restData[bone] = [keyframe]
                    if restData:
                        self.applyFrames(restData, 1)

            # -----------------------------------------------------------------
            # Jigglebones & hitboxes (DME / model-DMX mode, Source 1)
            # -----------------------------------------------------------------
            # These are no-ops when the DMX lacks DmeJiggleBone joints / a hitboxSetList, so
            # they are safe to attempt on any reference. Only for skeletal reference imports.
            if smd.a and smd.jobType != ANIM:
                jiggle_elems = [
                    (elem, smd.boneIDs.get(elem.id))
                    for (elem, _parent) in enumerateBonesAndAttachments(DmeModel)
                    if elem.type == "DmeJiggleBone"
                ]
                if jiggle_elems:
                    jb_count, jb_missing = import_jigglebones_from_dmx_elements(jiggle_elems, smd.a)
                    print(f"- Imported {jb_count} jigglebone(s) from DMX")
                    if jb_missing:
                        self.warning(
                            f"DMX jigglebones: {len(jb_missing)} bone(s) not found on "
                            f"'{smd.a.name}': {', '.join(jb_missing)}")

                hb_created, hb_skipped, hb_bones = import_hitboxes_from_dmx_root(dm.root, smd.a)
                if hb_created or hb_skipped:
                    print(f"- Imported {hb_created} hitbox(es) from DMX")
                    if hb_skipped:
                        self.warning(
                            f"DMX hitboxes: {hb_skipped} skipped, bone(s) not found on "
                            f"'{smd.a.name}': {', '.join(hb_bones)}")

                # Procedural (helper) bones: DmeQuatInterpBone (TRIGGER) /
                # DmeAimAtBone (LOOKAT) joints promoted on export. Rebuild each as a
                # vs.proc_bones entry (with a reconstructed slot action for triggers).
                proc_elems = []
                proc_attachments: dict[str, tuple] = {}
                for (elem, parent) in enumerateBonesAndAttachments(DmeModel):
                    if elem.type in ("DmeQuatInterpBone", "DmeAimAtBone"):
                        proc_elems.append((elem, smd.boneIDs.get(elem.id)))
                    elif elem.type == "DmeAttachment":
                        parent_name = smd.boneIDs.get(parent.id) if parent else None
                        proc_attachments[elem.name] = (
                            parent_name, get_transform_matrix(elem).to_translation())
                if proc_elems:
                    pb_count, pb_missing = import_proc_bones_from_dmx_elements(
                        proc_elems, smd.a, bpy.context.scene, proc_attachments)
                    print(f"- Imported {pb_count} procedural bone(s) from DMX")
                    if pb_missing:
                        self.warning(
                            f"DMX procedural bones: {len(pb_missing)} entr(y/ies) skipped, "
                            f"bone(s) not found on '{smd.a.name}': {', '.join(pb_missing)}")

            # -----------------------------------------------------------------
            # Mesh parser (nested helper)
            # -----------------------------------------------------------------
            # Every DmeMesh created during this import, so global flex data
            # (combinationOperator / QC flex text) can be applied to all of
            # them rather than only the last mesh parsed.
            imported_meshes: list[bpy.types.Object] = []

            def parseModel(elem, matrix=Matrix(), last_bone=None):
                if elem.type in ["DmeModel", "DmeDag", "DmeJoint", "DmeJiggleBone",
                                 "DmeQuatInterpBone", "DmeAimAtBone"]:
                    if elem.type == "DmeDag":
                        matrix = matrix @ get_transform_matrix(elem)
                    if elem.get("children") and elem["children"]:
                        if elem.type in ["DmeJoint", "DmeJiggleBone",
                                         "DmeQuatInterpBone", "DmeAimAtBone"]:
                            last_bone = elem
                        subelems = elem["children"]
                    elif elem.get("shape"):
                        subelems = [elem["shape"]]
                    else:
                        return
                    for subelem in subelems:
                        parseModel(subelem, matrix, last_bone)

                elif elem.type == "DmeMesh":
                    DmeMesh = elem
                    if bpy.context.active_object:
                        ops.object.mode_set(mode='OBJECT')
                    mesh_name = self.truncate_id_name(DmeMesh.name, bpy.types.Mesh)
                    ob = smd.m = bpy.data.objects.new(name=mesh_name, object_data=bpy.data.meshes.new(name=mesh_name))
                    smd.g.objects.link(ob)
                    ob.show_wire = smd.jobType == PHYS

                    DmeVertexData = DmeMesh["currentState"]
                    have_weightmap = keywords["weight"] in DmeVertexData["vertexFormat"]

                    if smd.a:
                        ob.parent = smd.a
                        if have_weightmap:
                            amod = ob.modifiers.new(name="Armature", type='ARMATURE')
                            amod.object = smd.a
                            amod.use_bone_envelopes = False
                    else:
                        ob.matrix_local = getUpAxisMat(smd.upAxis)

                    print(f"Importing DMX mesh \"{DmeMesh.name}\"")

                    bm = bmesh.new()
                    bm.from_mesh(ob.data)

                    positions = DmeVertexData[keywords['pos']]
                    positionsIndices = DmeVertexData[keywords['pos'] + "Indices"]

                    for pos in positions:
                        bm.verts.new(Vector(pos))
                    bm.verts.ensure_lookup_table()

                    skipfaces: set[int] = set()
                    vertex_layer_infos = []

                    class VertexLayerInfo:
                        def __init__(self, layer, indices, values):
                            self.layer   = layer
                            self.indices = indices
                            self.values  = values

                        def get_loop_value(self, loop_index):
                            return self.values[self.indices[loop_index]]

                    # Normals - stored temporarily as a float_vector attribute,
                    # applied via normals_split_custom_set after mesh conversion
                    normalsLayer = bm.loops.layers.float_vector.new("__bst_normal")
                    normalsLayerName = normalsLayer.name
                    vertex_layer_infos.append(VertexLayerInfo(
                        normalsLayer,
                        DmeVertexData[keywords['norm'] + "Indices"],
                        DmeVertexData[keywords['norm']],
                    ))

                    def warnUneditableVertexData(name: str):
                        self.warning(f"Vertex data '{name}' was imported but cannot be edited in Blender")

                    def isClothEnableMap(name: str) -> bool:
                        return name.startswith("cloth_enable$")

                    for vertexMap in [p for p in DmeVertexData["vertexFormat"] if p not in keywords.values()]:
                        indices = DmeVertexData.get(vertexMap + "Indices")
                        if not indices:
                            continue
                        values = DmeVertexData.get(vertexMap)
                        if not isinstance(values, list) or len(values) == 0:
                            continue

                        is_color = False
                        if isinstance(values[0], float):
                            if isClothEnableMap(vertexMap):
                                # Cloth-enable maps are imported as vertex groups below
                                continue
                            layers = bm.loops.layers.float
                            warnUneditableVertexData(vertexMap)
                        elif isinstance(values[0], int):
                            layers = bm.loops.layers.int
                            warnUneditableVertexData(vertexMap)
                        elif isinstance(values[0], str):
                            layers = bm.loops.layers.string
                            warnUneditableVertexData(vertexMap)
                        elif isinstance(values[0], datamodel.Vector2):
                            layers = bm.loops.layers.uv
                        elif isinstance(values[0], datamodel.Vector4) or isinstance(values[0], datamodel.Color):
                            layers = bm.loops.layers.color
                            is_color = True
                        else:
                            self.warning(f"Could not import vertex data '{vertexMap}'; unsupported type {type(values[0]).__name__}")
                            continue

                        # The primary Source 2 colour stream (color$0) maps back to Blender's
                        # default "Color" attribute, mirroring the export naming so the layer
                        # round-trips. All other streams keep their original name.
                        layer_name = vertexMap
                        if is_color and vertexMap.lower() == "color$0":
                            layer_name = "Color"

                        vertex_layer_infos.append(VertexLayerInfo(
                            layers.new(layer_name),
                            DmeVertexData[vertexMap + "Indices"],
                            values,
                        ))

                        if vertexMap != "textureCoordinates":
                            self._ensureSceneDmxVersion(dmx_version(9, 22))

                    deform_group_names = ordered_set.OrderedSet()

                    # ---------------------------------------------------------
                    # Weightmap
                    # When smd.a is None (no armature), we still create vertex
                    # groups using the joint name from the DMX jointList, or fall
                    # back to "joint_N" generic naming.
                    # ---------------------------------------------------------
                    if have_weightmap:
                        weighted_bone_indices = ordered_set.OrderedSet()
                        jointWeights  = DmeVertexData[keywords["weight"]]
                        jointIndices  = DmeVertexData[keywords["weight_indices"]]
                        jointRange    = range(DmeVertexData["jointCount"])
                        deformLayer   = bm.verts.layers.deform.new()

                        joint_index = 0
                        for vert in bm.verts:
                            for i in jointRange:
                                weight = jointWeights[joint_index]
                                if weight > 0:
                                    vg_index = weighted_bone_indices.add(jointIndices[joint_index])
                                    vert[deformLayer][vg_index] = weight
                                joint_index += 1

                        # Resolve joint index -> name.
                        # jointList may be absent for armature-less Source 2 DMXs.
                        joints_list = None
                        try:
                            key = "jointList" if dm.format_ver >= 11 else "jointTransforms"
                            joints_list = DmeModel.get(key)
                        except Exception:
                            pass

                        for jidx in weighted_bone_indices:
                            jname: str | None = None
                            try:
                                if joints_list:
                                    jname = joints_list[jidx].name or None
                            except (IndexError, KeyError, TypeError):
                                pass
                            deform_group_names.add(jname if jname else f"joint_{jidx}")

                    for face_set in DmeMesh["faceSets"]:
                        mat_path = face_set["material"]["mtlName"]
                        bpy.context.scene.vs.material_path = os.path.dirname(mat_path).replace("\\", "/")
                        mat, mat_ind = self.getMeshMaterial(os.path.basename(mat_path))
                        face_loops: list[int] = []
                        dmx_face = 0
                        for vert in face_set["faces"]:
                            if vert != -1:
                                face_loops.append(vert)
                                continue
                            try:
                                face = bm.faces.new([bm.verts[positionsIndices[loop]] for loop in face_loops])
                                face.smooth = True
                                face.material_index = mat_ind
                                for layer_info in vertex_layer_infos:
                                    is_uv = layer_info.layer.name in bm.loops.layers.uv
                                    for i, loop in enumerate(face.loops):
                                        value = layer_info.get_loop_value(face_loops[i])
                                        if is_uv:
                                            loop[layer_info.layer].uv = value
                                        else:
                                            loop[layer_info.layer] = value
                            except ValueError:
                                skipfaces.add(dmx_face)
                            dmx_face += 1
                            face_loops.clear()

                    # ---------------------------------------------------------
                    # Cloth-enable vertex groups
                    # Each cloth_enable$N entry in the vertex format becomes a
                    # vertex group so the exporter can round-trip it faithfully.
                    # ---------------------------------------------------------
                    cloth_maps = [n for n in DmeVertexData["vertexFormat"] if isClothEnableMap(n)]
                    if cloth_maps:
                        deformLayer = bm.verts.layers.deform.verify()
                        for cloth_name in cloth_maps:
                            vg_index = deform_group_names.add(cloth_name)
                            cloth_data    = DmeVertexData.get(cloth_name)
                            cloth_indices = DmeVertexData.get(cloth_name + "Indices")
                            if cloth_data is None or cloth_indices is None:
                                self.warning(f"Cloth group '{cloth_name}' has no data - skipped")
                                continue
                            loop_i = 0
                            for face in bm.faces:
                                for loop in face.loops:
                                    w = cloth_data[cloth_indices[loop_i]]
                                    loop.vert[deformLayer][vg_index] = w
                                    loop_i += 1
                        print(f"- Imported {len(cloth_maps)} cloth-enable vertex group(s)")

                    # Create vertex groups in order (bones first, then cloth)
                    for groupName in deform_group_names:
                        ob.vertex_groups.new(name=groupName)

                    if last_bone and not have_weightmap:
                        ob.parent_type = 'BONE'
                        ob.parent_bone = last_bone.name

                    # Move from BMesh -> Mesh
                    bm.to_mesh(ob.data)
                    del bm
                    ob.data.update()
                    ob.matrix_world @= matrix
                    if ob.parent_bone:
                        ob.matrix_world = (
                            ob.parent.matrix_world
                            @ ob.parent.data.bones[ob.parent_bone].matrix_local
                            @ ob.matrix_world
                        )
                    elif ob.parent:
                        ob.matrix_world = ob.parent.matrix_world @ ob.matrix_world
                    if smd.jobType == PHYS:
                        ob.display_type = 'SOLID'

                    # Apply custom split normals
                    # Blender 4.2+: normals_split_custom_set works without use_auto_smooth
                    normalsAttr = ob.data.attributes[normalsLayerName]
                    ob.data.normals_split_custom_set([v.vector for v in normalsAttr.data])
                    ob.data.attributes.remove(ob.data.attributes[normalsLayerName])

                    # Stereo balance
                    if keywords['balance'] in DmeVertexData["vertexFormat"]:
                        vg = ob.vertex_groups.new(name=get_id("importer_balance_group", data=True))
                        balanceIndices = DmeVertexData[keywords['balance'] + "Indices"]
                        balance        = DmeVertexData[keywords['balance']]
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

                    # Shape keys / flex
                    if DmeMesh.get("deltaStates"):
                        for DmeVertexDeltaData in DmeMesh["deltaStates"]:
                            if not ob.data.shape_keys:
                                ob.shape_key_add(name="Basis")
                                ob.show_only_shape_key = True
                                ob.data.shape_keys.name = DmeMesh.name
                            shape_key = ob.shape_key_add(name=DmeVertexDeltaData.name)
                            shape_key.value = 0.0
                            if keywords['pos'] in DmeVertexDeltaData["vertexFormat"]:
                                deltaPositions = DmeVertexDeltaData[keywords['pos']]
                                for i, posIndex in enumerate(DmeVertexDeltaData[keywords['pos'] + "Indices"]):
                                    shape_key.data[posIndex].co += Vector(deltaPositions[i])
                            if correctiveSeparator in DmeVertexDeltaData.name:
                                flex.AddCorrectiveShapeDrivers.addDrivers(
                                    shape_key,
                                    DmeVertexDeltaData.name.split(correctiveSeparator),
                                )

                    imported_meshes.append(ob)

            # Run mesh parser for reference / physics meshes
            if smd.jobType in [REF, PHYS]:
                parseModel(DmeModel)

            # Import flex controllers from combinationOperator (REF meshes only).
            # When called from readQC, defer so readQC can merge QC data on top.
            # Flex controllers/rules are global model data: apply them to every
            # imported mesh that has shape keys, not just the last one parsed.
            if smd.jobType == REF and smd.m:
                if self.qc:
                    self.qc.ref_mesh = smd.m
                flex_meshes = [m for m in imported_meshes if hasShapes(m)]
                _combo_op = dm.root.get("combinationOperator")
                if self.qc:
                    for m in flex_meshes:
                        if m not in self.qc.flex_meshes:
                            self.qc.flex_meshes.append(m)
                    if _combo_op:
                        self.qc.pending_combo_op = _combo_op
                elif _combo_op:
                    for m in (flex_meshes or [smd.m]):
                        self._populate_dme_flex_from_dmx(m, _combo_op)

            # -----------------------------------------------------------------
            # Animation
            # -----------------------------------------------------------------
            if smd.jobType == ANIM:
                print(f"Importing DMX animation \"{smd.jobName}\"")

                _anim_list = dm.root.get("animationList")
                if _anim_list is not None:
                    animation = _anim_list["animations"][0]
                elif dm.root.get("channels") is not None:
                    animation = dm.root
                else:
                    self.warning(f"DMX file \"{smd.jobName}\" has no animation data - skipping")
                    animation = None

                if animation is not None:
                    frameRate  = animation.get("frameRate", 30)
                    timeFrame  = animation["timeFrame"]
                    scale      = timeFrame.get("scale", 1.0)
                    duration   = timeFrame.get("duration") or timeFrame.get("durationTime")
                    offset     = timeFrame.get("offset") or timeFrame.get("offsetTime", 0.0)
                    start      = timeFrame.get("start", 0)

                    if type(duration) == int:
                        duration = datamodel.Time.from_int(duration)
                    if type(offset) == int:
                        offset = datamodel.Time.from_int(offset)

                    lastFrameIndex = 0
                    keyframes: dict[bpy.types.PoseBone, list[KeyFrame]] = collections.defaultdict(list)
                    unknown_bones: list[str] = []

                    for channel in animation["channels"]:
                        toElement = channel["toElement"]
                        if not toElement:
                            continue

                        bone_name = smd.boneTransformIDs.get(toElement.id)
                        bone = smd.a.pose.bones.get(bone_name) if bone_name else None
                        if not bone:
                            if self.append != 'NEW_ARMATURE' and toElement.name not in unknown_bones:
                                unknown_bones.append(toElement.name)
                                print(f"- Animation refers to unrecognised bone \"{toElement.name}\"")
                            continue

                        is_position_channel = channel["toAttribute"] == "position"
                        is_rotation_channel = channel["toAttribute"] == "orientation"
                        is_scale_channel    = channel["toAttribute"] == "scale"
                        if not (is_position_channel or is_rotation_channel or is_scale_channel):
                            continue

                        frame_log = channel["log"]["layers"][0]
                        times  = frame_log["times"]
                        values = frame_log["values"]

                        for i in range(len(times)):
                            frame_time = times[i] + start
                            if type(frame_time) == int:
                                frame_time = datamodel.Time.from_int(frame_time)
                            frame_value = values[i]

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
                                keyframe.matrix @= getBlenderQuat(frame_value).to_matrix().to_4x4()
                                keyframe.rot = True
                            elif is_scale_channel and not keyframe.scale:
                                # Source 2 stores a single uniform scale float per bone transform
                                keyframe.matrix @= Matrix.Scale(float(frame_value), 4)
                                keyframe.scale = True

                    if smd.a is None:
                        self.warning(get_id("importer_err_noanimationbones", True).format(smd.jobName))
                    else:
                        smd.a.hide_set(False)
                        bpy.context.view_layer.objects.active = smd.a
                        if unknown_bones:
                            self.warning(get_id("importer_err_missingbones", True).format(
                                smd.jobName, len(unknown_bones), smd.a.name))

                        total_frames = ceil((duration * frameRate) if duration else lastFrameIndex) + 1
                        self.applyFrames(keyframes, total_frames)
                        bpy.context.scene.frame_end += int(round(start * 2 * frameRate, 0))

        except datamodel.AttributeError as e:
            e.args = [f"Invalid DMX file: {e.args[0] if e.args else 'Unknown error'}"]
            raise

        bench.report("DMX imported in")
        return 1

    @classmethod
    def _ensureSceneDmxVersion(cls, version: dmx_version):
        if State.datamodelFormat < version.format:
            bpy.context.scene.vs.dmx_format = version.format_enum
        if State.datamodelEncoding < version.encoding:
            bpy.context.scene.vs.dmx_encoding = str(version.encoding)

    def _populate_dme_flex_from_dmx(self, ob: bpy.types.Object, combo_op) -> None:
        populate_dme_flex_from_dmx(ob, combo_op)

    # -------------------------------------------------------------------------
    # VMDL helpers
    # -------------------------------------------------------------------------

class SmdImporter(ImporterBase):
    """All formats in one operator. Superseded per-format as each one migrates to
    importsrc; still the entry point for SMD/VTA/QC/VMDL."""
    bl_idname = "import_scene.smd"
    bl_label = get_id("importer_title")
    bl_description = get_id("importer_tip")

    filter_glob: StringProperty(default="*.smd;*.vta;*.dmx;*.qc;*.qci;*.vmdl;*.vmdl_prefab", options={'HIDDEN'})

    doAnim: BoolProperty(name=get_id("importer_doanims"), default=True)
    makeCamera: BoolProperty(name=get_id("importer_makecamera"), description=get_id("importer_makecamera_tip"), default=False)

    def draw_options(self, layout) -> None:
        layout.prop(self.properties, "doAnim")
        layout.prop(self.properties, "makeCamera")

    def read_file(self, filepath: str) -> int | None:
        filepath_lc = filepath.lower()
        if filepath_lc.endswith(('.qc', '.qci', '.vmdl', '.vmdl_prefab')):
            count = self.readQC(filepath, False, self.properties.doAnim, self.properties.makeCamera, self.properties.rotMode, outer_qc=True)
            bpy.context.view_layer.objects.active = self.qc.a
            return count
        if filepath_lc.endswith('.smd'):
            return self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode)
        if filepath_lc.endswith('.vta'):
            return self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode, smd_type=FLEX)
        if filepath_lc.endswith('.dmx'):
            return self.readDMX(filepath, self.properties.upAxis, self.properties.rotMode)
        self.report_unreadable(filepath_lc)
        return None


class ImportSMD(ImporterBase):
    bl_idname = "import_scene.kst_smd"
    bl_label = get_id("importer_smd_title")
    bl_description = get_id("importer_smd_tip")

    filter_glob: StringProperty(default="*.smd;*.vta", options={'HIDDEN'})

    def read_file(self, filepath: str) -> int | None:
        filepath_lc = filepath.lower()
        if filepath_lc.endswith('.smd'):
            return self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode)
        if filepath_lc.endswith('.vta'):
            return self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode, smd_type=FLEX)
        self.report_unreadable(filepath)
        return None


class ImportQC(ImporterBase):
    bl_idname = "import_scene.kst_qc"
    bl_label = get_id("importer_qc_title")
    bl_description = get_id("importer_qc_tip")

    filter_glob: StringProperty(default="*.qc;*.qci", options={'HIDDEN'})

    doAnim: BoolProperty(name=get_id("importer_doanims"), default=True)
    makeCamera: BoolProperty(name=get_id("importer_makecamera"), description=get_id("importer_makecamera_tip"), default=False)
    prefabData: EnumProperty(
        name=get_id("importer_prefabdata"),
        description=get_id("importer_prefabdata_tip"),
        items=(
            ('JIGGLEBONES', get_id("importer_prefabdata_jiggle"), get_id("importer_prefabdata_jiggle_tip")),
            ('HITBOXES',    get_id("importer_prefabdata_hitbox"), get_id("importer_prefabdata_hitbox_tip")),
            ('PROCEDURAL',  get_id("importer_prefabdata_proc"),   get_id("importer_prefabdata_proc_tip")),
        ),
        options={'ENUM_FLAG'},
        default={'JIGGLEBONES', 'HITBOXES', 'PROCEDURAL'},
    )

    def draw_options(self, layout) -> None:
        layout.prop(self.properties, "doAnim")
        layout.prop(self.properties, "makeCamera")
        self.draw_prefab_data(layout)

    def read_file(self, filepath: str) -> int | None:
        if not filepath.lower().endswith(('.qc', '.qci')):
            self.report_unreadable(filepath)
            return None
        count = self.readQC(filepath, False, self.properties.doAnim,
                            self.properties.makeCamera, self.properties.rotMode, outer_qc=True)
        bpy.context.view_layer.objects.active = self.qc.a
        return count


class ImportPrefab(ImporterBase):
    """Attaches jigglebones / hitboxes / procedural bones to the active armature.

    Defined by what it does not do: never creates an armature and never creates a mesh.
    That is the whole distinction from ImportQC / ImportDMX / ImportVMDL, which read the
    same bytes but build geometry from them.
    """
    bl_idname = "import_scene.kst_prefab"
    bl_label = get_id("importer_prefab_title")
    bl_description = get_id("importer_prefab_tip")

    filter_glob: StringProperty(default="*.qc;*.qci;*.vrd;*.dmx;*.vmdl_prefab", options={'HIDDEN'})

    prefabData: EnumProperty(
        name=get_id("importer_prefabdata"),
        description=get_id("importer_prefabdata_tip"),
        items=(
            ('JIGGLEBONES', get_id("importer_prefabdata_jiggle"), get_id("importer_prefabdata_jiggle_tip")),
            ('HITBOXES',    get_id("importer_prefabdata_hitbox"), get_id("importer_prefabdata_hitbox_tip")),
            ('PROCEDURAL',  get_id("importer_prefabdata_proc"),   get_id("importer_prefabdata_proc_tip")),
        ),
        options={'ENUM_FLAG'},
        default={'JIGGLEBONES', 'HITBOXES', 'PROCEDURAL'},
    )

    @classmethod
    def poll(cls, context):
        return findArmatureForPrefab(context) is not None

    def draw(self, context):
        # Nothing is being built, so the build options (upAxis, append, rotMode,
        # boneMode) do not apply. Hitboxes are the only consumer of createCollections.
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        self.layout.prop(self.properties, "createCollections")
        self.draw_prefab_data(self.layout)

    def read_file(self, filepath: str) -> int | None:
        arm = findArmatureForPrefab(bpy.context)
        if not arm:
            self.error(get_id("importer_err_prefab_noarm"))
            return None

        ext = os.path.splitext(filepath)[1].lower()
        try:
            reader = _PREFAB_READERS[ext]
        except KeyError:
            self.report_unreadable(filepath)
            return None

        prev = (self.imported_jigglebones, self.imported_hitboxes, self.imported_procbones)
        # Hitbox and proc-bone geometry is authored against the rest pose.
        prev_pose_position = arm.data.pose_position
        arm.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        try:
            reader(self, filepath, arm)
        finally:
            arm.data.pose_position = prev_pose_position
            bpy.context.view_layer.update()

        added = (self.imported_jigglebones - prev[0],
                 self.imported_hitboxes - prev[1],
                 self.imported_procbones - prev[2])
        if not any(added):
            self.warning(get_id("importer_err_prefab_empty", True).format(os.path.basename(filepath)))
        return 1


def findArmatureForPrefab(context) -> bpy.types.Object | None:
    """Active armature, or the armature of the active mesh. Prefab import has no
    fallback scan - it must be unambiguous which rig is being modified."""
    ob = context.active_object
    if not ob:
        return None
    if ob.type == 'ARMATURE':
        return ob
    if ob.type == 'MESH':
        return ob.find_armature()
    return None


def _prefab_read_qc(op, filepath: str, arm) -> None:
    with open(filepath, 'r') as f:
        content = f.read()
    count, missing = import_jigglebones_from_content(content, arm)
    op.imported_jigglebones += count
    if missing:
        op.warning(f"Could not find bones for {len(missing)} jigglebone(s): {', '.join(missing)}")

    hboxset = ""
    for line in content.splitlines():
        words = line.split()
        if len(words) >= 2 and words[0].lower() == "$hboxset":
            hboxset = words[1].strip('"')
            break
    created, skipped, bones = import_hitboxes_from_content(
        content, arm, bpy.context, op.createCollections, hboxset_name=hboxset)
    op.imported_hitboxes += created
    if skipped:
        op.warning(f"Skipped {skipped} hitbox(es) with missing bones: {', '.join(bones)}")


def _prefab_read_vrd(op, filepath: str, arm) -> None:
    with open(filepath, 'r') as f:
        content = f.read()
    count, missing = import_proc_bones_from_vrd_content(content, arm, bpy.context.scene)
    op.imported_procbones += count
    if missing:
        op.warning(f"Could not find bones for {len(missing)} procedural entr(y/ies): "
                   f"{', '.join(missing)}")


def _prefab_read_dmx(op, filepath: str, arm) -> None:
    jb, hb, pb = importsrc.read_dmx_prefab(op, filepath, arm)
    op.imported_jigglebones += jb
    op.imported_hitboxes += hb
    op.imported_procbones += pb


def _prefab_read_kv3(op, filepath: str, arm) -> None:
    with open(filepath, 'r', encoding='utf-8') as f:
        kv_doc = keyvalues3.KVParser(f.read()).parse()
    count, missing = import_jigglebones_from_kv3(kv_doc, arm)
    op.imported_jigglebones += count
    if missing:
        op.warning(f"Could not find bones for {len(missing)} jigglebone(s): {', '.join(missing)}")
    created, skipped, bones = import_hitboxes_from_kv3(kv_doc, arm)
    op.imported_hitboxes += created
    if skipped:
        op.warning(f"Skipped {skipped} hitbox(es) with missing bones: "
                   f"{', '.join(sorted({b for b in bones if b}))}")


_PREFAB_READERS = {
    '.qc': _prefab_read_qc,
    '.qci': _prefab_read_qc,
    '.vrd': _prefab_read_vrd,
    '.dmx': _prefab_read_dmx,
    '.vmdl_prefab': _prefab_read_kv3,
}


class ImportVMDL(ImporterBase):
    bl_idname = "import_scene.kst_vmdl"
    bl_label = get_id("importer_vmdl_title")
    bl_description = get_id("importer_vmdl_tip")

    filter_glob: StringProperty(default="*.vmdl;*.vmdl_prefab", options={'HIDDEN'})

    doAnim: BoolProperty(name=get_id("importer_doanims"), default=True)
    contentPath: StringProperty(name=get_id("content_path"), description=get_id("content_path_tip"), subtype='DIR_PATH')
    prefabData: EnumProperty(
        name=get_id("importer_prefabdata"),
        description=get_id("importer_prefabdata_tip"),
        items=(
            ('JIGGLEBONES', get_id("importer_prefabdata_jiggle"), get_id("importer_prefabdata_jiggle_tip")),
            ('HITBOXES',    get_id("importer_prefabdata_hitbox"), get_id("importer_prefabdata_hitbox_tip")),
            ('PROCEDURAL',  get_id("importer_prefabdata_proc"),   get_id("importer_prefabdata_proc_tip")),
        ),
        options={'ENUM_FLAG'},
        default={'JIGGLEBONES', 'HITBOXES', 'PROCEDURAL'},
    )

    def draw_options(self, layout) -> None:
        layout.prop(self.properties, "doAnim")
        layout.prop(self.properties, "contentPath")
        self.draw_prefab_data(layout)

    def read_file(self, filepath: str) -> int | None:
        if not filepath.lower().endswith(('.vmdl', '.vmdl_prefab')):
            self.report_unreadable(filepath)
            return None
        # readQC builds the QcInfo that read_vmdl needs, then dispatches by extension.
        count = self.readQC(filepath, False, self.properties.doAnim, False,
                            self.properties.rotMode, outer_qc=True)
        if self.qc and self.qc.a:
            bpy.context.view_layer.objects.active = self.qc.a
        return count


class ImportDMX(ImporterBase):
    bl_idname = "import_scene.kst_dmx"
    bl_label = get_id("importer_dmx_title")
    bl_description = get_id("importer_dmx_tip")

    filter_glob: StringProperty(default="*.dmx", options={'HIDDEN'})

    prefabData: EnumProperty(
        name=get_id("importer_prefabdata"),
        description=get_id("importer_prefabdata_tip"),
        items=(
            ('JIGGLEBONES', get_id("importer_prefabdata_jiggle"), get_id("importer_prefabdata_jiggle_tip")),
            ('HITBOXES',    get_id("importer_prefabdata_hitbox"), get_id("importer_prefabdata_hitbox_tip")),
            ('PROCEDURAL',  get_id("importer_prefabdata_proc"),   get_id("importer_prefabdata_proc_tip")),
        ),
        options={'ENUM_FLAG'},
        default={'JIGGLEBONES', 'HITBOXES', 'PROCEDURAL'},
    )

    def draw_options(self, layout) -> None:
        self.draw_prefab_data(layout)

    def read_file(self, filepath: str) -> int | None:
        if not filepath.lower().endswith('.dmx'):
            self.report_unreadable(filepath)
            return None
        return self.readDMX(filepath, self.properties.upAxis, self.properties.rotMode)
