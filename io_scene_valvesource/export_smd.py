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

import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from bpy.app.translations import pgettext
from mathutils import Vector, Matrix, Euler
from math import * # pyright: ignore
from bpy.types import Collection

from .utils import *
from .keyvalues3 import *
from . import datamodel, ordered_set, flex
from .prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone
from .export import (BakedVertexAnimation, BakeResult, ExportTask, _SplitPart, _MeshPlan,
                     LODBuilder, EdgelineBuilder, BackfaceBuilder, MeshSplitBuilder,
                     Baker, ExportPlanner, DmxWriter)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

class ExportCheck:
    def check_duplicate_bone_names(self, bone_names_dict: dict) -> bool:
        seen = {}
        duplicates = []
        for bone, name in bone_names_dict.items():
            if name in seen:
                duplicates.append(name)
            else:
                seen[name] = bone

        if not duplicates:
            return True

        dupe_report = {
            name: [b for b, n in bone_names_dict.items() if n == name]
            for name in set(duplicates)
        }
        msg = "Found duplicate bone export names:\n"
        for name, bones in dupe_report.items():
            msg += f"- '{name}' used by: {', '.join(bones)}\n"
        self.report({"ERROR"}, msg)
        return False


# -----------------------------------------------------------------------------
# Main exporter operator
# -----------------------------------------------------------------------------

class SmdExporter(bpy.types.Operator, Logger, ExportCheck):
    bl_idname = "export_scene.smd"
    bl_label = get_id("exporter_title")
    bl_description = get_id("exporter_tip")

    collection: bpy.props.StringProperty(name=get_id("exporter_prop_group"), description=get_id("exporter_prop_group_tip"))
    export_scene: bpy.props.BoolProperty(name=get_id("scene_export"), description=get_id("exporter_prop_scene_tip"), default=False)
    object_name: bpy.props.StringProperty(name="Object", default="")

    def __init__(self, *args, **kwargs):
        bpy.types.Operator.__init__(self, *args, **kwargs)
        Logger.__init__(self)
        
    @classmethod
    def poll(cls, context):
        return len(context.scene.vs.export_list) != 0

    def invoke(self, context, event) -> set:
        State.update_scene()
        ops.wm.call_menu(name="SMD_MT_ExportChoice")
        return {"PASS_THROUGH"}

    def execute(self, context) -> set:
        if State.datamodelEncoding != 0 and context.scene.vs.export_format == "DMX":
            datamodel.check_support("binary", State.datamodelEncoding)
            if State.datamodelEncoding < 3 and State.datamodelFormat > 11 and not State.use_kv2:
                self.report({"ERROR"}, "DMX format \"Model {}\" requires DMX encoding \"Binary 3\" or later".format(State.datamodelFormat))
                return {"CANCELLED"}
        if not context.scene.vs.export_path:
            bpy.ops.wm.call_menu(name="SMD_MT_ConfigureScene")
            return {"CANCELLED"}
        if context.scene.vs.export_path.startswith("//") and not context.blend_data.filepath:
            self.report({"ERROR"}, get_id("exporter_err_relativeunsaved"))
            return {"CANCELLED"}
        if State.datamodelEncoding == 0 and context.scene.vs.export_format == "DMX":
            self.report({"ERROR"}, get_id("exporter_err_dmxother"))
            return {"CANCELLED"}

        jiggle_was_enabled = context.scene.vs.jiggle_sim_enabled
        if jiggle_was_enabled:
            context.scene.vs.jiggle_sim_enabled = False

        prev_mode = prev_hidden = None
        if context.active_object:
            if context.active_object.hide_viewport:
                prev_hidden = context.active_object.name
                context.active_object.hide_viewport = False
            prev_mode = context.mode
            if prev_mode.find("EDIT") != -1:
                prev_mode = "EDIT"
            elif prev_mode.find("PAINT") != -1:
                prev_mode = "_".join(reversed(prev_mode.split("_")))
            ops.object.mode_set(mode="OBJECT")

        State.update_scene()
        self.materials_used = set()

        for ob in [ob for ob in bpy.context.scene.objects if ob.type == "ARMATURE" and len(ob.vs.subdir) == 0]:
            ob.vs.subdir = "anims"

        ops.ed.undo_push(message=self.bl_label)

        try:
            context.tool_settings.use_keyframe_insert_auto = False
            context.tool_settings.use_keyframe_insert_keyingset = False
            context.preferences.edit.use_enter_edit_mode = False
            State.unhook_events()
            if context.scene.rigidbody_world:
                context.scene.frame_set(context.scene.rigidbody_world.point_cache.frame_start)

            # Baseline frame every bake starts from. Animation export and vertex-animation
            # baking sweep the timeline and leave the scene parked on their last frame; a
            # subsequent reference-model bake would otherwise capture that stale frame
            # (frame-driven shape keys, geometry nodes, driven modifiers). Reset to this
            # baseline before each task so bakes are deterministic regardless of export
            # order. Falls back to the sim start above when a rigidbody world exists.
            self._bake_frame = context.scene.frame_current

            for view_layer in bpy.context.scene.view_layers:
                unhide_all(view_layer.layer_collection)

            self.files_exported = self.attemptedExports = 0

            export_ids = self._collect_export_ids(context)
            success = True
            for id in export_ids:
                if not self._export_one(context, id):
                    success = False
                    break

            if success:
                if self.export_scene:
                    self._auto_export_prefabs_scene(context, export_ids)
                self.errorReport(get_id("exporter_report", True).format(self.files_exported, self.elapsed_time()))

        finally:
            ops.ed.undo_push(message=self.bl_label)
            if bpy.app.debug_value <= 1:
                ops.ed.undo()
            if prev_mode:
                ops.object.mode_set(mode=prev_mode)
            if prev_hidden:
                context.scene.objects[prev_hidden].hide_viewport = True
            context.scene.update_tag()
            context.window_manager.progress_end()
            State.hook_events()
            if jiggle_was_enabled:
                context.scene.vs.jiggle_sim_enabled = True

        self.collection = ""
        self.export_scene = False
        self.object_name = ""
        return {"FINISHED"}

    def _collect_export_ids(self, context) -> list:
        ids = []
        if self.export_scene:
            for exportable in context.scene.vs.export_list:
                if exportable.prefab_type:
                    continue
                id = exportable.item
                if isinstance(id, Collection):
                    if shouldExportGroup(id):
                        ids.append(id)
                elif id.vs.export:
                    ids.append(id)
        elif self.collection:
            col = bpy.data.collections[self.collection]
            if col.vs.mute:
                self.error(get_id("exporter_err_groupmuted", True).format(col.name))
            elif is_bypassed_into_parent(col):
                self.error(get_id("exporter_err_groupbypassed", True).format(col.name))
            elif not col.objects:
                self.error(get_id("exporter_err_groupempty", True).format(col.name))
            else:
                ids.append(col)
        else:
            exportables = getSelectedExportables()
            if self.object_name:
                exportables = [e for e in exportables if not isinstance(e.item, Collection) and e.item.name == self.object_name]
            for exportable in exportables:
                if not isinstance(exportable.item, Collection):
                    ids.append(exportable.item)
        return ids

    def _auto_export_prefabs_scene(self, context, export_ids: list) -> None:
        arms = set()
        
        for item in export_ids:
            if isinstance(item, bpy.types.Collection):
                for ob in item.objects:
                    if ob.type == 'ARMATURE':
                        arms.add(ob)
            elif isinstance(item, bpy.types.Object) and item.type == 'ARMATURE':
                arms.add(item)

        valid_arms = []
        for arm in arms:
            if not getattr(arm.vs, 'export', False):
                #print(f"\nPulseSrcOps skipping prefab export for armature '{arm.name}'")
                continue

            if arm.users_collection:
                has_exportable_col = any(
                    getattr(col, 'vs', None) and getattr(col.vs, 'export', False)
                    for col in arm.users_collection
                )
                if not has_exportable_col:
                    #print(f"\nPulseSrcOps skipping prefab export for armature '{arm.name}'")
                    continue
                    
            valid_arms.append(arm)

        if not valid_arms:
            return
            
        for arm in valid_arms:
            print(f"\nPulseSrcOps auto-exporting prefabs for armature '{arm.name}'")
            self._auto_export_prefabs_for_armature(arm, context)
        
        print("\n")

    def _auto_export_prefabs_for_armature(self, arm: bpy.types.Object, context) -> None:
        runner = _PrefabRunnerAdapter(self.report)
        avs = getattr(arm.data, 'vs', None)
        prefab_items = {p.prefab_type: p for p in avs.prefab_items} if avs else {}

        # In DME mode jigglebones/attachments/hitboxes AND procedural bones are all encoded
        # into the model DMX, so we skip their standalone .qci/.vrd output here to avoid a
        # duplicate that would conflict with the embedded data.
        dme = prefab_mode_is_dme(context.scene)

        for export_type, _count in prefab_available_types(arm, context.scene):
            if dme and export_type in ('JIGGLEBONES', 'ATTACHMENTS', 'HITBOXES', 'PROCEDURAL'):
                print(f"  - {export_type}: skipped - encoded into model DMX (DME mode)")
                continue

            pitem = prefab_items.get(export_type)
            if pitem is not None and not pitem.export:
                print(f"  - {export_type}: skipped - export disabled")
                continue

            resolved = resolve_prefab_output(arm, export_type, context.scene)
            if resolved is None:
                print(f"  - {export_type}: skipped - could not resolve output path")
                continue
            export_path, fmt = resolved

            warnings = None
            if export_type == 'JIGGLEBONES':
                compiled = runner._run_jigglebones(arm, fmt, export_path)
            elif export_type == 'ATTACHMENTS':
                compiled = runner._run_attachments(arm, fmt, export_path, context)
            elif export_type == 'HITBOXES':
                compiled, warnings = runner._run_hitboxes(arm, fmt, export_path)
            elif export_type == 'PROCEDURAL':
                compiled = runner._run_procedural(arm, context)
            else:
                continue

            if compiled is None:
                print(f"  - {export_type}: nothing to export")
                continue
            if runner._write_output(compiled, export_path, warnings):
                print(f"  - {export_type}: exported to {export_path}")
            else:
                print(f"  - {export_type}: export failed")

    def _export_one(self, context, id) -> bool:
        self.attemptedExports += 1
        self._last_bake_results = []
        # Baked-armature cache shared by every task of this one export id (reset per id so
        # a mutated animation armature can't leak into an unrelated export). See Baker.bake.
        self._armature_bake_cache = {}
        bench = BenchMarker()
        subdir = id.vs.subdir.lstrip("/")
        print(f"\nPulseSrcOps exporting {id.name}")

        path = os.path.join(bpy.path.abspath(context.scene.vs.export_path), subdir)
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as err:
                self.error(get_id("exporter_err_makedirs", True).format(err))
                return False

        if isinstance(id, Collection) and not any(ob.vs.export for ob in get_collection_export_objects(id)):
            self.error(get_id("exporter_err_nogroupitems", True).format(id.name))
            return False

        if isinstance(id, bpy.types.Object) and id.type == "ARMATURE":
            ad = id.animation_data
            if not ad:
                return False

        check_obs = get_collection_export_objects(id) if isinstance(id, Collection) else [id]

        if State.exportFormat != ExportFormat.DMX:
            for _ob in check_obs:
                vs = getattr(_ob, 'vs', None)
                if not vs:
                    continue
                if getattr(vs, 'mesh_type', 'DEFAULT') == 'CLOTHPROXY':
                    self.warning(f"'{_ob.name}' is set to Cloth Proxy but scene export format is not DMX - cloth attributes will be omitted.")
                if getattr(vs, 'flex_controller_mode', '') == 'DME':
                    self.warning(get_id("exporter_warn_dme_smd", True).format(_ob.name))

        for _ob in check_obs:
            vs = getattr(_ob, 'vs', None)
            if vs and getattr(vs, 'flex_controller_mode', '') == 'DME':
                dme_errors = validate_dme_flex_for_export(_ob)
                for err in dme_errors:
                    self.error(err)
                if dme_errors:
                    return False

        planner = ExportPlanner(self)
        try:
            tasks = planner.build_queue(id)
            bench.report("planning")

            for task in tasks:
                if not self._execute_task(context, id, task, path, bench, planner):
                    return False
        finally:
            planner.cleanup()

        self._warn_unicode(id)
        return True

    def _execute_task(self, context, original_id, task: ExportTask, path: str,
                      bench: BenchMarker, planner: ExportPlanner = None) -> bool:
        source = task.source_id

        if isinstance(source, Collection) and not any(ob.vs.export for ob in source.objects):
            return True

        # -- hide unwanted metaballs ------------------------------------------
        for meta in [ob for ob in context.scene.objects if ob.type == "META" and (
            not ob.vs.export or (isinstance(source, Collection) and ob.name not in source.objects)
        )]:
            for element in meta.data.elements:
                element.hide = True

        # -- bake -------------------------------------------------------------
        # Reset to the baseline frame so this task's bake is deterministic and never
        # inherits a stale frame left behind by a previous animation / vertex-animation
        # export in the same run (see self._bake_frame in execute()).
        baseline_frame = getattr(self, "_bake_frame", None)
        if baseline_frame is not None and context.scene.frame_current != baseline_frame:
            context.scene.frame_set(baseline_frame)

        baker = Baker(self)
        bake_results = []

        if isinstance(source, Collection):
            group_vmaps = valvesource_vertex_maps(source)
            baked_metaballs = []

            for ob in [ob for ob in source.objects if ob.vs.export and ob.session_uid in task.allowed_uids]:
                if ob.type == "META":
                    ob = self._find_basis_metaball(ob)
                    if ob in baked_metaballs:
                        continue
                    baked_metaballs.append(ob)

                bake = baker.bake(ob)
                if bake:
                    if planner:
                        orig = planner.original_name(ob.session_uid)
                        if orig:
                            bake.name = orig
                    for vmap_name in group_vmaps:
                        if vmap_name not in bake.object.data.vertex_colors:
                            vc = bake.object.data.vertex_colors.new(name=vmap_name)
                            vc.data.foreach_set("color", [1.0] * 4)
                    bake_results.append(bake)
        else:
            if source.type == "META":
                bake = baker.bake(self._find_basis_metaball(source))
            else:
                bake = baker.bake(source)
            if bake:
                if planner:
                    orig = planner.original_name(source.session_uid)
                    if orig:
                        bake.name = orig
                bake_results.append(bake)

            for companion in task.companions:
                comp_bake = baker.bake(companion)
                if comp_bake:
                    bake_results.append(comp_bake)

        bench.report("bake", len(bake_results))

        if not any(bake_results):
            return True

        # -- vertex animations ------------------------------------------------
        self._process_vertex_animations(source, bake_results, bench)

        # -- DMX automerge ----------------------------------------------------
        if isinstance(source, Collection) and State.exportFormat == ExportFormat.DMX:
            if len(getattr(source.vs, "vertex_animations", [])) and len(source.objects) > 1:
                mesh_bakes_check = [b for b in bake_results if b.object.type == "MESH"]
                mergeable_check = [
                    b for b in bake_results
                    if (type(b.envelope) is str and b.envelope == bake_results[0].envelope)
                    or b.envelope is None
                ]
                if len(mesh_bakes_check) > len(mergeable_check):
                    self.error(get_id("exporter_err_unmergable", True).format(source.name))
                    return False
                elif not source.vs.automerge:
                    source.vs.automerge = True

            if source.vs.automerge:
                bake_results = self._dmx_automerge(source, bake_results, bench)

        # -- skeleton setup ---------------------------------------------------
        self.armature = self.armature_src = None
        self.bone_ids = {}
        self.exportable_bones = []
        self.exportable_boneNames = {}
        self.exportable_empties = None

        for result in bake_results:
            if result.armature:
                if not self.armature:
                    self.armature = result.armature.object
                    self.armature_src = result.armature.src
                elif self.armature != result.armature.object:
                    self.warning(get_id("exporter_warn_multiarmature"))

        if planner and self.armature_src:
            self.armature_src = planner._original_ob_map.get(
                self.armature_src.session_uid, self.armature_src
            )

        if self.armature_src:
            if not self._setup_skeleton(source, bake_results, baker):
                return False

        self.bake_results = list(baker._cache.values())
        self._last_bake_results.extend(bake_results)

        # -- flex controller setup ---------------------------------------------
        src_mt_flex = getattr(getattr(source, 'vs', None), 'mesh_type', 'DEFAULT')
        if State.exportFormat == ExportFormat.DMX and hasShapes(source) and src_mt_flex == 'DEFAULT':
            self.flex_controller_mode = source.vs.flex_controller_mode
            self.flex_controller_source = source.vs.flex_controller_source

        bpy.context.view_layer.objects.active = bake_results[0].object
        bpy.ops.object.mode_set(mode="OBJECT")

        # -- VCA automerge check -----------------------------------------------
        if isinstance(source, Collection) and len(source.vs.vertex_animations) and len(source.objects) > 1:
            mesh_bakes = [b for b in bake_results if b.object.type == "MESH"]
            if len(mesh_bakes) > len([b for b in bake_results if (type(b.envelope) is str and b.envelope == bake_results[0].envelope) or b.envelope is None]):
                self.error(get_id("exporter_err_unmergable", True).format(source.name))
                skip_vca = True
            elif not source.vs.automerge:
                source.vs.automerge = True

        # -- write -------------------------------------------------------------
        # Rewrite phases 1-2: route skeleton/mesh/shape and animation DMX exports through the
        # new DmxWriter. Vertex-animation (VCA) exports still use the old writeDMX until that
        # phase lands. Set KST_OLD_DMX=1 to force the old writer for A/B diffing.
        if State.exportFormat == ExportFormat.DMX:
            has_vca = bool(bake_results[0].vertex_animations) if bake_results else False
            if not has_vca and not os.environ.get("KST_OLD_DMX"):
                write_func = self._run_dmx_writer
            else:
                write_func = self.writeDMX
        else:
            write_func = self.writeSMD
        bench.report("Post Bake")

        if isinstance(source, bpy.types.Object) and source.type == "ARMATURE" and source.data.vs.action_selection != "CURRENT":
            baked_armature = bake_results[0].object
            if source.data.vs.action_selection == "FILTERED":
                for slot in actionSlotsForFilter(baked_armature):
                    baked_armature.animation_data.action_slot = slot
                    self.files_exported += write_func(source, bake_results, self.sanitiseFilename(slot.name_display), path)
            else:
                for action in actionsForFilter(baked_armature.vs.action_filter):
                    baked_armature.animation_data.action = action
                    self.files_exported += write_func(source, bake_results, self.sanitiseFilename(action.name), path)
        else:
            self.files_exported += write_func(source, bake_results, self.sanitiseFilename(task.export_name), path)

        bench.report(write_func.__name__)

        if State.compiler > Compiler.STUDIOMDL or State.datamodelFormat >= 22:
            if re.match(r"[^a-z0-9_]", source.name):
                self.warning(get_id("exporter_warn_source2names", format_string=True).format(source.name))

        return True

    def _setup_skeleton(self, source, bake_results: list[BakeResult], baker: Baker) -> bool:
        if list(self.armature_src.scale).count(self.armature_src.scale[0]) != 3:
            self.warning(get_id("exporter_err_arm_nonuniform", True).format(self.armature_src.name))

        if not self.armature:
            self.armature = baker.bake(self.armature_src).object

        exporting_armature = isinstance(source, bpy.types.Object) and source.type == "ARMATURE"
        self.exportable_bones = [
            self.armature.pose.bones[b.name]
            for b in self.armature.data.bones
            if exporting_armature or b.use_deform
        ]
        self.exportable_boneNames = {
            b.name: get_bone_exportname(b)
            for b in self.armature.data.bones
            if exporting_armature or b.use_deform
        }

        if not self.check_duplicate_bone_names(self.exportable_boneNames):
            return False

        skipped = len(self.armature.pose.bones) - len(self.exportable_bones)
        if skipped:
            print(f"- Skipping {skipped} non-deforming bones")

        # Snapshot bone-parented attachment empties at REST pose. Gather the candidates
        # first (a set membership test instead of rebuilding the bone-name list per empty),
        # and only pay for the pose_position toggle + the two view_layer.update() flushes
        # when there is actually something to capture and the armature isn't already at REST.
        bone_names = {pb.name for pb in self.armature.pose.bones}
        candidate_empties = [
            e
            for e in bpy.data.objects
            if e.type == "EMPTY"
            and e.parent == self.armature_src
            and e.parent_type == "BONE"
            and e.parent_bone in bone_names
            and isinstance(getattr(e.vs, "dmx_attachment", None), bool)
            and e.vs.dmx_attachment
        ]

        if candidate_empties:
            original_pose = self.armature_src.data.pose_position
            toggle_rest = original_pose != "REST"
            if toggle_rest:
                self.armature_src.data.pose_position = "REST"
                bpy.context.view_layer.update()

            self.exportable_empties = [(e, e.matrix_world.copy()) for e in candidate_empties]

            if toggle_rest:
                self.armature_src.data.pose_position = original_pose
                bpy.context.view_layer.update()
        else:
            self.exportable_empties = []

        return True

    def _process_vertex_animations(self, source, bake_results: list[BakeResult], bench: BenchMarker) -> None:
        if not (isinstance(source, Collection) and len(getattr(source.vs, "vertex_animations", []))):
            return

        mesh_bakes = [b for b in bake_results if b.object.type == "MESH"]

        for va in source.vs.vertex_animations:
            if State.exportFormat == ExportFormat.DMX:
                va.name = va.name.replace("_", "-")

            vca = bake_results[0].vertex_animations[va.name]
            vca.export_sequence = va.export_sequence
            vca.num_frames = va.end - va.start
            two_percent = vca.num_frames * len(bake_results) / 50
            print(f"- Generating vertex animation \"{va.name}\"")
            anim_bench = BenchMarker(1, va.name)

            for f in range(va.start, va.end):
                bpy.context.scene.frame_set(f)
                bpy.ops.object.select_all(action="DESELECT")
                depsgraph = bpy.context.evaluated_depsgraph_get()

                for bake in mesh_bakes:
                    bake.fob = bpy.data.objects.new(
                        f"{va.name}-{f}",
                        bpy.data.meshes.new_from_object(bake.src.evaluated_get(depsgraph))
                    )
                    bake.fob.matrix_world = bake.src.matrix_world
                    bpy.context.scene.collection.objects.link(bake.fob)
                    bpy.context.view_layer.objects.active = bake.fob
                    bake.fob.select_set(True)

                    tp = self.getTopParent(bake.src)
                    if tp:
                        bake.fob.location -= tp.location

                    if bpy.context.scene.rigidbody_world:
                        prev_rbw = bpy.context.scene.rigidbody_world.enabled
                        bpy.context.scene.rigidbody_world.enabled = False

                    bpy.ops.object.transform_apply(location=True, scale=True, rotation=True)

                    if bpy.context.scene.rigidbody_world:
                        bpy.context.scene.rigidbody_world.enabled = prev_rbw

                if bpy.context.selected_objects and State.exportFormat == ExportFormat.SMD:
                    bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
                    ops.object.join()

                vca.append(
                    bpy.context.active_object
                    if len(bpy.context.selected_objects) == 1
                    else bpy.context.selected_objects
                )
                anim_bench.report("bake")

                if len(bpy.context.selected_objects) != 1:
                    for bake in mesh_bakes:
                        bpy.context.scene.collection.objects.unlink(bake.fob)
                        del bake.fob

                anim_bench.report("record")

                if two_percent and len(vca) / len(bake_results) % two_percent == 0:
                    print(".", debug_only=True, newline=False)
                    bpy.context.window_manager.progress_update(len(vca) / vca.num_frames)

            bench.report("\n" + va.name)
            bpy.context.view_layer.objects.active = bake_results[0].src

    def _dmx_automerge(self, source: Collection, bake_results: list[BakeResult], bench: BenchMarker) -> list[BakeResult]:
        bone_parents = collections.defaultdict(list)
        scene_obs = bpy.context.scene.collection.objects
        view_obs = bpy.context.view_layer.objects

        for bake in [b for b in bake_results if type(b.envelope) is str or b.envelope is None]:
            bone_parents[bake.envelope].append(bake)

        for bp, parts in bone_parents.items():
            if len(parts) <= 1:
                continue

            shape_names = {key for part in parts for key in part.shapes.keys()}

            ops.object.select_all(action="DESELECT")
            for part in parts:
                ob = part.object.copy()
                ob.data = ob.data.copy()
                ob.data.uv_layers.active.name = "__dmx_uv__"
                scene_obs.link(ob)
                ob.select_set(True)
                view_obs.active = ob
                bake_results.remove(part)

            bpy.ops.object.join()
            joined = BakeResult(bp + "_meshes" if bp else "loose_meshes")
            joined.object = bpy.context.active_object
            joined.object.name = joined.object.data.name = joined.name
            joined.envelope = bp

            if parts[0].vertex_animations:
                for src_name, src_vca in parts[0].vertex_animations.items():
                    vca = joined.vertex_animations[src_name] = BakedVertexAnimation()
                    vca.bone_id = src_vca.bone_id
                    vca.export_sequence = src_vca.export_sequence
                    vca.num_frames = src_vca.num_frames

                    for i, frame in enumerate(src_vca):
                        ops.object.select_all(action="DESELECT")
                        frame.reverse()
                        for ob in frame:
                            scene_obs.link(ob)
                            ob.select_set(True)
                        bpy.context.view_layer.objects.active = frame[0]
                        bpy.ops.object.join()
                        bpy.context.active_object.name = f"{src_name}-{i}"
                        bpy.ops.object.transform_apply(location=True, scale=True, rotation=True)
                        vca.append(bpy.context.active_object)
                        scene_obs.unlink(bpy.context.active_object)

            bake_results.append(joined)

            for shape_name in shape_names:
                ops.object.select_all(action="DESELECT")
                for part in parts:
                    mesh = part.shapes.get(shape_name, part.object.data)
                    ob = bpy.data.objects.new(name=f"{part.name} -> {shape_name}", object_data=mesh.copy())
                    scene_obs.link(ob)
                    ob.matrix_local = part.matrix
                    ob.select_set(True)
                    view_obs.active = ob

                bpy.ops.object.join()
                joined.shapes[shape_name] = bpy.context.active_object.data
                joined.shapes[shape_name].name = f"{joined.object.name} -> {shape_name}"
                scene_obs.unlink(bpy.context.active_object)
                bpy.data.objects.remove(bpy.context.active_object)

            view_obs.active = joined.object

        bench.report("Mesh merge")
        return bake_results

    # -- utilities ------------------------------------------------------------

    def _find_basis_metaball(self, id: bpy.types.Object) -> bpy.types.Object:
        basis_ns = id.name.rsplit(".")
        if len(basis_ns) == 1:
            return id
        basis = id
        for meta in [ob for ob in bpy.data.objects if ob.type == "META"]:
            ns = meta.name.rsplit(".")
            if ns[0] != basis_ns[0]:
                continue
            if len(ns) == 1:
                return meta
            try:
                if int(ns[1]) < int(basis_ns[1]):
                    basis = meta
                    basis_ns = ns
            except ValueError:
                pass
        return basis

    def _warn_unicode(self, id) -> None:
        unicode_tested = set()

        def check(name, obj, display_type):
            if obj in unicode_tested:
                return
            unicode_tested.add(obj)
            try:
                name.encode("ascii")
            except UnicodeEncodeError:
                self.warning(get_id("exporter_warn_unicode", format_string=True).format(pgettext(display_type), name))

        for bake in getattr(self, "_last_bake_results", []):
            check(bake.name, bake, type(bake.src).__name__)
            for shape_name, shape_id in bake.shapes.items():
                check(shape_name, shape_id, "Shape Key")
        for mat in self.materials_used:
            check(mat[0], mat[1], type(mat[1]).__name__)

    def sanitiseFilename(self, name: str) -> str:
        new_name = name
        for ch in r'/?<>\:*|"':
            new_name = new_name.replace(ch, "_")
        if new_name != name:
            self.warning(get_id("exporter_warn_sanitised_filename", True).format(name, new_name))
        return new_name

    def getWeightmap(self, bake_result: BakeResult) -> list:
        out = []
        amod = bake_result.envelope
        ob = bake_result.object
        if not amod or not isinstance(amod, bpy.types.ArmatureModifier):
            return out

        amod_vg = ob.vertex_groups.get(amod.vertex_group)

        try:
            amod_ob = next(bake.object for bake in self.bake_results if bake.src == amod.object)
        except StopIteration as e:
            raise ValueError(f"Armature for exportable \"{bake_result.name}\" was not baked") from e

        model_mat = amod_ob.matrix_world.inverted() @ ob.matrix_world
        num_verts = len(ob.data.vertices)
        progress_step = max(50, num_verts // 100)

        # Pre-build vg_index -> bone_id map so the inner loop is O(1) instead of doing
        # ob.vertex_groups lookup + pose.bones.get + list-contains per vertex per group.
        exportable_bone_names = {b.name for b in self.exportable_bones}
        vg_to_bone_id: dict[int, int] = {}
        if amod.use_vertex_groups:
            for vg in ob.vertex_groups:
                bone = amod_ob.pose.bones.get(vg.name)
                if bone and bone.name in exportable_bone_names:
                    vg_to_bone_id[vg.index] = self.bone_ids[bone.name]

        # Pre-filter exportable bones list for envelope fallback.
        exportable_bones_list = [pb for pb in amod_ob.pose.bones if pb in self.exportable_bones] \
            if amod.use_bone_envelopes else []

        for v in ob.data.vertices:
            weights = []
            total_weight = 0
            if len(out) % progress_step == 0:
                bpy.context.window_manager.progress_update(len(out) / num_verts)

            if amod.use_vertex_groups:
                for v_group in v.groups:
                    bone_id = vg_to_bone_id.get(v_group.group)
                    if bone_id is not None:
                        weights.append([bone_id, v_group.weight])
                        total_weight += v_group.weight

            if amod.use_bone_envelopes and total_weight == 0:
                for pb in exportable_bones_list:
                    weight = pb.bone.envelope_weight * pb.evaluate_envelope(model_mat @ v.co)
                    if weight:
                        weights.append([self.bone_ids[pb.name], weight])
                        total_weight += weight

            if total_weight not in (0, 1):
                for link in weights:
                    link[1] *= 1 / total_weight

            if amod_vg and total_weight > 0:
                amod_vg_weight = 0
                for v_group in v.groups:
                    if v_group.group == amod_vg.index:
                        amod_vg_weight = v_group.weight
                        break
                if amod.invert_vertex_group:
                    amod_vg_weight = 1 - amod_vg_weight
                for link in weights:
                    link[1] *= amod_vg_weight

            out.append(weights)
        return out

    def GetMaterialName(self, ob: bpy.types.Object, material_index: int) -> tuple[str, bool]:
        mat_name = mat_id = None
        if len(ob.material_slots) > material_index:
            mat_id = ob.material_slots[material_index].material
            if mat_id:
                mat_name = sanitize_string(mat_id.name, allow_unicode=True)
        if mat_name:
            self.materials_used.add((mat_name, mat_id))
            return mat_name, True # pyright: ignore
        return "no_material", ob.display_type != "TEXTURED"

    def getTopParent(self, id: bpy.types.Object) -> bpy.types.Object:
        top = id
        while top.parent:
            top = top.parent
        return top

    def getEvaluatedPoseBones(self) -> list:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = self.armature.evaluated_get(depsgraph)
        assert isinstance(evaluated, bpy.types.Object) and evaluated.pose
        return [evaluated.pose.bones[b.name] for b in self.exportable_bones]

    def _keyframedBoneNames(self) -> set:
        # Bone names that have at least one fcurve in the armature's current action/slot.
        # These are the bones the animation actually drives; anything else that is posed
        # will be wiped by the reset_pose_per_anim pass below.
        ad = self.armature.animation_data
        if not ad or not ad.action:
            return set()
        try:
            channelbag = ad.action.layers[0].strips[0].channelbag(ad.action_slot)
        except (IndexError, AttributeError):
            return set()
        if channelbag is None:
            return set()
        names = set()
        for fcurve in channelbag.fcurves:
            m = re.match(r'pose\.bones\["(.+?)"\]', fcurve.data_path)
            if m:
                names.add(m.group(1))
        return names

    def warnUnkeyframedPose(self, anim_name: str):
        # reset_pose_per_anim zeroes every pose bone's matrix_basis before sampling, so a
        # bone the user posed but never keyframed silently snaps back to rest - a common
        # cause of "broken" exported animations. Warn about those bones so the user can
        # keyframe them (or disable the reset) instead of losing the pose.
        #
        # NB: the baked armature (self.armature) has already had every matrix_basis reset
        # to identity in Baker.bake(), so the manual pose only survives on the untouched
        # source armature - check that for the posed transforms.
        src = self.armature_src
        if not src:
            return
        keyframed = self._keyframedBoneNames()
        posed = []
        for pb in self.exportable_bones:
            if pb.name in keyframed:
                continue
            src_pb = src.pose.bones.get(pb.name)
            if src_pb is None:
                continue
            mb = src_pb.matrix_basis
            if any(abs(mb[r][c] - (1.0 if r == c else 0.0)) > 1e-5 for r in range(4) for c in range(4)):
                posed.append(self.exportable_boneNames.get(pb.name, pb.name))
        if not posed:
            return
        shown = ", ".join(posed[:8]) + (f", +{len(posed) - 8} more" if len(posed) > 8 else "")
        self.warning(get_id("exporter_warn_unkeyframed_pose", True).format(anim_name, len(posed), shown))

    def applyUnkeyframedSourcePose(self):
        # Counterpart to the reset_pose_per_anim reset. Baker.bake() UNCONDITIONALLY
        # zeroes every matrix_basis on the baked armature (needed so meshes bind at rest
        # pose), which means that with reset_pose_per_anim disabled the user's manual pose
        # would still be silently lost. Copy matrix_basis back from the untouched source
        # armature so posed-but-un-keyframed bones keep their pose; frame_set() still
        # overrides any bone the action actually keyframes during sampling.
        src = self.armature_src
        if not src:
            return
        for pb in self.armature.pose.bones:
            src_pb = src.pose.bones.get(pb.name)
            if src_pb is not None:
                pb.matrix_basis = src_pb.matrix_basis.copy()

    # -------------------------------------------------------------------------
    # SMD writing - logic unchanged from original
    # -------------------------------------------------------------------------

    def openSMD(self, path, name, description):
        full_path = os.path.realpath(os.path.join(path, name))
        try:
            f = open(full_path, "w", encoding="utf-8")
        except Exception as err:
            self.error(get_id("exporter_err_open", True).format(description, err))
            return None
        f.write("version 1\n")
        print("-", full_path)
        return f

    def writeSMD(self, id, bake_results, name, filepath, filetype="smd"):
        bench = BenchMarker(1, "SMD")
        goldsrc = bpy.context.scene.vs.smd_format == "GOLDSOURCE"

        self.smd_file = self.openSMD(filepath, sanitize_string(name, allow_unicode=True) + "." + filetype, filetype.upper())
        if self.smd_file is None:
            return 0

        if State.compiler > Compiler.STUDIOMDL:
            self.warning(get_id("exporter_warn_source2smdsupport"))

        self.smd_file.write("nodes\n")
        curID = 0
        if not self.armature:
            self.smd_file.write("0 \"root\" -1\n")
            if filetype == "smd":
                print("- No skeleton to export")
        else:
            if self.armature.data.vs.implicit_zero_bone:
                self.smd_file.write(f"0 \"{implicit_bone_name}\" -1\n")
                curID += 1

            for bone in self.exportable_bones:
                parent = bone.parent
                while parent and parent not in self.exportable_bones:
                    parent = parent.parent

                self.bone_ids[bone.name] = curID
                bone_name = self.exportable_boneNames[bone.name]
                parent_id = str(self.bone_ids[parent.name]) if parent else "-1"
                self.smd_file.write(f"{curID} \"{bone_name}\" {parent_id}\n")
                curID += 1

            num_bones = len(self.armature.data.bones)
            if filetype == "smd":
                print(f"- Exported {num_bones} bones")
            if num_bones > 128:
                self.warning(get_id("exporter_err_bonelimit", True).format(num_bones, 128))

        for vca in [v for v in bake_results[0].vertex_animations.items() if v[1].export_sequence]:
            curID += 1
            vca[1].bone_id = curID
            self.smd_file.write(f"{curID} \"vcabone_{vca[0]}\" -1\n")

        self.smd_file.write("end\n")

        if filetype == "smd":
            self.smd_file.write("skeleton\n")
            if not self.armature:
                self.smd_file.write("time 0\n0 0 0 0 0 0 0\nend\n")
            else:
                is_anim = len(bake_results) == 1 and bake_results[0].object.type == "ARMATURE"
                # first_frame lets actions that don't start on frame 0 export their real
                # motion: we sample scene frames first_frame..first_frame+span but keep the
                # SMD "time" 0-based so the compiled sequence still starts at time 0.
                first_frame, span = animationFrameRange(self.armature.animation_data) if is_anim else (0, 0)
                anim_len = span + 1 if is_anim else 1

                if not is_anim:
                    for pb in self.armature.pose.bones:
                        pb.matrix_basis.identity()
                elif self.armature.data.vs.reset_pose_per_anim:
                    self.warnUnkeyframedPose(name)
                    for pb in self.armature.pose.bones:
                        pb.matrix_basis.identity()
                else:
                    self.applyUnkeyframedSourcePose()

                for i in range(anim_len):
                    bpy.context.window_manager.progress_update(i / anim_len)
                    self.smd_file.write(f"time {i}\n")
                    if self.armature.data.vs.implicit_zero_bone:
                        self.smd_file.write("0  0 0 0  0 0 0\n")
                    if is_anim:
                        bpy.context.scene.frame_set(first_frame + i)

                    evaluated = self.getEvaluatedPoseBones()
                    for pb in evaluated:
                        parent = pb.parent
                        while parent and parent not in evaluated:
                            parent = parent.parent

                        mat = get_bone_matrix(pb, rest_space=not is_anim)
                        if parent:
                            pmat = get_bone_matrix(parent, rest_space=not is_anim)
                            mat = pmat.inverted() @ mat
                        else:
                            mat = self.armature.matrix_world @ mat

                        self.smd_file.write(f"{self.bone_ids[pb.name]}  {getSmdVec(mat.to_translation())}  {getSmdVec(mat.to_euler())}\n")

                self.smd_file.write("end\n")
                ops.object.mode_set(mode="OBJECT")
                print(f"- Exported {anim_len} frames")

            done_header = False
            for bake in [b for b in bake_results if b.object.type != "ARMATURE"]:
                if not done_header:
                    self.smd_file.write("triangles\n")
                    done_header = True

                ob = bake.object
                uv_loop = ob.data.uv_layers.active.data
                weights = self.getWeightmap(bake)

                ob_weight_str = None
                if type(bake.envelope) == str and bake.envelope in self.bone_ids:
                    ob_weight_str = (" 1 {} 1" if not goldsrc else "{}").format(self.bone_ids[bake.envelope])
                elif not weights:
                    ob_weight_str = " 0" if not goldsrc else "0"

                bad_face_mats = 0
                multi_weight_verts = set()

                # Pre-compute per-vertex weight strings so vertices shared across polygons
                # don't repeat the same string-building work on every loop.
                weight_strs: dict[int, str] = {}
                if weights and not ob_weight_str:
                    for vi, w_list in enumerate(weights):
                        valid = [(bi, bw) for bi, bw in w_list if bw > 0]
                        if not goldsrc:
                            weight_strs[vi] = " {}{}".format(
                                len(valid),
                                "".join(f" {bi} {getSmdFloat(bw)}" for bi, bw in valid),
                            )
                        else:
                            if not valid:
                                weight_strs[vi] = "0"
                            else:
                                weight_strs[vi] = str(valid[0][0])

                src_mt = getattr(bake.src.vs, 'mesh_type', 'DEFAULT') if bake.src else 'DEFAULT'
                num_polys = len(ob.data.polygons)
                poly_progress_step = max(10, num_polys // 100)
                lines = []

                for p, poly in enumerate(ob.data.polygons):
                    if p % poly_progress_step == 0:
                        bpy.context.window_manager.progress_update(p / num_polys)
                    if src_mt in ('COLLISION', 'CLOTHPROXY'):
                        mat_name, mat_ok = "no_material", True
                    else:
                        mat_name, mat_ok = self.GetMaterialName(ob, poly.material_index)
                    if not mat_ok:
                        bad_face_mats += 1
                    lines.append(mat_name + "\n")

                    for loop in [ob.data.loops[l] for l in poly.loop_indices]:
                        v = ob.data.vertices[loop.vertex_index]
                        pos_norm = f"  {getSmdVec(v.co)}  {getSmdVec(loop.normal)}  "
                        uv = " ".join(getSmdFloat(j) for j in uv_loop[loop.index].uv)

                        if not goldsrc:
                            ws = ob_weight_str if ob_weight_str else weight_strs.get(v.index, " 0")
                            lines.append("0" + pos_norm + uv + ws + "\n")
                        else:
                            if ob_weight_str:
                                ws = ob_weight_str
                            else:
                                ws = weight_strs.get(v.index, "0")
                                gw = [link for link in weights[v.index] if link[1] > 0]
                                if len(gw) > 1:
                                    multi_weight_verts.add(v)
                            lines.append(ws + pos_norm + uv + "\n")

                self.smd_file.writelines(lines)

                if goldsrc and multi_weight_verts:
                    self.warning(get_id("exporterr_goldsrc_multiweights", format_string=True).format(len(multi_weight_verts), bake.src.data.name))
                if bad_face_mats:
                    self.warning(get_id("exporter_err_facesnotex_ormat").format(bad_face_mats, bake.src.data.name))
                print(f"- Exported {len(ob.data.polygons)} polys")
                print(f"- Exported {len(self.materials_used)} materials")
                for mat in self.materials_used:
                    print("   " + mat[0])

            if done_header:
                self.smd_file.write("end\n")

        elif filetype == "vta":
            self.smd_file.write("skeleton\n")

            def write_time(time, shape_name=None):
                self.smd_file.write("time {}{}\n".format(time, f" # {shape_name}" if shape_name else ""))

            shape_names = ordered_set.OrderedSet()
            for bake in [b for b in bake_results if b.object.type != "ARMATURE"]:
                for sn in bake.shapes.keys():
                    shape_names.add(sn)

            write_time(0)
            for i, sn in enumerate(shape_names):
                write_time(i + 1, sn)
            self.smd_file.write("end\n\nvertexanimation\n")

            vert_id = 0
            write_time(0)
            for bake in [b for b in bake_results if b.object.type != "ARMATURE"]:
                bake.offset = vert_id
                verts = bake.object.data.vertices
                for loop in [bake.object.data.loops[l] for poly in bake.object.data.polygons for l in poly.loop_indices]:
                    self.smd_file.write(f"{vert_id} {getSmdVec(verts[loop.vertex_index].co)} {getSmdVec(loop.normal)}\n")
                    vert_id += 1

            total_verts = 0
            for i, shape_name in enumerate(shape_names):
                i += 1
                bpy.context.window_manager.progress_update(i / len(shape_names))
                write_time(i, shape_name)
                for bake in [b for b in bake_results if b.object.type != "ARMATURE"]:
                    shape = bake.shapes.get(shape_name)
                    if not shape:
                        continue
                    vi = bake.offset
                    preserve_basis_normals = bake.src.data.vs.bake_shapekey_as_basis_normals
                    for ml in [bake.object.data.loops[l] for poly in bake.object.data.polygons for l in poly.loop_indices]:
                        sv = shape.vertices[ml.vertex_index]
                        mv = bake.object.data.vertices[ml.vertex_index]
                        if preserve_basis_normals:
                            if sv.co - mv.co > epsilon:
                                self.smd_file.write(f"{vi} {getSmdVec(sv.co)} {getSmdVec(ml.normal)}\n")
                                total_verts += 1
                        else:
                            sl = shape.loops[ml.index]
                            if sv.co - mv.co > epsilon or sl.normal - ml.normal > epsilon:
                                self.smd_file.write(f"{vi} {getSmdVec(sv.co)} {getSmdVec(sl.normal)}\n")
                                total_verts += 1
                        vi += 1

            self.smd_file.write("end\n")
            print(f"- Exported {i} flex shapes ({total_verts} verts)")

        self.smd_file.close()
        if bench.quiet:
            print(f"- {filetype.upper()} export took", bench.total(), "\n")

        written = 1
        if filetype == "smd":
            for bake in [b for b in bake_results if b.shapes]:
                written += self.writeSMD(id, bake_results, name, filepath, filetype="vta")
            for vca_name, vca in bake_results[0].vertex_animations.items():
                written += self.writeVCA(vca_name, vca, filepath)
                if vca.export_sequence:
                    written += self.writeVCASequence(vca_name, vca, filepath)
        return written

    def writeVCA(self, name, vca, filepath):
        bench = BenchMarker()
        self.smd_file = self.openSMD(filepath, name + ".vta", "vertex animation")
        if self.smd_file is None:
            return 0

        self.smd_file.write("nodes\n0 \"root\" -1\nend\nskeleton\n")
        for i in range(len(vca)):
            self.smd_file.write(f"time {i}\n0 0 0 0 0 0 0\n")
        self.smd_file.write("end\nvertexanimation\n")

        num_frames = len(vca)
        two_percent = num_frames / 50

        for frame, vca_ob in enumerate(vca):
            self.smd_file.write(f"time {frame}\n")
            self.smd_file.writelines(
                f"{loop.index} {getSmdVec(vca_ob.data.vertices[loop.vertex_index].co)} {getSmdVec(loop.normal)}\n"
                for loop in vca_ob.data.loops
            )
            if two_percent and frame % two_percent == 0:
                print(".", debug_only=True, newline=False)
                bpy.context.window_manager.progress_update(frame / num_frames)
            removeObject(vca_ob)
            vca[frame] = None

        self.smd_file.write("end\n")
        print(debug_only=True)
        print(f"Exported {num_frames} frames ({self.smd_file.tell() / 1024 / 1024:.1f}MB)")
        self.smd_file.close()
        bench.report("Vertex animation")
        return 1

    def writeVCASequence(self, name, vca, dir_path):
        self.smd_file = self.openSMD(dir_path, f"vcaanim_{name}.smd", "SMD")
        if self.smd_file is None:
            return 0

        root_bones = (
            "\n".join(f'{self.bone_ids[b.name]} "{b.name}" -1' for b in self.exportable_bones if b.parent is None)
            if self.armature_src else '0 "root" -1'
        )
        self.smd_file.write(f"nodes\n{root_bones}\n{vca.bone_id} \"vcabone_{name}\" -1\nend\nskeleton\n")

        max_frame = float(len(vca) - 1)
        for i in range(len(vca)):
            self.smd_file.write(f"time {i}\n")
            if self.armature_src:
                for rb in [b for b in self.exportable_bones if b.parent is None]:
                    mat = getUpAxisMat("Y").inverted() @ self.armature.matrix_world @ rb.matrix
                    self.smd_file.write(f"{self.bone_ids[rb.name]} {getSmdVec(mat.to_translation())} {getSmdVec(mat.to_euler())}\n")
            else:
                self.smd_file.write("0 0 0 0 {} 0 0\n".format("-1.570797" if bpy.context.scene.vs.up_axis == "Z" else "0"))
            self.smd_file.write(f"{vca.bone_id} 1.0 {getSmdFloat(i / max_frame)} 0 0 0 0\n")

        self.smd_file.write("end\n")
        self.smd_file.close()
        return 1

    # -------------------------------------------------------------------------
    # DMX writing - logic unchanged from original
    # -------------------------------------------------------------------------

    def _run_dmx_writer(self, datablock, bake_results, name, dir_path):
        writer = DmxWriter(
            self, datablock, bake_results, name, dir_path,
            armature=self.armature, armature_src=self.armature_src,
            exportable_bones=self.exportable_bones,
            exportable_boneNames=self.exportable_boneNames,
            all_bake_results=self.bake_results,
            flex_mode=getattr(self, "flex_controller_mode", "DME"),
            flex_source=getattr(self, "flex_controller_source", ""),
        )
        return writer.write()

    def writeDMX(self, datablock: bpy.types.ID, bake_results: list[BakeResult], name: str, dir_path: str):
        bench = BenchMarker(1, "DMX")
        filepath = os.path.realpath(os.path.join(dir_path, sanitize_string(name, allow_unicode=True) + ".dmx"))
        print("-", filepath)
        armature_name = self.armature_src.name if self.armature_src else name
        materials = {}
        written = 0

        dm = datamodel.DataModel("model", State.datamodelFormat)
        dm.allow_random_ids = False
        source2 = dm.format_ver >= 22

        # Source 2 supports a per-bone scale on the DmeTransform (a single uniform float).
        # Source 1 studiomdl ignores it, so only emit it for format 22+.
        export_bone_scale = source2

        def makeTransform(name, matrix, object_name):
            trfm = dm.add_element(name, "DmeTransform", id=object_name + "transform")
            trfm["position"] = datamodel.Vector3(matrix.to_translation())
            trfm["orientation"] = getDatamodelQuat(matrix.to_quaternion())
            if export_bone_scale:
                s = matrix.to_scale()
                trfm["scale"] = (s.x + s.y + s.z) / 3.0
            return trfm

        # DME prefab mode: encode jigglebones + hitboxes (and keep attachments) inside the
        # model DMX instead of writing .qci prefabs. This is a Source 1 concept only - the
        # `not source2` guard guarantees no DmeJiggleBone/hitboxSetList is ever written into
        # a format-22 (Source 2) DMX, regardless of the prefab_export_mode setting.
        dme_mode = (not source2) and prefab_mode_is_dme(bpy.context.scene)

        root = dm.add_element(bpy.context.scene.name, id="Scene" + bpy.context.scene.name)
        DmeModel = dm.add_element(armature_name, "DmeModel", id="Object" + armature_name)
        DmeModel_children = DmeModel["children"] = datamodel.make_array([], datamodel.Element)
        DmeModel["transform"] = makeTransform("", Matrix(), (DmeModel.name or "") + "transform")

        DmeModel_transforms = dm.add_element("base", "DmeTransformList", id="transforms" + bpy.context.scene.name)
        DmeModel["baseStates"] = datamodel.make_array([DmeModel_transforms], datamodel.Element)
        DmeModel_transforms["transforms"] = datamodel.make_array([], datamodel.Element)
        DmeModel_transforms = DmeModel_transforms["transforms"]

        if source2:
            DmeAxisSystem = DmeModel["axisSystem"] = dm.add_element("axisSystem", "DmeAxisSystem", "AxisSys" + armature_name)
            DmeAxisSystem["upAxis"] = axes_lookup_source2[bpy.context.scene.vs.up_axis]
            DmeAxisSystem["forwardParity"] = 1
            DmeAxisSystem["coordSys"] = 0

        keywords = getDmxKeywords(dm.format_ver)

        is_anim = bool(len(bake_results) == 1 and bake_results[0].object.type == "ARMATURE")

        if not is_anim and self.armature:
            self.armature.data.pose_position = "REST"
        elif is_anim:
            self.armature.data.pose_position = "POSE"

        if self.armature:
            if self.armature.data.vs.reset_pose_per_anim:
                if is_anim:
                    self.warnUnkeyframedPose(name)
                for pb in self.armature.pose.bones:
                    pb.matrix_basis.identity()
            elif is_anim:
                self.applyUnkeyframedSourcePose()
            bpy.context.view_layer.update()

        root["skeleton"] = DmeModel
        want_jointlist = dm.format_ver >= 11
        want_jointtransforms = dm.format_ver in range(0, 21)

        if want_jointlist:
            jointList = DmeModel["jointList"] = datamodel.make_array([], datamodel.Element)
            if source2:
                jointList.append(DmeModel)
        if want_jointtransforms:
            jointTransforms = DmeModel["jointTransforms"] = datamodel.make_array([], datamodel.Element)
            if source2:
                jointTransforms.append(DmeModel["transform"])

        bone_elements = {}
        if self.armature:
            armature_scale = self.armature.matrix_world.to_scale()

        def writeBone(bone):
            if isinstance(bone, str):
                bone_name, bone = bone, None
            else:
                if bone and bone not in self.exportable_bones:
                    children = []
                    for child_elems in [writeBone(c) for c in bone.children]:
                        if child_elems:
                            children.extend(child_elems)
                    return children
                bone_name = bone.name

            bone_exportname = self.exportable_boneNames[bone.name] if bone else bone_name
            # In DME mode a jigglebone is a skeleton joint of element type DmeJiggleBone
            # (a DmeJoint subclass); KitsuneMDL's HandleDmeJiggleBone casts each dag joint.
            # `bone` here is a PoseBone; the .vs props live on the data Bone (bone.bone).
            data_bone = bone.bone if bone is not None else None
            is_dme_jiggle = dme_mode and not is_anim and data_bone is not None and data_bone.vs.bone_is_jigglebone
            bone_elem_type = "DmeJiggleBone" if is_dme_jiggle else "DmeJoint"
            bone_elements[bone_name] = bone_elem = dm.add_element(bone_exportname, bone_elem_type, id=bone_name)
            if is_dme_jiggle:
                _jigglebone.write_dme_attrs(bone_elem, data_bone)
            if want_jointlist:
                jointList.append(bone_elem)
            self.bone_ids[bone_name] = len(bone_elements) - (0 if source2 else 1)

            if not bone:
                relMat = Matrix()
            else:
                cur_p = bone.parent
                while cur_p and cur_p not in self.exportable_bones:
                    cur_p = cur_p.parent
                if cur_p:
                    relMat = get_bone_matrix(cur_p, rest_space=True).inverted() @ bone.matrix
                else:
                    relMat = self.armature.matrix_world @ bone.matrix

            relMat = get_bone_matrix(relMat, bone, rest_space=True)
            trfm = makeTransform(bone_exportname, relMat, "bone" + bone_name)
            trfm_base = makeTransform(bone_exportname, relMat, "bone_base" + bone_name)

            if bone and bone.parent:
                for j in range(3):
                    trfm["position"][j] *= armature_scale[j]
            trfm_base["position"] = trfm["position"]

            if want_jointtransforms:
                jointTransforms.append(trfm)
            bone_elem["transform"] = trfm
            DmeModel_transforms.append(trfm_base)

            if bone:
                children = bone_elem["children"] = datamodel.make_array([], datamodel.Element)
                for child_elems in [writeBone(c) for c in bone.children]:
                    if child_elems:
                        children.extend(child_elems)
                bpy.context.window_manager.progress_update(len(bone_elements) / num_bones)
            return [bone_elem]

        if self.armature:
            num_bones = len(self.exportable_bones)
            add_implicit = not source2 and self.armature.data.vs.implicit_zero_bone
            if add_implicit:
                DmeModel_children.extend(writeBone(implicit_bone_name))
            for root_elems in [writeBone(b) for b in self.armature.pose.bones if not b.parent and not (add_implicit and b.name == implicit_bone_name)]:
                if root_elems:
                    DmeModel_children.extend(root_elems)
            bench.report("Bones")

        def _write_attach(name: str, relMat: Matrix, boneelem) -> datamodel.Element:
            dag = dm.add_element(name, "DmeDag", id=name)
            att = dm.add_element(name, "DmeAttachment", id="attachment" + name)
            att["visible"] = True
            att["isRigid"] = True
            att["isWorldAligned"] = False
            dag["shape"] = att
            dag["visible"] = True
            dag["children"] = datamodel.make_array([], datamodel.Element)

            if want_jointlist:
                jointList.append(dag)

            if "children" not in boneelem:
                boneelem["children"] = datamodel.make_array([], datamodel.Element)

            trfm  = makeTransform(name, relMat, name)
            trfm_base = makeTransform(name, relMat, "empty_base" + name)

            for j in range(3):
                trfm["position"][j] *= armature_scale[j]
            trfm_base["position"] = trfm["position"]

            dag["transform"] = trfm
            DmeModel_transforms.append(trfm_base)
            if want_jointtransforms:
                jointTransforms.append(trfm)

            boneelem["children"].append(dag)
            return dag

        def writeattachment(empty: bpy.types.Object, empty_matrix: Matrix):
            current_bone = self.armature.data.bones.get(empty.parent_bone)
            exportable_parent = None
            while current_bone:
                if current_bone.name in self.exportable_boneNames:
                    exportable_parent = self.armature.pose.bones.get(current_bone.name)
                    break
                current_bone = current_bone.parent

            if not exportable_parent:
                self.warning(f"Attachment '{empty.name}' has no exportable parent bone. Skipping.")
                return None

            pmat   = get_bone_matrix(exportable_parent, rest_space=True)
            relMat = pmat.inverted() @ empty_matrix
            return _write_attach(empty.name, relMat, bone_elements[exportable_parent.name])

        # Source 2 (.vmdl workflow) always embeds attachments in the DMX. For Source 1, they
        # are embedded only in DME mode; in QCI mode they are exported via the .qci prefab.
        embed_attachments = source2 or dme_mode
        if embed_attachments and not is_anim and self.exportable_empties and self.armature:
            for empty, world_matrix in self.exportable_empties:
                writeattachment(empty, world_matrix)
            bench.report("Empties")

        if dme_mode and not is_anim and not source2 and self.armature and self.armature_src:
            avs = getattr(self.armature_src.data, 'vs', None)
            proc_bones_list = list(getattr(avs, 'proc_bones', [])) if avs else []

            # --- LOOKAT aim targets: a zero offset aims the DmeAimAtBone directly at
            #     the driver joint (Source supports a bone target, so no attachment is
            #     needed). Only a non-zero offset gets a {base}_lookat[idx]
            #     DmeAttachment placed at lookat_offset in the driver bone's local
            #     space - parity with the QC $attachment / VRD <aimconstraint> path.
            #     Naming + dedup mirror PrefabExporter._collect_lookat_attachments so
            #     the QCI and DME paths produce the same attachment names.
            lookat_by_driver: dict[str, list[tuple]] = {}
            for entry in proc_bones_list:
                if getattr(entry, 'proc_type', 'TRIGGER') != 'LOOKAT':
                    continue
                dn = entry.driver_bone
                if not dn or dn not in bone_elements:
                    continue
                off = tuple(entry.lookat_offset)
                if off == (0.0, 0.0, 0.0):
                    continue
                lookat_by_driver.setdefault(dn, [])
                if off not in lookat_by_driver[dn]:
                    lookat_by_driver[dn].append(off)

            lookat_name_map: dict[tuple, str] = {}
            for dn, offsets in lookat_by_driver.items():
                db = self.armature_src.data.bones.get(dn)
                if not db:
                    continue
                attach_base = get_bone_exportname(db).split('.', 1)[-1]
                multiple    = len(offsets) > 1
                for idx, off in enumerate(offsets, start=1):
                    attach_name = f"{attach_base}_lookat{idx}" if multiple else f"{attach_base}_lookat"
                    lookat_name_map[(dn, off)] = attach_name
                    # relMat is driver-bone-local; _write_attach scales it by
                    # armature_scale, matching how basePos is scaled.
                    _write_attach(attach_name, Matrix.Translation(Vector(off)), bone_elements[dn])

            # --- Procedural bones: promote each helper's skeleton joint to a
            #     DmeQuatInterpBone (TRIGGER) or DmeAimAtBone (LOOKAT) and populate
            #     it. On failure the element stays a plain DmeJoint (its transform
            #     was already written during the bone walk). Uses armature_src for
            #     trigger sampling so the real drivers/constraints/action are live,
            #     matching the VRD path.
            seen_helpers: set[str] = set()
            for entry_idx, entry in enumerate(proc_bones_list):
                helper_name = entry.helper_bone
                if not helper_name or helper_name not in bone_elements:
                    continue
                if helper_name in seen_helpers:
                    self.warning(get_id('exporter_warn_procbone_duplicate', True).format(helper_name))
                    continue
                seen_helpers.add(helper_name)

                data_bone = self.armature.data.bones.get(helper_name)
                if data_bone is not None and data_bone.vs.bone_is_jigglebone:
                    self.warning(get_id('exporter_warn_procbone_jiggle_conflict', True).format(helper_name))
                    continue

                # Resolve the helper's nearest exportable (deform) ancestor. The
                # DMX skeleton parents the helper joint there, so basePos (which is
                # parent-relative) and the aim parentBone must both reference that
                # bone. If the direct parent isn't a deform bone, walk up; fall back
                # to the driver bone for an unparented helper.
                helper_db = self.armature_src.data.bones.get(helper_name)
                parent_db = helper_db.parent if helper_db else None
                while parent_db and parent_db.name not in self.exportable_boneNames:
                    parent_db = parent_db.parent
                parent_bname = parent_db.name if parent_db else entry.driver_bone

                bone_elem = bone_elements[helper_name]
                proc_type = getattr(entry, 'proc_type', 'TRIGGER')
                if proc_type == 'TRIGGER':
                    control_bone = self.exportable_boneNames.get(entry.driver_bone) if entry.driver_bone else None
                    if _proceduralbone.write_dme_quatinterp_attrs(
                            bone_elem, self.armature_src, entry, entry_idx,
                            bpy.context.scene, control_bone, armature_scale, self.warning,
                            parent_bname):
                        bone_elem.type = "DmeQuatInterpBone"
                else:
                    # Aim at the {base}_lookat attachment written above when the
                    # offset is non-zero. A zero offset (or a non-exportable driver,
                    # which has no attachment) aims directly at the driver joint by
                    # name - Source supports a bone aim target.
                    off = tuple(entry.lookat_offset)
                    aim_target = lookat_name_map.get((entry.driver_bone, off))
                    if aim_target is None:
                        aim_target = self.exportable_boneNames.get(entry.driver_bone, entry.driver_bone) if entry.driver_bone else None
                    parent_control = self.exportable_boneNames.get(parent_bname, "")
                    if _proceduralbone.write_dme_aimat_attrs(
                            bone_elem, self.armature_src, entry, aim_target,
                            armature_scale, self.warning, parent_control, parent_bname):
                        bone_elem.type = "DmeAimAtBone"

        # Hitboxes are encoded into the model DMX in DME mode (root.hitboxSetList), matching
        # KitsuneMDL's LoadHitboxSetList. In QCI mode they are written to the .qci instead.
        if dme_mode and not is_anim and self.armature and self.armature_src:
            arm_data = self.armature_src.data
            havs = getattr(arm_data, 'vs', None)
            hbox_entries = list(getattr(havs, 'hitboxes', [])) if havs else []
            valid_hbox = [e for e in hbox_entries if e.bone_name and arm_data.bones.get(e.bone_name)]
            hboxset_name = (getattr(havs, 'hboxset_name', '').strip() if havs else '') or 'default'

            if valid_hbox:
                inverted = [e.bone_name for e in valid_hbox
                            if e.scale <= 0.0 and any(e.vec_min[i] > e.vec_max[i] for i in range(3))]
                if inverted:
                    self.warning(
                        f"Hitbox min/max are inverted on {len(inverted)} box hitbox(es): Source Engine "
                        f"will invert hit registration. Swap Min and Max for: {', '.join(inverted)}")

                hbox_set_list = dm.add_element("hitboxSetList", "DmeHitboxSetList", id="hitboxSetList")
                hbox_set_list["hitboxSetList"] = datamodel.make_array([], datamodel.Element)

                hbox_set = dm.add_element(hboxset_name, "DmeHitboxSet", id="hitboxSet_" + hboxset_name)
                hbox_set["hitboxList"] = datamodel.make_array([], datamodel.Element)
                hbox_set_list["hitboxSetList"].append(hbox_set)

                for hi, e in enumerate(valid_hbox):
                    bone = arm_data.bones[e.bone_name]
                    bone_export = self.exportable_boneNames.get(e.bone_name, get_bone_exportname(bone))
                    hb = dm.add_element(bone_export, "DmeHitbox", id=f"hitbox_{hboxset_name}_{hi}_{e.bone_name}")
                    _hitbox.write_dme_attrs(hb, e, bone_export)
                    hbox_set["hitboxList"].append(hb)

                root["hitboxSetList"] = hbox_set_list
                bench.report("Hitboxes")

        for vca in bake_results[0].vertex_animations:
            DmeModel_children.extend(writeBone(f"vcabone_{vca}"))

        DmeCombinationOperator = None
        for _ in [b for b in bake_results if b.shapes]:
            if self.flex_controller_mode == "ADVANCED":
                if not hasFlexControllerSource(self.flex_controller_source):
                    self.error(get_id("exporter_err_flexctrl_undefined", True).format(name))
                    return written
                text = bpy.data.texts.get(self.flex_controller_source)
                msg = "- Loading flex controllers from "
                element_path = ["combinationOperator"]
                try:
                    if text:
                        print(msg + f"text block \"{text.name}\"")
                        controller_dm = datamodel.parse(text.as_string(), element_path=element_path)
                    else:
                        path_fc = os.path.realpath(bpy.path.abspath(self.flex_controller_source))
                        print(msg + path_fc)
                        controller_dm = datamodel.load(path=path_fc, element_path=element_path)
                    DmeCombinationOperator = controller_dm.root["combinationOperator"]
                    for elem in [e for e in DmeCombinationOperator["targets"] if e.type != "DmeFlexRules"]:
                        DmeCombinationOperator["targets"].remove(elem)
                except Exception as err:
                    self.error(get_id("exporter_err_flexctrl_loadfail", True).format(err))
                    return written
            else:
                DmeCombinationOperator = flex.DmxWriteFlexControllers.make_controllers(datablock).root["combinationOperator"]
            break

        if not DmeCombinationOperator and bake_results[0].vertex_animations:
            DmeCombinationOperator = flex.DmxWriteFlexControllers.make_controllers(datablock).root["combinationOperator"]

        if DmeCombinationOperator:
            root["combinationOperator"] = DmeCombinationOperator
            bench.report("Flex setup")

        for bake in [b for b in bake_results if b.object.type != "ARMATURE"]:
            root["model"] = DmeModel
            ob = bake.object
            assert isinstance(ob.data, bpy.types.Mesh)

            vertex_data = dm.add_element("bind", "DmeVertexData", id=bake.name + "verts")
            DmeMesh = dm.add_element(bake.name, "DmeMesh", id=bake.name + "mesh")
            DmeMesh["visible"] = True
            DmeMesh["bindState"] = vertex_data
            DmeMesh["currentState"] = vertex_data
            DmeMesh["baseStates"] = datamodel.make_array([vertex_data], datamodel.Element)

            DmeDag = dm.add_element(bake.name, "DmeDag", id="ob" + bake.name + "dag")
            if want_jointlist:
                jointList.append(DmeDag)
            DmeDag["shape"] = DmeMesh

            bone_child = isinstance(bake.envelope, str)
            if bone_child and bake.envelope in bone_elements:
                bone_elements[bake.envelope]["children"].append(DmeDag)
                trfm_mat = bake.bone_parent_matrix
            else:
                DmeModel_children.append(DmeDag)
                trfm_mat = ob.matrix_world

            trfm = makeTransform(bake.name, trfm_mat, "ob" + bake.name)
            if want_jointtransforms:
                jointTransforms.append(trfm)
            DmeDag["transform"] = trfm
            DmeModel_transforms.append(makeTransform(bake.name, trfm_mat, "ob_base" + bake.name))

            _limit_mode = getattr(bpy.context.scene.vs, 'vertex_influence_limit_mode', 'AUTO')
            _src_mt = getattr(bake.src.vs, 'mesh_type', 'DEFAULT') if bake.src else 'DEFAULT'
            if _src_mt == 'COLLISION':
                weight_link_limit = 1
            elif _src_mt == 'CLOTHPROXY':
                weight_link_limit = min(8, max(4, bpy.context.scene.vs.vertex_influence_limit))
            elif _limit_mode == 'MANUAL':
                weight_link_limit = bpy.context.scene.vs.vertex_influence_limit
            else:
                weight_link_limit = 4 if source2 else 3

            jointCount = badJointCounts = 0
            have_weightmap = False
            src_mt = getattr(bake.src.vs, 'mesh_type', 'DEFAULT') if bake.src else 'DEFAULT'
            cloth_groups = findDmxClothVertexGroups(ob) if (source2 and src_mt != 'COLLISION') else None

            if type(bake.envelope) is bpy.types.ArmatureModifier:
                ob_weights = self.getWeightmap(bake)
                for vw in ob_weights:
                    count = len(vw)
                    if weight_link_limit and count > weight_link_limit:
                        badJointCounts += 1
                    jointCount = max(jointCount, count)
                if jointCount:
                    have_weightmap = True
            elif bake.envelope:
                jointCount = 1

            if badJointCounts:
                self.warning(get_id("exporter_warn_weightlinks_excess", True).format(badJointCounts, bake.src.name, weight_link_limit))

            fmt = vertex_data["vertexFormat"] = datamodel.make_array([keywords["pos"], keywords["norm"]], str)
            vertex_data["flipVCoordinates"] = True
            vertex_data["jointCount"] = jointCount

            num_verts = len(ob.data.vertices)
            num_loops = len(ob.data.loops)
            norms = [None] * num_loops
            texco = ordered_set.OrderedSet()
            face_sets = collections.OrderedDict()
            texcoIndices = [None] * num_loops
            jointWeights = []
            jointIndices = []
            balance = [0.0] * num_verts
            cloth_weights = {}
            Indices = [-1] * num_loops

            if cloth_groups:
                for vgroup in cloth_groups:
                    cloth_weights[vgroup.name] = [0.0] * num_verts

            # Stereo flex (balance) setup
            if bake.shapes and bake.src and hasattr(bake.src, 'data') and hasattr(bake.src.data, 'vs'):
                stereo_mode = bake.src.data.vs.flex_stereo_mode
                if stereo_mode == 'VGROUP':
                    vg_name = bake.src.data.vs.flex_stereo_vg
                    if not vg_name:
                        self.warning(f"'{bake.name}': stereo mode is VGROUP but no vertex group is specified")
                    else:
                        bake.balance_vg = ob.vertex_groups.get(vg_name)
                        if bake.balance_vg is None:
                            self.warning(f"'{bake.name}': stereo vertex group '{vg_name}' not found")
                elif stereo_mode in axes_lookup:
                    axis = axes_lookup[stereo_mode]
                    sharpness = bake.src.data.vs.flex_stereo_sharpness
                    balance_width = ob.dimensions[axis] * (1 - (sharpness / 100))
                    if balance_width:
                        for _v in ob.data.vertices:
                            balance[_v.index] = max(0.0, min(1.0, (-_v.co[axis] / balance_width / 2) + 0.5))
                    bake.balance_vg = True  # sentinel: balance[] is pre-populated

            uv_layer = ob.data.uv_layers.active.data

            def remap(val, a, b, c, d):
                return (((val - a) * (d - c)) / (b - a)) + c

            bench.report("object setup")

            for v in ob.data.vertices:
                v.select = False
                if bake.shapes and bake.balance_vg:
                    if isinstance(bake.balance_vg, bpy.types.VertexGroup):
                        try:
                            balance[v.index] = bake.balance_vg.weight(v.index)
                        except Exception:
                            pass
                    # else: balance[] was pre-populated by axis-based stereo setup

                if cloth_groups:
                    for vgroup in cloth_groups:
                        try:
                            w = vgroup.weight(v.index)
                            for r in ob.vs.vertex_map_remaps:
                                if r.group == vgroup.name:
                                    w = remap(w, 0.0, 1.0, r.min, r.max)
                                    break
                            cloth_weights[vgroup.name][v.index] = w
                        except Exception:
                            for r in ob.vs.vertex_map_remaps:
                                if r.group == vgroup.name:
                                    cloth_weights[vgroup.name][v.index] = r.min
                                    break

                if have_weightmap:
                    weights_row = [0.0] * jointCount
                    indices_row = [0] * jointCount
                    total = 0
                    for i, link in enumerate(ob_weights[v.index]):
                        indices_row[i] = link[0]
                        weights_row[i] = link[1]
                        total += link[1]
                    if source2 and total == 0:
                        weights_row[0] = 1.0
                    jointWeights.extend(weights_row)
                    jointIndices.extend(indices_row)

                if v.index % 50 == 0:
                    bpy.context.window_manager.progress_update(v.index / num_verts)

            bench.report("verts")

            for loop in [ob.data.loops[i] for poly in ob.data.polygons for i in poly.loop_indices]:
                texcoIndices[loop.index] = texco.add(datamodel.Vector2(uv_layer[loop.index].uv)) # pyright: ignore
                norms[loop.index] = datamodel.Vector3(loop.normal)
                Indices[loop.index] = loop.vertex_index

            bench.report("loops")

            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.mode_set(mode="EDIT")
            bm = bmesh.from_edit_mesh(ob.data)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

            vertex_data[keywords["pos"]] = datamodel.make_array((v.co for v in bm.verts), datamodel.Vector3)
            vertex_data[keywords["pos"] + "Indices"] = datamodel.make_array((l.vert.index for f in bm.faces for l in f.loops), int)

            if source2 and src_mt != 'COLLISION':
                loops = [loop for face in bm.faces for loop in face.loops]
                loop_indices = datamodel.make_array([loop.index for loop in loops], int)
                layerGroups = bm.loops.layers

                class exportLayer:
                    def __init__(self, layer, exportName=None):
                        self._layer = layer
                        self.name = exportName or layer.name
                    def data_for(self, loop):
                        return loop[self._layer]

                def get_bmesh_layers(group):
                    return [exportLayer(l) for l in group if re.match(r".*\$[0-9]+", l.name)]

                defaultUvLayer = "texcoord$0"
                uv_layers_to_export = list(get_bmesh_layers(layerGroups.uv))
                if defaultUvLayer not in [l.name for l in uv_layers_to_export]:
                    uv_render = next((l.name for l in ob.data.uv_layers if l.active_render and l not in uv_layers_to_export), None)
                    if uv_render:
                        uv_layers_to_export.append(exportLayer(layerGroups.uv[uv_render], defaultUvLayer))
                        print(f"- Exporting '{uv_render}' as {defaultUvLayer}")
                    else:
                        self.warning(f"'{bake.name}' has no UV map named {defaultUvLayer} and no fallback was found.")

                # texcoord$1: if a 2nd UV layer exists that isn't already captured by the
                # $-naming convention, export it under the texcoord$1 attribute name.
                _second_uv_dmx = "texcoord$1"
                if _second_uv_dmx not in [l.name for l in uv_layers_to_export]:
                    _exported_uv_blender_names = {l._layer.name for l in uv_layers_to_export}
                    _second_uv = next(
                        (uv for uv in ob.data.uv_layers if uv.name not in _exported_uv_blender_names),
                        None
                    )
                    if _second_uv is not None:
                        uv_layers_to_export.append(exportLayer(layerGroups.uv[_second_uv.name], _second_uv_dmx))
                        print(f"- Exporting '{_second_uv.name}' as {_second_uv_dmx}")

                for layer in uv_layers_to_export:
                    uv_set = ordered_set.OrderedSet()
                    uv_indices = []
                    for uv in (layer.data_for(loop).uv for loop in loops):
                        uv_indices.append(uv_set.add(datamodel.Vector2(uv)))
                    vertex_data[layer.name] = datamodel.make_array(uv_set, datamodel.Vector2)
                    vertex_data[layer.name + "Indices"] = datamodel.make_array(uv_indices, int)
                    fmt.append(layer.name)

                def make_vertex_layer(layer, array_type):
                    vertex_data[layer.name] = datamodel.make_array([layer.data_for(l) for l in loops], array_type)
                    vertex_data[layer.name + "Indices"] = loop_indices
                    fmt.append(layer.name)

                # Vertex colour layers. Every colour attribute is exported, choosing its
                # Source 2 DMX stream name as follows (in priority order):
                #   1. A VertexPaint map listed in vertex_maps -> its mapped DMX name.
                #   2. The default Blender attribute named "Color" -> the primary color$0
                #      stream (so a freshly-painted mesh just works).
                #   3. Anything else -> its original layer name (expected to already carry
                #      a $N suffix for Source 2).
                # Both byte (`color`) and float (`float_color`, Blender 3.2+) layers count.
                _color_groups = [layerGroups.color]
                if hasattr(layerGroups, "float_color"):
                    _color_groups.append(layerGroups.float_color)

                _seen_color_dmx = set()
                for _color_group in _color_groups:
                    for _color_layer in _color_group:
                        _blender_name = _color_layer.name
                        if _blender_name in vertex_maps:
                            _export_name = vertex_maps[_blender_name].lower()
                        elif _blender_name.lower() == "color":
                            _export_name = "color$0"
                        else:
                            _export_name = _blender_name
                        if _export_name in _seen_color_dmx:
                            continue
                        _seen_color_dmx.add(_export_name)
                        make_vertex_layer(exportLayer(_color_layer, _export_name), datamodel.Vector4)

                for layer in get_bmesh_layers(layerGroups.float):
                    make_vertex_layer(layer, float)
                for layer in get_bmesh_layers(layerGroups.int):
                    make_vertex_layer(layer, int)
                for layer in get_bmesh_layers(layerGroups.string):
                    make_vertex_layer(layer, str)

                bench.report("Source 2 vertex data")
            else:
                fmt.append("textureCoordinates")
                vertex_data["textureCoordinates"] = datamodel.make_array(texco, datamodel.Vector2)
                vertex_data["textureCoordinatesIndices"] = datamodel.make_array(texcoIndices, int)

            if have_weightmap:
                vertex_data[keywords["weight"]] = datamodel.make_array(jointWeights, float)
                vertex_data[keywords["weight_indices"]] = datamodel.make_array(jointIndices, int)
                fmt.extend([keywords["weight"], keywords["weight_indices"]])

            deform_layer = bm.verts.layers.deform.active
            if deform_layer:
                for cloth_enable in (g for g in ob.vertex_groups if re.match(r"cloth_enable\$[0-9]+", g.name)):
                    fmt.append(cloth_enable.name)
                    values = [v[deform_layer].get(cloth_enable.index, 0) for v in bm.verts]
                    value_set = ordered_set.OrderedSet(values)
                    vertex_data[cloth_enable.name] = datamodel.make_array(value_set, float)
                    vertex_data[cloth_enable.name + "Indices"] = datamodel.make_array(
                        (value_set.index(values[i]) for i in Indices), int
                    )

            if bake.shapes and bake.balance_vg:
                vertex_data[keywords["balance"]] = datamodel.make_array(balance, float)
                vertex_data[keywords["balance"] + "Indices"] = datamodel.make_array(Indices, int)
                fmt.append(keywords["balance"])

            if cloth_groups:
                for vgroup in cloth_groups:
                    fmt.append(vgroup.name + "$0")

            vertex_data[keywords["norm"]] = datamodel.make_array(norms, datamodel.Vector3)
            vertex_data[keywords["norm"] + "Indices"] = datamodel.make_array(range(len(norms)), int)

            if cloth_groups:
                for kw in cloth_weights:
                    vertex_data[kw + "$0"] = datamodel.make_array(cloth_weights[kw], float)
                    vertex_data[kw + "$0Indices"] = datamodel.make_array(Indices, int)

            bench.report("insert")

            bad_face_mats = 0
            num_polys = len(bm.faces)
            two_percent = int(num_polys / 50)
            print("Polygons: ", debug_only=True, newline=False)

            bm_face_sets = collections.defaultdict(list)
            for p, face in enumerate(bm.faces):
                if src_mt in ('COLLISION', 'CLOTHPROXY'):
                    mat_name, mat_ok = "no_material", True
                else:
                    mat_name, mat_ok = self.GetMaterialName(ob, face.material_index)
                if not mat_ok:
                    bad_face_mats += 1
                bm_face_sets[mat_name].extend((*(l.index for l in face.loops), -1))
                if two_percent and p % two_percent == 0:
                    print(".", debug_only=True, newline=False)
                    bpy.context.window_manager.progress_update(p / num_polys)

            for mat_name, indices in bm_face_sets.items():
                material_elem = materials.get(mat_name)
                if not material_elem:
                    materials[mat_name] = material_elem = dm.add_element(mat_name, "DmeMaterial", id=mat_name + "mat")
                    matdata = ob.data.materials.get(mat_name)
                    if matdata and matdata.vs.override_dmx_export_path.strip():
                        mat_path = matdata.vs.override_dmx_export_path
                    else:
                        mat_path = bpy.context.scene.vs.material_path
                    material_elem["mtlName"] = os.path.join(mat_path, mat_name).replace("\\", "/")

                face_set = dm.add_element(mat_name, "DmeFaceSet", id=bake.name + mat_name + "faces")
                face_sets[mat_name] = face_set
                face_set["material"] = material_elem
                face_set["faces"] = datamodel.make_array(indices, int)

            print(debug_only=True)
            DmeMesh["faceSets"] = datamodel.make_array(list(face_sets.values()), datamodel.Element)

            if bad_face_mats:
                self.warning(get_id("exporter_err_facesnotex_ormat").format(bad_face_mats, bake.name))
            bench.report("polys")

            bpy.ops.object.mode_set(mode="OBJECT")
            del bm

            two_percent = int(len(bake.shapes) / 50)
            print("Shapes: ", debug_only=True, newline=False)
            delta_states = []
            corrective_shapes_seen = []

            if bake.shapes:
                shape_names = []
                num_shapes = len(bake.shapes)
                num_correctives = num_wrinkles = 0

                bake_flex_mode = getattr(getattr(bake.src, 'vs', None), 'flex_controller_mode', 'DME')
                dme_corrective_names = get_dme_corrective_delta_names(bake.src) if bake_flex_mode == 'DME' else None
                dme_delta_map = get_dme_delta_name_map(bake.src) if bake_flex_mode == 'DME' else None
                # Shape keys flagged to split into <base>L / <base>R deltas (eligible only when
                # not assigned to a flex controller). Keyed by raw shape key name.
                dme_split_map = get_dme_split_delta_map(bake.src) if bake_flex_mode == 'DME' else {}
                if dme_split_map and not bake.balance_vg:
                    self.warning(get_id("exporter_warn_dme_split_no_balance", True).format(bake.name))
                for _idx in get_dme_split_delta_conflicts(bake.src) if bake_flex_mode == 'DME' else ():
                    _ov = bake.src.vs.dme_delta_overrides[_idx]
                    self.warning(get_id("exporter_warn_dme_split_on_controller", True).format(bake.name, _ov.shapekey))

                for shape_name, shape in bake.shapes.items():
                    wrinkle_scale = 0
                    _extra_delta_names = []
                    _split_base = None  # set to the base delta name when this shape splits into <base>L/<base>R

                    if bake_flex_mode == 'DME':
                        corrective = shape_name in dme_corrective_names
                        if corrective:
                            num_correctives += 1
                        elif shape_name in dme_split_map:
                            # Split into <base>L / <base>R deltas using the mesh stereo balance.
                            _split_base = dme_split_map[shape_name]
                            shape_name = _split_base + "L"
                        elif '+' in shape_name:
                            # Compound "nameL+nameR" shape key - write one delta per component
                            parts = [dme_delta_map.get(c.strip(), sanitize_string_for_delta(c.strip())) for c in shape_name.split('+') if c.strip()]
                            shape_name = parts[0]
                            _extra_delta_names = parts[1:]
                        else:
                            shape_name = dme_delta_map.get(shape_name, sanitize_string_for_delta(shape_name))
                    else:
                        corrective = getCorrectiveShapeSeparator() in shape_name

                        if corrective:
                            driver_targets = ordered_set.OrderedSet(flex.getCorrectiveShapeKeyDrivers(bake.src.data.shape_keys.key_blocks[shape_name]) or [])
                            name_targets = ordered_set.OrderedSet(shape_name.split(getCorrectiveShapeSeparator()))
                            corrective_targets = driver_targets or name_targets
                            corrective_targets.source = shape_name

                            if corrective_targets in corrective_shapes_seen:
                                prev = next(x for x in corrective_shapes_seen if x == corrective_targets)
                                self.warning(get_id("exporter_warn_correctiveshape_duplicate", True).format(shape_name, "+".join(corrective_targets), prev.source))
                                continue
                            corrective_shapes_seen.append(corrective_targets)

                            if driver_targets and driver_targets != name_targets:
                                generated = getCorrectiveShapeSeparator().join(driver_targets)
                                print(f"- Renamed shape key '{shape_name}' to '{generated}' to match corrective drivers.")
                                shape_name = generated
                            num_correctives += 1
                        else:
                            if bake_flex_mode == "ADVANCED":
                                def _find_scale():
                                    for ctrl in controller_dm.root["combinationOperator"]["controls"]:
                                        for i in range(len(ctrl["rawControlNames"])):
                                            if ctrl["rawControlNames"][i] == shape_name:
                                                scales = ctrl.get("wrinkleScales")
                                                return scales[i] if scales else 0
                                    raise ValueError()
                                try:
                                    wrinkle_scale = _find_scale()
                                except ValueError:
                                    self.warning(get_id("exporter_err_flexctrl_missing", True).format(shape_name))

                    shape_names.append(shape_name)
                    DmeVertexDeltaData = dm.add_element(shape_name, "DmeVertexDeltaData", id=ob.name + shape_name)
                    delta_states.append(DmeVertexDeltaData)
                    vtxFmt = DmeVertexDeltaData["vertexFormat"] = datamodel.make_array([keywords["pos"], keywords["norm"]], str)

                    shape_pos, shape_posIdx = [], []
                    shape_norms, shape_normIdx = [], []
                    wrinkle, wrinkleIdx = [], []
                    cache_deltas = wrinkle_scale
                    delta_lengths = [None] * num_verts if cache_deltas else None
                    max_delta = 0

                    for ob_vert in ob.data.vertices:
                        sv = shape.vertices[ob_vert.index]
                        if ob_vert.co != sv.co:
                            delta = sv.co - ob_vert.co
                            dl = delta.length
                            if abs(dl) > 1e-5:
                                if cache_deltas:
                                    delta_lengths[ob_vert.index] = dl # pyright: ignore
                                shape_pos.append(datamodel.Vector3(delta))
                                shape_posIdx.append(ob_vert.index)

                    if corrective:
                        corrective_target_shapes = []
                        for ct_name in corrective_targets:
                            ct = bake.shapes.get(ct_name)
                            if ct:
                                corrective_target_shapes.append(ct)
                                for sv in shape.vertices:
                                    sv.co -= ob.data.vertices[sv.index].co - ct.vertices[sv.index].co
                            else:
                                self.warning(get_id("exporter_err_missing_corrective_target", format_string=True).format(shape_name, ct_name))

                    preserve_basis_normals = bake.src.data.vs.bake_shapekey_as_basis_normals
                    for ob_loop in ob.data.loops:
                        sl = shape.loops[ob_loop.index]
                        norm = ob_loop.normal if preserve_basis_normals else sl.normal
                        if corrective:
                            base = ob_loop.normal.copy()
                            for ct in corrective_target_shapes:
                                base += ct.loops[sl.index].normal - ob_loop.normal
                        else:
                            base = ob_loop.normal
                        if norm.dot(base.normalized()) < 1 - 1e-3:
                            shape_norms.append(datamodel.Vector3(norm - base))
                            shape_normIdx.append(sl.index)
                        if wrinkle_scale and delta_lengths and delta_lengths[ob_loop.vertex_index]:
                            dl = delta_lengths[ob_loop.vertex_index]
                            max_delta = max(max_delta, dl)
                            wrinkle.append(dl)
                            wrinkleIdx.append(texcoIndices[ob_loop.index])

                    if wrinkle_scale and max_delta:
                        mod = wrinkle_scale / max_delta
                        if mod != 1:
                            wrinkle = [w * mod for w in wrinkle]

                    if _split_base is not None:
                        # Split the whole delta into <base>L (scaled by 1-balance) and <base>R
                        # (scaled by balance), per the mesh stereo balance. balance[v]==1 is
                        # full-right, ==0 full-left. This is what the compiler does at compile time.
                        def _scaled(vecs, idxs, vert_of, left):
                            out_v, out_i = [], []
                            for vec, idx in zip(vecs, idxs):
                                b = balance[vert_of(idx)]
                                w = (1.0 - b) if left else b
                                if w <= 1e-6:
                                    continue
                                out_v.append(datamodel.Vector3([vec[0] * w, vec[1] * w, vec[2] * w]))
                                out_i.append(idx)
                            return out_v, out_i

                        def _emit_split(elem, left):
                            lp, lpi = _scaled(shape_pos, shape_posIdx, lambda i: i, left)
                            ln, lni = _scaled(shape_norms, shape_normIdx, lambda i: ob.data.loops[i].vertex_index, left)
                            elem[keywords["pos"]] = datamodel.make_array(lp, datamodel.Vector3)
                            elem[keywords["pos"] + "Indices"] = datamodel.make_array(lpi, int)
                            elem[keywords["norm"]] = datamodel.make_array(ln, datamodel.Vector3)
                            elem[keywords["norm"] + "Indices"] = datamodel.make_array(lni, int)

                        # DmeVertexDeltaData was created as "<base>L"; fill it with the left half.
                        _emit_split(DmeVertexDeltaData, left=True)

                        _r_name = _split_base + "R"
                        shape_names.append(_r_name)
                        _rvdd = dm.add_element(_r_name, "DmeVertexDeltaData", id=ob.name + _r_name)
                        delta_states.append(_rvdd)
                        _rvdd["vertexFormat"] = datamodel.make_array([keywords["pos"], keywords["norm"]], str)
                        _emit_split(_rvdd, left=False)
                    else:
                        DmeVertexDeltaData[keywords["pos"]] = datamodel.make_array(shape_pos, datamodel.Vector3)
                        DmeVertexDeltaData[keywords["pos"] + "Indices"] = datamodel.make_array(shape_posIdx, int)
                        DmeVertexDeltaData[keywords["norm"]] = datamodel.make_array(shape_norms, datamodel.Vector3)
                        DmeVertexDeltaData[keywords["norm"] + "Indices"] = datamodel.make_array(shape_normIdx, int)

                    if wrinkle_scale:
                        vtxFmt.append(keywords["wrinkle"])
                        num_wrinkles += 1
                        DmeVertexDeltaData[keywords["wrinkle"]] = datamodel.make_array(wrinkle, float)
                        DmeVertexDeltaData[keywords["wrinkle"] + "Indices"] = datamodel.make_array(wrinkleIdx, int)

                    for _ename in _extra_delta_names:
                        shape_names.append(_ename)
                        _evdd = dm.add_element(_ename, "DmeVertexDeltaData", id=ob.name + _ename)
                        delta_states.append(_evdd)
                        _evdd["vertexFormat"] = datamodel.make_array([keywords["pos"], keywords["norm"]], str)
                        _evdd[keywords["pos"]] = datamodel.make_array(shape_pos[:], datamodel.Vector3)
                        _evdd[keywords["pos"] + "Indices"] = datamodel.make_array(shape_posIdx[:], int)
                        _evdd[keywords["norm"]] = datamodel.make_array(shape_norms[:], datamodel.Vector3)
                        _evdd[keywords["norm"] + "Indices"] = datamodel.make_array(shape_normIdx[:], int)

                    bpy.context.window_manager.progress_update(len(shape_names) / num_shapes)
                    if two_percent and len(shape_names) % two_percent == 0:
                        print(".", debug_only=True, newline=False)

                if bpy.app.debug_value <= 1:
                    for shape in bake.shapes.values():
                        bpy.data.meshes.remove(shape)
                    bake.shapes.clear()

                print(debug_only=True)
                bench.report("shapes")
                print(f"- {num_shapes - num_correctives} flexes ({num_wrinkles} with wrinklemaps) + {num_correctives} correctives")

            vca_matrix = ob.matrix_world.inverted()
            for vca_name, vca in bake_results[0].vertex_animations.items():
                frame_shapes = []
                for i, vca_ob in enumerate(vca):
                    VDD = dm.add_element(f"{vca_name}-{i}", "DmeVertexDeltaData", id=ob.name + vca_name + str(i))
                    delta_states.append(VDD)
                    frame_shapes.append(VDD)
                    VDD["vertexFormat"] = datamodel.make_array(["positions", "normals"], str)

                    sp, spi, sn, sni = [], [], [], []
                    for sl in vca_ob.data.loops:
                        sv = vca_ob.data.vertices[sl.vertex_index]
                        ol = ob.data.loops[sl.index]
                        ov = ob.data.vertices[ol.vertex_index]
                        if ov.co != sv.co:
                            delta = vca_matrix @ sv.co - ov.co
                            if abs(delta.length) > 1e-5:
                                sp.append(datamodel.Vector3(delta))
                                spi.append(ov.index)
                        norm = Vector(sl.normal)
                        norm.rotate(vca_matrix)
                        if abs(1.0 - norm.dot(ol.normal)) > epsilon[0]:
                            sn.append(datamodel.Vector3(norm - ol.normal))
                            sni.append(sl.index)

                    VDD["positions"] = datamodel.make_array(sp, datamodel.Vector3)
                    VDD["positionsIndices"] = datamodel.make_array(spi, int)
                    VDD["normals"] = datamodel.make_array(sn, datamodel.Vector3)
                    VDD["normalsIndices"] = datamodel.make_array(sni, int)

                    removeObject(vca_ob)
                    vca[i] = None

                if vca.export_sequence:
                    vca_arm = bpy.data.objects.new("vca_arm", bpy.data.armatures.new("vca_arm"))
                    bpy.context.scene.collection.objects.link(vca_arm)
                    bpy.context.view_layer.objects.active = vca_arm
                    bpy.ops.object.mode_set(mode="EDIT")
                    vca_bone = vca_arm.data.edit_bones.new("vcabone_" + vca_name)
                    vca_bone.tail.y = 1
                    bpy.context.scene.frame_set(0)
                    mat = getUpAxisMat("y").inverted()
                    if self.armature_src:
                        for bone in [b for b in self.armature_src.data.bones if b.parent is None]:
                            b = vca_arm.data.edit_bones.new(bone.name)
                            b.head = mat @ bone.head
                            b.tail = mat @ bone.tail
                    else:
                        for bk in bake_results:
                            bm_mat = mat @ bk.object.matrix_world
                            b = vca_arm.data.edit_bones.new(bk.name)
                            b.head = bm_mat @ b.head
                            b.tail = bm_mat @ Vector([0, 1, 0])

                    bpy.ops.object.mode_set(mode="POSE")
                    ops.pose.armature_apply()

                    fcurves = channelBagForNewActionSlot(vca_arm, vca_name).fcurves

                    for ax in range(2):
                        fc = fcurves.new(f'pose.bones["vcabone_{vca_name}"].location', index=ax)
                        fc.keyframe_points.add(count=2)
                        for kp in fc.keyframe_points:
                            kp.interpolation = "LINEAR"
                        if ax == 0:
                            fc.keyframe_points[0].co = (0, 1.0)
                        fc.keyframe_points[1].co = (vca.num_frames, 1.0)
                        fc.update()

                    self._execute_task(bpy.context, vca_arm, ExportTask(vca_arm, vca_arm.name), os.path.dirname(filepath), bench)
                    written += 1

            if delta_states:
                DmeMesh["deltaStates"] = datamodel.make_array(delta_states, datamodel.Element)
                DmeMesh["deltaStateWeights"] = DmeMesh["deltaStateWeightsLagged"] = datamodel.make_array(
                    [datamodel.Vector2([0.0, 0.0])] * len(delta_states), datamodel.Vector2
                )
                if not DmeCombinationOperator:
                    raise RuntimeError("Internal error: shapes exist but no DmeCombinationOperator was created.")
                targets = DmeCombinationOperator["targets"]
                added = False
                for elem in targets:
                    if elem.type == "DmeFlexRules":
                        if elem["deltaStates"][0].name in shape_names:
                            elem["target"] = DmeMesh
                            added = True
                if not added:
                    targets.append(DmeMesh)

        if is_anim:
            ad = self.armature.animation_data
            # first_frame offsets sampling so actions that don't start on frame 0 export
            # their real motion; the DmeChannelsClip timeline stays 0-based (keyframe_time
            # uses the loop index, not the scene frame). See animationFrameRange.
            first_frame, anim_len = animationFrameRange(ad) if ad else (0, 0)
            fps = bpy.context.scene.render.fps * bpy.context.scene.render.fps_base

            DmeChannelsClip = dm.add_element(name, "DmeChannelsClip", id=name + "clip")
            DmeAnimationList = dm.add_element(armature_name, "DmeAnimationList", id=armature_name + "list")
            DmeAnimationList["animations"] = datamodel.make_array([DmeChannelsClip], datamodel.Element)
            root["animationList"] = DmeAnimationList

            DmeTimeFrame = dm.add_element("timeframe", "DmeTimeFrame", id=name + "time")
            duration = anim_len / fps
            if dm.format_ver >= 11:
                DmeTimeFrame["duration"] = datamodel.Time(duration)
            else:
                DmeTimeFrame["durationTime"] = int(duration * 10000)
            DmeTimeFrame["scale"] = 1.0
            DmeChannelsClip["timeFrame"] = DmeTimeFrame
            DmeChannelsClip["frameRate"] = fps if source2 else int(fps)

            channels = DmeChannelsClip["channels"] = datamodel.make_array([], datamodel.Element)
            bone_channels = {}

            channel_template = [
                ("_p", "position", "Vector3", datamodel.Vector3),
                ("_o", "orientation", "Quaternion", datamodel.Quaternion),
            ]
            if export_bone_scale:
                channel_template.append(("_s", "scale", "Float", float))

            def makeChannel(bone):
                export_name = self.exportable_boneNames[bone.name]
                bone_channels[bone.name] = []
                for suffix, attr, type_name, dm_type in channel_template:
                    ch_name = export_name + suffix
                    cur = dm.add_element(ch_name, "DmeChannel", id=bone.name + suffix)
                    cur["toAttribute"] = attr
                    cur["toElement"] = (bone_elements[bone.name] if bone else DmeModel)["transform"]
                    cur["mode"] = 1
                    if attr == "scale":
                        # scale is a single float on the transform, not an indexed vector component
                        cur["fromIndex"] = 0
                        cur["toIndex"] = 0
                    layer = dm.add_element(type_name + " log", f"Dme{type_name}LogLayer", ch_name + "loglayer")
                    cur["log"] = dm.add_element(type_name + " log", f"Dme{type_name}Log", ch_name + "log")
                    cur["log"]["layers"] = datamodel.make_array([layer], datamodel.Element)
                    layer["times"] = datamodel.make_array([], datamodel.Time if dm.format_ver > 11 else int)
                    layer["values"] = datamodel.make_array([], dm_type)
                    if bone:
                        bone_channels[bone.name].append(layer)
                    channels.append(cur)

            for bone in self.exportable_bones:
                makeChannel(bone)

            num_frames = int(anim_len + 1)
            bench.report("Animation setup")
            two_percent = num_frames / 50
            print("Frames: ", debug_only=True, newline=False)

            for frame in range(num_frames):
                bpy.context.window_manager.progress_update(frame / num_frames)
                bpy.context.scene.frame_set(first_frame + frame)
                keyframe_time = datamodel.Time(frame / fps) if dm.format_ver > 11 else int(frame / fps * 10000)
                evaluated = self.getEvaluatedPoseBones()

                for bone in evaluated:
                    channel = bone_channels[bone.name]
                    cur_p = bone.parent
                    while cur_p and cur_p not in evaluated:
                        cur_p = cur_p.parent
                    if cur_p:
                        relMat = get_bone_matrix(cur_p).inverted() @ bone.matrix
                    else:
                        relMat = self.armature.matrix_world @ bone.matrix
                    relMat = get_bone_matrix(relMat, bone)

                    pos = relMat.to_translation()
                    if bone.parent:
                        for j in range(3):
                            pos[j] *= armature_scale[j]

                    channel[0]["times"].append(keyframe_time)
                    channel[0]["values"].append(datamodel.Vector3(pos))
                    channel[1]["times"].append(keyframe_time)
                    channel[1]["values"].append(getDatamodelQuat(relMat.to_quaternion()))
                    if export_bone_scale:
                        s = relMat.to_scale()
                        channel[2]["times"].append(keyframe_time)
                        channel[2]["values"].append((s.x + s.y + s.z) / 3.0)

                if two_percent and frame % two_percent:
                    print(".", debug_only=True, newline=False)

            print(debug_only=True)

        bpy.context.window_manager.progress_update(0.99)
        print("- Writing DMX...")
        try:
            if State.use_kv2:
                dm.write(filepath, "keyvalues2", 1)
            else:
                dm.write(filepath, "binary", State.datamodelEncoding)
            written += 1
        except (PermissionError, FileNotFoundError) as err:
            self.error(get_id("exporter_err_open", True).format("DMX", err))

        bench.report("write")
        if bench.quiet:
            print("- DMX export took", bench.total(), "\n")

        return written


def _s2_prefab_bonename(bone) -> str:
    # I don't know if ValveBiped. is only stripped or it applies to any with . separator
    # TODO: Confirm.
    name = get_bone_exportname(bone)
    prefix = "ValveBiped."
    return name[len(prefix):] if name.startswith(prefix) else name


# Default output filename suffix per prefab type. The full default name is
# "<armature name>_<suffix><ext>".
PREFAB_FILENAME_SUFFIX = {
    'JIGGLEBONES':   'jigglebones',
    'ATTACHMENTS':   'attachments',
    'HITBOXES':      'hitbox',
    'PROCEDURAL':    'procedural',
}

_PREFAB_EXTENSIONS = {'.qc', '.qci', '.vmdl', '.vmdl_prefab', '.vrd'}


def _prefab_extension(prefab_type: str) -> str:
    """File extension for a prefab type: .vrd for procedural (Source 1 only),
    otherwise .vmdl for Source 2 (ModelDoc) and .qci for Source 1."""
    if prefab_type == 'PROCEDURAL':
        return '.vrd'
    return '.vmdl' if State.compiler == Compiler.MODELDOC else '.qci'


def _prefab_format_from_ext(ext: str) -> str | None:
    ext = ext.lower()
    if ext in {'.qc', '.qci'}:
        return 'QC'
    if ext in {'.vmdl', '.vmdl_prefab'}:
        return 'VMDL'
    if ext == '.vrd':
        return 'VRD'
    return None


def resolve_prefab_output(arm: bpy.types.Object, prefab_type: str, scene) -> tuple[str, str] | None:
    """Resolve the output path and format for an armature's prefab.

    The path comes from the matching PrefabItem.filepath:
      - blank            -> "<export_path>/<armature>_<suffix><ext>"
      - a directory      -> "<that dir>/<armature>_<suffix><ext>"
      - a full file path -> used as-is (relative paths resolve against export_path)
    Relative paths are taken relative to the scene export path; "//" and absolute
    paths resolve normally. Returns (abs_path, fmt) or None if unresolvable.
    """
    suffix = PREFAB_FILENAME_SUFFIX[prefab_type]
    ext = _prefab_extension(prefab_type)
    default_name = f"{sanitize_string(arm.name, allow_unicode=True)}_{suffix}{ext}"

    raw = ''
    avs = getattr(arm.data, 'vs', None)
    if avs is not None:
        for p in avs.prefab_items:
            if p.prefab_type == prefab_type:
                raw = (p.filepath or '').strip()
                break

    base_dir = bpy.path.abspath(scene.vs.export_path) if scene.vs.export_path else ''

    if not raw:
        if not base_dir:
            return None
        full = os.path.join(base_dir, default_name)
    else:
        raw_norm = raw.replace('\\', '/')
        if raw_norm.startswith('//') or os.path.isabs(raw_norm):
            expanded = bpy.path.abspath(raw_norm)
        elif base_dir:
            expanded = os.path.join(base_dir, raw_norm)
        else:
            expanded = bpy.path.abspath(raw_norm)

        if os.path.splitext(expanded)[1].lower() in _PREFAB_EXTENSIONS:
            full = expanded
        else:
            full = os.path.join(expanded, default_name)

    full = os.path.normpath(full)
    fmt = _prefab_format_from_ext(os.path.splitext(full)[1])
    if fmt is None:
        return None
    return full, fmt


class PrefabExporter(bpy.types.Operator, ExportCheck):
    bl_idname = "smd.export_prefab"
    bl_label = "Export Prefab"

    export_type: bpy.props.EnumProperty(
        items=[
            ('JIGGLEBONES',   "Jigglebones",   ""),
            ('ATTACHMENTS',   "Attachments",   ""),
            ('HITBOXES',      "Hitboxes",      ""),
            ('PROCEDURAL',    "Procedural",    ""),
        ]
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and get_armature(context.active_object) is not None
    
    def _write_output(self, compiled, export_path=None, warnings=None):
        if not compiled:
            return False

        if self.to_clipboard:
            bpy.context.window_manager.clipboard = compiled
            self.report({'INFO'}, "Data copied to clipboard")
            return True

        if not export_path:
            self.report({'ERROR'}, "No export path provided")
            return False

        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(compiled)

        if warnings:
            self.report({'WARNING'}, f"Exported with {len(warnings)} warning(s) (see console)")
            for w in warnings:
                print(w)
        else:
            self.report({'INFO'}, f"Data exported to {export_path}")
        return True

    def execute(self, context) -> set:
        jiggle_was_enabled = context.scene.vs.jiggle_sim_enabled
        if jiggle_was_enabled:
            context.scene.vs.jiggle_sim_enabled = False

        ops.ed.undo_push(message=self.bl_label)
        try:
            for view_layer in bpy.context.scene.view_layers:
                    unhide_all(view_layer.layer_collection)

            bpy.context.view_layer.update()

            arm = get_armature(context.active_object)
            self.to_clipboard = context.scene.vs.prefab_to_clipboard

            bone_names = {bone.name: get_bone_exportname(bone) for bone in arm.data.bones}
            if not self.check_duplicate_bone_names(bone_names):
                return {'CANCELLED'}

            export_path = None
            fmt = None

            # In DME mode these prefabs are embedded into the model DMX, not written to .qci.
            # Block file export (clipboard copy of the QC text stays allowed for convenience).
            if (not self.to_clipboard and prefab_mode_is_dme(context.scene)
                    and self.export_type in ('JIGGLEBONES', 'ATTACHMENTS', 'HITBOXES', 'PROCEDURAL')):
                self.report({'ERROR'},
                    f"{self.export_type.title()} are embedded into the model DMX in DME mode. "
                    f"Export the model instead, or switch Prefab Mode to QCI.")
                return {'CANCELLED'}

            if not self.to_clipboard:
                resolved = resolve_prefab_output(arm, self.export_type, context.scene)
                if resolved is None:
                    self.report({'ERROR'}, "Could not resolve prefab output path. Set a Scene export path or a prefab filepath.")
                    return {'CANCELLED'}
                export_path, fmt = resolved

            warnings = None
            if self.export_type == 'JIGGLEBONES':
                compiled = self._run_jigglebones(arm, fmt, export_path)
            elif self.export_type == 'ATTACHMENTS':
                compiled = self._run_attachments(arm, fmt, export_path, context)
            elif self.export_type == 'HITBOXES':
                compiled, warnings = self._run_hitboxes(arm, fmt, export_path)
            elif self.export_type == 'PROCEDURAL':
                compiled = self._run_procedural(arm, context)
            else:
                return {'CANCELLED'}

            if compiled is None:
                return {'CANCELLED'}

            if not self._write_output(compiled, export_path, warnings):
                return {'CANCELLED'}
        finally:
            ops.ed.undo_push(message=self.bl_label)
            if bpy.app.debug_value <= 1: ops.ed.undo()
            if jiggle_was_enabled:
                context.scene.vs.jiggle_sim_enabled = True

        return {'FINISHED'}

    # Jigglebones

    def _run_jigglebones(self, arm, fmt, export_path):
        jigglebones = [b for b in arm.data.bones if b.vs.bone_is_jigglebone]
        if not jigglebones:
            self.report({'WARNING'}, "No jigglebones found")
            return None

        collection_groups = {}
        for bone in jigglebones:
            group_name = bone.collections[0].name if bone.collections else "Others"
            collection_groups.setdefault(group_name, []).append(bone)

        if self.to_clipboard:
            return self._jigglebones_vmdl(collection_groups, None) if State.compiler == Compiler.MODELDOC else self._jigglebones_qc(collection_groups)
        if fmt == 'QC':
            return self._jigglebones_qc(collection_groups)
        if fmt == 'VMDL':
            return self._jigglebones_vmdl(collection_groups, export_path)
        return None

    def _jigglebones_qc(self, collection_groups):
        entries = []
        for group_name, group_bones in collection_groups.items():
            entries.append(f"// Jigglebones: {group_name}")
            entries.append("")
            for bone in group_bones:
                entries.append("\n".join(_jigglebone.qc_block_lines(bone)))
        return "\n".join(entries)

    def _jigglebones_vmdl(self, collection_groups, export_path):
        folder_nodes = []
        for group_name, group_bones in collection_groups.items():
            folder = KVNode(_class="Folder", name=sanitize_string(group_name))
            for bone in group_bones:
                s2name = _s2_prefab_bonename(bone)
                jiggle_length = bone.length if bone.vs.use_bone_length_for_jigglebone_length else bone.vs.jiggle_length
                folder.add_child(KVNode(
                    _class="JiggleBone",
                    name=f"JiggleBone_{s2name}",
                    **_jigglebone.kv3_kwargs(bone.vs, s2name, jiggle_length),
                ))
            folder_nodes.append(folder)

        kv_doc = update_vmdl_container(
            container_class="JiggleBoneList" if not self.to_clipboard else "ScratchArea",
            nodes=folder_nodes,
            export_path=export_path,
            to_clipboard=self.to_clipboard
        )
        if kv_doc is False:
            self.report({"WARNING"}, 'Existing file may not be a valid KeyValues3')
            return None
        return kv_doc.to_text()

    # Attachments

    @staticmethod
    def _collect_lookat_attachments(arm) -> list[tuple]:
        avs = getattr(arm.data, 'vs', None)
        if not avs:
            return []
        lookat_by_driver: dict[str, list[tuple]] = {}
        for entry in getattr(avs, 'proc_bones', []):
            if getattr(entry, 'proc_type', 'TRIGGER') != 'LOOKAT':
                continue
            driver_name = entry.driver_bone
            if not driver_name or not arm.data.bones.get(driver_name):
                continue
            off = tuple(entry.lookat_offset)
            lookat_by_driver.setdefault(driver_name, [])
            if off not in lookat_by_driver[driver_name]:
                lookat_by_driver[driver_name].append(off)

        result = []
        for driver_name, offsets in lookat_by_driver.items():
            driver_export = get_bone_exportname(arm.data.bones[driver_name])
            attach_base   = driver_export.split('.', 1)[-1]
            multiple      = len(offsets) > 1
            for idx, off in enumerate(offsets, start=1):
                attach_name = f"{attach_base}_lookat{idx}" if multiple else f"{attach_base}_lookat"
                result.append((attach_name, driver_name, off))
        return result

    def _run_attachments(self, arm, fmt, export_path, context):
        attachments = get_attachments(arm)

        is_qc = (fmt == 'QC') or (self.to_clipboard and State.compiler != Compiler.MODELDOC)
        lookat_attachments = self._collect_lookat_attachments(arm) if is_qc else []

        if not attachments and not lookat_attachments:
            self.report({'WARNING'}, "No attachments found")
            return None

        if self.to_clipboard:
            if State.compiler == Compiler.MODELDOC:
                return self._attachments_vmdl(arm, attachments, None)
            return self._attachments_qc(arm, attachments, lookat_attachments)
        if fmt == 'QC':
            return self._attachments_qc(arm, attachments, lookat_attachments)
        if fmt == 'VMDL':
            return self._attachments_vmdl(arm, attachments, export_path)
        return None

    def _attachments_qc(self, arm, attachments, lookat_attachments=()):
        lines = []
        for empty in attachments:
            if not empty.parent_bone:
                continue
            bone = arm.data.bones.get(empty.parent_bone)
            if not bone:
                continue
            pose_bone = arm.pose.bones.get(empty.parent_bone)
            if not pose_bone:
                continue
            pmat = get_bone_matrix(pose_bone, rest_space=True)
            relMat = pmat.inverted() @ empty.matrix_world
            position = relMat.to_translation()
            rotation = relMat.to_quaternion().to_euler('XYZ')
            lines.append(f'$attachment "{empty.name}" "{get_bone_exportname(bone)}" {position.x:.2f} {position.y:.2f} {position.z:.2f} rotate {math.degrees(rotation.y):.0f} {math.degrees(rotation.z):.0f} {math.degrees(rotation.x):.0f}')
        for attach_name, driver_name, off in lookat_attachments:
            bone = arm.data.bones.get(driver_name)
            if not bone:
                continue
            lines.append(f'$attachment "{attach_name}" "{get_bone_exportname(bone)}" {off[0]:.6f} {off[1]:.6f} {off[2]:.6f} rotate 0 0 0')
        return '\n'.join(lines)

    def _attachments_vmdl(self, arm, attachments, export_path):
        nodes = []
        for empty in attachments:
            if not empty.parent_bone:
                continue
            bone = arm.data.bones.get(empty.parent_bone)
            if not bone:
                continue
            pose_bone = arm.pose.bones.get(empty.parent_bone)
            if not pose_bone:
                continue
            pmat = get_bone_matrix(pose_bone, rest_space=True)
            relMat = pmat.inverted() @ empty.matrix_world
            position = relMat.translation
            rotation = relMat.to_euler('YZX')
            nodes.append(KVNode(
                _class="Attachment",
                name=empty.name,
                parent_bone=_s2_prefab_bonename(bone),
                relative_origin=KVVector3(position.x, position.y, position.z),
                relative_angles=KVVector3(math.degrees(rotation.y), math.degrees(rotation.z), math.degrees(rotation.x)),
                weight=1.0,
                ignore_rotation=KVBool(False)
            ))

        kv_doc = update_vmdl_container(
            container_class="ScratchArea" if self.to_clipboard else "AttachmentList",
            nodes=nodes,
            export_path=export_path,
            to_clipboard=self.to_clipboard
        )
        if kv_doc is False:
            self.report({"WARNING"}, 'Existing file may not be a valid KeyValues3')
            return None
        return kv_doc.to_text()

    # Hitboxes

    def _run_hitboxes(self, arm, fmt=None, export_path=None):
        avs = getattr(arm.data, 'vs', None)
        entries = list(getattr(avs, 'hitboxes', [])) if avs else []
        valid = [e for e in entries if e.bone_name and arm.data.bones.get(e.bone_name)]

        if not valid:
            self.report({'WARNING'}, "No hitboxes found")
            return None, None

        hboxset = getattr(avs, 'hboxset_name', '').strip() or 'default'

        if self.to_clipboard:
            use_vmdl = (State.compiler == Compiler.MODELDOC)
        else:
            use_vmdl = (fmt == 'VMDL')

        if use_vmdl:
            return self._hitboxes_vmdl(arm, valid, hboxset, export_path)
        return self._hitboxes_qc(arm, valid, hboxset)

    def _hitboxes_qc(self, arm, valid, hboxset):
        avs = getattr(arm.data, 'vs', None)
        bones_for_sort = []
        seen_bones = {}
        for e in valid:
            bone = arm.data.bones[e.bone_name]
            if bone not in seen_bones:
                bones_for_sort.append(bone)
                seen_bones[bone] = []
            seen_bones[bone].append(e)

        inverted = [e.bone_name for e in valid
                    if e.scale <= 0.0 and any(e.vec_min[i] > e.vec_max[i] for i in range(3))]
        if inverted:
            self.report({'WARNING'},
                f"Hitbox min/max are inverted on {len(inverted)} box hitbox(es) : Source Engine will "
                f"invert hit registration. Swap Min and Max for: {', '.join(inverted)}")

        sorted_bones = sort_bone_by_hierarchy(bones_for_sort)

        lines = []
        lines.append(f'$hboxset\t"{hboxset}"')
        for bone in sorted_bones:
            for e in seen_bones[bone]:
                lines.append(_hitbox.qc_line(e, get_bone_exportname(bone)))
        lines.append('$skipboneinbbox')

        return '\n'.join(lines), None

    def _hitboxes_vmdl(self, arm, valid, hboxset, export_path):
        # Source 2 / ModelDoc only supports capsule hitboxes. A hitbox is a capsule
        # when its scale (capsule radius) is > 0; scale <= 0 means an oriented box.
        capsules = [e for e in valid if e.scale > 0.0]
        boxes    = [e for e in valid if e.scale <= 0.0]

        if boxes:
            bnames = ', '.join(sorted({e.bone_name for e in boxes}))
            self.report({'WARNING'},
                f"Source 2 hitboxes only support capsules : skipping {len(boxes)} box hitbox(es) "
                f"(bones: {bnames}). Give them a capsule radius (scale > 0) to export them.")

        if not capsules:
            self.report({'WARNING'},
                "No capsule hitboxes to export (Source 2 supports capsules only)")
            return None, None

        bones_for_sort = []
        seen_bones = {}
        for e in capsules:
            bone = arm.data.bones[e.bone_name]
            if bone not in seen_bones:
                bones_for_sort.append(bone)
                seen_bones[bone] = []
            seen_bones[bone].append(e)
        sorted_bones = sort_bone_by_hierarchy(bones_for_sort)

        hbset_node = KVNode(_class="HitboxSet", name=sanitize_string(hboxset))
        for bone in sorted_bones:
            for e in seen_bones[bone]:
                hbset_node.add_child(KVNode(
                    _class="HitboxCapsule",
                    **_hitbox.kv3_capsule_kwargs(e, _s2_prefab_bonename(bone)),
                ))

        # update_vmdl_container matches the HitboxSet by name inside HitboxSetList and
        # replaces its children, so an existing set with this name is overwritten in full.
        kv_doc = update_vmdl_container(
            container_class="HitboxSetList" if not self.to_clipboard else "ScratchArea",
            nodes=hbset_node,
            export_path=export_path,
            to_clipboard=self.to_clipboard,
        )
        if kv_doc is False:
            self.report({"WARNING"}, 'Existing file may not be a valid KeyValues3')
            return None, None
        return kv_doc.to_text(), None

    # Procedural VRD

    def _run_procedural(self, arm, context):
        avs = getattr(arm.data, 'vs', None)
        entries = list(getattr(avs, 'proc_bones', [])) if avs else []
        valid = [e for e in entries if e.helper_bone and arm.data.bones.get(e.helper_bone)]
        if not valid:
            self.report({'WARNING'}, "No procedural bone entries found")
            return None
        return self._write_proc_vrd(arm, valid, context.scene)


    def _write_proc_vrd(self, arm, entries, scene):
        scale = scene.vs.world_scale * arm.matrix_world.to_scale().x

        # axes / export-offset / trigger-transform math is shared with the DME
        # writer (prefab_io.proceduralbone) so the VRD and DME paths never drift.
        def _axes_to_vec(axes):
            return _proceduralbone.axes_to_vec(axes)

        def _vrd_name(bone):
            return get_bone_exportname(bone).split('.', 1)[-1]

        def _basepos(helper_name, parent_name):
            pos = _proceduralbone.basepos_local(arm, helper_name, parent_name)
            return pos.x * scale, pos.y * scale, pos.z * scale

        def _driver_parent_vrd(driver_bone_name):
            db = arm.data.bones.get(driver_bone_name)
            if db and db.parent:
                return _vrd_name(db.parent)
            return _vrd_name(arm.data.bones[driver_bone_name]) if db else driver_bone_name.split('.', 1)[-1]

        # Build lookat attachment name map (same deduplication as _collect_lookat_attachments)
        lookat_by_driver: dict[str, list[tuple]] = {}
        for entry in entries:
            if getattr(entry, 'proc_type', 'TRIGGER') != 'LOOKAT':
                continue
            dn = entry.driver_bone
            if not dn or not arm.data.bones.get(dn):
                continue
            off = tuple(entry.lookat_offset)
            lookat_by_driver.setdefault(dn, [])
            if off not in lookat_by_driver[dn]:
                lookat_by_driver[dn].append(off)
        lookat_name_map: dict[tuple, str] = {}
        for dn, offsets in lookat_by_driver.items():
            attach_base = get_bone_exportname(arm.data.bones[dn]).split('.', 1)[-1]
            multiple = len(offsets) > 1
            for idx, off in enumerate(offsets, start=1):
                lookat_name_map[(dn, off)] = f"{attach_base}_lookat{idx}" if multiple else f"{attach_base}_lookat"

        # studiomdl's .vrd compiler treats the text before the first '.' in a bone
        # name as a prefix and strips it ("ValveBiped.Bip01" -> "Bip01"). That is
        # intended for real prefixes like "ValveBiped.", but an accidental dot in a
        # bone name silently drops part of the name. Only the Source 1 .vrd path is
        # affected - DME prefab, Source 2 and newer studiomdl/PulseMDL don't strip.
        preserved = tuple(p.lower() for p in get_preserved_bone_prefixes())
        warned_dotnames: set[str] = set()
        for entry in entries:
            for bname in (entry.helper_bone, entry.driver_bone):
                bone = arm.data.bones.get(bname) if bname else None
                if not bone:
                    continue
                export_name = get_bone_exportname(bone)
                if '.' not in export_name or export_name in warned_dotnames:
                    continue
                if export_name.lower().startswith(preserved):
                    continue
                warned_dotnames.add(export_name)
                self.report(
                    {'WARNING'},
                    f"Procedural bone name '{export_name}' contains a '.'"
                )

        lines: list[str] = []

        for entry_idx, entry in enumerate(entries):
            proc_type   = getattr(entry, 'proc_type', 'TRIGGER')
            helper_name = entry.helper_bone
            driver_name = entry.driver_bone

            if not driver_name or not arm.data.bones.get(driver_name):
                continue

            helper_bone = arm.data.bones[helper_name]
            driver_bone = arm.data.bones[driver_name]
            helper_vrd  = _vrd_name(helper_bone)
            driver_vrd  = _vrd_name(driver_bone)

            if helper_bone.parent:
                parent_name = helper_bone.parent.name
                parent_vrd  = _vrd_name(helper_bone.parent)
            else:
                parent_name = driver_name
                parent_vrd  = driver_vrd

            bx, by, bz = _basepos(helper_name, parent_name)

            if proc_type == 'TRIGGER':
                drv_parent_vrd = _driver_parent_vrd(driver_name)
                lines.append(f'<helper>  {helper_vrd}  {parent_vrd}  {drv_parent_vrd}  {driver_vrd}')
                lines.append(f'<basepos>  {bx:.6f} {by:.6f} {bz:.6f}')

                if not entry.action:
                    self.report({'WARNING'}, f"Procedural entry '{helper_name}' has no action; skipping triggers")
                    lines.append('')
                    continue

                # Shared per-trigger transform build (also used by the DME writer).
                # Returns absolute local (d_mat, h_export) plus the raw (dq, dloc)
                # kept for the near-duplicate warning below.
                transforms = _proceduralbone.build_trigger_transforms(arm, entry, entry_idx, scene)
                if not transforms:
                    lines.append('')
                    continue

                # Warn when two triggers share a nearly-identical driver state.
                # Both rotation and location are checked: purely positional drivers
                # will have near-zero rotation on every trigger, so the position
                # distance is needed to avoid false positives in that case.
                # VRD only uses rotation for trigger selection, so two triggers that
                # are close in rotation AND location are genuinely indistinguishable.
                NEAR_TRIGGER_DEG  = 1.0
                NEAR_TRIGGER_DIST = 0.001
                for _ti in range(len(transforms)):
                    for _tj in range(_ti + 1, len(transforms)):
                        _dq_i,   _dloc_i = transforms[_ti][3], transforms[_ti][4]
                        _dq_j,   _dloc_j = transforms[_tj][3], transforms[_tj][4]
                        _dot   = abs(_dq_i.dot(_dq_j))
                        _angle = degrees(2.0 * acos(min(_dot, 1.0)))
                        _pdist = (_dloc_i - _dloc_j).length
                        if _angle < NEAR_TRIGGER_DEG and _pdist < NEAR_TRIGGER_DIST:
                            self.report(
                                {'WARNING'},
                                f"Procedural bone '{helper_name}' (driver '{driver_name}'): "
                                f"triggers {_ti} and {_tj} have nearly identical driver "
                                f"state (rotation {_angle:.3f}°, position {_pdist:.5f} apart)"
                                f"- VRD may not distinguish them."
                            )

                for d_mat, h_export, tol, _dq, _dloc in transforms:
                    tol_deg = degrees(tol)

                    d_euler = d_mat.to_euler('XYZ')
                    drx, dry, drz = degrees(d_euler.x), degrees(d_euler.y), degrees(d_euler.z)

                    h_pos    = h_export.to_translation()
                    h_euler  = h_export.to_euler('XYZ')

                    hpx = h_pos.x * scale
                    hpy = h_pos.y * scale
                    hpz = h_pos.z * scale
                    hrx, hry, hrz = degrees(h_euler.x), degrees(h_euler.y), degrees(h_euler.z)

                    lines.append(f'<trigger>  {tol_deg:.4f}  {drx:.6f} {dry:.6f} {drz:.6f}  {hrx:.6f} {hry:.6f} {hrz:.6f}  {hpx:.6f} {hpy:.6f} {hpz:.6f}')

                lines.append('')

            elif proc_type == 'LOOKAT':
                off           = tuple(entry.lookat_offset)
                target_attach = lookat_name_map.get((driver_name, off))
                if not target_attach:
                    continue

                aim = _axes_to_vec(entry.lookat_aim_axis)
                up  = _axes_to_vec(entry.lookat_up_axis)

                lines.append(f'<aimconstraint>  {helper_vrd}  {parent_vrd}  {target_attach}')
                lines.append(f'<basepos>  {bx:.6f} {by:.6f} {bz:.6f}')
                lines.append(f'<aimvector>  {aim[0]:.6f} {aim[1]:.6f} {aim[2]:.6f}')
                lines.append(f'<upvector>  {up[0]:.6f} {up[1]:.6f} {up[2]:.6f}')
                lines.append('')

        return '\n'.join(lines)

# -----------------------------------------------------------------------------
# Adapter used by SmdExporter._auto_export_prefabs_for_armature to invoke
# PrefabExporter logic without needing a live Blender operator instance.
# All PrefabExporter methods are rebound here so internal self.* calls resolve.
# -----------------------------------------------------------------------------

class _PrefabRunnerAdapter(ExportCheck):
    def __init__(self, reporter):
        self.to_clipboard = False
        self._report_fn = reporter

    def report(self, level, msg):
        self._report_fn(level, msg)

    _write_output               = PrefabExporter._write_output
    _run_jigglebones            = PrefabExporter._run_jigglebones
    _jigglebones_qc             = PrefabExporter._jigglebones_qc
    _jigglebones_vmdl           = PrefabExporter._jigglebones_vmdl
    _run_attachments            = PrefabExporter._run_attachments
    _attachments_qc             = PrefabExporter._attachments_qc
    _attachments_vmdl           = PrefabExporter._attachments_vmdl
    _run_hitboxes               = PrefabExporter._run_hitboxes
    _hitboxes_qc                = PrefabExporter._hitboxes_qc
    _hitboxes_vmdl              = PrefabExporter._hitboxes_vmdl
    _run_procedural             = PrefabExporter._run_procedural
    _write_proc_vrd             = PrefabExporter._write_proc_vrd
    _collect_lookat_attachments = staticmethod(PrefabExporter._collect_lookat_attachments)