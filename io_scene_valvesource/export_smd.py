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
                     Baker, ExportPlanner, DmxWriter, SmdWriter)


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
        write_func = self._run_dmx_writer if State.exportFormat == ExportFormat.DMX else self._run_smd_writer
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



    def getTopParent(self, id: bpy.types.Object) -> bpy.types.Object:
        top = id
        while top.parent:
            top = top.parent
        return top


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






    def _run_dmx_writer(self, datablock, bake_results, name, dir_path):
        writer = DmxWriter(
            self, datablock, bake_results, name, dir_path,
            armature=self.armature, armature_src=self.armature_src,
            exportable_bones=self.exportable_bones,
            exportable_boneNames=self.exportable_boneNames,
            exportable_empties=self.exportable_empties,
            all_bake_results=self.bake_results,
            flex_mode=getattr(self, "flex_controller_mode", "DME"),
            flex_source=getattr(self, "flex_controller_source", ""),
        )
        return writer.write()

    def _run_smd_writer(self, id, bake_results, name, dir_path):
        writer = SmdWriter(
            self, id, bake_results, name, dir_path,
            armature=self.armature, armature_src=self.armature_src,
            exportable_bones=self.exportable_bones,
            exportable_boneNames=self.exportable_boneNames,
            all_bake_results=self.bake_results,
        )
        return writer.write()


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