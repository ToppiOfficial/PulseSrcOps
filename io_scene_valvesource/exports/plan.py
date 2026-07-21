import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from .. import datamodel, ordered_set, flex
from .records import BakedVertexAnimation, BakeResult, ExportTask, _SplitPart, _MeshPlan
from .geometry import LODBuilder, EdgelineBuilder, BackfaceBuilder, MeshSplitBuilder


class ExportPlanner:
    def __init__(self, reporter):
        self._reporter = reporter
        self._lod_builder = LODBuilder(reporter)
        self._edgeline_builder = EdgelineBuilder(reporter, merge_fn=self._apply_merge_vertices)
        self._backface_builder = BackfaceBuilder(reporter)
        self._mesh_split_builder = MeshSplitBuilder(reporter)
        self._owned_objects: list[bpy.types.Object] = []
        self._owned_collections: list[bpy.types.Collection] = []
        self._name_map: dict[int, str] = {}
        self._original_ob_map: dict[int, bpy.types.Object] = {}

    def original_name(self, uid: int) -> str | None:
        return self._name_map.get(uid)

    def build_queue(self, id) -> list[ExportTask]:
        if isinstance(id, Collection):
            return self._plan_collection(id)
        elif isinstance(id, bpy.types.Object) and id.type == "ARMATURE":
            return [ExportTask(id, self._armature_export_name(id))]
        else:
            return self._plan_object(id, id.name)

    def cleanup(self) -> None:
        for col in self._owned_collections:
            if col.name in bpy.data.collections:
                bpy.data.collections.remove(col)
        for ob in self._owned_objects:
            if ob.name in bpy.data.objects:
                bpy.data.objects.remove(ob, do_unlink=True)
        self._owned_collections.clear()
        self._owned_objects.clear()

    def _is_existing_lod(self, name: str) -> bool:
        if "_lod" not in name:
            return False
        suffix = name.rsplit("_lod", 1)[-1]
        return suffix.isdigit() and int(suffix) > 0

    def _apply_merge_vertices(self, ob: bpy.types.Object) -> None:
        MERGE_DIST = 1e-5
        OPPOSING_DOT = -0.9
        # Edgeline / backface / non-exportable vgroups are hard per-vertex masks
        # that drive the solidify shell. Welding two coincident verts whose mask
        # weight differs moves the mask boundary or spreads it to verts that never
        # had it ("leaking"), so those pairs are refused below.
        PP_WEIGHT_TOL = 1e-4

        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.normal_update()

        raw_map = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=MERGE_DIST)["targetmap"]

        if not raw_map:
            bm.free()
            return

        # Post-process mask vgroup indices; a pair is only welded when its weights
        # match across every one of these.
        pp_vg_indices = [
            vg.index for vg in ob.vertex_groups
            if vg.name and vg.name in {
                getattr(ob.vs, "toon_edgeline_vertexgroup", ""),
                getattr(ob.vs, "backface_vgroup", ""),
                getattr(ob.vs, "non_exportable_vgroup", ""),
            }
        ]
        deform = bm.verts.layers.deform.active if pp_vg_indices else None

        def mask_differs(a, b) -> bool:
            if deform is None:
                return False
            wa, wb = a[deform], b[deform]
            return any(
                abs(wa.get(i, 0.0) - wb.get(i, 0.0)) > PP_WEIGHT_TOL
                for i in pp_vg_indices
            )

        protected_indices = set()
        for src, tgt in raw_map.items():
            opposing = any(
                n1.dot(n2) < OPPOSING_DOT
                for n1 in (f.normal for f in src.link_faces)
                for n2 in (f.normal for f in tgt.link_faces)
            )
            if opposing or mask_differs(src, tgt):
                protected_indices.add(src.index)
                protected_indices.add(tgt.index)

        bm.free()

        # Deselecting protected vertices excludes them from the operator's scope,
        # so mask boundaries are never welded across.
        bpy.context.view_layer.objects.active = ob
        select_only(ob)
        bpy.ops.object.mode_set(mode='EDIT')

        bm_edit = bmesh.from_edit_mesh(me)
        bm_edit.verts.ensure_lookup_table()
        for v in bm_edit.verts:
            v.select = v.index not in protected_indices
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)

        try:
            bpy.ops.mesh.remove_doubles(threshold=MERGE_DIST, use_sharp_edge_from_normals=True)
        except TypeError:
            bpy.ops.mesh.remove_doubles(threshold=MERGE_DIST)

        bpy.ops.object.mode_set(mode='OBJECT')

        print(f"- Merged duplicate vertices in '{ob.name}'")

    # -- per-object post-processing helpers -----------------------------------

    def _make_ob_copy(self, ob: bpy.types.Object) -> bpy.types.Object:
        copy = ob.copy()
        if ob.data:
            copy.data = ob.data.copy()
        bpy.context.scene.collection.objects.link(copy)
        self._owned_objects.append(copy)
        self._original_ob_map[copy.session_uid] = ob
        return copy

    def _apply_edgeline(
        self,
        target:         bpy.types.Object,
        export_name:    str,
        source_ob:      bpy.types.Object,
        for_collection: bool,
        ) -> typing.Optional[bpy.types.Object]:
        
        if not source_ob.vs.use_toon_edgeline:
            return None
        if source_ob.vs.export_edgeline_separately:
            return None  # caller handles separately-exported case
        if for_collection or State.exportFormat == ExportFormat.DMX:
            target.vs.export_edgeline_separately = True
            el = self._edgeline_builder.build(target, export_name)
            target.vs.export_edgeline_separately = False
            if el and el is not target:
                self._owned_objects.append(el)
                State.exportableObjects.add(el.session_uid)
                return el
        else:
            self._edgeline_builder.build(target, export_name)
        return None

    def _apply_backface(self, target: bpy.types.Object, export_name: str, post_ok: bool) -> typing.Optional[bpy.types.Object]:
        if not post_ok or not target.vs.generate_backface:
            return None
        if not is_mesh_compatible(target) or target.type not in modifier_compatible:
            return None
        if State.exportFormat == ExportFormat.SMD:
            self._backface_builder.build_merged(target, export_name)
            return None
        bf = self._backface_builder.build(target, export_name)
        if bf:
            self._owned_objects.append(bf)
            State.exportableObjects.add(bf.session_uid)
            return bf
        return None

    def _plan_mesh_ob(self, ob: bpy.types.Object, export_name: str, *, for_collection: bool = False ) -> _MeshPlan:
        post_ok  = getattr(ob.vs, 'mesh_type', 'DEFAULT') == 'DEFAULT'
        is_mesh  = is_mesh_compatible(ob) and ob.type in modifier_compatible

        needs_pp = post_ok and is_mesh and (ob.vs.use_mesh_split or ob.vs.use_toon_edgeline or ob.vs.generate_backface)

        lod_source = None
        if post_ok and is_mesh and ob.vs.generate_lods and ob.vs.lod_count > 0 \
                and not self._is_existing_lod(export_name):
            lod_source = self._make_ob_copy(ob)
            if not hasShapes(ob):
                self._apply_merge_vertices(lod_source)

        target = ob
        if for_collection or needs_pp:
            target = self._make_ob_copy(ob)
            self._name_map[target.session_uid] = ob.name

        split_parts: list[_SplitPart] = []
        if post_ok and is_mesh and ob.vs.use_mesh_split \
                and not export_name.endswith(("_order", "_edgeline", "_backface")):
            for so in self._mesh_split_builder.build(target, export_name):
                self._owned_objects.append(so)
                State.exportableObjects.add(so.session_uid)
                n       = so.get("mesh_split_n", 0)
                so_name = re.sub(r"_lod[1-9]\d*$", "", export_name) + f"_split{n}"
                so_el   = self._apply_edgeline(so, so_name, ob, for_collection)
                so_bf   = self._apply_backface(so, so_name, post_ok)
                split_parts.append(_SplitPart(so, so_name, so_el, so_bf))

        base_edgeline = None
        if post_ok and not export_name.endswith("_edgeline"):
            base_edgeline = self._apply_edgeline(target, export_name, ob, for_collection)

        base_backface = None
        if not export_name.endswith("_backface"):
            base_backface = self._apply_backface(target, export_name, post_ok)

        return _MeshPlan(ob, target, lod_source, base_edgeline, base_backface, split_parts)

    # -- collection planning --------------------------------------------------

    def _plan_collection(self, col: Collection) -> list[ExportTask]:
        # Objects folded in from 'bypass' child collections are exported as part of
        # this group. Anything beyond col's own objects means we must route the
        # export through a temp collection so the folded objects are actually linked.
        export_objects = get_collection_export_objects(col)
        has_folded = len(export_objects) > len(col.objects)

        plans: dict[bpy.types.Object, _MeshPlan] = {}
        for ob in export_objects:
            # Never copy an object that yields no geometry. ob.copy() keeps the parent
            # pointer, so a copied attachment empty still resolves to the original armature
            # and gets written a second time. Instancer empties are exempt - the baker
            # turns their dupli geometry real.
            if not ob.vs.export or (ob.type not in exportable_types and ob.instance_type == 'NONE'):
                continue
            plans[ob] = self._plan_mesh_ob(ob, ob.name, for_collection=True)

        base_obs:          list[bpy.types.Object]                     = []
        effective_objects: dict[bpy.types.Object, bpy.types.Object]   = {}
        edgeline_copies:   list[bpy.types.Object]                     = []

        for ob, plan in plans.items():
            effective_objects[ob] = plan.target
            base_obs.append(plan.target)
            for sp in plan.split_parts:
                base_obs.append(sp.ob)
                if sp.edgeline:
                    base_obs.append(sp.edgeline)
                    edgeline_copies.append(sp.edgeline)
                if sp.backface:
                    base_obs.append(sp.backface)
            if plan.base_edgeline:
                base_obs.append(plan.base_edgeline)
                edgeline_copies.append(plan.base_edgeline)
            if plan.base_backface:
                base_obs.append(plan.base_backface)

        for copy in effective_objects.values():
            for mod in copy.modifiers:
                if hasattr(mod, 'object') and mod.object and mod.object in effective_objects:
                    mod.object = effective_objects[mod.object]
        for edgeline_ob in edgeline_copies:
            for mod in edgeline_ob.modifiers:
                if hasattr(mod, 'object') and mod.object and mod.object in effective_objects:
                    mod.object = effective_objects[mod.object]

        needs_temp = has_folded or any(p.target is not p.source for p in plans.values())
        if needs_temp:
            target_col = self._make_collection(col.name + "_temp_base", base_obs)
            self._copy_collection_export_settings(col, target_col)
        else:
            target_col = col
            effective_objects = {ob: ob for ob in col.objects}

        base_allowed_uids = {ob.session_uid for ob in target_col.objects if ob.vs.export}
        tasks = [ExportTask(target_col, col.name, base_allowed_uids)]

        lod_buckets:  dict[int, list[bpy.types.Object]] = collections.defaultdict(list)
        edgeline_obs: list[bpy.types.Object] = []

        for ob in export_objects:
            if not ob.vs.export or ob.session_uid not in State.exportableObjects:
                continue
            if not is_mesh_compatible(ob) or ob.type not in modifier_compatible:
                continue

            plan          = plans.get(ob)
            working_ob    = effective_objects.get(ob, ob)
            post_ok       = getattr(ob.vs, 'mesh_type', 'DEFAULT') == 'DEFAULT'
            is_lod_member = self._is_existing_lod(working_ob.name)

            if plan and plan.lod_source and post_ok and not is_lod_member:
                for lod_idx, lod_ob in self._lod_builder.build_all(plan.lod_source, working_ob.name):
                    self._owned_objects.append(lod_ob)
                    lod_buckets[lod_idx].append(lod_ob)
                    bf = self._apply_backface(lod_ob, lod_ob.name, post_ok=True)
                    if bf:
                        lod_buckets[lod_idx].append(bf)

            if post_ok and not working_ob.name.endswith("_edgeline") \
                    and working_ob.vs.use_toon_edgeline and working_ob.vs.export_edgeline_separately:
                el = self._edgeline_builder.build(working_ob, working_ob.name)
                if el and el is not working_ob:
                    self._owned_objects.append(el)
                    edgeline_obs.append(el)
            if plan:
                for sp in plan.split_parts:
                    if ob.vs.export_edgeline_separately:
                        sp_el = self._edgeline_builder.build(sp.ob, sp.name)
                        if sp_el and sp_el is not sp.ob:
                            self._owned_objects.append(sp_el)
                            edgeline_obs.append(sp_el)

        for lod_idx, lod_obs in lod_buckets.items():
            lod_col = self._make_lod_collection(target_col, lod_idx, lod_obs, col.name)
            lod_allowed_uids = {ob.session_uid for ob in lod_col.objects if ob.vs.export}
            tasks.append(ExportTask(lod_col, lod_col.name, lod_allowed_uids))

        if edgeline_obs:
            base_name = re.sub(r"_lod\d+$", "", col.name)
            edgeline_col = self._make_collection(base_name + "_edgeline", edgeline_obs)
            edge_allowed_uids = {ob.session_uid for ob in edgeline_col.objects if ob.vs.export}
            tasks.append(ExportTask(edgeline_col, edgeline_col.name, edge_allowed_uids))

        return tasks

    def _make_lod_collection(self, source_col: Collection, lod_idx: int, lod_obs: list, base_name: str = None) -> Collection:
        col_name = f"{base_name or source_col.name}_lod{lod_idx}"
        lod_col = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(lod_col)
        self._owned_collections.append(lod_col)

        for lod_ob in lod_obs:
            lod_ob.vs.export = True
            lod_col.objects.link(lod_ob)
            State.exportableObjects.add(lod_ob.session_uid)

        effective_copies = {}

        for ob in source_col.objects:
            if ob.vs.export and not ob.vs.generate_lods \
                    and (ob.type in exportable_types or ob.instance_type != 'NONE'):
                copy = ob.copy()
                if ob.data:
                    copy.data = ob.data.copy()
                copy.vs.export = True
                copy.vs.generate_lods = False
                bpy.context.scene.collection.objects.link(copy)
                lod_col.objects.link(copy)
                State.exportableObjects.add(copy.session_uid)
                self._owned_objects.append(copy)
                self._name_map[copy.session_uid] = ob.name
                effective_copies[ob] = copy

        for lod_ob in lod_col.objects:
            for mod in lod_ob.modifiers:
                if hasattr(mod, 'object') and mod.object and mod.object in effective_copies:
                    mod.object = effective_copies[mod.object]

        return lod_col

    def _make_collection(self, name: str, obs: list[bpy.types.Object]) -> Collection:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
        self._owned_collections.append(col)
        for ob in obs:
            ob.vs.export = True
            col.objects.link(ob)
            State.exportableObjects.add(ob.session_uid)
        return col

    def _copy_collection_export_settings(self, src: Collection, dst: Collection) -> None:
        # The temp "_temp_base" collection is created fresh, so it carries default
        # .vs settings. Propagate the collection-level export settings that the
        # exporter reads off the source collection (vertex animations, automerge,
        # flex controller config); otherwise features like VCA export silently
        # no-op because dst.vs.vertex_animations is empty.
        dst.vs.automerge = src.vs.automerge
        dst.vs.flex_controller_mode = src.vs.flex_controller_mode
        dst.vs.flex_controller_source = src.vs.flex_controller_source

        dst.vs.vertex_animations.clear()
        for src_va in src.vs.vertex_animations:
            dst_va = dst.vs.vertex_animations.add()
            dst_va.name = src_va.name
            dst_va.start = src_va.start
            dst_va.end = src_va.end
            dst_va.export_sequence = src_va.export_sequence

    # -- object planning ------------------------------------------------------

    def _plan_object(self, ob: bpy.types.Object, export_name: str) -> list[ExportTask]:
        post_ok = getattr(ob.vs, 'mesh_type', 'DEFAULT') == 'DEFAULT'
        plan    = self._plan_mesh_ob(ob, export_name, for_collection=False)
        target  = plan.target

        allowed_uids = {target.session_uid}
        companions   = [x for x in [plan.base_edgeline, plan.base_backface] if x is not None]

        order_tasks: list[ExportTask] = []
        for sp in plan.split_parts:
            if ob.vs.export_mesh_split_separately:
                sp_companions = [x for x in [sp.edgeline, sp.backface] if x is not None]
                order_tasks.append(ExportTask(sp.ob, sp.name, {sp.ob.session_uid}, sp_companions))
            else:
                companions.append(sp.ob)
                if sp.edgeline:
                    companions.append(sp.edgeline)
                if sp.backface:
                    companions.append(sp.backface)

        tasks = [ExportTask(target, export_name, allowed_uids, companions)]

        if not is_mesh_compatible(ob) or ob.type not in modifier_compatible:
            tasks.extend(order_tasks)
            return tasks

        if plan.lod_source is not None:
            for lod_idx, lod_ob in self._lod_builder.build_all(plan.lod_source, export_name):
                self._owned_objects.append(lod_ob)
                State.exportableObjects.add(lod_ob.session_uid)
                lod_companions = []
                if not export_name.endswith("_backface") and ob.vs.generate_backface:
                    bf = self._apply_backface(lod_ob, lod_ob.name, post_ok=True)
                    if bf:
                        lod_companions.append(bf)
                tasks.append(ExportTask(lod_ob, lod_ob.name, {lod_ob.session_uid}, lod_companions))

        if post_ok and not export_name.endswith("_edgeline") \
                and ob.vs.use_toon_edgeline and ob.vs.export_edgeline_separately:
            el = self._edgeline_builder.build(target, export_name)
            if el and el is not target:
                self._owned_objects.append(el)
                State.exportableObjects.add(el.session_uid)
                base = re.sub(r"_lod[1-9]\d*$", "", export_name)
                tasks.append(ExportTask(el, base + "_edgeline", {el.session_uid}))
            for sp in plan.split_parts:
                sp_el = self._edgeline_builder.build(sp.ob, sp.name)
                if sp_el and sp_el is not sp.ob:
                    self._owned_objects.append(sp_el)
                    State.exportableObjects.add(sp_el.session_uid)
                    el_base = re.sub(r"_lod[1-9]\d*$", "", sp.name)
                    tasks.append(ExportTask(sp_el, el_base + "_edgeline", {sp_el.session_uid}))

        tasks.extend(order_tasks)
        return tasks

    def _armature_export_name(self, id: bpy.types.Object) -> str:
        ad = id.animation_data
        if not ad:
            return id.name
        if id.data.vs.action_selection in ("FILTERED", "FILTERED_ACTIONS"):
            return id.name
        if ad.action_slot:
            return actionSlotExportName(ad)
        return id.name
