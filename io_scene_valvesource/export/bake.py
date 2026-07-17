import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from .. import datamodel, ordered_set, flex
from .records import BakedVertexAnimation, BakeResult, ExportTask, _SplitPart, _MeshPlan
from .geometry import EdgelineBuilder


class Baker:
    def __init__(self, exporter: "SmdExporter"):
        self._exporter = exporter
        self._cache: dict[int, BakeResult] = {}  # session_uid -> BakeResult

    def bake(self, ob: bpy.types.Object) -> typing.Optional[BakeResult]:
        uid = ob.session_uid
        if uid in self._cache:
            return self._cache[uid]

        # Reuse an armature already baked by an earlier task within this same export id
        # (a collection's base / LOD / edgeline / split tasks all reference one skeleton).
        # The baked armature's pose was reset to identity and mesh export never mutates it,
        # so sharing it avoids re-copying the whole armature for every task.
        arm_cache = getattr(self._exporter, "_armature_bake_cache", None)
        if ob.type == "ARMATURE" and arm_cache is not None:
            shared = arm_cache.get(uid)
            if shared is not None and shared.object and shared.object.name in bpy.data.objects:
                self._cache[uid] = shared
                return shared

        result = BakeResult(ob.name)
        result.src = ob
        self._cache[uid] = result

        try:
            select_only(ob)
        except RuntimeError:
            self._exporter.warning(get_id("exporter_err_hidden", True).format(ob.name))
            return None

        should_tri = State.exportFormat == ExportFormat.SMD or ob.vs.triangulate

        # -- realize instances ------------------------------------------------
        duplis = None
        if ob.instance_type != "NONE":
            bpy.ops.object.duplicates_make_real()
            ob.select_set(False)
            if bpy.context.selected_objects:
                bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
                bpy.ops.object.join()
                dup_ob = bpy.context.active_object
                dup_ob.parent = ob
                dup_bake = self.bake(dup_ob)
                if dup_bake:
                    duplis = dup_bake.object
                    if should_tri:
                        self._triangulate()
            elif ob.type not in exportable_types:
                return None

        # -- copy for non-destructive baking ---------------------------------
        top_parent = self._exporter.getTopParent(ob)

        if ob.type != "META":
            ob = ob.copy()
            bpy.context.scene.collection.objects.link(ob)
        if ob.data:
            ob.data = ob.data.copy()

        if bpy.context.active_object:
            ops.object.mode_set(mode="OBJECT")
        select_only(ob)

        if hasShapes(ob):
            ob.active_shape_key_index = 0

        # -- envelope / armature detection ------------------------------------
        self._setup_envelope(ob, result, top_parent)

        # -- per-type pre-bake mesh ops ---------------------------------------
        if ob.type == "MESH":
            self._pre_bake_mesh_ops(ob)

        # -- coordinate transform ---------------------------------------------
        ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        ob.matrix_world = (
            Matrix.Translation(top_parent.location).inverted()
            @ getUpAxisMat(bpy.context.scene.vs.up_axis).inverted()
            @ getForwardAxisMat(bpy.context.scene.vs.forward_axis).inverted()
            @ getUpAxisOffsetMat(bpy.context.scene.vs.up_axis, bpy.context.scene.vs.up_axis_offset)
            @ Matrix.Scale(bpy.context.scene.vs.world_scale, 4)
            @ ob.matrix_world
        )

        if ob.type == "ARMATURE":
            for pb in ob.pose.bones:
                pb.matrix_basis.identity()
            result.armature = result
            result.object = ob
            if arm_cache is not None:
                arm_cache[uid] = result
            return result

        if ob.type == "CURVE":
            ob.data.dimensions = "3D"

        for con in [c for c in ob.constraints if not c.mute]:
            con.mute = True

        # -- modifier scan ----------------------------------------------------
        solidify_fill_rim = None
        shapes_invalid = False
        for mod in ob.modifiers:
            if mod.type == "ARMATURE" and mod.object:
                if result.envelope and any(br for br in self._cache.values() if br.envelope != mod.object):
                    self._exporter.warning(get_id("exporter_err_dupeenv_arm", True).format(mod.name, ob.name))
                else:
                    result.armature = self.bake(mod.object)
                    result.envelope = mod
                    select_only(ob)
                mod.show_viewport = False
            elif mod.type == "SOLIDIFY" and solidify_fill_rim is None:
                solidify_fill_rim = mod.use_rim
            elif hasShapes(ob) and mod.type == "DECIMATE" and mod.decimate_type != "UNSUBDIV":
                self._exporter.error(get_id("exporter_err_shapes_decimate", True).format(ob.name, mod.decimate_type))
                shapes_invalid = True

        ops.object.mode_set(mode="OBJECT")

        # -- bake mesh --------------------------------------------------------
        if ob.type in exportable_types:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            data = bpy.data.meshes.new_from_object(
                ob.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
            )
            data.name = ob.name + "_baked"
            baked = self._put_in_object(ob, data, solidify_fill_rim)
            if should_tri:
                bpy.context.view_layer.objects.active = baked
                select_only(baked)
                self._triangulate()
        else:
            baked = None

        # Zero-state basis normal capture: when the user has shape keys at non-zero
        # default values, normals from the regular bake are shape-deformed.
        # Re-evaluate with all values at 0 and override the baked normals.
        # The zero-state mesh goes through _put_in_object so face filtering matches baked.
        if hasShapes(ob) and baked and getattr(ob.data.vs, 'bake_shapekey_as_basis_normals', False):
            keys = ob.data.shape_keys.key_blocks
            has_nonzero = any(sk.value != 0.0 for sk in keys[1:])
            if has_nonzero:
                saved_values = [(sk, sk.value) for sk in keys[1:]]
                for sk, _ in saved_values:
                    sk.value = 0.0
                depsgraph = bpy.context.evaluated_depsgraph_get()
                zero_data = bpy.data.meshes.new_from_object(
                    ob.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
                )
                zero_data.name = ob.name + "_zero_normals"
                zero_ob = self._put_in_object(ob, zero_data, solidify_fill_rim, quiet=True)
                if should_tri:
                    prev_active = bpy.context.view_layer.objects.active
                    bpy.context.view_layer.objects.active = zero_ob
                    select_only(zero_ob)
                    self._triangulate()
                    bpy.context.view_layer.objects.active = prev_active
                zero_loop_normals = [tuple(l.normal) for l in zero_ob.data.loops]
                bpy.context.scene.collection.objects.unlink(zero_ob)
                bpy.data.objects.remove(zero_ob, do_unlink=True)
                baked.data.normals_split_custom_set(zero_loop_normals)
                print(f"- Applied zero-state basis normals to '{result.name}'")
                for sk, v in saved_values:
                    sk.value = v

        if duplis:
            if not ob.type in exportable_types:
                ob.select_set(False)
                bpy.context.view_layer.objects.active = duplis
            duplis.select_set(True)
            bpy.ops.object.join()
            baked = bpy.context.active_object

        if baked is None:
            return None

        result.object = baked

        if not baked.data.polygons:
            self._exporter.error(get_id("exporter_err_nopolys", True).format(result.name))
            return None

        if ob.type == "MESH":
            for remap in ob.vs.vertex_map_remaps:
                copy = baked.vs.vertex_map_remaps.add()
                copy.group = remap.group
                copy.min = remap.min
                copy.max = remap.max

        result.matrix = baked.matrix_world

        # -- shape key baking -------------------------------------------------
        if not shapes_invalid and hasShapes(ob) and getattr(ob.vs, 'mesh_type', 'DEFAULT') == 'DEFAULT':
            self._bake_shapes(ob, result, solidify_fill_rim)

        for mod in ob.modifiers:
            mod.show_viewport = False

        bpy.context.view_layer.objects.active = baked
        baked.select_set(True)

        self._generate_uvs_if_needed(baked, result)
        self._check_vertex_limit(baked, result)

        return result

    # -- private helpers ------------------------------------------------------

    def _triangulate(self) -> None:
        ops.object.mode_set(mode="EDIT")
        ops.mesh.select_all(action="SELECT")
        ops.mesh.quads_convert_to_tris(quad_method="FIXED")
        ops.object.mode_set(mode="OBJECT")

    def _setup_envelope(self, ob: bpy.types.Object, result: BakeResult, top_parent) -> None:
        def capture_bone_parent(armature, bone_name):
            result.envelope = bone_name
            result.armature = self.bake(armature)
            select_only(ob)
            result.bone_parent_matrix = (
                armature.pose.bones[bone_name].matrix.inverted()
                @ armature.matrix_world.inverted()
                @ ob.matrix_world
            )

        cur = ob
        while cur:
            if cur.parent_bone and cur.parent_type == "BONE" and not result.envelope:
                capture_bone_parent(cur.parent, cur.parent_bone)
            for con in [c for c in cur.constraints if not c.mute]:
                if con.type in ("CHILD_OF", "COPY_TRANSFORMS") and con.target and con.target.type == "ARMATURE" and con.subtarget:
                    if not result.envelope:
                        capture_bone_parent(con.target, con.subtarget)
                    else:
                        self._exporter.warning(get_id("exporter_err_dupeenv_con", True).format(con.name, cur.name))
            if result.envelope:
                break
            cur = cur.parent

    def _pre_bake_mesh_ops(self, ob: bpy.types.Object) -> None:
        scene_vs = bpy.context.scene.vs
        mt = getattr(ob.vs, 'mesh_type', 'DEFAULT')
        limit_mode = getattr(scene_vs, 'vertex_influence_limit_mode', 'AUTO')
        if mt == 'COLLISION':
            vgroup_limit = 1
        elif mt == 'CLOTHPROXY':
            vgroup_limit = min(8, max(4, scene_vs.vertex_influence_limit))
        else:
            if limit_mode == 'AUTO':
                source2 = State.datamodelFormat >= 22 or State.compiler > Compiler.STUDIOMDL
                vgroup_limit = 4 if source2 else 3
            else:
                vgroup_limit = scene_vs.vertex_influence_limit

        if not hasShapes(ob):
            VertexGroupNormalizer(ob, vgroup_limit=vgroup_limit, clean_tolerance=scene_vs.weightlink_threshold).run()
            ops.object.mode_set(mode="EDIT")
            ops.mesh.reveal()
            if ob.matrix_world.is_negative:
                ops.mesh.select_all(action="SELECT")
                ops.mesh.flip_normals()
            ops.mesh.select_all(action="DESELECT")
            ops.object.mode_set(mode="OBJECT")
            return

        # Shape key normalization
        if not ob.data.vs.normalize_shapekeys:
            print("- Normalizing shape keys disabled, resetting all shapekey values to 0")
            for sk in ob.data.shape_keys.key_blocks:
                sk.value = 0
        else:
            self._normalize_shapekeys(ob)

        VertexGroupNormalizer(ob, vgroup_limit=vgroup_limit, clean_tolerance=scene_vs.weightlink_threshold).run()

        ops.object.mode_set(mode="EDIT")
        ops.mesh.reveal()
        if ob.matrix_world.is_negative:
            ops.mesh.select_all(action="SELECT")
            ops.mesh.flip_normals()
        ops.mesh.select_all(action="DESELECT")
        ops.object.mode_set(mode="OBJECT")

    def _normalize_shapekeys(self, ob: bpy.types.Object) -> None:
        print("- Normalizing Basis and Keys (Reference-Based)")
        blocks = ob.data.shape_keys.key_blocks
        base_key = blocks[0]
        orig_coords = [v.co.copy() for v in base_key.data]

        for key in blocks[1:]:
            if key.slider_min == 0.0:
                continue
            for i, b_v in enumerate(base_key.data):
                b_v.co += (key.data[i].co - orig_coords[i]) * key.slider_min

        new_basis = [v.co.copy() for v in base_key.data]

        for key in blocks[1:]:
            s_min, s_max = key.slider_min, key.slider_max
            old_val = key.value
            rng = s_max - s_min
            for i, k_v in enumerate(key.data):
                delta = k_v.co - orig_coords[i]
                k_v.co = new_basis[i] + (delta * s_max - delta * s_min)
            key.slider_min = 0.0
            key.slider_max = 1.0
            key.value = (old_val - s_min) / rng if rng != 0 else 0.0

    def _put_in_object(self, source_ob: bpy.types.Object, data, solidify_fill_rim, quiet=False) -> bpy.types.Object:
        if bpy.context.view_layer.objects.active:
            ops.object.mode_set(mode="OBJECT")

        ob = bpy.data.objects.new(name=source_ob.name, object_data=data)
        ob.matrix_world = source_ob.matrix_world
        bpy.context.scene.collection.objects.link(ob)
        select_only(ob)

        exporting_smd = State.exportFormat == ExportFormat.SMD
        ops.object.transform_apply(scale=True, location=exporting_smd, rotation=exporting_smd)

        if hasCurves(source_ob):
            ops.object.mode_set(mode="EDIT")
            ops.mesh.select_all(action="SELECT")
            if source_ob.data.vs.faces == "BOTH":
                ops.mesh.duplicate()
                if solidify_fill_rim:
                    self._exporter.warning(get_id("exporter_err_solidifyinside", True).format(source_ob.name))
            if source_ob.data.vs.faces != "FORWARD":
                ops.mesh.flip_normals()
            ops.object.mode_set(mode="OBJECT")

        self._delete_filtered_faces(ob, source_ob, quiet=quiet)

        # Not the way I hope to fix it but too bad.
        # if source_ob.vs.use_toon_edgeline and not source_ob.vs.edgeline_per_material:
        # oh ffs.
        #
        if (source_ob.vs.use_toon_edgeline or source_ob.get("is_edgeline_only")) and not source_ob.vs.edgeline_per_material:
            self._collapse_edgeline_materials(ob)

        return ob
    
    def _delete_filtered_faces(self, ob: bpy.types.Object, vg_source: bpy.types.Object, quiet: bool = False) -> None:
        me = ob.data
        if not getattr(vg_source, "vs", None):
            return

        # Non-exportable vgroup: base faces (and their edgeline shell counterparts)
        # where all vertices meet the weight threshold are removed.
        _mt = getattr(vg_source.vs, 'mesh_type', 'DEFAULT')
        nonexp_vg_name = getattr(vg_source.vs, "non_exportable_vgroup", "") if _mt == 'DEFAULT' else ""
        nonexp_vg      = vg_source.vertex_groups.get(nonexp_vg_name) if nonexp_vg_name else None
        nonexp_tol     = getattr(vg_source.vs, "non_exportable_vgroup_tolerance", 0.90)

        # Edgeline thickness vgroup: shell faces where all vertices are fully weighted
        # (>= 0.90) are pruned - this is where the user intentionally hides the outline.
        edge_vg_name = getattr(vg_source.vs, "toon_edgeline_vertexgroup", "")
        edge_vg      = vg_source.vertex_groups.get(edge_vg_name) if edge_vg_name else None
        EDGE_VG_TOL  = 0.90

        # Reliable separate-edgeline detection - survives Baker's ob.copy() rename.
        is_separate_edgeline = vg_source.get("is_edgeline_only", False)

        if not nonexp_vg and not edge_vg and not is_separate_edgeline:
            return

        # Pre-build per-vertex weighted sets so the polygon loop does O(1) set lookups
        # instead of rescanning all vertex groups for every polygon vertex.
        nonexp_weighted: frozenset[int] = frozenset()
        edge_weighted:   frozenset[int] = frozenset()
        if nonexp_vg:
            nonexp_weighted = frozenset(
                v.index for v in me.vertices
                if any(g.group == nonexp_vg.index and g.weight >= nonexp_tol for g in v.groups)
            )
        if edge_vg:
            edge_weighted = frozenset(
                v.index for v in me.vertices
                if any(g.group == edge_vg.index and g.weight >= EDGE_VG_TOL for g in v.groups)
            )

        faces_to_delete = set()

        for poly in me.polygons:
            mat = (
                me.materials[poly.material_index]
                if me.materials and poly.material_index < len(me.materials)
                else None
            )
            is_edgeline_face = mat and (
                mat.name == EdgelineBuilder.EDGELINE_MAT
                or mat.name.endswith("_edgeline")
            )

            if is_edgeline_face:
                # Shell faces are pruned when:
                # 1. The edgeline thickness VG fully weights all verts (outline intentionally
                #    hidden at those vertices).
                if edge_weighted and all(vi in edge_weighted for vi in poly.vertices):
                    faces_to_delete.add(poly.index)
                    continue
                # 2. The non-exportable VG marks all verts as excluded - so the shell
                #    face that sits on top of a deleted base face is also removed.
                if nonexp_weighted and all(vi in nonexp_weighted for vi in poly.vertices):
                    faces_to_delete.add(poly.index)
            else:
                # Base mesh faces:
                # For a separate edgeline object, ALL base faces must be stripped -
                # they belong to the original mesh and must not appear in the edgeline export.
                if is_separate_edgeline:
                    faces_to_delete.add(poly.index)
                    continue
                # Non-exportable vgroup filter on the base mesh.
                if nonexp_weighted and all(vi in nonexp_weighted for vi in poly.vertices):
                    faces_to_delete.add(poly.index)

        if not faces_to_delete:
            return

        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        geom = [f for f in bm.faces if f.index in faces_to_delete]
        if geom:
            if not quiet:
                print(f"- Deleting {len(geom)} non-exportable faces")
            bmesh.ops.delete(bm, geom=geom, context="FACES")
        bm.to_mesh(me)
        bm.free()
        me.update()


    # SmdExporter - _collapse_edgeline_materials (unchanged)
    def _collapse_edgeline_materials(self, ob: bpy.types.Object) -> None:
        me = ob.data
        generic_mat = bpy.data.materials.get(EdgelineBuilder.EDGELINE_MAT) or bpy.data.materials.new(name=EdgelineBuilder.EDGELINE_MAT)
        for i, mat in enumerate(me.materials):
            if mat and mat.name != EdgelineBuilder.EDGELINE_MAT and mat.name.endswith("_edgeline"):
                me.materials[i] = generic_mat

    def _bake_shapes(self, source_ob: bpy.types.Object, result: BakeResult, solidify_fill_rim) -> None:
        should_tri = State.exportFormat == ExportFormat.SMD or source_ob.vs.triangulate
        normalize = source_ob.data.vs.normalize_shapekeys
        source_ob.show_only_shape_key = not normalize
        preserve_basis_normals = source_ob.data.vs.bake_shapekey_as_basis_normals

        shapes_to_process = list(enumerate(source_ob.data.shape_keys.key_blocks))[1:]

        if preserve_basis_normals:
            print(f"- Ignoring changed normals for shapekeys in {result.name}")

        for i, shape in shapes_to_process:
            source_ob.active_shape_key_index = i
            if normalize:
                original_value = shape.value
                shape.value = 1.0

            depsgraph = bpy.context.evaluated_depsgraph_get()
            baked_shape_data = bpy.data.meshes.new_from_object(source_ob.evaluated_get(depsgraph))
            baked_shape_data.name = f"{source_ob.name} -> {shape.name}"

            shape_ob = self._put_in_object(source_ob, baked_shape_data, solidify_fill_rim, quiet=True)

            result.shapes[shape.name] = shape_ob.data

            if normalize:
                shape.value = original_value

            if should_tri:
                bpy.context.view_layer.objects.active = shape_ob
                self._triangulate()

            bpy.context.scene.collection.objects.unlink(shape_ob)
            bpy.data.objects.remove(shape_ob)

    def _generate_uvs_if_needed(self, ob: bpy.types.Object, result: BakeResult) -> None:
        if ob.data.uv_layers:
            return
        ops.object.mode_set(mode="EDIT")
        ops.mesh.select_all(action="SELECT")
        if len(result.object.data.vertices) < 2000:
            result.object.data.uv_layers.new()
            ops.uv.smart_project()
        else:
            ops.uv.unwrap()
        ops.object.mode_set(mode="OBJECT")

    def _check_vertex_limit(self, ob: bpy.types.Object, result: BakeResult) -> None:
        if State.compiler > Compiler.STUDIOMDL or State.datamodelFormat >= 22:
            return
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_ob = ob.evaluated_get(depsgraph)
        try:
            mesh = eval_ob.to_mesh()
            count = len(mesh.vertices)
            print(f"- Vertices count for {result.name}: {count}")
            #if count > 16384:
            #    self._exporter.warning(f"Vertices count for {result.name} is over 16384!")
        finally:
            eval_ob.to_mesh_clear()
