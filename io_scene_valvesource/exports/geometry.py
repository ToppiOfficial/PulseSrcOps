import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from .. import datamodel, ordered_set, flex
from .records import BakedVertexAnimation, BakeResult, ExportTask, _MeshPlan


class LODBuilder:
    def __init__(self, reporter):
        self._reporter = reporter

    def build_all(self, ob: bpy.types.Object, export_name: str) -> list[tuple[int, bpy.types.Object]]:
        """
        Returns [(lod_index, lod_ob), ...] for each LOD level.
        Caller owns the returned objects and must remove them when done.
        """
        if pulsemodel_compiler_enabled():
            return []
        results = []
        for idx in range(1, ob.vs.lod_count + 1):
            ratio = max(0.0, 1.0 - (ob.vs.decimate_factor / 100.0) * idx)
            if ratio <= 0.0:
                self._reporter.warning(
                    f"LOD{idx} for '{export_name}' skipped: decimate ratio reached 0."
                )
                break

            lod = ob.copy()
            lod.data = ob.data.copy()
            lod.name = f"{export_name}_lod{idx}"
            bpy.context.scene.collection.objects.link(lod)

            # LODs don't need morph targets.
            if lod.data.shape_keys:
                lod.shape_key_clear()

            # Decimate corrupts custom split normals; reset to auto-computed.
            # This is horrible but Blender forced my hands.
            prev_active = bpy.context.view_layer.objects.active
            bpy.context.view_layer.objects.active = lod
            select_only(lod)
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.normals_tools(mode='RESET')
            bpy.ops.object.mode_set(mode='OBJECT')
            bpy.context.view_layer.objects.active = prev_active

            mod = lod.modifiers.new(name="Decimate_LOD", type="DECIMATE")
            mod.ratio = ratio
            lod.vs.generate_lods = False
            if not pulsemodel_vertex_data_enabled():
                lod.vs.use_toon_edgeline = False
            lod.vs.export_edgeline_separately = False

            results.append((idx, lod))
        return results


class EdgelineBuilder:
    EDGELINE_MAT = "edgeline"
    TEMP_MAT = "temp_material"
    SOLIDIFY_MOD = "Toon_Edgeline"

    def __init__(self, reporter, merge_fn=None):
        self._reporter = reporter
        self._merge_fn = merge_fn

    def build(self, ob: bpy.types.Object, export_name: str) -> typing.Optional[bpy.types.Object]:
        """
        Builds the edgeline mesh.
        - If ob.vs.export_edgeline_separately: returns a new separate object.
        - Otherwise: modifies ob in-place and returns ob.
        Returns None if nothing should be done.
        """
        if pulsemodel_vertex_data_enabled() or not ob.vs.use_toon_edgeline:
            return None
        if not is_mesh_compatible(ob):
            return None
        
        # Likely a physics mesh? "apply_edgeline_materials" does support no material but I keep running into the habit
        # of exporting ragdoll/collision mesh with outline which is catastrophic.
        if not ob.data or not ob.data.materials:
            self._reporter.warning(f"Toon Edgeline is disabled due to lacking of materials in {export_name}")
            return None

        if self._all_faces_suppressed(ob):
            return None

        base_name = re.sub(r"_lod[1-9]\d*$", "", export_name)
        temp = self._make_temp_copy(ob)
        try:
            material_count = self._ensure_material(temp)
            thickness_vg = self._build_thickness_vg(ob, temp)
            self._apply_edgeline_materials(temp, material_count, ob.vs.edgeline_per_material, ob.vs.toon_edgeline_vertexgroup, ob)
            self._apply_solidify(temp, ob, thickness_vg, material_count)

            if not ob.vs.export_edgeline_separately:
                self._merge_into_source(ob, temp)
                return ob

            return self._make_separate_object(temp, ob, base_name)
        finally:
            self._cleanup_temp(temp)

    # -- private helpers ------------------------------------------------------

    def _make_temp_copy(self, ob: bpy.types.Object) -> bpy.types.Object:
        temp = ob.copy()
        temp.data = ob.data.copy()
        temp.name = ob.name + "_edgeline_temp"
        bpy.context.scene.collection.objects.link(temp)

        if temp.type != "MESH":
            bpy.context.view_layer.objects.active = temp
            bpy.ops.object.convert(target="MESH")
            bpy.context.view_layer.objects.active = ob

        if self._merge_fn is not None and ob.vs.edgeline_weld and countShapes(ob) == (0, 0):
            self._merge_fn(temp)

        return temp

    def _ensure_material(self, temp: bpy.types.Object) -> int:
        if not temp.data.materials:
            mat = bpy.data.materials.get(self.TEMP_MAT) or bpy.data.materials.new(name=self.TEMP_MAT)
            temp.data.materials.append(mat)
            temp.vs.edgeline_per_material = False
        return len(temp.data.materials)

    def _build_thickness_vg(self, ob: bpy.types.Object, temp: bpy.types.Object) -> typing.Optional[bpy.types.VertexGroup]:
        if not getattr(temp, "vertex_groups", None):
            return None

        vg_name = ob.vs.toon_edgeline_vertexgroup
        if not vg_name:
            return None

        vg = temp.vertex_groups.get(vg_name)
        if vg is None:
            self._reporter.warning(
                f"Toon edgeline vertex group '{vg_name}' not found on '{ob.name}'; "
                f"thickness weighting will be skipped."
            )
            return None

        return vg

    def _apply_edgeline_materials(
        self,
        temp: bpy.types.Object,
        material_count: int,
        per_material: bool,
        vg_name: str,
        ob: bpy.types.Object,
    ) -> None:
        """
        Appends edgeline material variants to temp.
        All non-exportable filtering is driven purely by ob.vs (per-object).
        No material-level filter properties are read or written.
        """
        slots = list(temp.material_slots)
        ob_vg     = ob.vs.non_exportable_vgroup
        ob_vg_tol = ob.vs.non_exportable_vgroup_tolerance

        def make_edgeline_mat(slot):
            name = f"{slot.material.name}_edgeline" if slot.material else self.EDGELINE_MAT
            mat = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)

            if slot.material:
                mat.vs.material_path_index = slot.material.vs.material_path_index

            return mat

        if per_material:
            for slot in slots:
                temp.data.materials.append(make_edgeline_mat(slot))
        else:
            if ob_vg:
                # Object has a non-exportable vgroup - need per-slot edgeline mats
                # so each one can be individually resolved during export.
                for slot in slots:
                    temp.data.materials.append(make_edgeline_mat(slot))
            else:
                # No filtering at all - one shared edgeline material covers everything.
                mat = bpy.data.materials.get(self.EDGELINE_MAT) or bpy.data.materials.new(name=self.EDGELINE_MAT)
                for _ in range(material_count):
                    temp.data.materials.append(mat)

    def _apply_solidify(self, temp: bpy.types.Object, ob: bpy.types.Object, vg, material_count: int) -> None:
        solid = temp.modifiers.get(self.SOLIDIFY_MOD) or temp.modifiers.new(name=self.SOLIDIFY_MOD, type="SOLIDIFY")
        solid.use_rim = False
        solid.use_flip_normals = True
        solid.material_offset = material_count
        solid.offset = -1.0
        solid.thickness = -1 * round(ob.vs.base_toon_edgeline_thickness, 3)
        solid.use_quality_normals = True
        solid.thickness_clamp = 3.5

        if vg:
            solid.vertex_group = vg.name
            solid.invert_vertex_group = True

    def _merge_into_source(self, ob: bpy.types.Object, temp: bpy.types.Object) -> None:
        ob.data = temp.data
        for vg in temp.vertex_groups:
            if ob.vertex_groups.get(vg.name) is None:
                ob.vertex_groups.new(name=vg.name)
        for mod in temp.modifiers:
            if ob.modifiers.get(mod.name) is None:
                new_mod = ob.modifiers.new(name=mod.name, type=mod.type)
                for attr in [a for a in dir(mod) if not a.startswith("_") and a not in ("bl_rna", "rna_type", "type", "name")]:
                    try:
                        setattr(new_mod, attr, getattr(mod, attr))
                    except (AttributeError, TypeError):
                        pass

    def _make_separate_object(self, temp: bpy.types.Object, ob: bpy.types.Object, base_name: str) -> bpy.types.Object:
        edgeline = temp.copy()
        edgeline.data = temp.data.copy()
        edgeline.name = base_name + "_edgeline"
        bpy.context.scene.collection.objects.link(edgeline)

        edgeline.vs.use_toon_edgeline = False
        edgeline.vs.export_edgeline_separately = False
        edgeline.vs.generate_lods = False
        edgeline["is_edgeline_only"] = True

        no_mat = edgeline.data.materials.find(self.TEMP_MAT)
        if no_mat != -1:
            edgeline.data.materials.pop(index=no_mat)

        return edgeline

    def _cleanup_temp(self, temp: bpy.types.Object) -> None:
        no_mat = bpy.data.materials.get(self.TEMP_MAT)
        if no_mat and no_mat.users == 0:
            bpy.data.materials.remove(no_mat)
        if temp.name in bpy.data.objects:
            bpy.context.scene.collection.objects.unlink(temp)
            bpy.data.objects.remove(temp, do_unlink=True)

    def _all_faces_suppressed(self, ob: bpy.types.Object) -> bool:
        """True if every polygon would be removed by the edgeline thickness VG filter."""
        vg_name = ob.vs.toon_edgeline_vertexgroup
        if not vg_name:
            return False
        vg = ob.vertex_groups.get(vg_name) if getattr(ob, "vertex_groups", None) else None
        if vg is None:
            return False
        EDGE_VG_TOL = 0.90
        suppressed = frozenset(
            v.index for v in ob.data.vertices
            if any(g.group == vg.index and g.weight >= EDGE_VG_TOL for g in v.groups)
        )
        if not suppressed:
            return False
        return all(all(vi in suppressed for vi in poly.vertices) for poly in ob.data.polygons)


class BackfaceBuilder:
    def __init__(self, reporter):
        self._reporter = reporter

    def build(self, ob: bpy.types.Object, export_name: str) -> typing.Optional[bpy.types.Object]:
        """
        Returns a new object containing only the vgroup-weighted faces with flipped normals.
        Caller owns the returned object. Returns None if not applicable or no geometry.
        """
        if not ob.vs.generate_backface:
            return None
        if not is_mesh_compatible(ob):
            return None
        if not ob.data or not ob.data.materials:
            self._reporter.warning(f"Backface disabled due to lack of materials on '{export_name}'")
            return None

        vg_name = ob.vs.backface_vgroup
        if not vg_name:
            return None
        vg = ob.vertex_groups.get(vg_name)
        if vg is None:
            self._reporter.warning(
                f"Backface vertex group '{vg_name}' not found on '{ob.name}'; skipping."
            )
            return None

        tol = ob.vs.backface_vgroup_tolerance
        base_name = re.sub(r"_lod[1-9]\d*$", "", export_name)

        bf = ob.copy()
        bf.data = ob.data.copy()
        bf.name = base_name + "_backface"
        bpy.context.scene.collection.objects.link(bf)

        me = bf.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        deform = bm.verts.layers.deform.active
        if deform is None:
            bm.free()
            bpy.context.scene.collection.objects.unlink(bf)
            bpy.data.objects.remove(bf, do_unlink=True)
            return None

        def all_verts_weighted(face):
            return all(
                vg.index in v[deform] and v[deform][vg.index] >= tol
                for v in face.verts
            )

        # Non-exportable faces are excluded here - the nonexp_vg filter in
        # Baker._delete_filtered_faces handles them on the baked result, matching
        # the same rule applied to edgeline faces.
        faces_to_delete = [f for f in bm.faces if not all_verts_weighted(f)]
        if faces_to_delete:
            bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")

        if not bm.faces:
            bm.free()
            bpy.context.scene.collection.objects.unlink(bf)
            bpy.data.objects.remove(bf, do_unlink=True)
            return None

        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(me)
        bm.free()
        me.update()

        bf["is_backface_only"] = True
        bf.vs.generate_backface = False
        bf.vs.use_toon_edgeline = False
        bf.vs.generate_lods = False

        return bf

    def build_merged(self, ob: bpy.types.Object, export_name: str) -> bool:
        """
        Appends flipped duplicates of the vgroup-weighted faces into ob.data in-place.
        Used for SMD export (single mesh object per file). Non-exportable faces are left
        for Baker._delete_filtered_faces to remove via the nonexp_vg filter.
        Returns True if any faces were added.
        """
        if not ob.vs.generate_backface:
            return False
        if not is_mesh_compatible(ob):
            return False
        if not ob.data or not ob.data.materials:
            self._reporter.warning(f"Backface disabled due to lack of materials on '{export_name}'")
            return False

        vg_name = ob.vs.backface_vgroup
        if not vg_name:
            return False
        vg = ob.vertex_groups.get(vg_name)
        if vg is None:
            self._reporter.warning(
                f"Backface vertex group '{vg_name}' not found on '{ob.name}'; skipping."
            )
            return False

        tol = ob.vs.backface_vgroup_tolerance
        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        deform = bm.verts.layers.deform.active
        if deform is None:
            bm.free()
            return False

        def all_verts_weighted(face):
            return all(
                vg.index in v[deform] and v[deform][vg.index] >= tol
                for v in face.verts
            )

        candidates = [f for f in bm.faces if all_verts_weighted(f)]
        if not candidates:
            bm.free()
            return False

        result = bmesh.ops.duplicate(bm, geom=candidates)
        new_faces = [e for e in result["geom"] if isinstance(e, bmesh.types.BMFace)]
        if new_faces:
            bmesh.ops.reverse_faces(bm, faces=new_faces)

        bm.to_mesh(me)
        bm.free()
        me.update()
        return bool(new_faces)
