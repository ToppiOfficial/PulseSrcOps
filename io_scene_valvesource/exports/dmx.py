import bpy, bmesh, collections, re, os
from mathutils import Vector, Matrix

from ..utils import *
from .. import datamodel, ordered_set, flex
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone

from .records import BakeResult, ExportTask


# DmxWriter - the DMX model exporter (replaces the old SmdExporter.writeDMX). Covers skeleton,
# mesh (Source 1 + Source 2 vertex streams), shape-key flex deltas, bone animation channels,
# attachments, hitboxes, procedural-bone DME embedding, and vertex animations (VCA).
class DmxWriter:
    def __init__(self, reporter, datablock, bake_results, name, dir_path, *,
                 armature, armature_src, exportable_bones, exportable_boneNames,
                 exportable_empties, all_bake_results, flex_mode, flex_source):
        self.r = reporter
        self.datablock = datablock
        self.bake_results = bake_results
        self.name = name
        self.dir_path = dir_path
        self.armature = armature
        self.armature_src = armature_src
        self.exportable_bones = exportable_bones
        self.exportable_boneNames = exportable_boneNames
        self.exportable_empties = exportable_empties
        self.all_bake_results = all_bake_results
        self.flex_controller_mode = flex_mode
        self.flex_controller_source = flex_source
        self.bone_ids: dict[str, int] = {}

    # -- reporting -----------------------------------------------------------
    def _warning(self, *a): self.r.warning(*a)
    def _error(self, *a): self.r.error(*a)

    @staticmethod
    def _scale_translation(vec, scale):
        for j in range(3):
            vec[j] *= scale[j]

    # -----------------------------------------------------------------------
    def write(self) -> int:
        bench = BenchMarker(1, "DMX")
        armature_name = self.armature_src.name if self.armature_src else self.name
        filepath = os.path.realpath(os.path.join(
            self.dir_path, sanitize_string(self.name, allow_unicode=True) + ".dmx"))
        print("-", filepath)
        self.filepath = filepath
        self.materials = {}
        self._written = 0
        self.is_anim = len(self.bake_results) == 1 and self.bake_results[0].object.type == "ARMATURE"

        dm = self.dm = datamodel.DataModel("model", State.datamodelFormat)
        dm.allow_random_ids = False
        self.source2 = source2 = dm.format_ver >= 22
        self.export_bone_scale = source2
        self.keywords = getDmxKeywords(dm.format_ver)
        # DME prefab mode (Source 1 only): embed jigglebones/hitboxes/procedural bones + keep
        # attachments inside the model DMX instead of writing .qci prefabs.
        self.dme_mode = (not source2) and prefab_mode_is_dme(bpy.context.scene)

        self.want_jointlist = dm.format_ver >= 11
        self.want_jointtransforms = dm.format_ver in range(0, 21)

        root = self.root = dm.add_element(bpy.context.scene.name, id="Scene" + bpy.context.scene.name)
        DmeModel = self.DmeModel = dm.add_element(armature_name, "DmeModel", id="Object" + armature_name)
        self.DmeModel_children = DmeModel["children"] = datamodel.make_array([], datamodel.Element)
        DmeModel["transform"] = self._make_transform("", Matrix(), (DmeModel.name or "") + "transform")

        transforms = dm.add_element("base", "DmeTransformList", id="transforms" + bpy.context.scene.name)
        DmeModel["baseStates"] = datamodel.make_array([transforms], datamodel.Element)
        transforms["transforms"] = datamodel.make_array([], datamodel.Element)
        self.DmeModel_transforms = transforms["transforms"]

        if source2:
            axis = DmeModel["axisSystem"] = dm.add_element("axisSystem", "DmeAxisSystem", "AxisSys" + armature_name)
            axis["upAxis"] = axes_lookup_source2[bpy.context.scene.vs.up_axis]
            axis["forwardParity"] = 1
            axis["coordSys"] = 0

        if self.armature:
            self.armature.data.pose_position = "POSE" if self.is_anim else "REST"
            if self.armature.data.vs.reset_pose_per_anim:
                if self.is_anim:
                    self.r.warnUnkeyframedPose(self.name)
                for pb in self.armature.pose.bones:
                    pb.matrix_basis.identity()
            elif self.is_anim:
                self.r.applyUnkeyframedSourcePose()
            bpy.context.view_layer.update()

        root["skeleton"] = DmeModel
        if self.want_jointlist:
            self.jointList = DmeModel["jointList"] = datamodel.make_array([], datamodel.Element)
            if source2:
                self.jointList.append(DmeModel)
        if self.want_jointtransforms:
            self.jointTransforms = DmeModel["jointTransforms"] = datamodel.make_array([], datamodel.Element)
            if source2:
                self.jointTransforms.append(DmeModel["transform"])

        self.bone_elements = {}
        if self.armature:
            self.armature_scale = self.armature.matrix_world.to_scale()

        self._build_skeleton(bench)
        self._write_attachments(bench)
        self._write_procedural_bones()
        self._write_hitboxes(bench)
        self._write_vca_bones()

        combination_operator = self._setup_flex(bench)
        if not combination_operator and self.bake_results and self.bake_results[0].vertex_animations:
            combination_operator = flex.DmxWriteFlexControllers.make_controllers(self.datablock).root["combinationOperator"]
        if combination_operator:
            root["combinationOperator"] = combination_operator

        self._write_meshes(combination_operator, bench)

        if self.is_anim:
            self._write_animation(bench)

        return self._write_out(bench)

    # -- transforms ----------------------------------------------------------
    def _make_transform(self, name, matrix, object_name, scale_divisor=None):
        trfm = self.dm.add_element(name, "DmeTransform", id=object_name + "transform")
        trfm["position"] = datamodel.Vector3(matrix.to_translation())
        trfm["orientation"] = getDatamodelQuat(matrix.to_quaternion())
        if self.export_bone_scale:
            trfm["scale"] = getDatamodelScale(matrix, scale_divisor)
        return trfm

    # -- skeleton ------------------------------------------------------------
    def _build_skeleton(self, bench):
        if not self.armature:
            return
        self.num_bones = len(self.exportable_bones)
        add_implicit = not self.source2 and self.armature.data.vs.implicit_zero_bone
        if add_implicit:
            self.DmeModel_children.extend(self._write_bone(implicit_bone_name))
        for b in self.armature.pose.bones:
            if b.parent or (add_implicit and b.name == implicit_bone_name):
                continue
            elems = self._write_bone(b)
            if elems:
                self.DmeModel_children.extend(elems)
        bench.report("Bones")

    def _write_bone(self, bone):
        dm = self.dm
        if isinstance(bone, str):
            bone_name, bone = bone, None
        else:
            if bone and bone not in self.exportable_bones:
                children = []
                for child_elems in [self._write_bone(c) for c in bone.children]:
                    if child_elems:
                        children.extend(child_elems)
                return children
            bone_name = bone.name

        bone_exportname = self.exportable_boneNames[bone.name] if bone else bone_name
        # In DME mode a jigglebone is a skeleton joint of element type DmeJiggleBone
        # (a DmeJoint subclass); the .vs props live on the data Bone (bone.bone).
        data_bone = bone.bone if bone is not None else None
        is_dme_jiggle = self.dme_mode and not self.is_anim and data_bone is not None and data_bone.vs.bone_is_jigglebone
        bone_elem_type = "DmeJiggleBone" if is_dme_jiggle else "DmeJoint"
        self.bone_elements[bone_name] = bone_elem = dm.add_element(bone_exportname, bone_elem_type, id=bone_name)
        if is_dme_jiggle:
            _jigglebone.write_dme_attrs(bone_elem, data_bone)
        if self.want_jointlist:
            self.jointList.append(bone_elem)
        self.bone_ids[bone_name] = len(self.bone_elements) - (0 if self.source2 else 1)

        # A root bone's matrix comes from matrix_world, so it carries the armature object's
        # scale. Its position needs that (children get it via armature_scale below), but the
        # transform scale must not repeat it - Source 2 would apply it a second time and blow
        # the model up by the armature's scale factor.
        scale_divisor = None
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
                scale_divisor = self.armature_scale

        relMat = get_bone_matrix(relMat, bone, rest_space=True)
        trfm = self._make_transform(bone_exportname, relMat, "bone" + bone_name, scale_divisor)
        trfm_base = self._make_transform(bone_exportname, relMat, "bone_base" + bone_name, scale_divisor)

        if bone and bone.parent:
            self._scale_translation(trfm["position"], self.armature_scale)
        trfm_base["position"] = trfm["position"]

        if self.want_jointtransforms:
            self.jointTransforms.append(trfm)
        bone_elem["transform"] = trfm
        self.DmeModel_transforms.append(trfm_base)

        if bone:
            children = bone_elem["children"] = datamodel.make_array([], datamodel.Element)
            for child_elems in [self._write_bone(c) for c in bone.children]:
                if child_elems:
                    children.extend(child_elems)
            bpy.context.window_manager.progress_update(len(self.bone_elements) / self.num_bones)
        return [bone_elem]

    # -- attachments / procedural bones / hitboxes --------------------------
    def _write_attach(self, name, relMat, boneelem):
        dm = self.dm
        dag = dm.add_element(name, "DmeDag", id=name)
        att = dm.add_element(name, "DmeAttachment", id="attachment" + name)
        att["visible"] = True
        att["isRigid"] = True
        att["isWorldAligned"] = False
        dag["shape"] = att
        dag["visible"] = True
        dag["children"] = datamodel.make_array([], datamodel.Element)

        if self.want_jointlist:
            self.jointList.append(dag)

        if "children" not in boneelem:
            boneelem["children"] = datamodel.make_array([], datamodel.Element)

        trfm = self._make_transform(name, relMat, name)
        trfm_base = self._make_transform(name, relMat, "empty_base" + name)

        self._scale_translation(trfm["position"], self.armature_scale)
        trfm_base["position"] = trfm["position"]

        dag["transform"] = trfm
        self.DmeModel_transforms.append(trfm_base)
        if self.want_jointtransforms:
            self.jointTransforms.append(trfm)

        boneelem["children"].append(dag)
        return dag

    def _write_attachment(self, empty, empty_matrix):
        current_bone = self.armature.data.bones.get(empty.parent_bone)
        exportable_parent = None
        while current_bone:
            if current_bone.name in self.exportable_boneNames:
                exportable_parent = self.armature.pose.bones.get(current_bone.name)
                break
            current_bone = current_bone.parent

        if not exportable_parent:
            self._warning(f"Attachment '{empty.name}' has no exportable parent bone. Skipping.")
            return None

        pmat = get_bone_matrix(exportable_parent, rest_space=True)
        relMat = pmat.inverted() @ empty_matrix
        return self._write_attach(empty.name, relMat, self.bone_elements[exportable_parent.name])

    def _write_attachments(self, bench):
        # Source 2 (.vmdl) always embeds attachments; Source 1 embeds them only in DME mode.
        embed_attachments = self.source2 or self.dme_mode
        if embed_attachments and not self.is_anim and self.exportable_empties and self.armature:
            for empty, world_matrix in self.exportable_empties:
                self._write_attachment(empty, world_matrix)
            bench.report("Empties")

    def _write_procedural_bones(self):
        if not (self.dme_mode and not self.is_anim and not self.source2 and self.armature and self.armature_src):
            return
        avs = getattr(self.armature_src.data, 'vs', None)
        proc_bones_list = list(getattr(avs, 'proc_bones', [])) if avs else []
        bone_elements = self.bone_elements

        # LOOKAT aim targets: non-zero offsets get a {base}_lookat[idx] DmeAttachment in the
        # driver bone's local space; a zero offset aims the DmeAimAtBone at the driver joint
        # directly. Naming/dedup mirror PrefabExporter so QCI and DME produce the same names.
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
            multiple = len(offsets) > 1
            for idx, off in enumerate(offsets, start=1):
                attach_name = f"{attach_base}_lookat{idx}" if multiple else f"{attach_base}_lookat"
                lookat_name_map[(dn, off)] = attach_name
                self._write_attach(attach_name, Matrix.Translation(Vector(off)), bone_elements[dn])

        # Promote each helper's joint to DmeQuatInterpBone (TRIGGER) or DmeAimAtBone (LOOKAT).
        # On failure the element stays a plain DmeJoint. armature_src is used so the real
        # drivers/constraints/action are live, matching the VRD path.
        seen_helpers: set[str] = set()
        for entry_idx, entry in enumerate(proc_bones_list):
            helper_name = entry.helper_bone
            if not helper_name or helper_name not in bone_elements:
                continue
            if helper_name in seen_helpers:
                self._warning(get_id('exporter_warn_procbone_duplicate', True).format(helper_name))
                continue
            seen_helpers.add(helper_name)

            data_bone = self.armature.data.bones.get(helper_name)
            if data_bone is not None and data_bone.vs.bone_is_jigglebone:
                self._warning(get_id('exporter_warn_procbone_jiggle_conflict', True).format(helper_name))
                continue

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
                        bpy.context.scene, control_bone, self.armature_scale, self._warning,
                        parent_bname):
                    bone_elem.type = "DmeQuatInterpBone"
            else:
                off = tuple(entry.lookat_offset)
                aim_target = lookat_name_map.get((entry.driver_bone, off))
                if aim_target is None:
                    aim_target = self.exportable_boneNames.get(entry.driver_bone, entry.driver_bone) if entry.driver_bone else None
                parent_control = self.exportable_boneNames.get(parent_bname, "")
                if _proceduralbone.write_dme_aimat_attrs(
                        bone_elem, self.armature_src, entry, aim_target,
                        self.armature_scale, self._warning, parent_control, parent_bname):
                    bone_elem.type = "DmeAimAtBone"

    def _write_hitboxes(self, bench):
        if not (self.dme_mode and not self.is_anim and self.armature and self.armature_src):
            return
        dm = self.dm
        arm_data = self.armature_src.data
        havs = getattr(arm_data, 'vs', None)
        hbox_entries = list(getattr(havs, 'hitboxes', [])) if havs else []
        valid_hbox = [e for e in hbox_entries if e.bone_name and arm_data.bones.get(e.bone_name)]
        hboxset_name = (getattr(havs, 'hboxset_name', '').strip() if havs else '') or 'default'

        if not valid_hbox:
            return

        inverted = [e.bone_name for e in valid_hbox
                    if e.scale <= 0.0 and any(e.vec_min[i] > e.vec_max[i] for i in range(3))]
        if inverted:
            self._warning(
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

        self.root["hitboxSetList"] = hbox_set_list
        bench.report("Hitboxes")

    # -- flex controller setup ----------------------------------------------
    def _setup_flex(self, bench):
        if not any(b.shapes for b in self.bake_results):
            return None

        if self.flex_controller_mode == "ADVANCED":
            if not hasFlexControllerSource(self.flex_controller_source):
                self._error(get_id("exporter_err_flexctrl_undefined", True).format(self.name))
                return None
            text = bpy.data.texts.get(self.flex_controller_source)
            element_path = ["combinationOperator"]
            try:
                if text:
                    print(f"- Loading flex controllers from text block \"{text.name}\"")
                    self.controller_dm = datamodel.parse(text.as_string(), element_path=element_path)
                else:
                    path_fc = os.path.realpath(bpy.path.abspath(self.flex_controller_source))
                    print("- Loading flex controllers from " + path_fc)
                    self.controller_dm = datamodel.load(path=path_fc, element_path=element_path)
                combination_operator = self.controller_dm.root["combinationOperator"]
                for elem in [e for e in combination_operator["targets"] if e.type != "DmeFlexRules"]:
                    combination_operator["targets"].remove(elem)
            except Exception as err:
                self._error(get_id("exporter_err_flexctrl_loadfail", True).format(err))
                return None
        else:
            combination_operator = flex.DmxWriteFlexControllers.make_controllers(self.datablock).root["combinationOperator"]

        bench.report("Flex setup")
        return combination_operator

    # -- weightmap / material ------------------------------------------------
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

    # -- meshes --------------------------------------------------------------
    def _write_meshes(self, combination_operator, bench):
        dm = self.dm
        keywords = self.keywords
        source2 = self.source2
        materials = self.materials
        bone_elements = self.bone_elements

        for bake in [b for b in self.bake_results if b.object.type != "ARMATURE"]:
            self.root["model"] = self.DmeModel
            ob = bake.object
            assert isinstance(ob.data, bpy.types.Mesh)

            vertex_data = dm.add_element("bind", "DmeVertexData", id=bake.name + "verts")
            DmeMesh = dm.add_element(bake.name, "DmeMesh", id=bake.name + "mesh")
            DmeMesh["visible"] = True
            DmeMesh["bindState"] = vertex_data
            DmeMesh["currentState"] = vertex_data
            DmeMesh["baseStates"] = datamodel.make_array([vertex_data], datamodel.Element)

            DmeDag = dm.add_element(bake.name, "DmeDag", id="ob" + bake.name + "dag")
            if self.want_jointlist:
                self.jointList.append(DmeDag)
            DmeDag["shape"] = DmeMesh

            bone_child = isinstance(bake.envelope, str)
            if bone_child and bake.envelope in bone_elements:
                bone_elements[bake.envelope]["children"].append(DmeDag)
                trfm_mat = bake.bone_parent_matrix
            else:
                self.DmeModel_children.append(DmeDag)
                trfm_mat = ob.matrix_world

            trfm = self._make_transform(bake.name, trfm_mat, "ob" + bake.name)
            if self.want_jointtransforms:
                self.jointTransforms.append(trfm)
            DmeDag["transform"] = trfm
            self.DmeModel_transforms.append(self._make_transform(bake.name, trfm_mat, "ob_base" + bake.name))

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
            src_mt = _src_mt
            cloth_groups = findDmxClothVertexGroups(ob) if (source2 and src_mt != 'COLLISION') else None

            if isinstance(bake.envelope, bpy.types.ArmatureModifier):
                ob_weights = self.build_weightmap(bake)
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
                self._warning(get_id("exporter_warn_weightlinks_excess", True).format(badJointCounts, bake.src.name, weight_link_limit))

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
                        self._warning(f"'{bake.name}': stereo mode is VGROUP but no vertex group is specified")
                    else:
                        bake.balance_vg = ob.vertex_groups.get(vg_name)
                        if bake.balance_vg is None:
                            self._warning(f"'{bake.name}': stereo vertex group '{vg_name}' not found")
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
                        except RuntimeError:
                            pass  # vertex not in the balance group
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
                        except RuntimeError:
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
                texcoIndices[loop.index] = texco.add(datamodel.Vector2(uv_layer[loop.index].uv))  # pyright: ignore
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
                self._write_source2_layers(vertex_data, fmt, bm, ob, bake)
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

            self._write_facesets(DmeMesh, bm, ob, bake, src_mt, face_sets, bench)

            bpy.ops.object.mode_set(mode="OBJECT")
            del bm

            self._write_shapes(DmeMesh, ob, bake, balance, texcoIndices, num_verts, combination_operator, bench)

    def _write_source2_layers(self, vertex_data, fmt, bm, ob, bake):
        dm = self.dm
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
                self._warning(f"'{bake.name}' has no UV map named {defaultUvLayer} and no fallback was found.")

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

    def _write_facesets(self, DmeMesh, bm, ob, bake, src_mt, face_sets, bench):
        dm = self.dm
        materials = self.materials
        bad_face_mats = 0
        num_polys = len(bm.faces)
        two_percent = int(num_polys / 50)
        print("Polygons: ", debug_only=True, newline=False)

        bm_face_sets = collections.defaultdict(list)
        for p, face in enumerate(bm.faces):
            if src_mt in ('COLLISION', 'CLOTHPROXY'):
                mat_name, mat_ok = "no_material", True
            else:
                mat_name, mat_ok = self.resolve_material(ob, face.material_index)
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
            self._warning(get_id("exporter_err_facesnotex_ormat").format(bad_face_mats, bake.name))
        bench.report("polys")

    # -- shapes --------------------------------------------------------------
    def _write_shapes(self, DmeMesh, ob, bake, balance, texcoIndices, num_verts, combination_operator, bench):
        dm = self.dm
        keywords = self.keywords
        delta_states = []
        corrective_shapes_seen = []
        shape_names = []
        two_percent = int(len(bake.shapes) / 50)
        print("Shapes: ", debug_only=True, newline=False)

        if bake.shapes:
            num_shapes = len(bake.shapes)
            num_correctives = num_wrinkles = 0

            bake_flex_mode = getattr(getattr(bake.src, 'vs', None), 'flex_controller_mode', 'DME')
            dme_corrective_names = get_dme_corrective_delta_names(bake.src) if bake_flex_mode == 'DME' else None
            dme_delta_map = get_dme_delta_name_map(bake.src) if bake_flex_mode == 'DME' else None
            dme_split_map = get_dme_split_delta_map(bake.src) if bake_flex_mode == 'DME' else {}
            if dme_split_map and not bake.balance_vg:
                self._warning(get_id("exporter_warn_dme_split_no_balance", True).format(bake.name))
            for _idx in get_dme_split_delta_conflicts(bake.src) if bake_flex_mode == 'DME' else ():
                _ov = bake.src.vs.dme_delta_overrides[_idx]
                self._warning(get_id("exporter_warn_dme_split_on_controller", True).format(bake.name, _ov.shapekey))

            for shape_name, shape in bake.shapes.items():
                wrinkle_scale = 0
                _extra_delta_names = []
                _split_base = None

                if bake_flex_mode == 'DME':
                    corrective = shape_name in dme_corrective_names
                    if corrective:
                        num_correctives += 1
                    shape_name, _extra_delta_names, _split_base = resolve_dme_delta_names(
                        shape_name, dme_corrective_names, dme_delta_map, dme_split_map)
                else:
                    corrective = getCorrectiveShapeSeparator() in shape_name

                    if corrective:
                        driver_targets = ordered_set.OrderedSet(flex.getCorrectiveShapeKeyDrivers(bake.src.data.shape_keys.key_blocks[shape_name]) or [])
                        name_targets = ordered_set.OrderedSet(shape_name.split(getCorrectiveShapeSeparator()))
                        corrective_targets = driver_targets or name_targets
                        corrective_targets.source = shape_name

                        if corrective_targets in corrective_shapes_seen:
                            prev = next(x for x in corrective_shapes_seen if x == corrective_targets)
                            self._warning(get_id("exporter_warn_correctiveshape_duplicate", True).format(shape_name, "+".join(corrective_targets), prev.source))
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
                                for ctrl in self.controller_dm.root["combinationOperator"]["controls"]:
                                    for i in range(len(ctrl["rawControlNames"])):
                                        if ctrl["rawControlNames"][i] == shape_name:
                                            scales = ctrl.get("wrinkleScales")
                                            return scales[i] if scales else 0
                                raise ValueError()
                            try:
                                wrinkle_scale = _find_scale()
                            except ValueError:
                                self._warning(get_id("exporter_err_flexctrl_missing", True).format(shape_name))

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
                                delta_lengths[ob_vert.index] = dl  # pyright: ignore
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
                            self._warning(get_id("exporter_err_missing_corrective_target", format_string=True).format(shape_name, ct_name))

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

        self._write_vca_deltas(ob, delta_states, bench)

        if delta_states:
            DmeMesh["deltaStates"] = datamodel.make_array(delta_states, datamodel.Element)
            DmeMesh["deltaStateWeights"] = DmeMesh["deltaStateWeightsLagged"] = datamodel.make_array(
                [datamodel.Vector2([0.0, 0.0])] * len(delta_states), datamodel.Vector2
            )
            if not combination_operator:
                raise RuntimeError("Internal error: shapes exist but no DmeCombinationOperator was created.")
            targets = combination_operator["targets"]
            # match on any rule, not just the first - localvar declarations sort ahead of
            # the expression rules and never name a delta. Each rule set drives one mesh,
            # so an already-claimed set must not be stolen: every mesh has to end up either
            # as some rule set's target or as a target in its own right.
            added = False
            for elem in targets:
                if elem.type != "DmeFlexRules" or "target" in elem:
                    continue
                if any(d.name in shape_names for d in elem["deltaStates"]):
                    elem["target"] = DmeMesh
                    added = True
                    break
            if not added:
                targets.append(DmeMesh)

    # -- vertex animations (VCA) --------------------------------------------
    def _write_vca_bones(self):
        if not self.bake_results:
            return
        for vca in self.bake_results[0].vertex_animations:
            self.DmeModel_children.extend(self._write_bone(f"vcabone_{vca}"))

    def _write_vca_deltas(self, ob, delta_states, bench):
        dm = self.dm
        vca_matrix = ob.matrix_world.inverted()
        for vca_name, vca in self.bake_results[0].vertex_animations.items():
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
                    for bk in self.bake_results:
                        bm_mat = mat @ bk.object.matrix_world
                        b = vca_arm.data.edit_bones.new(bk.name)
                        b.head = bm_mat @ b.head
                        b.tail = bm_mat @ Vector([0, 1, 0])

                bpy.ops.object.mode_set(mode="POSE")
                bpy.ops.pose.armature_apply()

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

                self.r._execute_task(bpy.context, vca_arm, ExportTask(vca_arm, vca_arm.name), os.path.dirname(self.filepath), bench)
                self._written += 1

    # -- animation -----------------------------------------------------------
    def _evaluated_pose_bones(self):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = self.armature.evaluated_get(depsgraph)
        assert isinstance(evaluated, bpy.types.Object) and evaluated.pose
        return [evaluated.pose.bones[b.name] for b in self.exportable_bones]

    def _write_animation(self, bench):
        dm = self.dm
        armature_name = self.armature_src.name if self.armature_src else self.name
        ad = self.armature.animation_data
        # first_frame offsets sampling so actions that don't start on frame 0 export their real
        # motion; the DmeChannelsClip timeline stays 0-based. See animationFrameRange.
        first_frame, anim_len = animationFrameRange(ad) if ad else (0, 0)
        fps = bpy.context.scene.render.fps * bpy.context.scene.render.fps_base

        DmeChannelsClip = dm.add_element(self.name, "DmeChannelsClip", id=self.name + "clip")
        DmeAnimationList = dm.add_element(armature_name, "DmeAnimationList", id=armature_name + "list")
        DmeAnimationList["animations"] = datamodel.make_array([DmeChannelsClip], datamodel.Element)
        self.root["animationList"] = DmeAnimationList

        DmeTimeFrame = dm.add_element("timeframe", "DmeTimeFrame", id=self.name + "time")
        duration = anim_len / fps
        if dm.format_ver >= 11:
            DmeTimeFrame["duration"] = datamodel.Time(duration)
        else:
            DmeTimeFrame["durationTime"] = int(duration * 10000)
        DmeTimeFrame["scale"] = 1.0
        DmeChannelsClip["timeFrame"] = DmeTimeFrame
        DmeChannelsClip["frameRate"] = fps if self.source2 else int(fps)

        channels = DmeChannelsClip["channels"] = datamodel.make_array([], datamodel.Element)
        bone_channels = {}

        channel_template = [
            ("_p", "position", "Vector3", datamodel.Vector3),
            ("_o", "orientation", "Quaternion", datamodel.Quaternion),
        ]
        if self.export_bone_scale:
            channel_template.append(("_s", "scale", "Float", float))

        def makeChannel(bone):
            export_name = self.exportable_boneNames[bone.name]
            bone_channels[bone.name] = []
            for suffix, attr, type_name, dm_type in channel_template:
                ch_name = export_name + suffix
                cur = dm.add_element(ch_name, "DmeChannel", id=bone.name + suffix)
                cur["toAttribute"] = attr
                cur["toElement"] = (self.bone_elements[bone.name] if bone else self.DmeModel)["transform"]
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
            evaluated = self._evaluated_pose_bones()

            for bone in evaluated:
                channel = bone_channels[bone.name]
                cur_p = bone.parent
                while cur_p and cur_p not in evaluated:
                    cur_p = cur_p.parent
                scale_divisor = None
                if cur_p:
                    relMat = get_bone_matrix(cur_p).inverted() @ bone.matrix
                else:
                    relMat = self.armature.matrix_world @ bone.matrix
                    scale_divisor = self.armature_scale
                relMat = get_bone_matrix(relMat, bone)

                pos = relMat.to_translation()
                if bone.parent:
                    self._scale_translation(pos, self.armature_scale)

                channel[0]["times"].append(keyframe_time)
                channel[0]["values"].append(datamodel.Vector3(pos))
                channel[1]["times"].append(keyframe_time)
                channel[1]["values"].append(getDatamodelQuat(relMat.to_quaternion()))
                if self.export_bone_scale:
                    channel[2]["times"].append(keyframe_time)
                    channel[2]["values"].append(getDatamodelScale(relMat, scale_divisor))

            if two_percent and frame % two_percent:
                print(".", debug_only=True, newline=False)

        print(debug_only=True)

    # -- write-out -----------------------------------------------------------
    def _write_out(self, bench) -> int:
        bpy.context.window_manager.progress_update(0.99)
        print("- Writing DMX...")
        try:
            if State.use_kv2:
                self.dm.write(self.filepath, "keyvalues2", 1)
            else:
                self.dm.write(self.filepath, "binary", State.datamodelEncoding)
            self._written += 1
        except (PermissionError, FileNotFoundError) as err:
            self._error(get_id("exporter_err_open", True).format("DMX", err))

        bench.report("write")
        if bench.quiet:
            print("- DMX export took", bench.total(), "\n")
        return self._written
