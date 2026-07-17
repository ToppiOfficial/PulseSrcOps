import bpy, os, math
from math import *  # pyright: ignore

from ..utils import *
from ..keyvalues3 import *
from ..prefab_io import jigglebone as _jigglebone, hitbox as _hitbox, proceduralbone as _proceduralbone

from .check import ExportCheck


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

        hboxset = getattr(avs, 'hboxset_name', '').strip() or 'default'

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

        lines = []
        lines.append(f'$hboxset\t"{hboxset}"')
        for bone in sorted_bones:
            for e in seen_bones[bone]:
                lines.append(_hitbox.qc_line(e, get_bone_exportname(bone)))
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

        # studiomdl's .vrd compiler treats the text before the first '.' in a bone
        # name as a prefix and strips it ("ValveBiped.Bip01" -> "Bip01"). That is
        # intended for real prefixes like "ValveBiped.", but an accidental dot in a
        # bone name silently drops part of the name. Only the Source 1 .vrd path is
        # affected - DME prefab, Source 2 and newer studiomdl/PulseMDL don't strip.
        preserved = tuple(p.lower() for p in get_preserved_bone_prefixes())
        warned_dotnames: set[str] = set()
        for entry in entries:
            for bname in (entry.helper_bone, entry.driver_bone):
                bone = arm.data.bones.get(bname) if bname else None
                if not bone:
                    continue
                export_name = get_bone_exportname(bone)
                if '.' not in export_name or export_name in warned_dotnames:
                    continue
                if export_name.lower().startswith(preserved):
                    continue
                warned_dotnames.add(export_name)
                self.report(
                    {'WARNING'},
                    f"Procedural bone name '{export_name}' contains a '.'"
                )

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