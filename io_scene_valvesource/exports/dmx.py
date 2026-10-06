import bpy, bmesh, collections, re, os
import numpy as np
from array import array
from mathutils import Vector, Matrix

from ..utils import *
from .. import datamodel, ordered_set, flex
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone, physicsshape as _physicsshape

from .records import BakeResult, ExportTask, is_proxy_only


def _read_floats(collection, attr, width):
    buf = array('f', bytes(4 * width * len(collection)))
    collection.foreach_get(attr, buf)
    return buf

def _read_ints(collection, attr):
    buf = array('i', bytes(4 * len(collection)))
    collection.foreach_get(attr, buf)
    return buf

def _rows(buf, width):
    return np.frombuffer(buf, dtype=np.float32).reshape(-1, width).tolist()

def _dedup_pairs(flat):
    # Same first-seen order and indices OrderedSet.add would give, without a Vector2 per loop.
    seen = {}
    indices = [seen.setdefault(k, len(seen)) for k in zip(flat[0::2], flat[1::2])]
    return list(seen), indices


class DmxWriter:
    def __init__(self, reporter, datablock, bake_results, name, dir_path, *,
                 armature, armature_src, exportable_bones, exportable_boneNames,
                 exportable_empties, all_bake_results, flex_mode, flex_source,
                 skeleton_only=False, anim_jobs=None):
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
        # FBX companion: skeleton + flex controllers + prefabs, no DmeMesh. The mesh, its
        # morphs and its materials ship in the .fbx instead.
        self.skeleton_only = skeleton_only
        # (name, action, slot) clips embedded into a model DMX; see embedded_anim_allowed.
        self.anim_jobs = anim_jobs
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
        # DME prefab mode: embed jigglebones/hitboxes/procedural bones + keep attachments
        # inside the model DMX instead of writing .qci/.vmdl prefabs.
        self.dme_mode = prefab_mode_is_dme(bpy.context.scene)
        self.proxy_only = is_proxy_only(self.bake_results)

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
            if self.is_anim:
                self._prepare_anim_pose(self.name)
            elif self.armature.data.vs.reset_pose_per_anim:
                for pb in self.armature.pose.bones:
                    pb.matrix_basis.identity()
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
        bench.report("Procedural bones")
        self._write_hitboxes(bench)
        self._write_physics_shapes(bench)
        if not self.skeleton_only:
            self._write_vca_bones()

        combination_operator = self._setup_flex(bench)
        if not combination_operator and not self.skeleton_only and self.bake_results and self.bake_results[0].vertex_animations:
            combination_operator = flex.DmxWriteFlexControllers.make_controllers(self.datablock).root["combinationOperator"]
        if combination_operator:
            root["combinationOperator"] = combination_operator

        if self.skeleton_only:
            root["model"] = self.DmeModel
        else:
            self._write_meshes(combination_operator, bench)

        if self.is_anim:
            ad = self.armature.animation_data
            self._write_animation_list([self._write_clip(self.name, "", ad, bench)])
        elif self.anim_jobs and self.armature and not self.skeleton_only:
            self._write_embedded_animations(bench)

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
        is_dme_jiggle = (self.dme_mode and not self.is_anim and not self.proxy_only
                         and data_bone is not None and data_bone.vs.bone_is_jigglebone
                         and self._prefab_type_enabled('JIGGLEBONES'))
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
    def _prefab_type_enabled(self, prefab_type: str) -> bool:
        """Whether the exportables-list checkbox for this prefab type is on.
        File mode reads this via prefab_items in exporter.py; DME mode needs the
        same check here since these types skip the file writer entirely."""
        avs = getattr(self.armature_src.data, 'vs', None) if self.armature_src else None
        if avs is None:
            return True
        item = next((p for p in avs.prefab_items if p.prefab_type == prefab_type), None)
        return item.export if item is not None else True

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
        embed_attachments = (self.source2 or (self.dme_mode and self._prefab_type_enabled('ATTACHMENTS'))) and not self.proxy_only
        if embed_attachments and not self.is_anim and self.exportable_empties and self.armature:
            for empty, world_matrix in self.exportable_empties:
                self._write_attachment(empty, world_matrix)
            bench.report("Empties")

    def _write_procedural_bones(self):
        if not (self.dme_mode and not self.is_anim and self.armature and self.armature_src) or self.proxy_only:
            return
        if not self._prefab_type_enabled('PROCEDURAL'):
            return
        avs = getattr(self.armature_src.data, 'vs', None)
        proc_bones_list = list(getattr(avs, 'proc_bones', [])) if avs else []
        bone_elements = self.bone_elements

        # LOOKAT aim targets: Point targets and non-zero offsets get a {base}_lookat[idx]
        # DmeAttachment in the aim bone's local space; otherwise the DmeAimAtBone aims at
        # the joint directly. Naming/dedup mirror PrefabExporter so QCI and DME produce the same names.
        lookat_by_driver: dict[str, list[tuple]] = {}
        for entry in proc_bones_list:
            if getattr(entry, 'proc_type', 'TRIGGER') != 'LOOKAT':
                continue
            dn = entry.aim_bone
            if not dn or dn not in bone_elements or not entry.aim_needs_attachment:
                continue
            off = entry.aim_offset
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

        from .. import procbones_sim as _pbsim
        _pbsim.prefetch_proc_triggers(
            self.armature_src,
            [(i, e) for i, e in enumerate(proc_bones_list) if e.helper_bone in bone_elements],
            bpy.context.scene)
        try:
            self._promote_procedural_joints(proc_bones_list, lookat_name_map)
        finally:
            _pbsim.clear_proc_trigger_prefetch()

    def _promote_procedural_joints(self, proc_bones_list, lookat_name_map):
        # Promote each helper's joint to DmeQuatInterpBone (TRIGGER) or DmeAimAtBone (LOOKAT).
        # On failure the element stays a plain DmeJoint. armature_src is used so the real
        # drivers/constraints/action are live, matching the VRD path.
        bone_elements = self.bone_elements
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
            is_lookat = getattr(entry, 'proc_type', 'TRIGGER') == 'LOOKAT'
            parent_bname = parent_db.name if parent_db else (entry.aim_bone if is_lookat else entry.driver_bone)

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
                ab = entry.aim_bone
                aim_target = lookat_name_map.get((ab, entry.aim_offset))
                if aim_target is None:
                    aim_target = self.exportable_boneNames.get(ab, ab) if ab else None
                parent_control = self.exportable_boneNames.get(parent_bname, "")
                if _proceduralbone.write_dme_aimat_attrs(
                        bone_elem, self.armature_src, entry, aim_target,
                        self.armature_scale, self._warning, parent_control, parent_bname):
                    bone_elem.type = "DmeAimAtBone"

    def _write_hitboxes(self, bench):
        if not (self.dme_mode and not self.is_anim and self.armature and self.armature_src) or self.proxy_only:
            return
        if not self._prefab_type_enabled('HITBOXES'):
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

    def _write_physics_shapes(self, bench):
        if not (self.dme_mode and not self.is_anim and self.armature and self.armature_src) or self.proxy_only:
            return
        dm = self.dm
        arm_data = self.armature_src.data
        avs = getattr(arm_data, 'vs', None)
        entries = [e for e in (getattr(avs, 'physics_shapes', []) if avs else [])
                   if e.bone_name and arm_data.bones.get(e.bone_name)]
        if not entries:
            return

        empty = [e.bone_name for e in entries
                 if e.shape_type == 'CAPSULE' and e.radius0 <= 0.0 and e.radius1 <= 0.0]
        if empty:
            self._warning(f"Skipping {len(empty)} physics capsule(s) with zero radius: {', '.join(empty)}")
            entries = [e for e in entries if not (e.shape_type == 'CAPSULE' and e.radius0 <= 0.0 and e.radius1 <= 0.0)]
            if not entries:
                return
        inverted = [e.bone_name for e in entries
                    if e.shape_type == 'BOX' and any(e.vec_min[i] > e.vec_max[i] for i in range(3))]
        if inverted:
            self._warning(f"Physics box min/max are inverted on: {', '.join(inverted)}")

        prim_list = dm.add_element("physicsPrimitiveList", "DmePhysicsPrimitiveList", id="physicsPrimitiveList")
        prim_list["primitives"] = datamodel.make_array([], datamodel.Element)
        for pi, e in enumerate(entries):
            bone_export = self.exportable_boneNames.get(e.bone_name, get_bone_exportname(arm_data.bones[e.bone_name]))
            el = dm.add_element(bone_export, _physicsshape.element_class(e), id=f"physprim_{pi}_{e.bone_name}")
            _physicsshape.write_dme_attrs(el, e, bone_export)
            prim_list["primitives"].append(el)

        self.root["physicsPrimitiveList"] = prim_list
        bench.report("Physics shapes")

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

        use_groups = amod.use_vertex_groups
        use_envelopes = amod.use_bone_envelopes
        amod_vg_index = amod_vg.index if amod_vg else -1
        wm = bpy.context.window_manager

        for v in ob.data.vertices:
            weights = []
            total_weight = 0
            amod_vg_weight = 0
            if len(out) % progress_step == 0:
                wm.progress_update(len(out) / num_verts)

            if use_groups or amod_vg_index >= 0:
                for v_group in v.groups:
                    group, weight = v_group.group, v_group.weight
                    if use_groups:
                        bone_id = vg_to_bone_id.get(group)
                        if bone_id is not None:
                            weights.append([bone_id, weight])
                            total_weight += weight
                    if group == amod_vg_index:
                        amod_vg_weight = weight

            if use_envelopes and total_weight == 0:
                for pb in exportable_bones_list:
                    weight = pb.bone.envelope_weight * pb.evaluate_envelope(model_mat @ v.co)
                    if weight:
                        weights.append([self.bone_ids[pb.name], weight])
                        total_weight += weight

            if total_weight not in (0, 1):
                for link in weights:
                    link[1] *= 1 / total_weight

            if amod_vg and total_weight > 0:
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

            _src_mt = getattr(bake.src.vs, 'mesh_type', 'DEFAULT') if bake.src else 'DEFAULT'
            # DME mode embeds collision in the model DMX as DmePhysicsShape, which
            # $rendermesh skips and $datamodelphysics reads.
            shape_class = "DmePhysicsShape" if (self.dme_mode and not self.proxy_only
                                                and _src_mt == 'COLLISION') else "DmeMesh"
            vertex_data = dm.add_element("bind", "DmeVertexData", id=bake.name + "verts")
            DmeMesh = dm.add_element(bake.name, shape_class, id=bake.name + "mesh")
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

            face_sets = collections.OrderedDict()
            jointWeights = []
            jointIndices = []
            balance = bake.stereo_balance(ob, self._warning)
            cloth_weights = {}

            if cloth_groups:
                for vgroup in cloth_groups:
                    cloth_weights[vgroup.name] = [0.0] * num_verts

            uv_layer = ob.data.uv_layers.active.data

            def remap(val, a, b, c, d):
                return (((val - a) * (d - c)) / (b - a)) + c

            bench.report("object setup")

            if cloth_groups:
                for vgroup in cloth_groups:
                    remap_entry = next((r for r in ob.vs.vertex_map_remaps if r.group == vgroup.name), None)
                    weights = cloth_weights[vgroup.name]
                    for vi in range(num_verts):
                        try:
                            w = vgroup.weight(vi)
                            if remap_entry:
                                w = remap(w, 0.0, 1.0, remap_entry.min, remap_entry.max)
                            weights[vi] = w
                        except RuntimeError:
                            if remap_entry:
                                weights[vi] = remap_entry.min

            if have_weightmap:
                for links in ob_weights:
                    weights_row = [0.0] * jointCount
                    indices_row = [0] * jointCount
                    total = 0
                    for i, link in enumerate(links):
                        indices_row[i] = link[0]
                        weights_row[i] = link[1]
                        total += link[1]
                    if source2 and total == 0:
                        weights_row[0] = 1.0
                    jointWeights.extend(weights_row)
                    jointIndices.extend(indices_row)

            bench.report("verts")

            # Mesh loops are stored polygon by polygon, so loop index order is face order.
            mesh = ob.data
            Indices = _read_ints(mesh.loops, "vertex_index").tolist()
            if hasattr(mesh, "corner_normals"):
                nrm = _read_floats(mesh.corner_normals, "vector", 3)
            else:
                nrm = _read_floats(mesh.loops, "normal", 3)
            norms = _rows(nrm, 3)
            texco, texcoIndices = _dedup_pairs(_read_floats(uv_layer, "uv", 2))
            positions = _read_floats(mesh.vertices, "co", 3)
            uv_flat = {uv.name: _read_floats(uv.data, "uv", 2) for uv in mesh.uv_layers}
            poly_data = (_read_ints(mesh.polygons, "loop_start"), _read_ints(mesh.polygons, "loop_total"),
                         _read_ints(mesh.polygons, "material_index"))

            bench.report("loops")

            # Only $N vertex groups and string layers still need BMesh; built on demand.
            bm = None
            def get_bm():
                nonlocal bm
                if bm is None:
                    bm = bmesh.new()
                    bm.from_mesh(ob.data)
                    bm.verts.ensure_lookup_table()
                return bm

            vertex_data[keywords["pos"]] = datamodel.make_vector_array(_rows(positions, 3), datamodel.Vector3)
            vertex_data[keywords["pos"] + "Indices"] = datamodel.make_array(Indices, int)

            if source2 and src_mt != 'COLLISION':
                self._write_source2_layers(vertex_data, fmt, get_bm, ob, bake, uv_flat)
                bench.report("Source 2 vertex data")
            else:
                fmt.append("textureCoordinates")
                vertex_data["textureCoordinates"] = datamodel.make_vector_array(texco, datamodel.Vector2)
                vertex_data["textureCoordinatesIndices"] = datamodel.make_array(texcoIndices, int)

            if have_weightmap:
                vertex_data[keywords["weight"]] = datamodel.make_array(jointWeights, float)
                vertex_data[keywords["weight_indices"]] = datamodel.make_array(jointIndices, int)
                fmt.extend([keywords["weight"], keywords["weight_indices"]])

            # Any group named "<name>$<N>" is written as a per-vertex float stream
            stream_groups = [g for g in ob.vertex_groups if re.fullmatch(r".+\$[0-9]+", g.name)]
            deform_layer = get_bm().verts.layers.deform.active if stream_groups else None
            if deform_layer:
                for vgroup in stream_groups:
                    fmt.append(vgroup.name)
                    values = [v[deform_layer].get(vgroup.index, 0) for v in bm.verts]
                    value_set = ordered_set.OrderedSet(values)
                    vertex_data[vgroup.name] = datamodel.make_array(value_set, float)
                    vertex_data[vgroup.name + "Indices"] = datamodel.make_array(
                        (value_set.index(values[i]) for i in Indices), int
                    )

            if bake.shapes and bake.balance_vg:
                vertex_data[keywords["balance"]] = datamodel.make_array(balance, float)
                vertex_data[keywords["balance"] + "Indices"] = datamodel.make_array(Indices, int)
                fmt.append(keywords["balance"])

            if cloth_groups:
                for vgroup in cloth_groups:
                    fmt.append(vgroup.name + "$0")

            vertex_data[keywords["norm"]] = datamodel.make_vector_array(norms, datamodel.Vector3)
            vertex_data[keywords["norm"] + "Indices"] = datamodel.make_array(range(len(norms)), int)

            if cloth_groups:
                for kw in cloth_weights:
                    vertex_data[kw + "$0"] = datamodel.make_array(cloth_weights[kw], float)
                    vertex_data[kw + "$0Indices"] = datamodel.make_array(Indices, int)

            bench.report("insert")

            self._write_facesets(DmeMesh, poly_data, ob, bake, src_mt, face_sets, bench)

            if bm is not None:
                bm.free()

            self._write_shapes(DmeMesh, ob, bake, balance, texcoIndices, num_verts, combination_operator, bench)

    def _write_source2_layers(self, vertex_data, fmt, get_bm, ob, bake, uv_flat):
        mesh = ob.data
        loop_indices = datamodel.make_array(range(len(mesh.loops)), int)
        export_suffix = re.compile(r".*\$[0-9]+")

        # (blender name, dmx name)
        defaultUvLayer = "texcoord$0"
        uv_layers_to_export = [(uv.name, uv.name) for uv in mesh.uv_layers if export_suffix.match(uv.name)]
        if defaultUvLayer not in [d for _, d in uv_layers_to_export]:
            uv_render = next((l.name for l in mesh.uv_layers if l.active_render), None)
            if uv_render:
                uv_layers_to_export.append((uv_render, defaultUvLayer))
                print(f"- Exporting '{uv_render}' as {defaultUvLayer}")
            else:
                self._warning(f"'{bake.name}' has no UV map named {defaultUvLayer} and no fallback was found.")

        _second_uv_dmx = "texcoord$1"
        if _second_uv_dmx not in [d for _, d in uv_layers_to_export]:
            _exported_uv_blender_names = {b for b, _ in uv_layers_to_export}
            _second_uv = next(
                (uv for uv in mesh.uv_layers if uv.name not in _exported_uv_blender_names),
                None
            )
            if _second_uv is not None:
                uv_layers_to_export.append((_second_uv.name, _second_uv_dmx))
                print(f"- Exporting '{_second_uv.name}' as {_second_uv_dmx}")

        for blender_name, dmx_name in uv_layers_to_export:
            uv_set, uv_indices = _dedup_pairs(uv_flat[blender_name])
            vertex_data[dmx_name] = datamodel.make_vector_array(uv_set, datamodel.Vector2)
            vertex_data[dmx_name + "Indices"] = datamodel.make_array(uv_indices, int)
            fmt.append(dmx_name)

        def make_vertex_layer(name, values, array_type):
            make = datamodel.make_vector_array if array_type is datamodel.Vector4 else datamodel.make_array
            vertex_data[name] = make(values, array_type)
            vertex_data[name + "Indices"] = loop_indices
            fmt.append(name)

        def corner_attrs(data_type):
            return [a for a in mesh.attributes if a.domain == 'CORNER' and a.data_type == data_type]

        # color_srgb gives byte colors as c / 255; BMesh gives c * (1 / 255) in float32,
        # which can differ by an ulp, so the byte is recovered and re-scaled the BMesh way.
        _byte_scale = np.float32(1.0) / np.float32(255.0)
        _seen_color_dmx = set()
        for data_type, prop in (('BYTE_COLOR', "color_srgb"), ('FLOAT_COLOR', "color")):
            for attr in corner_attrs(data_type):
                _blender_name = attr.name
                if _blender_name in vertex_maps:
                    _export_name = vertex_maps[_blender_name].lower()
                elif _blender_name.lower() == "color":
                    _export_name = "color$0"
                else:
                    _export_name = _blender_name
                if _export_name in _seen_color_dmx:
                    continue
                _seen_color_dmx.add(_export_name)
                rgba = np.frombuffer(_read_floats(attr.data, prop, 4), dtype=np.float32)
                if data_type == 'BYTE_COLOR':
                    rgba = np.rint(rgba * 255.0).astype(np.float32) * _byte_scale
                make_vertex_layer(_export_name, rgba.reshape(-1, 4).tolist(), datamodel.Vector4)

        for attr in corner_attrs('FLOAT'):
            if export_suffix.match(attr.name):
                make_vertex_layer(attr.name, _read_floats(attr.data, "value", 1).tolist(), float)
        for attr in corner_attrs('INT'):
            if export_suffix.match(attr.name):
                make_vertex_layer(attr.name, _read_ints(attr.data, "value").tolist(), int)
        string_layers = [a.name for a in corner_attrs('STRING') if export_suffix.match(a.name)]
        if string_layers:
            bm = get_bm()
            loops = [loop for face in bm.faces for loop in face.loops]
            for name in string_layers:
                layer = bm.loops.layers.string[name]
                make_vertex_layer(name, [loop[layer] for loop in loops], str)

    def _write_facesets(self, DmeMesh, poly_data, ob, bake, src_mt, face_sets, bench):
        dm = self.dm
        materials = self.materials
        bad_face_mats = 0
        loop_starts, loop_totals, mat_indices = poly_data

        resolved = {}
        bm_face_sets = collections.defaultdict(list)
        for start, total, mat_index in zip(loop_starts, loop_totals, mat_indices):
            res = resolved.get(mat_index)
            if res is None:
                if src_mt in ('COLLISION', 'CLOTHPROXY'):
                    res = ("no_material", True)
                else:
                    res = self.resolve_material(ob, mat_index)
                resolved[mat_index] = res
            mat_name, mat_ok = res
            if not mat_ok:
                bad_face_mats += 1
            face_list = bm_face_sets[mat_name]
            face_list.extend(range(start, start + total))
            face_list.append(-1)

        for mat_name, indices in bm_face_sets.items():
            material_elem = materials.get(mat_name)
            if not material_elem:
                materials[mat_name] = material_elem = dm.add_element(mat_name, "DmeMaterial", id=mat_name + "mat")
                matdata = ob.data.materials.get(mat_name)
                mat_path = get_material_path(bpy.context.scene, matdata)
                material_elem["mtlName"] = os.path.join(mat_path, mat_name).replace("\\", "/")

            face_set = dm.add_element(mat_name, "DmeFaceSet", id=bake.name + mat_name + "faces")
            face_sets[mat_name] = face_set
            face_set["material"] = material_elem
            face_set["faces"] = datamodel.make_array(indices, int)

        DmeMesh["faceSets"] = datamodel.make_array(list(face_sets.values()), datamodel.Element)

        if bad_face_mats:
            self._warning(get_id("exporter_err_facesnotex_ormat").format(bad_face_mats, bake.name))
        bench.report("polys")

    # -- shapes --------------------------------------------------------------
    @staticmethod
    def _shape_candidates(shape, base_co, base_nrm, short_nrm, preserve_basis_normals):
        s_co = np.frombuffer(_read_floats(shape.vertices, "co", 3), dtype=np.float32).reshape(-1, 3)
        v_cand = np.flatnonzero(np.any(s_co != base_co, axis=1))
        if preserve_basis_normals:
            l_cand = short_nrm
        else:
            s_nrm = np.frombuffer(_read_floats(shape.loops, "normal", 3), dtype=np.float32).reshape(-1, 3)
            l_cand = np.union1d(np.flatnonzero(np.any(s_nrm != base_nrm, axis=1)), short_nrm)
        return v_cand.tolist(), l_cand.tolist()

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

            base_co = np.frombuffer(_read_floats(ob.data.vertices, "co", 3), dtype=np.float32).reshape(-1, 3)
            base_nrm = np.frombuffer(_read_floats(ob.data.loops, "normal", 3), dtype=np.float32).reshape(-1, 3)
            # An unchanged normal still fails the dot test below when shorter than 0.999,
            # so those loops are always candidates.
            short_nrm = np.flatnonzero(np.einsum('ij,ij->i', base_nrm, base_nrm) < 0.9981)

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

                # Correctives must rebase before the deltas are read, or they export the
                # full shape movement on top of the targets they are meant to correct.
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

                # Only elements that differ at all can pass the checks below, so the
                # per-element mathutils code runs on those alone.
                fast = not corrective and not wrinkle_scale
                if fast:
                    v_cand, l_cand = self._shape_candidates(shape, base_co, base_nrm, short_nrm,
                                                            bake.src.data.vs.bake_shapekey_as_basis_normals)
                    verts_iter = (ob.data.vertices[i] for i in v_cand)
                    loops_iter = (ob.data.loops[i] for i in l_cand)
                else:
                    verts_iter, loops_iter = ob.data.vertices, ob.data.loops

                for ob_vert in verts_iter:
                    sv = shape.vertices[ob_vert.index]
                    if ob_vert.co != sv.co:
                        delta = sv.co - ob_vert.co
                        dl = delta.length
                        if abs(dl) > 1e-5:
                            if cache_deltas:
                                delta_lengths[ob_vert.index] = dl  # pyright: ignore
                            shape_pos.append(datamodel.Vector3(delta))
                            shape_posIdx.append(ob_vert.index)

                preserve_basis_normals = bake.src.data.vs.bake_shapekey_as_basis_normals
                for ob_loop in loops_iter:
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
            # Match any delta rule, allowing missing targets to bind to this mesh.
            # Preserve resolved targets so each rule set drives only one mesh.
            added = False
            for elem in targets:
                if elem.type != "DmeFlexRules":
                    continue
                target = elem.get("target")
                if target is not None and not target._is_placeholder:
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

    def _prepare_anim_pose(self, anim_name):
        if self.armature.data.vs.reset_pose_per_anim:
            self.r.warnUnkeyframedPose(anim_name)
            for pb in self.armature.pose.bones:
                pb.matrix_basis.identity()
        else:
            self.r.applyUnkeyframedSourcePose()

    def _write_animation_list(self, clips):
        armature_name = self.armature_src.name if self.armature_src else self.name
        DmeAnimationList = self.dm.add_element(armature_name, "DmeAnimationList", id=armature_name + "list")
        DmeAnimationList["animations"] = datamodel.make_array(clips, datamodel.Element)
        self.root["animationList"] = DmeAnimationList

    def _write_embedded_animations(self, bench):
        # Meshes are already written at REST; sample every clip in POSE, then put the
        # shared baked armature back as later tasks of this export id expect it.
        scene = bpy.context.scene
        arm_data = self.armature.data
        ad = self.armature.animation_data or self.armature.animation_data_create()
        saved_action, saved_slot = ad.action, ad.action_slot
        saved_pose, saved_frame = arm_data.pose_position, scene.frame_current
        clips = []
        try:
            arm_data.pose_position = "POSE"
            for name, action, slot in self.anim_jobs:
                ad.action = action
                if slot is not None:
                    ad.action_slot = slot
                print(f"- Embedding animation \"{name}\"")
                self._prepare_anim_pose(name)
                # Element ids must stay unique with several clips in one datamodel.
                clips.append(self._write_clip(name, name + ":", ad, bench))
        finally:
            ad.action = saved_action
            if saved_action is not None and saved_slot is not None:
                ad.action_slot = saved_slot
            for pb in self.armature.pose.bones:
                pb.matrix_basis.identity()
            arm_data.pose_position = saved_pose
            scene.frame_set(saved_frame)
        if clips:
            self._write_animation_list(clips)

    def _write_clip(self, name, id_prefix, ad, bench):
        dm = self.dm
        # first_frame offsets sampling so actions that don't start on frame 0 export their real
        # motion; the DmeChannelsClip timeline stays 0-based. See animationFrameRange.
        first_frame, anim_len = animationFrameRange(ad) if ad else (0, 0)
        fps = bpy.context.scene.render.fps * bpy.context.scene.render.fps_base

        DmeChannelsClip = dm.add_element(name, "DmeChannelsClip", id=name + "clip")

        DmeTimeFrame = dm.add_element("timeframe", "DmeTimeFrame", id=name + "time")
        duration = anim_len / fps
        if dm.format_ver >= 11:
            DmeTimeFrame["duration"] = datamodel.Time(duration)
        else:
            DmeTimeFrame["durationTime"] = int(duration * 10000)
        DmeTimeFrame["scale"] = 1.0
        DmeChannelsClip["timeFrame"] = DmeTimeFrame
        # Only the Source 2 compilers read a float frameRate.
        DmeChannelsClip["frameRate"] = fps if self.source2 and State.compiler > Compiler.STUDIOMDL else int(fps)

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
                ch_id = id_prefix + ch_name
                cur = dm.add_element(ch_name, "DmeChannel", id=id_prefix + bone.name + suffix)
                cur["toAttribute"] = attr
                cur["toElement"] = (self.bone_elements[bone.name] if bone else self.DmeModel)["transform"]
                cur["mode"] = 1
                if attr == "scale":
                    # scale is a single float on the transform, not an indexed vector component
                    cur["fromIndex"] = 0
                    cur["toIndex"] = 0
                layer = dm.add_element(type_name + " log", f"Dme{type_name}LogLayer", ch_id + "loglayer")
                cur["log"] = dm.add_element(type_name + " log", f"Dme{type_name}Log", ch_id + "log")
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
        return DmeChannelsClip

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
