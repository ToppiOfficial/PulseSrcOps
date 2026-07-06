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
from math import ceil, radians
from typing import cast
from .utils import *
from . import datamodel, ordered_set, flex, keyvalues3

from .utils import KST_ATTACHMENT_COLL as _KST_ATTACHMENT_COLL, ensure_kst_collection_at_top as _ensure_kst_collection_at_top


# QC flex type keywords that map directly onto a real flexgroup enum option. Anything not
# in this set (e.g. 'phoneme', 'nose') becomes the CUSTOM flexgroup, preserving the raw QC
# type keyword in flexgroup_custom rather than being lumped into MISC.
# DEFAULT and CUSTOM are deliberately excluded - they are never produced by a direct match.
_VALID_FLEXGROUP_ENUMS = {'EYES', 'EYELID', 'BROW', 'MOUTH', 'MISC', 'CHEEK'}


def _set_flexgroup_from_qc(item, fc_type: str) -> None:
    """Map a QC flexcontroller type keyword onto a FlexControllerItem's flexgroup.

    A type maps to its enum equivalent only when it directly matches a real flexgroup
    option; otherwise the controller is set to CUSTOM with the raw keyword preserved in
    flexgroup_custom. An empty/missing type stays at the DEFAULT flexgroup."""
    if not fc_type:
        item.flexgroup = 'DEFAULT'
        item.flexgroup_custom = ""
        return
    upper = fc_type.upper()
    if upper in _VALID_FLEXGROUP_ENUMS:
        item.flexgroup = upper
        item.flexgroup_custom = ""
    else:
        item.flexgroup = 'CUSTOM'
        item.flexgroup_custom = fc_type


def parse_flex_text(text: str) -> dict:
    """Parse QC-style flex text into intermediate lists.

    Recognises the same tokens as the QC importer:
      flexcontroller <type> [range <min> <max>] <name...>
      flexpair <name>        (marks <name> as stereo)
      localvar a b c
      %delta = expression

    Returns a dict: {'controllers': [(name, fc_type, fmin, fmax)],
                     'localvars': [name], 'expressions': [(delta, expr)],
                     'stereo_names': set()}.
    """
    controllers = []
    localvars = []
    expressions = []
    stereo_names = set()

    in_block_comment = False
    for raw_line in text.splitlines():
        line_str = raw_line

        # Strip block comments (best-effort; the QC flex syntax is line-comment based).
        if in_block_comment:
            end = line_str.find('*/')
            if end == -1:
                continue
            line_str = line_str[end + 2:]
            in_block_comment = False
        while '/*' in line_str:
            start = line_str.find('/*')
            end = line_str.find('*/', start + 2)
            if end == -1:
                line_str = line_str[:start]
                in_block_comment = True
                break
            line_str = line_str[:start] + line_str[end + 2:]

        # Strip line comments.
        for token in ('//', '#', ';'):
            idx = line_str.find(token)
            if idx != -1:
                line_str = line_str[:idx]

        line_str = line_str.strip()
        if not line_str:
            continue

        line = line_str.split()
        kw = line[0]

        if kw == "flexpair" and len(line) >= 2:
            stereo_names.add(line[1])
            continue

        if kw == "flexcontroller" and len(line) >= 3:
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
            for name in names:
                controllers.append((name, fc_type, flex_min, flex_max))
            continue

        if kw == "localvar" and len(line) >= 2:
            localvars.extend(line[1:])
            continue

        if kw.startswith('%'):
            m = re.match(r'^\s*%(\w+)\s*=\s*(.+?)\s*$', line_str)
            if m:
                expressions.append((m.group(1), m.group(2).strip()))
            continue

    return {
        'controllers': controllers,
        'localvars': localvars,
        'expressions': expressions,
        'stereo_names': stereo_names,
    }


def apply_flex_text_to_object(ob, parsed: dict) -> tuple[int, int]:
    """Merge parsed flex data (from parse_flex_text) into ob's DME flex collections.

    Controllers/expressions with a matching name are updated in place; everything else is
    appended. Returns (controllers_touched, rules_touched)."""
    controllers = parsed.get('controllers', [])
    localvars = parsed.get('localvars', [])
    expressions = parsed.get('expressions', [])
    stereo_names = parsed.get('stereo_names', set())

    n_controllers = 0
    for name, fc_type, flex_min, flex_max in controllers:
        stereo = name in stereo_names
        existing = next((i for i in ob.vs.dme_flexcontrollers if i.controller_name == name), None)
        item = existing if existing else ob.vs.dme_flexcontrollers.add()
        if not existing:
            item.controller_name = name
        item.flex_min = flex_min
        item.flex_max = flex_max
        _set_flexgroup_from_qc(item, fc_type)
        item.eyelid = False
        item.stereo = stereo
        n_controllers += 1

    n_rules = 0
    existing_lv = {r.name for r in ob.vs.dme_flex_rules if r.rule_type == 'LOCALVAR'}
    for varname in localvars:
        if varname not in existing_lv:
            item = ob.vs.dme_flex_rules.add()
            item.rule_type = 'LOCALVAR'
            item.name = varname
            existing_lv.add(varname)
            n_rules += 1

    for delta_name, expr in expressions:
        existing = next(
            (r for r in ob.vs.dme_flex_rules if r.rule_type == 'EXPRESSION' and r.name == delta_name),
            None,
        )
        if existing:
            existing.expression = expr
        else:
            item = ob.vs.dme_flex_rules.add()
            item.rule_type = 'EXPRESSION'
            item.name = delta_name
            item.expression = expr
        n_rules += 1

    if ob.vs.dme_flexcontrollers:
        ob.vs.flex_controller_mode = 'DME'

    return n_controllers, n_rules


class SmdImporter(bpy.types.Operator, Logger):
    bl_idname = "import_scene.smd"
    bl_label = get_id("importer_title")
    bl_description = get_id("importer_tip")
    bl_options = {'UNDO', 'PRESET'}

    qc: QcInfo | None = None
    smd: SmdInfo

    # Properties used by the file browser
    filepath: StringProperty(name="File Path", description="File filepath used for importing the SMD/VTA/DMX/QC file", maxlen=1024, default="", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN'})
    directory: StringProperty(maxlen=1024, default="", subtype='FILE_PATH', options={'HIDDEN'})
    filter_folder: BoolProperty(name="Filter Folders", description="", default=True, options={'HIDDEN'})
    filter_glob: StringProperty(default="*.smd;*.vta;*.dmx;*.qc;*.qci;*.vmdl;*.vmdl_prefab", options={'HIDDEN'})

    # Custom properties
    doAnim: BoolProperty(name=get_id("importer_doanims"), default=True)
    createCollections: BoolProperty(name=get_id("importer_use_collections"), description=get_id("importer_use_collections_tip"), default=True)
    makeCamera: BoolProperty(name=get_id("importer_makecamera"), description=get_id("importer_makecamera_tip"), default=False)
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

        for filepath in [os.path.join(self.directory, file.name) for file in self.files] if self.files else [self.filepath]:
            filepath_lc = filepath.lower()
            if filepath_lc.endswith(('.qc', '.qci', '.vmdl', '.vmdl_prefab')):
                self.num_files_imported = self.readQC(filepath, False, self.properties.doAnim, self.properties.makeCamera, self.properties.rotMode, outer_qc=True)
                bpy.context.view_layer.objects.active = self.qc.a
            elif filepath_lc.endswith('.smd'):
                self.num_files_imported = self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode)
            elif filepath_lc.endswith('.vta'):
                self.num_files_imported = self.readSMD(filepath, self.properties.upAxis, self.properties.rotMode, smd_type=FLEX)
            elif filepath_lc.endswith('.dmx'):
                self.num_files_imported = self.readDMX(filepath, self.properties.upAxis, self.properties.rotMode)
            else:
                if len(filepath_lc) == 0:
                    self.report({'ERROR'}, get_id("importer_err_nofile"))
                else:
                    self.report({'ERROR'}, get_id("importer_err_badfile", True).format(os.path.basename(filepath)))

            self.append = pre_append

        report_message = get_id("importer_complete", True).format(self.num_files_imported, self.elapsed_time())
        details = []
        if self.imported_hitboxes > 0:
            details.append(f"{self.imported_hitboxes} hitboxes")
        if self.imported_jigglebones > 0:
            details.append(f"{self.imported_jigglebones} jigglebones")
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
        if bpy.data.collections.get(_KST_ATTACHMENT_COLL):
            _ensure_kst_collection_at_top(context.scene, context.view_layer)
        return {'FINISHED'}

    def invoke(self, context, event):
        self.properties.upAxis = context.scene.vs.up_axis
        bpy.context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

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

    def scanSMD(self):
        smd = self.smd
        for line in smd.file:
            if line == "triangles\n":
                smd.jobType = REF
                print("- This is a mesh")
                break
            if line == "vertexanimation\n":
                print("- This is a flex animation library")
                smd.jobType = FLEX
                break
        if smd.jobType is None:
            print("- This is a skeletal animation or pose")
            smd.jobType = ANIM
            self.ensureAnimationBonesValidated()
        smd.file.seek(0, 0)

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

    def readNodes(self):
        smd = self.smd
        boneParents: dict[str, int] = {}

        def addBone(id, name, parent):
            bone = smd.a.data.edit_bones.new(self.truncate_id_name(name, bpy.types.Bone))
            bone.tail = 0, 5, 0
            smd.boneIDs[int(id)] = bone.name
            boneParents[bone.name] = int(parent)
            return bone

        if self.append != 'NEW_ARMATURE':
            smd.a = smd.a or self.findArmature()
            if smd.a:
                append = self.append == 'APPEND' and smd.jobType in [REF, ANIM]
                if append:
                    bpy.context.view_layer.objects.active = smd.a
                    smd.a.hide_set(False)
                    ops.object.mode_set(mode='EDIT', toggle=False)
                    self.existingBones.extend([b.name for b in smd.a.data.bones])

                missing = validated = 0
                for line in smd.file:
                    if smdBreak(line): break
                    if smdContinue(line): continue
                    id, name, parent = self.parseQuoteBlockedLine(line, lower=False)[:3]
                    id, parent = int(id), int(parent)
                    targetBone = smd.a.data.bones.get(name)
                    if targetBone:
                        validated += 1
                    elif append:
                        targetBone = addBone(id, name, parent)
                    else:
                        missing += 1
                    if not smd.boneIDs.get(parent):
                        smd.phantomParentIDs[id] = parent
                    smd.boneIDs[id] = targetBone.name if targetBone else name

                print("- Validated {} bones against armature \"{}\"{}".format(
                    validated, smd.a.name,
                    " (could not find {})".format(missing) if missing > 0 else ""))

        if not smd.a:
            smd.a = self.createArmature(self.truncate_id_name(
                (self.qc.jobName if self.qc else smd.jobName) + "_skeleton", bpy.types.Armature))
            if self.qc:
                self.qc.a = smd.a
            smd.a.data.vs.implicit_zero_bone = False

            ops.object.mode_set(mode='EDIT', toggle=False)
            for line in smd.file:
                if smdBreak(line): break
                if smdContinue(line): continue
                id, name, parent = self.parseQuoteBlockedLine(line, lower=False)[:3]
                addBone(id, name, parent)

        for bone_name, parent_id in boneParents.items():
            if parent_id != -1:
                smd.a.data.edit_bones[bone_name].parent = smd.a.data.edit_bones[smd.boneIDs[parent_id]]

        ops.object.mode_set(mode='OBJECT')
        if boneParents:
            print(f"- Imported {len(boneParents)} new bones")

        if len(smd.a.data.bones) > 128:
            self.warning(get_id("importer_err_bonelimit_smd"))

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

    def readFrames(self):
        smd = self.smd
        if smd.jobType not in [REF, ANIM]:
            for line in smd.file:
                line = line.strip()
                if smdBreak(line):
                    return
                if smd.jobType == FLEX and line.startswith("time"):
                    smd.shapeNames = smd.shapeNames or {}
                    for c in line:
                        if c in ['#', ';', '/']:
                            pos = line.index(c)
                            frame = line[:pos].split()[1]
                            if c == '/':
                                pos += 1
                            smd.shapeNames[frame] = line[pos + 1:].strip()

        bpy.context.view_layer.objects.active = smd.a
        ops.object.mode_set(mode='POSE')

        num_frames = 0
        keyframes: dict[bpy.types.PoseBone, list[KeyFrame]] = collections.defaultdict(list)
        phantom_keyframes: dict[int, list[KeyFrame]] = collections.defaultdict(list)

        for line in smd.file:
            if smdBreak(line):
                break
            if smdContinue(line):
                continue

            values = line.split()
            if values[0] == "time":
                if num_frames > 0:
                    if smd.jobType == REF:
                        self.warning(get_id("importer_err_refanim", True).format(smd.jobName))
                        for line in smd.file:
                            if smdBreak(line): break
                            if smdContinue(line): continue
                num_frames += 1
                continue

            pos = Vector([float(values[1]), float(values[2]), float(values[3])])
            rot = Euler([float(values[4]), float(values[5]), float(values[6])])

            keyframe = KeyFrame()
            keyframe.frame = num_frames - 1
            keyframe.matrix = Matrix.Translation(pos) @ rot.to_matrix().to_4x4()
            keyframe.pos = keyframe.rot = True

            frameIndex = int(values[0])
            try:
                bone = smd.a.pose.bones[smd.boneIDs[frameIndex]]
                if smd.jobType == REF and not bone.parent:
                    keyframe.matrix = getUpAxisMat(smd.upAxis) @ keyframe.matrix
                keyframes[bone].append(keyframe)
            except KeyError:
                if smd.jobType == REF and not smd.phantomParentIDs.get(frameIndex):
                    keyframe.matrix = getUpAxisMat(smd.upAxis) @ keyframe.matrix
                phantom_keyframes[frameIndex].append(keyframe)

        self.applyFrames(keyframes, num_frames)

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

    def readPolys(self):
        smd = self.smd
        if smd.jobType not in [REF, PHYS]:
            return

        mesh_name = smd.jobName
        if smd.jobType == REF and "reference" not in smd.jobName.lower() and not smd.jobName.lower().endswith("ref"):
            mesh_name += " ref"
        mesh_name = self.truncate_id_name(mesh_name, bpy.types.Mesh)

        smd.m = bpy.data.objects.new(mesh_name, bpy.data.meshes.new(mesh_name))
        smd.m.parent = smd.a
        smd.g.objects.link(smd.m)
        if smd.jobType == REF:
            if self.qc:
                self.qc.ref_mesh = smd.m

        for bone in smd.a.data.bones.values():
            smd.m.vertex_groups.new(name=bone.name)

        modifier = smd.m.modifiers.new(type="ARMATURE", name=pgettext("Armature"))
        modifier.object = smd.a

        md = cast(bpy.types.Mesh, smd.m.data)
        norms = []

        bm = bmesh.new()
        bm.from_mesh(md)
        weightLayer = bm.verts.layers.deform.new()
        uvLayer = bm.loops.layers.uv.new()

        countPolys = 0
        badWeights = 0
        vertMap: dict = {}

        for line in smd.file:
            line = line.rstrip("\n")
            if line and smdBreak(line):
                break
            if smdContinue(line):
                continue

            mat, mat_ind = self.getMeshMaterial(line if line else pgettext(get_id("importer_name_nomat", data=True)))

            vertexCount = 0
            faceUVs = []
            vertKeys = []
            for line in smd.file:
                if smdBreak(line):
                    break
                if smdContinue(line):
                    continue
                values = line.split()

                vertexCount += 1
                co = tuple(float(v) for v in values[1:4])
                norms.append(tuple(float(v) for v in values[4:7]))
                faceUVs.append((float(values[7]), float(values[8])))

                vertWeights = []
                if len(values) > 10 and values[9] != "0":
                    for i in range(10, 10 + (int(values[9]) * 2), 2):
                        try:
                            bone = smd.a.data.bones[smd.boneIDs[int(values[i])]]
                            vertWeights.append((smd.m.vertex_groups.find(bone.name), float(values[i + 1])))
                        except KeyError:
                            badWeights += 1
                else:
                    try:
                        bone = smd.a.data.bones[smd.boneIDs[int(values[0])]]
                        vertWeights.append((smd.m.vertex_groups.find(bone.name), 1.0))
                    except KeyError:
                        badWeights += 1

                vertKeys.append((co, tuple(vertWeights)))

                if vertexCount == 3:
                    def createFace(use_cache=True):
                        bmVerts = []
                        for vertKey in vertKeys:
                            bmv = vertMap.get(vertKey, None) if use_cache else None
                            if bmv is None:
                                bmv = bm.verts.new(vertKey[0])
                                for (bone_idx, weight) in vertKey[1]:
                                    bmv[weightLayer][bone_idx] = weight
                                vertMap[vertKey] = bmv
                            bmVerts.append(bmv)
                        face = bm.faces.new(bmVerts)
                        face.material_index = mat_ind
                        for i in range(3):
                            face.loops[i][uvLayer].uv = faceUVs[i]

                    try:
                        createFace()
                    except ValueError:
                        createFace(use_cache=False)
                    break

            countPolys += 1

        bm.to_mesh(md)
        del vertMap
        bm.free()
        md.update()

        if countPolys:
            ops.object.select_all(action="DESELECT")
            smd.m.select_set(True)
            bpy.context.view_layer.objects.active = smd.m
            ops.object.shade_smooth()

            for poly in smd.m.data.polygons:
                poly.select = True

            smd.m.show_wire = smd.jobType == PHYS

            # Blender 4.2+: normals_split_custom_set does not require use_auto_smooth
            md.normals_split_custom_set(norms)

            if smd.upAxis == 'Y':
                md.transform(rx90)
                md.update()

            if badWeights:
                self.warning(get_id("importer_err_badweights", True).format(badWeights, smd.jobName))
            print(f"- Imported {countPolys} polys")

    # -------------------------------------------------------------------------
    # Flex / VTA shapes
    # -------------------------------------------------------------------------

    def readShapes(self):
        smd = self.smd
        if smd.jobType is not FLEX:
            return

        if not smd.m:
            if self.qc:
                smd.m = self.qc.ref_mesh
            else:
                if bpy.context.active_object.type in shape_types:
                    smd.m = bpy.context.active_object
                else:
                    for obj in bpy.context.selected_objects:
                        if obj.type in shape_types:
                            smd.m = obj

        if not smd.m:
            self.error(get_id("importer_err_shapetarget"))
            return

        if hasShapes(smd.m):
            smd.m.active_shape_key_index = 0
        smd.m.show_only_shape_key = True

        def vec_round(v):
            return Vector([round(co, 3) for co in v])

        co_map: dict[int, int] = {}
        mesh_cos = [vert.co for vert in smd.m.data.vertices]
        mesh_cos_rnd = None

        smd.vta_ref = None
        vta_cos = []
        vta_ids = []

        making_base_shape = True
        bad_vta_verts = []
        num_shapes = 0
        md = smd.m.data

        for line in smd.file:
            line = line.rstrip("\n")
            if smdBreak(line): break
            if smdContinue(line): continue

            values = line.split()

            if values[0] == "time":
                shape_name = smd.shapeNames.get(values[1])
                if smd.vta_ref is None:
                    if not hasShapes(smd.m, False):
                        smd.m.shape_key_add(name=shape_name if shape_name else "Basis")
                    vd = bpy.data.meshes.new(name="VTA vertices")
                    vta_ref = smd.vta_ref = bpy.data.objects.new(name=vd.name, object_data=vd)
                    vta_ref.matrix_world = smd.m.matrix_world
                    smd.g.objects.link(vta_ref)
                    vta_err_vg = vta_ref.vertex_groups.new(name=get_id("importer_name_unmatchedvta"))
                elif making_base_shape:
                    vd.vertices.add(int(len(vta_cos) / 3))
                    vd.vertices.foreach_set("co", vta_cos)
                    num_vta_verts = len(vd.vertices)
                    del vta_cos

                    mod = vta_ref.modifiers.new(name="VTA Shrinkwrap", type='SHRINKWRAP')
                    mod.target = smd.m
                    mod.wrap_method = 'NEAREST_VERTEX'

                    vd = bpy.data.meshes.new_from_object(vta_ref.evaluated_get(bpy.context.evaluated_depsgraph_get()))
                    vta_ref.modifiers.remove(mod)
                    del mod

                    for i in range(len(vd.vertices)):
                        id = vta_ids[i]
                        co = vd.vertices[i].co
                        map_id = None
                        try:
                            map_id = mesh_cos.index(co)
                        except ValueError:
                            if not mesh_cos_rnd:
                                mesh_cos_rnd = [vec_round(co) for co in mesh_cos]
                            try:
                                map_id = mesh_cos_rnd.index(vec_round(co))
                            except ValueError:
                                bad_vta_verts.append(i)
                                continue
                        co_map[id] = map_id

                    bpy.data.meshes.remove(vd)
                    del vd

                    if bad_vta_verts:
                        err_ratio = len(bad_vta_verts) / num_vta_verts
                        vta_err_vg.add(bad_vta_verts, 1.0, 'REPLACE')
                        message = get_id("importer_err_unmatched_mesh", True).format(len(bad_vta_verts), int(err_ratio * 100))
                        if err_ratio == 1:
                            self.error(message)
                            return
                        else:
                            self.warning(message)
                    else:
                        removeObject(vta_ref)
                    making_base_shape = False

                if not making_base_shape:
                    sk = smd.m.shape_key_add(name=shape_name if shape_name else values[1])
                    sk.value = 0.0
                    num_shapes += 1

                continue

            cur_id = int(values[0])
            vta_co = getUpAxisMat(smd.upAxis) @ Vector([float(values[1]), float(values[2]), float(values[3])])

            if making_base_shape:
                vta_ids.append(cur_id)
                vta_cos.extend(vta_co)
            else:
                try:
                    md.shape_keys.key_blocks[-1].data[co_map[cur_id]].co = vta_co
                except KeyError:
                    pass

        print(f"- Imported {num_shapes} flex shapes")

    # -------------------------------------------------------------------------
    # QC
    # -------------------------------------------------------------------------

    def readQC(self, filepath: str, newscene: bool, doAnim: bool, makeCamera: bool, rotMode: str, outer_qc: bool = False) -> int:
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
            return self._import_vmdl(filepath, qc, rotMode)

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
                    self.readQC(path, False, doAnim, makeCamera, rotMode)
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
            self.scanSMD()
        self.createCollection()

        for line in file:
            if line == "nodes\n":     self.readNodes()
            if line == "skeleton\n":  self.readFrames()
            if line == "triangles\n": self.readPolys()
            if line == "vertexanimation\n": self.readShapes()

        file.close()
        printTimeMessage(smd.startTime, smd.jobName, "import")
        return 1

    # -------------------------------------------------------------------------
    # DMX file reader
    # -------------------------------------------------------------------------

    def readDMX(self, filepath: str, upAxis: str, rotMode: str, newscene: bool = False, smd_type=None, target_layer: int = 0) -> int:
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
                return elem.type in ["DmeDag", "DmeJoint", "DmeJiggleBone"]

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

            # -----------------------------------------------------------------
            # Mesh parser (nested helper)
            # -----------------------------------------------------------------
            # Every DmeMesh created during this import, so global flex data
            # (combinationOperator / QC flex text) can be applied to all of
            # them rather than only the last mesh parsed.
            imported_meshes: list[bpy.types.Object] = []

            def parseModel(elem, matrix=Matrix(), last_bone=None):
                if elem.type in ["DmeModel", "DmeDag", "DmeJoint", "DmeJiggleBone"]:
                    if elem.type == "DmeDag":
                        matrix = matrix @ get_transform_matrix(elem)
                    if elem.get("children") and elem["children"]:
                        if elem.type in ["DmeJoint", "DmeJiggleBone"]:
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
        ob.vs.dme_flexcontrollers.clear()
        ob.vs.dme_flex_rules.clear()

        # A DMX combinationOperator is global model data, but when it was exported
        # from multiple meshes its "controls" list and its "targets" (one DmeFlexRules
        # per mesh) hold the same controllers/rules repeated once per mesh. Deduplicate
        # so each controller/rule is imported a single time.
        seen_controllers: set[str] = set()
        for ctrl in combo_op.get("controls", []):
            if ctrl.name in seen_controllers:
                continue
            seen_controllers.add(ctrl.name)
            item = ob.vs.dme_flexcontrollers.add()
            item.controller_name = ctrl.name
            raw = ctrl.get("rawControlNames", [])
            item.shapekey = raw[0] if raw else ''
            item.stereo = bool(ctrl.get("stereo", False)) or len(raw) >= 2
            item.eyelid = bool(ctrl.get("eyelid", False))
            item.flex_min = float(ctrl.get("flexMin", 0.0))
            item.flex_max = float(ctrl.get("flexMax", 1.0))

        seen_doms: set[tuple] = set()
        for dom in combo_op.get("dominators", []):
            d_names = dom.get("dominators", [])
            s_names = dom.get("supressed", [])  # note: "supressed" is Valve's typo in the DMX format
            if d_names or s_names:
                key = (tuple(d_names), tuple(s_names))
                if key in seen_doms:
                    continue
                seen_doms.add(key)
                item = ob.vs.dme_flex_rules.add()
                item.rule_type = 'DOMINATION'
                item.dominator_names = ", ".join(d_names)
                item.suppressed_names = ", ".join(s_names)

        seen_rules: set[tuple] = set()
        for target in combo_op.get("targets", []):
            if target.type != "DmeFlexRules":
                continue
            for rule in target.get("deltaStates", []):
                rt = rule.type
                if rt not in ("DmeFlexRulePassThrough", "DmeFlexRuleExpression", "DmeFlexRuleLocalVar"):
                    continue
                key = (rt, rule.name)
                if key in seen_rules:
                    continue
                seen_rules.add(key)
                item = ob.vs.dme_flex_rules.add()
                if rt == "DmeFlexRulePassThrough":
                    item.rule_type = 'PASSTHROUGH'
                    item.name = rule.name
                elif rt == "DmeFlexRuleExpression":
                    item.rule_type = 'EXPRESSION'
                    item.name = rule.name
                    item.expression = rule.get("expr", "")
                else:  # DmeFlexRuleLocalVar
                    item.rule_type = 'LOCALVAR'
                    item.name = rule.name

        if ob.vs.dme_flexcontrollers:
            ob.vs.flex_controller_mode = 'DME'
            print(f"- Imported {len(ob.vs.dme_flexcontrollers)} flex controllers and "
                  f"{len(ob.vs.dme_flex_rules)} flex rules from DMX combinationOperator")

    # -------------------------------------------------------------------------
    # VMDL helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _vmdl_local_matrix(origin, angles_deg) -> Matrix:
        # Source stores rotations as a QAngle [pitch, yaw, roll] (degrees) where
        # pitch is about Y, yaw about Z and roll about X. Source builds the matrix
        # as Rz(yaw) @ Ry(pitch) @ Rx(roll), which is a Blender 'XYZ' Euler of
        # (roll, pitch, yaw).
        pitch, yaw, roll = angles_deg[0], angles_deg[1], angles_deg[2]
        rot = Euler((radians(roll), radians(pitch), radians(yaw)), 'XYZ').to_matrix().to_4x4()
        return Matrix.Translation(Vector(origin)) @ rot

    def _extract_vmdl_bones(self, skeleton_node) -> list[tuple[str, object]]:
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

    def _resolve_dmx_ref(self, vmdl_path: str, dmx_ref: str) -> str | None:
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

    def _import_vmdl(self, filepath: str, qc: QcInfo, rotMode: str) -> int:
        filename = os.path.basename(filepath)
        print(f"\nVMDL IMPORTER: now working on {filename}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                vmdl_text = f.read()
        except IOError as e:
            self.error(f"Could not read {filepath}: {e}")
            return 0

        try:
            kv_doc = keyvalues3.KVParser(vmdl_text).parse()
        except Exception as e:
            self.error(f"Failed to parse {filename}: {e}")
            return 0

        root_node = kv_doc.roots.get("rootNode")
        if not root_node:
            self.error(f"{filename}: no rootNode")
            return 0

        # Initialize self.smd so createArmature() can read self.smd.isDMX
        smd = self.smd = SmdInfo(qc.jobName)
        smd.isDMX = 1
        smd.jobType = REF
        smd.upAxis = qc.upAxis
        smd.rotMode = rotMode
        self.createCollection()

        # -----------------------------------------------------------------
        # Skeleton
        # -----------------------------------------------------------------
        skeleton_node = root_node.get(recursive=True, _class="Skeleton")
        if not skeleton_node:
            self.warning(f"{filename}: no Skeleton - only jigglebones imported")
            arm = qc.a or self.findArmature()
            if arm:
                cnt, missing = import_jigglebones_from_kv3(kv_doc, arm)
                self.imported_jigglebones += cnt
            return 1

        bone_pairs = self._extract_vmdl_bones(skeleton_node)
        if not bone_pairs:
            self.warning(f"{filename}: Skeleton has no Bone children")
            return 0

        arm_name = self.truncate_id_name(qc.jobName + "_skeleton", bpy.types.Armature)
        arm = self.createArmature(arm_name)
        qc.a = smd.a = arm

        bpy.context.view_layer.objects.active = arm
        ops.object.mode_set(mode='EDIT')

        edit_bone_map: dict[str, bpy.types.EditBone] = {}
        bone_matrices: dict[str, Matrix] = {}

        def _create_edit_bone(name: str, node, parent_name: str | None):
            eb = arm.data.edit_bones.new(self.truncate_id_name(name, bpy.types.Bone))
            eb.tail = (0, 5, 0)
            edit_bone_map[name] = eb
            if parent_name and parent_name in edit_bone_map:
                eb.parent = edit_bone_map[parent_name]
            origin = node.properties.get("origin", [0.0, 0.0, 0.0])
            angles_deg = node.properties.get("angles", [0.0, 0.0, 0.0])
            bone_matrices[eb.name] = self._vmdl_local_matrix(origin, angles_deg)

        def _dfs_create(node, parent_name: str | None):
            name = node.properties.get("name")
            if name:
                _create_edit_bone(name, node, parent_name)
                for child in node.children:
                    if child.properties.get("_class") == "Bone":
                        _dfs_create(child, name)

        for child in skeleton_node.children:
            if child.properties.get("_class") == "Bone":
                _dfs_create(child, None)

        ops.object.mode_set(mode='OBJECT')
        print(f"- Created {len(edit_bone_map)} bones from VMDL Skeleton")

        self.appliedReferencePose = False
        rest_data: dict[bpy.types.PoseBone, list[KeyFrame]] = {}
        for pbone in arm.pose.bones:
            mat = bone_matrices.get(pbone.name)
            if mat:
                kf = KeyFrame()
                kf.matrix = mat
                rest_data[pbone] = [kf]
        if rest_data:
            self.applyFrames(rest_data, 1)

        # -----------------------------------------------------------------
        # Meshes (RenderMeshList)
        # -----------------------------------------------------------------
        render_mesh_list = root_node.get(recursive=False, _class="RenderMeshList")
        if render_mesh_list:
            for rmf in render_mesh_list.children:
                if rmf.properties.get("_class") != "RenderMeshFile":
                    continue
                dmx_ref = rmf.properties.get("filename", "")
                if not dmx_ref:
                    continue
                dmx_path = self._resolve_dmx_ref(filepath, dmx_ref)
                if not dmx_path:
                    self.warning(f"{filename}: could not find DMX '{dmx_ref}'")
                    continue
                if dmx_path not in qc.imported_smds:
                    qc.imported_smds.append(dmx_path)
                    prev_append = self.append
                    self.append = 'VALIDATE'
                    self.num_files_imported += self.readDMX(dmx_path, qc.upAxis, rotMode, False, REF)
                    self.append = prev_append

        # -----------------------------------------------------------------
        # Attachments (AttachmentList)
        # -----------------------------------------------------------------
        att_list = root_node.get(recursive=False, _class="AttachmentList")
        if att_list:
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
                        self.warning(f"Attachment '{att_name}': bone '{parent_bone}' not found - skipped")
                        continue
                origin = att.properties.get("relative_origin", [0.0, 0.0, 0.0])
                angles_deg = att.properties.get("relative_angles", [0.0, 0.0, 0.0])
                mat = self._vmdl_local_matrix(origin, angles_deg)
                atch = bpy.data.objects.new(
                    name=self.truncate_id_name(att_name, "Attachment"), object_data=None)
                coll.objects.link(atch)
                atch.show_in_front = True
                atch.empty_display_type = 'ARROWS'
                atch.parent = arm
                if resolved_bone:
                    atch.parent_type = 'BONE'
                    atch.parent_bone = resolved_bone
                atch.vs.dmx_attachment = True
                atch.matrix_local = mat
                imported_att += 1
            if imported_att:
                print(f"- Imported {imported_att} attachment(s)")

        # -----------------------------------------------------------------
        # Jigglebones
        # -----------------------------------------------------------------
        cnt, missing = import_jigglebones_from_kv3(kv_doc, arm)
        if cnt:
            self.imported_jigglebones += cnt
            print(f"- Imported {cnt} jigglebone(s) from {filename}")
        if missing:
            self.warning(f"Could not find bones for {len(missing)} jigglebone(s): {', '.join(missing)}")

        # -----------------------------------------------------------------
        # Hitboxes (HitboxSetList)
        # -----------------------------------------------------------------
        hb_created, hb_skipped, hb_bones = import_hitboxes_from_kv3(kv_doc, arm)
        if hb_created:
            self.imported_hitboxes += hb_created
            print(f"- Imported {hb_created} hitbox(es) from {filename}")
        if hb_skipped:
            missing_names = ', '.join(sorted({b for b in hb_bones if b}))
            self.warning(f"Skipped {hb_skipped} hitbox(es) with missing bones: {missing_names}")

        # -----------------------------------------------------------------
        # Animations (AnimationList)
        # Every AnimFile (including those nested in Folder nodes) is imported
        # as a separate action slot on a single action named after the VMDL.
        # -----------------------------------------------------------------
        if self.properties.doAnim:
            anim_files = root_node.find_all(recursive=True, _class="AnimFile")
            if anim_files:
                action_name = self.truncate_id_name(
                    os.path.splitext(qc.jobName)[0], bpy.types.Action)
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
                    anim_path = self._resolve_dmx_ref(filepath, src)
                    if not anim_path:
                        self.warning(f"{filename}: could not find animation DMX '{src}'")
                        continue
                    if anim_path in qc.imported_smds:
                        continue
                    qc.imported_smds.append(anim_path)
                    prev_append = self.append
                    self.append = 'VALIDATE'
                    self.num_files_imported += self.readDMX(anim_path, qc.upAxis, rotMode, False, ANIM)
                    self.append = prev_append
                    imported_anims += 1
                if imported_anims:
                    print(f"- Imported {imported_anims} animation(s) into action '{action_name}'")

        printTimeMessage(qc.startTime, filename, "import", "VMDL")
        # Count referenced meshes when present, otherwise the VMDL itself.
        return self.num_files_imported or 1