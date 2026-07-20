import bpy, os
from mathutils import Vector, Matrix

from ..utils import *
from .. import ordered_set

from .records import BakeResult


# SmdWriter - the SMD/VTA model+animation exporter (replaces the old SmdExporter.writeSMD).
# Still actively used (GoldSrc modding, existing SMD projects). Mirrors DmxWriter's decomposed
# structure. Weightmap / material derivation is duplicated from DmxWriter for now; a shared
# helper is a future cleanup once both writers are verified together.
# ponytail: build_weightmap/resolve_material duplicated with DmxWriter; extract to a shared
# export/derive.py when SMD is verified.
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

        if self.filetype == "smd":
            self._write_skeleton()
            self._write_triangles()
        elif self.filetype == "vta":
            self._write_vta()

        self.smd_file.close()
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
            weights = self.build_weightmap(bake)

            ob_weight_str = None
            if isinstance(bake.envelope, str) and bake.envelope in self.bone_ids:
                ob_weight_str = (" 1 {} 1" if not goldsrc else "{}").format(self.bone_ids[bake.envelope])
            elif not weights:
                ob_weight_str = " 0" if not goldsrc else "0"

            bad_face_mats = 0
            multi_weight_verts = set()

            # Pre-compute per-vertex weight strings so vertices shared across polygons don't
            # repeat the same string-building work on every loop.
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
                        weight_strs[vi] = str(valid[0][0]) if valid else "0"

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
                    mat_name, mat_ok = self.resolve_material(ob, poly.material_index)
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

            f.writelines(lines)

            if goldsrc and multi_weight_verts:
                self._warning(get_id("exporterr_goldsrc_multiweights", format_string=True).format(len(multi_weight_verts), bake.src.data.name))
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

        vert_id = 0
        write_time(0)
        for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
            bake.offset = vert_id
            verts = bake.object.data.vertices
            for loop in [bake.object.data.loops[l] for poly in bake.object.data.polygons for l in poly.loop_indices]:
                f.write(f"{vert_id} {getSmdVec(verts[loop.vertex_index].co)} {getSmdVec(loop.normal)}\n")
                vert_id += 1

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
                vi = bake.offset
                preserve_basis_normals = bake.src.data.vs.bake_shapekey_as_basis_normals
                for ml in [bake.object.data.loops[l] for poly in bake.object.data.polygons for l in poly.loop_indices]:
                    sv = shape.vertices[ml.vertex_index]
                    mv = bake.object.data.vertices[ml.vertex_index]
                    if preserve_basis_normals:
                        if sv.co - mv.co > epsilon:
                            f.write(f"{vi} {getSmdVec(sv.co)} {getSmdVec(ml.normal)}\n")
                            total_verts += 1
                    else:
                        sl = shape.loops[ml.index]
                        if sv.co - mv.co > epsilon or sl.normal - ml.normal > epsilon:
                            f.write(f"{vi} {getSmdVec(sv.co)} {getSmdVec(sl.normal)}\n")
                            total_verts += 1
                    vi += 1

        f.write("end\n")
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
            f.writelines(
                f"{loop.index} {getSmdVec(vca_ob.data.vertices[loop.vertex_index].co)} {getSmdVec(loop.normal)}\n"
                for loop in vca_ob.data.loops
            )
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

    # -- shared derivation (see ponytail note at top) -----------------------
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
