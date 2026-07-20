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

import bpy, os
from bpy import ops
from bpy.app.translations import pgettext
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from ..utils import *
from .. import datamodel, keyvalues3
from . import anim as _anim, build as _build, dmx as _dmx
from . import prefab as _prefab, qc as _qc, smd as _smd

from ..utils import PULSE_ATTACHMENT_COLL as _PULSE_ATTACHMENT_COLL, ensure_pulse_collection_at_top as _ensure_pulse_collection_at_top
from .flexdata import populate_dme_flex_from_dmx


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
        if self.smd.jobType == ANIM and self.append == 'APPEND' and (hasattr(self.smd, "a") or _build.find_armature()):
            print("- Appending bones from animations is destructive; switching Bone Append Mode to \"Validate\"")
            self.append = 'VALIDATE'

    # -------------------------------------------------------------------------
    # Bones
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Frames / animation
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Mesh / materials
    # -------------------------------------------------------------------------

    def readQC(self, filepath: str, newscene: bool, doAnim: bool, makeCamera: bool, rotMode: str, outer_qc: bool = False) -> int:
        return _qc.read_qc(self, filepath, newscene, doAnim, makeCamera, rotMode, outer_qc)

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
            header = _smd.parse_quote_blocked_line(file.readline(), self.qc)
            if header:
                break

        if header != ["version", "1"]:
            self.warning(get_id("importer_err_smd_ver"))

        if smd.jobType is None:
            _smd.scan_smd(smd)
        self.createCollection()

        # Order is forced by the format: the node block must be built into an armature
        # before triangle weights can resolve, so this stays a single pass over the file.
        for line in file:
            if line == "nodes\n":
                _build.build_smd_skeleton(self, smd, _smd.read_nodes(smd, self.qc))
            if line == "skeleton\n":
                _anim.build_smd_anim(self, smd, _smd.read_frames(self, smd, self.qc))
            if line == "triangles\n":
                group_names = [b.name for b in smd.a.data.bones] if smd.a else []
                imesh = _smd.read_polys(self, smd, group_names, self.qc)
                if imesh:
                    ob = _build.build_mesh(self, smd, imesh)
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
                _smd.read_shapes(self, smd)

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

        target_arm = _build.find_armature() if self.append != 'NEW_ARMATURE' else None
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
                parsed = _dmx.load_dmx(filepath, smd_type, smd.upAxis)
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

            ifile = _dmx.read_file(parsed)
            for version in parsed.version_bumps:
                self._ensureSceneDmxVersion(version)
            for message in parsed.warnings:
                self.warning(message)

            bone_matrices = _build.build_skeleton(
                self, smd, ifile.skeleton, target_arm,
                parsed.DmeModel.name or smd.jobName)
            _build.apply_rest_pose(self, smd, bone_matrices)

            if smd.a and smd.jobType != ANIM:
                _prefab.apply_dmx_prefab_data(self, smd, parsed, ifile.skeleton)

            imported_meshes = [
                _build.build_mesh(self, smd, imesh, parsed.corrective_separator)
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
                _anim.build_anim(self, smd, ifile.anim)

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
    jb, hb, pb = _prefab.read_dmx_prefab(op, filepath, arm)
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

    # Set once the content-path popup has been answered; the file browser is stage two.
    contentPathChosen: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        # Two stages, because a folder-browse button cannot open a second file browser
        # once one is already open. The content root is collected in a popup first, then
        # Blender's file browser opens for the VMDL itself.
        self.properties.upAxis = context.scene.vs.up_axis
        self.properties.contentPathChosen = False
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):
        if not self.properties.contentPathChosen:
            self.properties.contentPathChosen = True
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return super().execute(context)

    def draw(self, context):
        # draw() serves both stages: the popup, then the file browser sidebar.
        if not self.properties.contentPathChosen:
            col = self.layout.column()
            col.prop(self.properties, "contentPath")
            col.separator()
            col.label(text=get_id("importer_contentpath_hint"), icon='INFO')
            return
        super().draw(context)

    def draw_options(self, layout) -> None:
        layout.prop(self.properties, "doAnim")
        # Still editable in the browser sidebar, in case the popup value was wrong
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
