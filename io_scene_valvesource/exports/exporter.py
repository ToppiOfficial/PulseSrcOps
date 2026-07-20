import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from bpy.app.translations import pgettext
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from ..keyvalues3 import *
from .. import datamodel, ordered_set, flex
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone

from .check import ExportCheck
from .records import BakedVertexAnimation, BakeResult, ExportTask, _SplitPart, _MeshPlan
from .geometry import LODBuilder, EdgelineBuilder, BackfaceBuilder, MeshSplitBuilder
from .bake import Baker
from .plan import ExportPlanner
from .dmx import DmxWriter
from .smd import SmdWriter
from .prefab import resolve_prefab_output, _PrefabRunnerAdapter


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
            else:
                self.errorReport(get_id("exporter_report_aborted", True).format(self.files_exported, self.elapsed_time()))

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
                if getattr(vs, 'flex_controller_mode', '') == 'DME' and hasShapes(_ob):
                    self.warning(get_id("exporter_warn_dme_smd", True).format(_ob.name))

        for _ob in check_obs:
            vs = getattr(_ob, 'vs', None)
            if vs and getattr(vs, 'flex_controller_mode', '') == 'DME' and hasShapes(_ob):
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
                    if isProcBoneAnimSkipped(source, None, slot.name_display):
                        self.warning(get_id("exporter_warn_procbone_anim", True).format(slot.name_display, source.name))
                        continue
                    baked_armature.animation_data.action_slot = slot
                    self.files_exported += write_func(source, bake_results, self.sanitiseFilename(slot.name_display), path)
            else:
                for action in actionsForFilter(baked_armature.vs.action_filter):
                    if isProcBoneAnimSkipped(source, action.name):
                        self.warning(get_id("exporter_warn_procbone_anim", True).format(action.name, source.name))
                        continue
                    baked_armature.animation_data.action = action
                    self.files_exported += write_func(source, bake_results, self.sanitiseFilename(action.name), path)
        elif (isinstance(source, bpy.types.Object) and source.type == "ARMATURE"
              and source.animation_data and source.animation_data.action_slot
              and isProcBoneAnimSkipped(source, None, source.animation_data.action_slot.name_display)):
            self.warning(get_id("exporter_warn_procbone_anim", True).format(
                source.animation_data.action_slot.name_display, source.name))
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
