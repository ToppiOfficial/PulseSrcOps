import bpy, os

from ..utils import *
from .. import ordered_set, flex

from .records import BakeResult


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

        # Animations are DMX-only, so this is always a model: rest pose, and bake_anim off so
        # a rig that merely has an action assigned does not drag it in - that spawns a stray
        # "<name>|Scene" action on every import.
        if self.armature:
            self.armature.data.pose_position = "REST"

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
                add_leaf_bones=False,
                use_armature_deform_only=deform_only,
                armature_nodetype='NULL',
                bake_anim=False,
                path_mode='AUTO',
                embed_textures=False,
                batch_mode='OFF',
            )
        except RuntimeError as err:
            self._error(get_id("exporter_err_open", True).format("FBX", err))
            return 0
        finally:
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
    # Names go through the same resolution DmxWriter uses, so the blendshapes ship under the
    # delta names the engine expects - including the L/R pair a split override expands into.
    def _restore_shapes(self, bake: BakeResult) -> None:
        ob = bake.object
        count = len(ob.data.vertices)
        basis = [0.0] * (count * 3)
        ob.data.vertices.foreach_get("co", basis)
        ob.shape_key_add(name="Basis", from_mix=False)

        dme = getattr(getattr(bake.src, 'vs', None), 'flex_controller_mode', 'DME') == 'DME'
        corrective_names = get_dme_corrective_delta_names(bake.src) if dme else set()
        delta_map = get_dme_delta_name_map(bake.src) if dme else {}
        split_map = get_dme_split_delta_map(bake.src) if dme else {}
        balance = bake.stereo_balance(ob, self._warning)
        if split_map and not bake.balance_vg:
            self._warning(get_id("exporter_warn_dme_split_no_balance", True).format(bake.name))

        # Correctives live as delta states on a DmeMesh, and the companion DMX carries no
        # mesh. The blendshape still ships in the FBX, but nothing declares it a corrective.
        sep = getCorrectiveShapeSeparator()
        if corrective_names or any(sep in n for n in bake.shapes):
            self._warning(get_id("exporter_warn_fbx_corrective", True).format(bake.name))

        def add(name, co):
            ob.shape_key_add(name=name, from_mix=False).data.foreach_set("co", co)

        for shape_name, mesh in bake.shapes.items():
            if len(mesh.vertices) != count:
                self._warning(get_id("exporter_warn_fbx_shapeverts", True).format(shape_name, bake.name))
                continue
            co = [0.0] * (count * 3)
            mesh.vertices.foreach_get("co", co)

            if dme:
                name, extras, split_base = resolve_dme_delta_names(
                    shape_name, corrective_names, delta_map, split_map)
            else:
                name, extras, split_base = self._corrective_name(bake, shape_name), [], None

            if split_base is not None:
                add(split_base + "L", self._split_co(co, basis, balance, left=True))
                add(split_base + "R", self._split_co(co, basis, balance, left=False))
                continue
            add(name, co)
            for extra in extras:  # one shape key feeding several deltas
                add(extra, co)
        ob.data.update()

    # Scales the shape's offset from basis by the stereo balance, as DmxWriter does per delta.
    @staticmethod
    def _split_co(co, basis, balance, left) -> list:
        out = []
        for i, b in enumerate(balance):
            w = (1.0 - b) if left else b
            for k in range(i * 3, i * 3 + 3):
                out.append(basis[k] + (co[k] - basis[k]) * w)
        return out

    # Legacy (non-DME) correctives: when the drivers disagree with the shape key's name, the
    # driver-derived name is the one the compiler looks up. Mirrors DmxWriter.
    def _corrective_name(self, bake: BakeResult, shape_name: str) -> str:
        sep = getCorrectiveShapeSeparator()
        kb = bake.src.data.shape_keys.key_blocks.get(shape_name) if sep in shape_name else None
        if not kb:
            return shape_name
        drivers = ordered_set.OrderedSet(flex.getCorrectiveShapeKeyDrivers(kb) or [])
        if drivers and drivers != ordered_set.OrderedSet(shape_name.split(sep)):
            return sep.join(drivers)
        return shape_name

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
    # armature itself, so they go into its rest pose. The mesh stays put - the same rest backs
    # the skin clusters. No action to re-key: animations are DMX-only.
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

        def mutate(edit_bones):
            # Connected children track their parent's tail, which the offset moves.
            for eb in edit_bones:
                eb.use_connect = False
            for name, off in offsets.items():
                eb = edit_bones[name]
                eb.matrix = eb.matrix @ off

        retarget_rest_pose(arm, (), mutate, post=offsets)
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
