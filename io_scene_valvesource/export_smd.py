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


# -----------------------------------------------------------------------------
# Data types
# -----------------------------------------------------------------------------

class BakedVertexAnimation(list):
    def __init__(self):
        super().__init__()
        self.export_sequence = False
        self.bone_id = -1
        self.num_frames = 0


class BakeResult:
    def __init__(self, name: str):
        self.name = name
        self.object: bpy.types.Object = None
        self.matrix = Matrix()
        self.envelope = None
        self.bone_parent_matrix = None
        self.src: bpy.types.Object = None
        self.armature: "BakeResult" = None
        self.balance_vg = None
        self.shapes = collections.OrderedDict()
        self.vertex_animations = collections.defaultdict(BakedVertexAnimation)


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
# LOD builder
# Produces decimated copies of a source object for each LOD level.
# -----------------------------------------------------------------------------

class LODBuilder:
    def __init__(self, reporter):
        self._reporter = reporter

    def build_all(self, ob: bpy.types.Object, export_name: str) -> list[tuple[int, bpy.types.Object]]:
        """
        Returns [(lod_index, lod_ob), ...] for each LOD level.
        Caller owns the returned objects and must remove them when done.
        """
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
            lod.vs.use_toon_edgeline = False
            lod.vs.export_edgeline_separately = False

            results.append((idx, lod))
        return results


# -----------------------------------------------------------------------------
# Edgeline builder
# Produces a solidified, normal-flipped copy of a mesh for toon edgeline use.
# -----------------------------------------------------------------------------

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
        if not ob.vs.use_toon_edgeline:
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

        if self._merge_fn is not None and countShapes(ob) == (0, 0):
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

            if slot.material and slot.material.vs.override_dmx_export_path.strip():
                mat.vs.override_dmx_export_path = slot.material.vs.override_dmx_export_path

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


# -----------------------------------------------------------------------------
# Backface builder
# Produces a backface for double sided faces
# NOTE: Not to be confused with $nocull 1, this is for cases where to have backface
# but not render the lighting on both side.
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# MeshsplitBuilder
# Splits a mesh object into per-order submeshes.
# Each "mesh split {n}" vertex group drives one split: faces where every vertex
# meets the weight threshold are peeled off into a new object named
# "{base_name}_split{n}".  Those faces are then removed from the original.
#
# Parameters mirrored from EdgelineBuilder / BackfaceBuilder:
#   ob.vs.use_mesh_split            – master enable/disable
#   ob.vs.export_mesh_split_separately   – True  -> separate ExportTask per order
#                                          False -> companions in the same task
#   ob.vs.mesh_split_threshold  – per-vertex weight threshold (0.8–1.0)
#   ob.vs.max_mesh_split        – cap on n (1–MAX_MESH_SPLIT)
# -----------------------------------------------------------------------------
 
class MeshSplitBuilder:
    def __init__(self, reporter):
        self._reporter = reporter
 
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
 
    def build(
        self,
        ob: bpy.types.Object,
        export_name: str,
    ) -> list[bpy.types.Object]:
        """
        Splits obf in-place (removing the order-assigned faces from ob.data)
        and returns a list of new objects, one per discovered order group.
 
        The caller owns all returned objects and must unlink/remove them when done.
        Returns an empty list if the feature is disabled or no groups are found.
 
        NOTE: ob is modified in-place - order-group faces are deleted from it.
        """
        if not ob.vs.use_mesh_split:
            return []
        if not self._is_mesh_compatible(ob):
            return []
        if not ob.data or not ob.data.materials:
            self._reporter.warning(
                f"Mesh Split separation disabled on '{export_name}': no materials."
            )
            return []
 
        threshold = ob.vs.mesh_split_threshold
        max_n     = min(ob.vs.max_mesh_split, MAX_MESH_SPLIT)
 
        # Collect valid split groups present on this object, sorted by n.
        split_groups: list[tuple[int, bpy.types.VertexGroup]] = []
        for vg in ob.vertex_groups:
            n = parse_order_vg_name(vg.name)
            if n is None or n >= max_n:
                continue
            split_groups.append((n, vg))
        split_groups.sort(key=lambda x: x[0])
 
        if not split_groups:
            return []
 
        base_name = re.sub(r"_lod[1-9]\d*$", "", export_name)
        results: list[bpy.types.Object] = []
 
        total_faces = len(ob.data.polygons)
        all_claimed: set[int] = set()
 
        for n, vg in split_groups:
            order_faces = self._faces_for_vg(ob, vg, threshold)
            if not order_faces:
                continue
 
            # Overlap correction: lowest split_n wins because we sorted split_groups.
            overlap = order_faces & all_claimed
            if overlap:
                self._reporter.warning(
                    f"Mesh Split overlap on '{export_name}': Group {n} ('{vg.name}') "
                    f"shares {len(overlap)} faces with higher-priority groups."
                )

            # Skip faces already claimed by an earlier (lower) split.
            new_faces = order_faces - all_claimed
            if not new_faces:
                continue

            # Base mesh integrity security: don't leave the base mesh empty.
            if len(all_claimed) + len(new_faces) >= total_faces:
                self._reporter.warning(
                    f"Mesh Split separation skipped for Group {n} ('{vg.name}') on '{export_name}': "
                    f"at least one face must remain in the base mesh."
                )
                continue
 
            split_ob = self._extract_faces(ob, new_faces, f"{base_name}_split{n}")
            if split_ob is None:
                continue
            
            all_claimed |= new_faces
 
            split_ob["is_mesh_split"] = True
            split_ob["mesh_split_n"]  = n
            split_ob.vs.use_mesh_split = False   # prevent recursion
            split_ob.vs.generate_lods       = False   # LOD intentionally excluded
            results.append(split_ob)
 
        # Remove all claimed faces from the original mesh.
        if all_claimed:
            self._delete_faces_from_ob(ob, all_claimed)
 
        return results
 
    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
 
    @staticmethod
    def _is_mesh_compatible(ob: bpy.types.Object) -> bool:
        return ob is not None and ob.type == "MESH"
 
    def _faces_for_vg(
        self,
        ob: bpy.types.Object,
        vg: bpy.types.VertexGroup,
        threshold: float,
    ) -> set[int]:
        """Return the set of face indices where every vertex meets the threshold."""
        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
 
        deform = bm.verts.layers.deform.active
        if deform is None:
            bm.free()
            return set()
 
        result = {
            f.index
            for f in bm.faces
            if all(
                vg.index in v[deform] and v[deform][vg.index] >= threshold
                for v in f.verts
            )
        }
        bm.free()
        return result
 
    def _extract_faces(
        self,
        ob: bpy.types.Object,
        face_indices: set[int],
        new_name: str,
    ) -> bpy.types.Object | None:
        """
        Create a new object containing only the faces in face_indices,
        with split normals preserved via bpy.ops.mesh.split().
 
        Returns the new object (already linked to the scene collection),
        or None if no geometry remained after extraction.
        """
        # Duplicate the source object so we can destructively trim it
        copy = ob.copy()
        copy.data = ob.data.copy()
        copy.name = new_name
        bpy.context.scene.collection.objects.link(copy)
 
        # Delete faces NOT in face_indices from the copy
        me = copy.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
 
        to_delete = [f for f in bm.faces if f.index not in face_indices]
        if to_delete:
            bmesh.ops.delete(bm, geom=to_delete, context="FACES")
 
        if not bm.faces:
            bm.free()
            bpy.context.scene.collection.objects.unlink(copy)
            bpy.data.objects.remove(copy, do_unlink=True)
            return None
 
        bm.to_mesh(me)
        bm.free()
        me.update()
 
        # Split the mesh to preserve custom split normals
        # bpy.ops.mesh.split() duplicates shared verts at seam edges so
        # each face owns its own loop normals independently.
        prev_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = copy
        select_only(copy)
 
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.split()    # preserves custom split normals
        bpy.ops.object.mode_set(mode="OBJECT")
 
        bpy.context.view_layer.objects.active = prev_active
        return copy
 
    @staticmethod
    def _delete_faces_from_ob(ob: bpy.types.Object, face_indices: set[int]) -> None:
        """Remove the given face indices from ob.data in-place."""
        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        geom = [f for f in bm.faces if f.index in face_indices]
        if geom:
            bmesh.ops.delete(bm, geom=geom, context="FACES")
        bm.to_mesh(me)
        bm.free()
        me.update()


# -----------------------------------------------------------------------------
# Export planning
#
# ExportPlanner takes a single export target (Collection or Object) and returns
# a flat, ordered list of ExportTask objects covering:
#   - the base export
#   - any LOD variants (one task per LOD level)
#   - any edgeline variants
#
# All temporary Blender objects are tracked and cleaned up via cleanup().
# This replaces the pending_decimation_exports / pending_edgeline_exports pattern.
# -----------------------------------------------------------------------------

class ExportTask:
    def __init__(self, source_id, export_name: str, allowed_uids: set = None, companions: list = None):
        self.source_id = source_id
        self.export_name = export_name
        self.allowed_uids = allowed_uids if allowed_uids is not None else set()
        self.companions = companions if companions is not None else []

    def __repr__(self):
        return f"<ExportTask {self.export_name!r}>"


@dataclasses.dataclass
class _SplitPart:
    ob:       bpy.types.Object
    name:     str
    edgeline: typing.Optional[bpy.types.Object]
    backface: typing.Optional[bpy.types.Object]


@dataclasses.dataclass
class _MeshPlan:
    source:        bpy.types.Object
    target:        bpy.types.Object
    lod_source:    typing.Optional[bpy.types.Object]
    base_edgeline: typing.Optional[bpy.types.Object]
    base_backface: typing.Optional[bpy.types.Object]
    split_parts:   list


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

        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.normal_update()

        raw_map = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=MERGE_DIST)["targetmap"]

        if not raw_map:
            bm.free()
            return

        protected_indices = set()
        for src, tgt in raw_map.items():
            if any(
                n1.dot(n2) < OPPOSING_DOT
                for n1 in (f.normal for f in src.link_faces)
                for n2 in (f.normal for f in tgt.link_faces)
            ):
                protected_indices.add(src.index)
                protected_indices.add(tgt.index)

        # -- Collect post-process VG names to preserve ----------------------------
        pp_vg_names = set()
        for name in (
            getattr(ob.vs, "toon_edgeline_vertexgroup", ""),
            getattr(ob.vs, "backface_vgroup", ""),
            getattr(ob.vs, "non_exportable_vgroup", ""),
        ):
            if name:
                pp_vg_names.add(name)

        pp_vg_index_to_name = {
            vg.index: vg.name
            for vg in ob.vertex_groups
            if vg.name in pp_vg_names
        }

        # Snapshot: target vert position (tuple) -> {vg_name: max_weight}
        # We accumulate max weights from both the target vert itself and any
        # src verts that would merge into it - all resolved now while BMVerts
        # still map 1:1 with me.vertices indices.
        pos_to_weights: dict[tuple, dict[str, float]] = {}

        if pp_vg_index_to_name:
            def _vert_pp_weights(vert_index: int) -> dict[str, float]:
                return {
                    pp_vg_index_to_name[g.group]: g.weight
                    for g in me.vertices[vert_index].groups
                    if g.group in pp_vg_index_to_name
                }

            def _accumulate(pos_key: tuple, weights: dict[str, float]) -> None:
                if not weights:
                    return
                entry = pos_to_weights.setdefault(pos_key, {})
                for vg_name, w in weights.items():
                    if w > entry.get(vg_name, 0.0):
                        entry[vg_name] = w

            def _ultimate_tgt(bv):
                # find_doubles can chain (A->B->C); follow to the vertex that survives.
                seen = set()
                while bv in raw_map:
                    if bv in seen:
                        break
                    seen.add(bv)
                    bv = raw_map[bv]
                return bv

            for src_bv, tgt_bv in raw_map.items():
                src_w = _vert_pp_weights(src_bv.index)
                tgt_w = _vert_pp_weights(tgt_bv.index)
                if not src_w and not tgt_w:
                    continue
                # Follow the merge chain: tgt_bv itself may be merged further.
                final_tgt = _ultimate_tgt(tgt_bv)
                pos_key = (round(final_tgt.co.x, 6), round(final_tgt.co.y, 6), round(final_tgt.co.z, 6))
                _accumulate(pos_key, tgt_w)
                _accumulate(pos_key, src_w)

        bm.free()

        # Deselecting protected vertices excludes them from the operator's scope.
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

        # Restore post-process weights by position lookup
        if pos_to_weights:
            for v in me.vertices:
                pos_key = (round(v.co.x, 6), round(v.co.y, 6), round(v.co.z, 6))
                target_weights = pos_to_weights.get(pos_key)
                if not target_weights:
                    continue
                for vg_name, w in target_weights.items():
                    vg = ob.vertex_groups.get(vg_name)
                    if vg:
                        vg.add([v.index], w, 'REPLACE')

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
            if not ob.vs.export:
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
            if ob.vs.export and not ob.vs.generate_lods:
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


# -----------------------------------------------------------------------------
# Baker
#
# Bakes an Object or Collection into BakeResult(s).
# Maintains a cache so the same source is never baked twice per export.
# -----------------------------------------------------------------------------

class Baker:
    def __init__(self, exporter: "SmdExporter"):
        self._exporter = exporter
        self._cache: dict[int, BakeResult] = {}  # session_uid -> BakeResult

    def bake(self, ob: bpy.types.Object) -> typing.Optional[BakeResult]:
        uid = ob.session_uid
        if uid in self._cache:
            return self._cache[uid]

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

        if source_ob.vs.flex_controller_mode == "BUILDER":
            shapes_to_process = []
            for delta_name, shape_name in self._exporter.get_delta_shapekeys(source_ob):
                idx = source_ob.data.shape_keys.key_blocks.find(shape_name)
                if idx != -1:
                    shape = source_ob.data.shape_keys.key_blocks[idx]
                    if delta_name != shape.name:
                        shape.name = delta_name
                    shapes_to_process.append((idx, shape))
        else:
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

        for export_type, _count in prefab_available_types(arm):
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
        write_func = self.writeDMX if State.exportFormat == ExportFormat.DMX else self.writeSMD
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

        original_pose = self.armature_src.data.pose_position
        self.armature_src.data.pose_position = "REST"
        bpy.context.view_layer.update()

        self.exportable_empties = [
            (e, e.matrix_world.copy())
            for e in bpy.data.objects
            if e.type == "EMPTY"
            and e.parent == self.armature_src
            and e.parent_type == "BONE"
            and e.parent_bone in [pb.name for pb in self.armature.pose.bones]
            and isinstance(getattr(e.vs, "dmx_attachment", None), bool)
            and e.vs.dmx_attachment
        ]

        self.armature_src.data.pose_position = original_pose
        bpy.context.view_layer.update()
        
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

    def get_delta_shapekeys(self, ob: bpy.types.Object) -> list[tuple[str, str]]:
        if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_flexcontrollers"):
            return []
        valid_keys = set(ob.data.shape_keys.key_blocks.keys()[1:]) if ob.data.shape_keys else set()
        seen = set()
        result = []
        for fc in ob.vs.dme_flexcontrollers:
            if fc.shapekey not in valid_keys:
                continue
            raw = fc.raw_delta_name.strip() if fc.raw_delta_name and fc.raw_delta_name.strip() else fc.shapekey
            delta = sanitize_string_for_delta(raw)
            if delta not in seen:
                seen.add(delta)
                result.append((delta, fc.shapekey))
        return result

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
                anim_len = animationLength(self.armature.animation_data) + 1 if is_anim else 1

                if not is_anim:
                    for pb in self.armature.pose.bones:
                        pb.matrix_basis.identity()
                elif self.armature.data.vs.reset_pose_per_anim:
                    for pb in self.armature.pose.bones:
                        pb.matrix_basis.identity()

                for i in range(anim_len):
                    bpy.context.window_manager.progress_update(i / anim_len)
                    self.smd_file.write(f"time {i}\n")
                    if self.armature.data.vs.implicit_zero_bone:
                        self.smd_file.write("0  0 0 0  0 0 0\n")
                    if is_anim:
                        bpy.context.scene.frame_set(i)

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
                for pb in self.armature.pose.bones:
                    pb.matrix_basis.identity()
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
            add_implicit = not source2
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

            # NOTE: DmeAimAtBone aims directly at the driver bone's skeleton joint
            # (aimTarget = its DMX name below). The VRD path aims at a {base}_lookat
            # attachment placed at lookat_offset, but attachment-input aim targets
            # are not implemented for DMX export yet, so the offset is dropped and no
            # _lookat attachment is written here.

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
                    # Aim at the actual driver bone's DMX joint (attachment-input
                    # targets aren't implemented for DMX yet, so the _lookat
                    # attachment name is not used).
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
            hboxset_name = getattr(havs, 'hboxset_name', '').strip() if havs else ''

            if valid_hbox and hboxset_name:
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
                DmeCombinationOperator = flex.DmxWriteFlexControllers.make_controllers(datablock, export=True).root["combinationOperator"]
            break

        if not DmeCombinationOperator and bake_results[0].vertex_animations:
            DmeCombinationOperator = flex.DmxWriteFlexControllers.make_controllers(datablock, export=True).root["combinationOperator"]

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
            anim_len = animationLength(ad) if ad else 0
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
                bpy.context.scene.frame_set(frame)
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

        hboxset = getattr(avs, 'hboxset_name', '').strip()
        if not hboxset:
            self.report({'WARNING'},
                "Hitbox export skipped: no HBox Set name is configured. "
                "Set a HBox Set name on the armature to export hitboxes.")
            return None, None

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
        capsule_support = getattr(avs, 'hbox_capsule_support', False)

        if not capsule_support:
            skipped_capsules  = [e.bone_name for e in valid if e.scale > 0.0]
            skipped_rotations = [e.bone_name for e in valid
                                 if any(abs(r) > 1e-6 for r in e.rotation)]
            if skipped_capsules:
                self.report({'WARNING'},
                    f"Capsule Support is disabled : {len(skipped_capsules)} capsule hitbox(es) will be "
                    f"exported as boxes (bones: {', '.join(skipped_capsules)})")
            if skipped_rotations:
                self.report({'WARNING'},
                    f"Capsule Support is disabled : rotation is ignored on {len(skipped_rotations)} "
                    f"hitbox(es) (bones: {', '.join(skipped_rotations)})")

        lines = []
        lines.append(f'$hboxset\t"{hboxset}"')
        for bone in sorted_bones:
            for e in seen_bones[bone]:
                lines.append(_hitbox.qc_line(e, get_bone_exportname(bone), capsule_support))
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