import bpy, bmesh, os
import numpy as np
from mathutils import Vector, Matrix

from ..utils import *
from .. import ordered_set

from .records import BakeResult


def _mesh_arrays(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    loop_verts = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loop_verts)
    normals = np.empty(len(me.loops) * 3, np.float32)
    me.loops.foreach_get("normal", normals)
    return co.reshape(-1, 3), loop_verts, normals.reshape(-1, 3)


def _vertex_group_weights(me) -> list:
    # Per-vertex [(group_index, weight), ...] in v.groups order, read through BMesh
    # because vertex groups have no foreach_get path.
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        layer = bm.verts.layers.deform.active
        if layer is None:
            return [()] * len(bm.verts)
        return [v[layer].items() for v in bm.verts]
    finally:
        bm.free()


def _fmt_rows(fmt, rows):
    # One C-level % over the whole block; tolist() widens float32 exactly like float(v) did.
    if not len(rows):
        return []
    return ((fmt + "\0") * len(rows) % tuple(rows.ravel().tolist())).split("\0")[:-1]


def _fmt_vta_rows(ids, co, normals):
    rows = np.empty((len(ids), 7))
    rows[:, 0] = ids
    rows[:, 1:4] = co
    rows[:, 4:] = normals
    return "%d %.6f %.6f %.6f %.6f %.6f %.6f\n" * len(rows) % tuple(rows.ravel().tolist())


_EPSILON = np.float64(epsilon[0])
_EPSILON_LEN_SQ = (_EPSILON * _EPSILON + _EPSILON * _EPSILON) + _EPSILON * _EPSILON

def _exceeds_epsilon(delta):
    # Matches mathutils `Vector > epsilon`: float32 delta, float64 squared length summed z, y, x.
    d = delta.astype(np.float64)
    return (d[:, 2] * d[:, 2] + d[:, 1] * d[:, 1]) + d[:, 0] * d[:, 0] > _EPSILON_LEN_SQ


class SmdWriter:
    def __init__(self, reporter, id, bake_results, name, dir_path, filetype="smd", *,
                 armature, armature_src, exportable_bones, exportable_boneNames,
                 all_bake_results):
        self.r = reporter
        self.id = id
        self.bake_results = bake_results
        self.name = name
        self.dir_path = dir_path
        self.filetype = filetype
        self.armature = armature
        self.armature_src = armature_src
        self.exportable_bones = exportable_bones
        self.exportable_boneNames = exportable_boneNames
        self.all_bake_results = all_bake_results
        self.bone_ids: dict[str, int] = {}

    def _warning(self, *a): self.r.warning(*a)
    def _error(self, *a): self.r.error(*a)

    # -----------------------------------------------------------------------
    def _open(self, path, name, description):
        full_path = os.path.realpath(os.path.join(path, name))
        try:
            f = open(full_path, "w", encoding="utf-8")
        except Exception as err:
            self._error(get_id("exporter_err_open", True).format(description, err))
            return None
        f.write("version 1\n")
        print("-", full_path)
        return f

    def write(self) -> int:
        bench = BenchMarker(1, "SMD")
        self.goldsrc = bpy.context.scene.vs.smd_format == "GOLDSOURCE"

        self.smd_file = self._open(
            self.dir_path,
            sanitize_string(self.name, allow_unicode=True) + "." + self.filetype,
            self.filetype.upper())
        if self.smd_file is None:
            return 0

        if State.compiler > Compiler.STUDIOMDL:
            self._warning(get_id("exporter_warn_source2smdsupport"))

        self._write_nodes()
        bench.report("nodes")

        if self.filetype == "smd":
            self._write_skeleton()
            bench.report("skeleton")
            self._write_triangles()
            bench.report("triangles")
        elif self.filetype == "vta":
            self._write_vta()
            bench.report("vertex animation")

        self.smd_file.close()
        bench.report("close")
        if bench.quiet:
            print(f"- {self.filetype.upper()} export took", bench.total(), "\n")

        written = 1
        if self.filetype == "smd":
            for bake in [b for b in self.bake_results if b.shapes]:
                written += self._sibling("vta").write()
            for vca_name, vca in self.bake_results[0].vertex_animations.items():
                written += self._write_vca(vca_name, vca)
                if vca.export_sequence:
                    written += self._write_vca_sequence(vca_name, vca)
        return written

    def _sibling(self, filetype):
        return SmdWriter(
            self.r, self.id, self.bake_results, self.name, self.dir_path, filetype,
            armature=self.armature, armature_src=self.armature_src,
            exportable_bones=self.exportable_bones,
            exportable_boneNames=self.exportable_boneNames,
            all_bake_results=self.all_bake_results)

    # -- nodes --------------------------------------------------------------
    def _write_nodes(self):
        f = self.smd_file
        f.write("nodes\n")
        curID = 0
        if not self.armature:
            f.write("0 \"root\" -1\n")
            if self.filetype == "smd":
                print("- No skeleton to export")
        else:
            if self.armature.data.vs.implicit_zero_bone:
                f.write(f"0 \"{implicit_bone_name}\" -1\n")
                curID += 1

            for bone in self.exportable_bones:
                parent = bone.parent
                while parent and parent not in self.exportable_bones:
                    parent = parent.parent

                self.bone_ids[bone.name] = curID
                bone_name = self.exportable_boneNames[bone.name]
                parent_id = str(self.bone_ids[parent.name]) if parent else "-1"
                f.write(f"{curID} \"{bone_name}\" {parent_id}\n")
                curID += 1

            num_bones = len(self.armature.data.bones)
            if self.filetype == "smd":
                print(f"- Exported {num_bones} bones")
            if num_bones > 128:
                self._warning(get_id("exporter_err_bonelimit", True).format(num_bones, 128))

        for vca in [v for v in self.bake_results[0].vertex_animations.items() if v[1].export_sequence]:
            curID += 1
            vca[1].bone_id = curID
            f.write(f"{curID} \"vcabone_{vca[0]}\" -1\n")

        f.write("end\n")

    # -- skeleton (reference pose or animation frames) ----------------------
    def _write_skeleton(self):
        f = self.smd_file
        f.write("skeleton\n")
        if not self.armature:
            f.write("time 0\n0 0 0 0 0 0 0\nend\n")
            return

        is_anim = len(self.bake_results) == 1 and self.bake_results[0].object.type == "ARMATURE"
        # first_frame lets actions that don't start on frame 0 export their real motion: we
        # sample scene frames first_frame..first_frame+span but keep the SMD "time" 0-based.
        first_frame, span = animationFrameRange(self.armature.animation_data) if is_anim else (0, 0)
        anim_len = span + 1 if is_anim else 1

        if not is_anim:
            for pb in self.armature.pose.bones:
                pb.matrix_basis.identity()
        elif self.armature.data.vs.reset_pose_per_anim:
            self.r.warnUnkeyframedPose(self.name)
            for pb in self.armature.pose.bones:
                pb.matrix_basis.identity()
        else:
            self.r.applyUnkeyframedSourcePose()

        for i in range(anim_len):
            bpy.context.window_manager.progress_update(i / anim_len)
            f.write(f"time {i}\n")
            if self.armature.data.vs.implicit_zero_bone:
                f.write("0  0 0 0  0 0 0\n")
            if is_anim:
                bpy.context.scene.frame_set(first_frame + i)

            evaluated = self._evaluated_pose_bones()
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

                f.write(f"{self.bone_ids[pb.name]}  {getSmdVec(mat.to_translation())}  {getSmdVec(mat.to_euler())}\n")

        f.write("end\n")
        bpy.ops.object.mode_set(mode="OBJECT")
        print(f"- Exported {anim_len} frames")

    # -- triangles ----------------------------------------------------------
    def _write_triangles(self):
        f = self.smd_file
        goldsrc = self.goldsrc
        done_header = False
        for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
            if not done_header:
                f.write("triangles\n")
                done_header = True

            ob = bake.object
            uv_loop = ob.data.uv_layers.active.data
            bench = BenchMarker(2)
            weights = self.build_weightmap(bake)
            bench.report("weightmap")

            ob_weight_str = None
            if isinstance(bake.envelope, str) and bake.envelope in self.bone_ids:
                ob_weight_str = (" 1 {} 1" if not goldsrc else "{}").format(self.bone_ids[bake.envelope])
            elif not weights:
                ob_weight_str = " 0" if not goldsrc else "0"

            me = ob.data
            num_verts = len(me.vertices)
            co, loop_verts, normals = _mesh_arrays(me)
            uvs = np.empty(len(me.loops) * 2, np.float32)
            uv_loop.foreach_get("uv", uvs)

            multi_weight_verts = 0
            if ob_weight_str:
                weight_strs = [ob_weight_str] * num_verts
            else:
                weight_strs = []
                multi = []
                for w_list in weights:
                    valid = [(bi, bw) for bi, bw in w_list if bw > 0]
                    if not goldsrc:
                        weight_strs.append(" {}{}".format(
                            len(valid), "".join(f" {bi} {getSmdFloat(bw)}" for bi, bw in valid)))
                    else:
                        weight_strs.append(str(valid[0][0]) if valid else "0")
                        multi.append(len(valid) > 1)
                if goldsrc:
                    multi_weight_verts = int(np.count_nonzero(np.asarray(multi, bool)[np.unique(loop_verts)]))

            pos_strs = _fmt_rows("%.6f %.6f %.6f", co)
            if not goldsrc:
                heads = ["0  " + p + "  " for p in pos_strs]
                tails = [w + "\n" for w in weight_strs]
            else:
                heads = [w + "  " + p + "  " for w, p in zip(weight_strs, pos_strs)]
                tails = ["\n"] * num_verts
            norm_uv = _fmt_rows("%.6f %.6f %.6f  %.6f %.6f", np.hstack((normals, uvs.reshape(-1, 2))))
            loop_lines = [heads[v] + nu + tails[v] for v, nu in zip(loop_verts.tolist(), norm_uv)]

            totals = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("loop_total", totals)
            mat_indices = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("material_index", mat_indices)

            src_mt = getattr(bake.src.vs, 'mesh_type', 'DEFAULT') if bake.src else 'DEFAULT'
            mat_lines: dict[int, tuple[str, bool]] = {}
            bad_face_mats = 0
            lines = []
            start = 0
            # Face corners are stored contiguously in face order, so loop_lines is already
            # in the order the faces are written.
            for total, mat_index in zip(totals.tolist(), mat_indices.tolist()):
                mat = mat_lines.get(mat_index)
                if mat is None:
                    if src_mt in ('COLLISION', 'CLOTHPROXY'):
                        mat_name, mat_ok = "no_material", True
                    else:
                        mat_name, mat_ok = self.resolve_material(ob, mat_index)
                    mat = mat_lines[mat_index] = (mat_name + "\n", mat_ok)
                if not mat[1]:
                    bad_face_mats += 1
                lines.append(mat[0])
                lines += loop_lines[start:start + total]
                start += total

            bench.report("format")
            f.writelines(lines)
            bench.report("write")

            if goldsrc and multi_weight_verts:
                self._warning(get_id("exporterr_goldsrc_multiweights", format_string=True).format(multi_weight_verts, bake.src.data.name))
            if bad_face_mats:
                self._warning(get_id("exporter_err_facesnotex_ormat").format(bad_face_mats, bake.src.data.name))
            print(f"- Exported {len(ob.data.polygons)} polys")
            mats = getattr(self.r, "materials_used", set())
            print(f"- Exported {len(mats)} materials")
            for mat in mats:
                print("   " + mat[0])

        if done_header:
            f.write("end\n")

    # -- vta (flex shapes) --------------------------------------------------
    def _write_vta(self):
        f = self.smd_file
        f.write("skeleton\n")

        def write_time(time, shape_name=None):
            f.write("time {}{}\n".format(time, f" # {shape_name}" if shape_name else ""))

        shape_names = ordered_set.OrderedSet()
        for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
            for sn in bake.shapes.keys():
                shape_names.add(sn)

        write_time(0)
        for i, sn in enumerate(shape_names):
            write_time(i + 1, sn)
        f.write("end\n\nvertexanimation\n")

        bench = BenchMarker(2)
        vert_id = 0
        write_time(0)
        base_data = {}
        for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
            bake.offset = vert_id
            co, loop_verts, normals = base_data[id(bake)] = _mesh_arrays(bake.object.data)
            ids = np.arange(vert_id, vert_id + len(loop_verts))
            f.write(_fmt_vta_rows(ids, co[loop_verts], normals))
            vert_id += len(loop_verts)

        bench.report("reference")
        total_verts = 0
        i = 0
        for i, shape_name in enumerate(shape_names):
            i += 1
            bpy.context.window_manager.progress_update(i / len(shape_names))
            write_time(i, shape_name)
            for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
                shape = bake.shapes.get(shape_name)
                if not shape:
                    continue
                co, loop_verts, normals = base_data[id(bake)]
                shape_co, _, shape_normals = _mesh_arrays(shape)
                shape_pos = shape_co[loop_verts]
                moved = _exceeds_epsilon(shape_pos - co[loop_verts])
                if bake.src.data.vs.bake_shapekey_as_basis_normals:
                    out_normals = normals
                else:
                    moved |= _exceeds_epsilon(shape_normals - normals)
                    out_normals = shape_normals
                idx = np.flatnonzero(moved)
                f.write(_fmt_vta_rows(bake.offset + idx, shape_pos[idx], out_normals[idx]))
                total_verts += len(idx)

        f.write("end\n")
        bench.report(f"shapes ({len(shape_names)})")
        print(f"- Exported {i} flex shapes ({total_verts} verts)")

    # -- VCA ----------------------------------------------------------------
    def _write_vca(self, name, vca):
        bench = BenchMarker()
        self.smd_file = f = self._open(self.dir_path, name + ".vta", "vertex animation")
        if f is None:
            return 0

        f.write("nodes\n0 \"root\" -1\nend\nskeleton\n")
        for i in range(len(vca)):
            f.write(f"time {i}\n0 0 0 0 0 0 0\n")
        f.write("end\nvertexanimation\n")

        num_frames = len(vca)
        two_percent = num_frames / 50

        for frame, vca_ob in enumerate(vca):
            f.write(f"time {frame}\n")
            co, loop_verts, normals = _mesh_arrays(vca_ob.data)
            f.write(_fmt_vta_rows(np.arange(len(loop_verts)), co[loop_verts], normals))
            if two_percent and frame % two_percent == 0:
                print(".", debug_only=True, newline=False)
                bpy.context.window_manager.progress_update(frame / num_frames)
            removeObject(vca_ob)
            vca[frame] = None

        f.write("end\n")
        print(debug_only=True)
        print(f"Exported {num_frames} frames ({f.tell() / 1024 / 1024:.1f}MB)")
        f.close()
        bench.report("Vertex animation")
        return 1

    def _write_vca_sequence(self, name, vca):
        self.smd_file = f = self._open(self.dir_path, f"vcaanim_{name}.smd", "SMD")
        if f is None:
            return 0

        root_bones = (
            "\n".join(f'{self.bone_ids[b.name]} "{b.name}" -1' for b in self.exportable_bones if b.parent is None)
            if self.armature_src else '0 "root" -1'
        )
        f.write(f"nodes\n{root_bones}\n{vca.bone_id} \"vcabone_{name}\" -1\nend\nskeleton\n")

        max_frame = float(len(vca) - 1)
        for i in range(len(vca)):
            f.write(f"time {i}\n")
            if self.armature_src:
                for rb in [b for b in self.exportable_bones if b.parent is None]:
                    mat = getUpAxisMat("Y").inverted() @ self.armature.matrix_world @ rb.matrix
                    f.write(f"{self.bone_ids[rb.name]} {getSmdVec(mat.to_translation())} {getSmdVec(mat.to_euler())}\n")
            else:
                f.write("0 0 0 0 {} 0 0\n".format("-1.570797" if bpy.context.scene.vs.up_axis == "Z" else "0"))
            f.write(f"{vca.bone_id} 1.0 {getSmdFloat(i / max_frame)} 0 0 0 0\n")

        f.write("end\n")
        f.close()
        return 1

    def _evaluated_pose_bones(self):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = self.armature.evaluated_get(depsgraph)
        assert isinstance(evaluated, bpy.types.Object) and evaluated.pose
        return [evaluated.pose.bones[b.name] for b in self.exportable_bones]

    def resolve_material(self, ob, material_index):
        mat_name = mat_id = None
        if len(ob.material_slots) > material_index:
            mat_id = ob.material_slots[material_index].material
            if mat_id:
                mat_name = sanitize_string(mat_id.name, allow_unicode=True)
        if mat_name:
            mu = getattr(self.r, "materials_used", None)
            if mu is not None:
                mu.add((mat_name, mat_id))
            return mat_name, True
        return "no_material", ob.display_type != "TEXTURED"

    def build_weightmap(self, bake_result: BakeResult) -> list:
        out = []
        amod = bake_result.envelope
        ob = bake_result.object
        if not amod or not isinstance(amod, bpy.types.ArmatureModifier):
            return out

        amod_vg = ob.vertex_groups.get(amod.vertex_group)
        try:
            amod_ob = next(bake.object for bake in self.all_bake_results if bake.src == amod.object)
        except StopIteration as e:
            raise ValueError(f"Armature for exportable \"{bake_result.name}\" was not baked") from e

        model_mat = amod_ob.matrix_world.inverted() @ ob.matrix_world
        num_verts = len(ob.data.vertices)
        progress_step = max(50, num_verts // 100)

        exportable_bone_names = {b.name for b in self.exportable_bones}
        vg_to_bone_id: dict[int, int] = {}
        if amod.use_vertex_groups:
            for vg in ob.vertex_groups:
                bone = amod_ob.pose.bones.get(vg.name)
                if bone and bone.name in exportable_bone_names:
                    vg_to_bone_id[vg.index] = self.bone_ids[bone.name]

        exportable_bones_list = [pb for pb in amod_ob.pose.bones if pb in self.exportable_bones] \
            if amod.use_bone_envelopes else []

        use_vertex_groups = amod.use_vertex_groups
        use_bone_envelopes = amod.use_bone_envelopes
        invert_vg = amod.invert_vertex_group
        amod_vg_index = amod_vg.index if amod_vg else None
        verts = ob.data.vertices

        for vi, v_groups in enumerate(_vertex_group_weights(ob.data)):
            weights = []
            total_weight = 0
            if vi % progress_step == 0:
                bpy.context.window_manager.progress_update(vi / num_verts)

            if use_vertex_groups:
                for group, weight in v_groups:
                    bone_id = vg_to_bone_id.get(group)
                    if bone_id is not None:
                        weights.append([bone_id, weight])
                        total_weight += weight

            if use_bone_envelopes and total_weight == 0:
                co = model_mat @ verts[vi].co
                for pb in exportable_bones_list:
                    weight = pb.bone.envelope_weight * pb.evaluate_envelope(co)
                    if weight:
                        weights.append([self.bone_ids[pb.name], weight])
                        total_weight += weight

            if total_weight not in (0, 1):
                for link in weights:
                    link[1] *= 1 / total_weight

            if amod_vg_index is not None and total_weight > 0:
                amod_vg_weight = next((w for g, w in v_groups if g == amod_vg_index), 0)
                if invert_vg:
                    amod_vg_weight = 1 - amod_vg_weight
                for link in weights:
                    link[1] *= amod_vg_weight

            out.append(weights)
        return out
