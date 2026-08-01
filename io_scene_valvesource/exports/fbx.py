import bpy, collections, json, os
from math import degrees
from mathutils import Matrix

from ..utils import *
from ..keyvalues3 import KVBool, KVVector3
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone

from .records import BakeResult


def _json_safe(v):
    if isinstance(v, KVBool):    return v.value
    if isinstance(v, KVVector3): return [v.x, v.y, v.z]
    if isinstance(v, (bool, int, float, str)): return v
    return list(v)  # Vector / Quaternion / datamodel arrays


# Hands the Baker's output to Blender's bundled FBX exporter. The bake already applied the
# axis/scale transform, so the operator runs with an identity conversion; the rest is glue.
# ponytail: wraps bpy.ops.export_scene.fbx rather than forking io_scene_fbx.
class FbxWriter:
    def __init__(self, reporter, id, bake_results, name, dir_path, *,
                 armature, armature_src, exportable_bones, exportable_boneNames,
                 all_bake_results):
        self.r = reporter
        self.id = id
        self.bake_results = bake_results
        self.name = name
        self.dir_path = dir_path
        self.armature = armature
        self.armature_src = armature_src
        self.exportable_bones = exportable_bones
        self.exportable_boneNames = exportable_boneNames
        self.all_bake_results = all_bake_results
        # Same test DmxWriter uses.
        self.is_anim = len(bake_results) == 1 and bake_results[0].object.type == "ARMATURE"

    def _warning(self, *a): self.r.warning(*a)
    def _error(self, *a): self.r.error(*a)

    def write(self) -> int:
        if not self._ensure_addon():
            self._error(get_id("exporter_err_fbx_addon", True))
            return 0

        objects = [b.object for b in self.bake_results if b.object]
        if not objects:
            return 0
        meshes = [ob for ob in objects if ob.type == 'MESH']

        if self.bake_results[0].vertex_animations:
            self._warning(get_id("exporter_warn_fbx_vca", True).format(self.name))

        for bake in self.bake_results:
            if bake.object and bake.object.type == 'MESH' and bake.shapes:
                self._restore_shapes(bake)

        self._write_custom_props(meshes)

        if self.armature:
            self._apply_bone_names(meshes)
            for bake in self.bake_results:
                if bake.object and bake.object.type == 'MESH':
                    self._bind_skin(bake)
            if self.armature not in objects:
                objects.append(self.armature)

        self._apply_armature_scale()
        self._apply_bone_offsets()
        renamed = self._claim_export_names()

        filepath = os.path.realpath(os.path.join(
            self.dir_path, sanitize_string(self.name, allow_unicode=True) + ".fbx"))

        bpy.ops.object.select_all(action='DESELECT')
        for ob in objects:
            ob.select_set(True)
        bpy.context.view_layer.objects.active = self.armature or objects[0]

        scene = bpy.context.scene
        # Only an animation export writes animation - a model whose rig merely has an action
        # assigned must not drag it in, or every import spawns stray "<name>|Scene" actions.
        action = self.armature.animation_data.action if (
            self.is_anim and self.armature and self.armature.animation_data) else None
        if self.armature:
            self.armature.data.pose_position = "POSE" if self.is_anim else "REST"
        saved_range = None
        if action:
            saved_range = (scene.frame_start, scene.frame_end)
            start, end = action.frame_range
            scene.frame_start = int(start)
            scene.frame_end = max(int(end), int(start))

        # Matches how _setup_skeleton built exportable_bones.
        deform_only = bool(self.armature) and \
            len(self.exportable_bones) < len(self.armature.pose.bones)

        try:
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                check_existing=False,
                use_selection=True,
                object_types={'ARMATURE', 'MESH'},
                # The bake already emitted engine units. Blender hard-codes a metre->cm factor
                # of 100; FBX_SCALE_ALL moves it out of the transforms into the header, where
                # 0.01 cancels it to UnitScaleFactor 1 - matching Source 2's own exports.
                global_scale=0.01,
                apply_unit_scale=False,
                apply_scale_options='FBX_SCALE_ALL',
                use_space_transform=True,
                bake_space_transform=False,
                axis_forward='Y',
                axis_up='Z',
                use_mesh_modifiers=False,
                mesh_smooth_type='FACE',
                use_triangles=False,
                use_custom_props=True,
                add_leaf_bones=False,
                use_armature_deform_only=deform_only,
                armature_nodetype='NULL',
                bake_anim=bool(action),
                bake_anim_use_all_bones=True,
                bake_anim_use_nla_strips=False,
                bake_anim_use_all_actions=False,
                bake_anim_force_startend_keying=True,
                bake_anim_step=1.0,
                bake_anim_simplify_factor=0.0,
                path_mode='AUTO',
                embed_textures=False,
                batch_mode='OFF',
            )
        except RuntimeError as err:
            self._error(get_id("exporter_err_open", True).format("FBX", err))
            return 0
        finally:
            if saved_range:
                scene.frame_start, scene.frame_end = saved_range
            for datablock, old_name in reversed(renamed):
                datablock.name = old_name

        print("-", filepath)
        return 1

    # -----------------------------------------------------------------------
    def _ensure_addon(self) -> bool:
        if hasattr(bpy.ops.export_scene, "fbx"):
            return True
        import addon_utils
        for module in ("io_scene_fbx", "bl_ext.blender_org.io_scene_fbx"):
            try:
                addon_utils.enable(module, default_set=False)
            except Exception:
                pass
        return hasattr(bpy.ops.export_scene, "fbx")

    # The Baker bakes each shape key into its own Mesh; FBX wants them back as shape keys.
    def _restore_shapes(self, bake: BakeResult) -> None:
        ob = bake.object
        count = len(ob.data.vertices)
        ob.shape_key_add(name="Basis", from_mix=False)
        for shape_name, mesh in bake.shapes.items():
            if len(mesh.vertices) != count:
                self._warning(get_id("exporter_warn_fbx_shapeverts", True).format(shape_name, bake.name))
                continue
            co = [0.0] * (count * 3)
            mesh.vertices.foreach_get("co", co)
            kb = ob.shape_key_add(name=shape_name, from_mix=False)
            kb.data.foreach_set("co", co)
        ob.data.update()

    # Baked meshes carry no modifiers, so the skin bind is recreated. Bone-parented meshes
    # become one full-weight group, as DMX/SMD emit for them anyway.
    def _bind_skin(self, bake: BakeResult) -> None:
        ob = bake.object
        if isinstance(bake.envelope, str):
            vg = ob.vertex_groups.get(bake.envelope) or ob.vertex_groups.new(name=bake.envelope)
            vg.add(range(len(ob.data.vertices)), 1.0, 'REPLACE')
        elif not isinstance(bake.envelope, bpy.types.ArmatureModifier):
            return
        if not any(m.type == 'ARMATURE' for m in ob.modifiers):
            mod = ob.modifiers.new("Armature", 'ARMATURE')
            mod.object = self.armature

    # The bake leaves world_scale on the armature's matrix for DmxWriter's armature_scale.
    # FBX has no such hook, so bake it in or bones ship at Blender scale and meshes at engine.
    def _apply_armature_scale(self) -> None:
        if not self.armature:
            return
        if all(abs(s - 1.0) < 1e-6 for s in self.armature.matrix_world.to_scale()):
            return
        select_only(self.armature)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # DMX/SMD apply bone.vs export offsets via get_bone_matrix(). The FBX is written from the
    # armature itself, so they go into its rest pose. The mesh stays put (same rest backs the
    # skin clusters), but an action needs re-keying to world @ off - the offset turns each
    # bone's local space.
    def _apply_bone_offsets(self) -> None:
        arm = self.armature
        if not arm:
            return

        offsets = {}
        for pb in arm.pose.bones:
            bvs = pb.bone.vs
            # Gate on the properties, not on comparing matrices: mathutils is single
            # precision, so far from the origin every bone reads as offset.
            has_rot = not bvs.ignore_rotation_offset and any(
                (bvs.export_rotation_offset_x, bvs.export_rotation_offset_y, bvs.export_rotation_offset_z))
            has_loc = not bvs.ignore_location_offset and any(
                (bvs.export_location_offset_x, bvs.export_location_offset_y, bvs.export_location_offset_z))
            if not (has_rot or has_loc):
                continue
            offsets[pb.name] = pb.bone.matrix_local.inverted() @ get_bone_matrix(pb, rest_space=True)
        src_name = (self.armature_src or arm).name
        if not offsets:
            print(f"- No bone export offsets on \"{src_name}\" ({len(arm.pose.bones)} bones)")
            return

        if self.is_anim and arm.animation_data and arm.animation_data.action:
            # retarget_rest_pose re-keys this, and the baked armature shares the user's Action.
            slot_id = getattr(arm.animation_data.action_slot, "identifier", None)
            action = arm.animation_data.action.copy()
            arm.animation_data.action = action
            if slot_id:
                slot = next((s for s in action.slots if s.identifier == slot_id), None)
                if slot:
                    arm.animation_data.action_slot = slot

        def mutate(edit_bones):
            # Connected children track their parent's tail, which the offset moves.
            for eb in edit_bones:
                eb.use_connect = False
            for name, off in offsets.items():
                eb = edit_bones[name]
                eb.matrix = eb.matrix @ off

        retarget_rest_pose(arm, offsets if self.is_anim else (), mutate, post=offsets)
        sample = ", ".join(sorted(offsets)[:5])
        print(f"- Baked export offsets into {len(offsets)}/{len(arm.pose.bones)} bones "
              f"of \"{src_name}\" ({sample}{', ...' if len(offsets) > 5 else ''})")

    # FBX identifies meshes by node name, so the baked copies need the clean export name
    # (DMX/SMD just write bake.name). Whatever holds that name is parked aside and restored.
    def _claim_export_names(self) -> list:
        renamed = []

        def rename(datablock, new_name):
            if datablock and datablock.name != new_name:
                renamed.append((datablock, datablock.name))
                datablock.name = new_name

        def take(datablock, collection, name):
            if not datablock or datablock.name == name:
                return
            holder = collection.get(name)
            if holder is not None and holder is not datablock:
                rename(holder, name + "_pulse_src")
            rename(datablock, name)

        # Read up front: if the armature is also a bake result, the loop parks armature_src and
        # reading its name afterwards would hand the baked armature its own parking name.
        arm_name = self.armature_src.name if (
            self.armature and self.armature_src and self.armature is not self.armature_src) else None

        baked = [b.object for b in self.bake_results if b.object]
        for bake in self.bake_results:
            if not bake.object:
                continue
            take(bake.object, bpy.data.objects, bake.name)
            if bake.object.data is not None:
                take(bake.object.data, self._data_collection(bake.object.data), bake.name)

        if arm_name and self.armature not in baked:
            take(self.armature, bpy.data.objects, arm_name)
            take(self.armature.data, bpy.data.armatures, arm_name)

        return renamed

    @staticmethod
    def _data_collection(data):
        return bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.armatures

    # -- custom content ------------------------------------------------------
    # use_custom_props turns Blender custom properties into FBX user properties (object ->
    # Model node, pose bone -> that bone's Model node), so Source data rides there as JSON in
    # the same vocabulary the KV3/DME writers use. Runs before _apply_bone_names.
    def _write_custom_props(self, meshes: list) -> None:
        for bake in self.bake_results:
            if bake.object in meshes and bake.src:
                payload = self._flex_payload(bake.src)
                if payload:
                    bake.object["source_flex"] = json.dumps(payload)

        if not (self.armature and self.armature_src):
            return
        self._write_jigglebone_props()
        self._write_hitbox_props()
        self._write_procbone_props()

    def _flex_payload(self, src: bpy.types.Object) -> dict:
        vs = getattr(src, "vs", None)
        if not vs:
            return {}
        # Only DME mode authors these collections. SIMPLE derives from shape key names, which a
        # consumer can do from the blendshapes; ADVANCED points at a file that is not in here.
        mode = getattr(vs, "flex_controller_mode", 'SIMPLE')
        if mode != 'DME':
            if mode == 'ADVANCED' and vs.flex_controller_source and hasShapes(src):
                self._warning(get_id("exporter_warn_fbx_flex_advanced", True).format(src.name))
            return {}
        controllers = [
            dict(name=fc.controller_name, shapekey=fc.shapekey, delta_name=fc.raw_delta_name,
                 group=fc.resolved_flexgroup(), eyelid=fc.eyelid, stereo=fc.stereo,
                 flex_min=fc.flex_min, flex_max=fc.flex_max)
            for fc in getattr(vs, "dme_flexcontrollers", []) if fc.controller_name
        ]
        rules = [
            dict(type=r.rule_type, name=r.name, expression=r.expression, components=r.components,
                 dominators=r.dominator_names, suppressed=r.suppressed_names)
            for r in getattr(vs, "dme_flex_rules", []) if r.name or r.dominator_names
        ]
        if not controllers and not rules:
            return {}
        return dict(controllers=controllers, rules=rules)

    def _write_jigglebone_props(self) -> None:
        for bone in self.armature_src.data.bones:
            vs = getattr(bone, "vs", None)
            if not vs or not vs.bone_is_jigglebone:
                continue
            pb = self.armature.pose.bones.get(bone.name)
            if not pb:
                continue
            length = bone.length if vs.use_bone_length_for_jigglebone_length else vs.jiggle_length
            kwargs = _jigglebone.kv3_kwargs(vs, self._export_name(bone.name), length)
            pb["source_jigglebone"] = json.dumps({k: _json_safe(v) for k, v in kwargs.items()})

    def _write_hitbox_props(self) -> None:
        arm_data = self.armature_src.data
        entries = [
            {k: _json_safe(v) for k, v in
             _hitbox.kv3_capsule_kwargs(e, self._export_name(e.bone_name)).items()}
            for e in getattr(arm_data.vs, "hitboxes", [])
            if e.bone_name and arm_data.bones.get(e.bone_name)
        ]
        if entries:
            self.armature["source_hitboxes"] = json.dumps(entries)

    # Mirrors prefab_io.proceduralbone.write_dme_* - same math, JSON instead of DME attrs.
    def _write_procbone_props(self) -> None:
        arm = self.armature_src
        scale = self.armature.matrix_world.to_scale()
        scene = bpy.context.scene
        seen: set[str] = set()

        for idx, entry in enumerate(getattr(arm.data.vs, "proc_bones", [])):
            helper = entry.helper_bone
            pb = self.armature.pose.bones.get(helper) if helper else None
            if not pb or helper in seen:
                continue
            seen.add(helper)

            helper_bone = arm.data.bones.get(helper)
            parent = (helper_bone.parent.name if helper_bone and helper_bone.parent
                      else entry.driver_bone)
            base = _proceduralbone.basepos_local(arm, helper, parent)
            payload = {
                "type": entry.proc_type,
                "parent_bone": self._export_name(parent),
                "base_pos": [base.x * scale[0], base.y * scale[1], base.z * scale[2]],
            }

            if entry.proc_type == 'TRIGGER':
                transforms = _proceduralbone.build_trigger_transforms(arm, entry, idx, scene)
                if not (entry.action and entry.driver_bone and transforms):
                    self._warning(get_id("exporter_warn_procbone_no_triggers", True).format(helper))
                    continue
                payload["control_bone"] = self._export_name(entry.driver_bone)
                payload["tolerances"] = [degrees(t[2]) for t in transforms]
                payload["trigger_rotations"] = [list(getDatamodelQuat(t[0].to_quaternion())) for t in transforms]
                payload["target_rotations"] = [list(getDatamodelQuat(t[1].to_quaternion())) for t in transforms]
                payload["target_positions"] = [
                    [p.x * scale[0], p.y * scale[1], p.z * scale[2]]
                    for p in (t[1].to_translation() for t in transforms)
                ]
            else:
                if not entry.driver_bone:
                    self._warning(get_id("exporter_warn_procbone_no_target", True).format(helper))
                    continue
                payload["aim_target"] = self._export_name(entry.driver_bone)
                payload["aim_offset"] = list(entry.lookat_offset)
                payload["aim_vector"] = list(_proceduralbone.axes_to_vec(entry.lookat_aim_axis))
                payload["up_vector"] = list(_proceduralbone.axes_to_vec(entry.lookat_up_axis))

            pb["source_procbone"] = json.dumps(payload)

    def _export_name(self, bone_name: str) -> str:
        return self.exportable_boneNames.get(bone_name, bone_name)

    # Groups before bones, so Blender's own bone-rename sync can't rename them twice.
    def _apply_bone_names(self, meshes: list) -> None:
        renames = {old: new for old, new in self.exportable_boneNames.items() if old != new}
        if not renames:
            return
        for ob in meshes:
            for old, new in renames.items():
                vg = ob.vertex_groups.get(old)
                if vg:
                    vg.name = new
        for old, new in renames.items():
            bone = self.armature.data.bones.get(old)
            if bone:
                bone.name = new
