import bpy
from bpy.types import UIList, UILayout, Collection, Object, UI_UL_list
from ..utils import State, get_armature, countShapes, MakeObjectIcon, sanitize_string_for_delta, get_id, get_jigglebones, get_hitboxes, get_attachments, hitbox_group, validate_flex_expression, validate_corrective_components, _build_dme_ctrl_names, _build_stereo_delta_names, get_dme_delta_override_conflicts, get_dme_renamed_delta_names, get_dme_split_delta_conflicts, is_bypassed_into_parent, get_active_exportable


class SMD_UL_ExportItems(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index, flt_flag):
        if item.prefab_type:
            self._draw_prefab_row(layout, item)
            return

        obj = item.item
        is_collection = isinstance(obj, Collection)
        enabled = not (is_collection and (obj.vs.mute or is_bypassed_into_parent(obj)))

        col = layout.column()
        split1 = self._draw_header_row(col, obj, item, enabled, index, is_collection = is_collection)

        if enabled:
            self._draw_stats_row(split1, obj)

    def _draw_header_row(self, col : UILayout, obj : Object, item, enabled, index, is_collection : bool):
        row = col.row(align=True)

        export_icon = 'CHECKBOX_HLT' if obj.vs.export and enabled else 'CHECKBOX_DEHLT'
        row.prop(obj.vs, "export", icon=export_icon, text="", emboss=False)
        row.label(text='', icon=item.icon)

        split1 = row.split(factor=0.7)
        split1.alert = not enabled
        split1.label(text=item.name)

        return split1

    def _draw_prefab_row(self, layout : UILayout, item):
        pitem = item.prefab_item
        row = layout.row(align=True)

        if pitem is not None:
            export_icon = 'CHECKBOX_HLT' if pitem.export else 'CHECKBOX_DEHLT'
            row.prop(pitem, "export", icon=export_icon, text="", emboss=False)
        else:
            row.label(text="", icon='BLANK1')

        row.label(text="", icon='FILE')
        row.label(text=item.name)

        right = row.row(align=True)
        right.alignment = 'RIGHT'
        if pitem is not None and (pitem.filepath or '').strip():
            right.label(text="", icon='FILE_TICK')
        right.label(text=str(item.prefab_count), icon=item.icon)

    def _draw_stats_row(self, split1 : UILayout, obj):
        row = split1.row(align=True)
        row.alignment = 'RIGHT'

        num_shapes, num_correctives = countShapes(obj)
        total_shapes = num_shapes + num_correctives
        if total_shapes > 0:
            row.label(text=str(total_shapes), icon='SHAPEKEY_DATA')

        num_vca = len(obj.vs.vertex_animations)
        if num_vca > 0:
            row.label(text=str(num_vca), icon='EDITMODE_HLT')

        subdir = obj.vs.subdir
        if subdir and subdir != ".":
            row.label(text=f"{subdir}/")


class SMD_UL_GroupItems(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index, flt_flag):
        ob = item.obj
        if not ob:
            return
        r = layout.row(align=True)
        r.prop(ob.vs,"export",text="",icon='CHECKBOX_HLT' if ob.vs.export else 'CHECKBOX_DEHLT',emboss=False)
        r.label(text=ob.name,translate=False,icon=MakeObjectIcon(ob,suffix="_DATA"))

    def filter_items(self, context, data, propname): # pyright: ignore
        # Entries (own objects plus those folded in from bypassed child groups)
        # are kept deduplicated in State.update_scene; here we hide non-exportable
        # objects, apply the search box, and sort by name.
        fname = self.filter_name.lower()
        entries = getattr(data, propname)
        flt = [
            self.bitflag_filter_item
            if e.obj and e.obj.session_uid in State.exportableObjects and (not fname or fname in e.obj.name.lower())
            else 0
            for e in entries
        ]
        if self.use_filter_sort_alpha:
            order = UI_UL_list.sort_items_helper(
                [(i, e.obj.name.lower() if e.obj else "") for i, e in enumerate(entries)],
                key=lambda t: t[1],
            )
        else:
            order = []
        return flt, order


class SMD_UL_ActionExport(UIList):
    # Read-only preview of the action slots (FILTERED) or actions (FILTERED_ACTIONS)
    # that the active armature's glob filter will export. Bound directly to
    # animation_data.action_suitable_slots / bpy.data.actions; filter_items applies
    # the same fnmatch used by the exporter so only exported entries are shown.
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index, flt_flag):
        row = layout.row(align=True)
        if hasattr(item, 'name_display'):  # ActionSlot
            row.label(text=item.name_display, icon='ACTION')
        else:  # Action
            row.label(text=item.name, icon='ACTION')

    def filter_items(self, context, data, propname):
        from fnmatch import fnmatch
        items = getattr(data, propname)
        ae = get_active_exportable(context)
        arm = ae.item if ae else None
        filt = arm.vs.action_filter if arm else ""

        flags = []
        for it in items:
            if hasattr(it, 'name_display'):  # slot
                ok = (not filt) or fnmatch(it.name_display, filt)
            else:  # action - mirror actionsForFilter: must have users
                ok = bool(it.users) and ((not filt) or fnmatch(it.name, filt))
            flags.append(self.bitflag_filter_item if ok else 0)
        return flags, []


class SMD_UL_DmeFlexControllers(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index, flt_flag):
        row = layout.row(align=True)

        has_name = bool(item.controller_name and item.controller_name.strip())
        name_row = row.row(align=True)
        name_row.alert = not has_name
        name_row.label(
            text=item.controller_name if has_name else "(unnamed)",
            icon='SHAPEKEY_DATA' if has_name else 'ERROR',
        )

        info_row = row.row(align=True)
        info_row.alignment = 'RIGHT'
        if item.shapekey:
            info_row.label(text=item.shapekey)
        if item.stereo:
            info_row.label(text="", icon='MOD_MIRROR')
        if item.eyelid:
            info_row.label(text="", icon='HIDE_OFF')


class SMD_UL_DmeFlexRules(UIList):
    _ICONS = {
        'EXPRESSION':  'DRIVER',
        'PASSTHROUGH': 'SHAPEKEY_DATA',
        'LOCALVAR':    'NODE',
        'DOMINATION':  'RESTRICT_SELECT_ON',
        'CORRECTIVE':  'SCULPTMODE_HLT',
    }

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index, flt_flag):
        ob = context.object
        row = layout.row(align=True)

        type_icon = self._ICONS.get(item.rule_type, 'QUESTION')
        row.label(text="", icon=type_icon)

        sk = ob.data.shape_keys if (ob.data and hasattr(ob.data, 'shape_keys')) else None
        sk_names = set(sk.key_blocks.keys()) if sk else set()
        ctrl_names = _build_dme_ctrl_names(ob.vs)
        localvar_names = {r.name for r in ob.vs.dme_flex_rules if r.rule_type == 'LOCALVAR' and r.name}
        stereo_delta_names = _build_stereo_delta_names(ob.vs)
        renamed_delta_names = get_dme_renamed_delta_names(ob)

        has_error = False

        if item.rule_type == 'CORRECTIVE':
            comp_str = item.components.strip()
            has_error = not comp_str or bool(validate_corrective_components(comp_str, sk_names))
            name_row = row.row(align=True)
            name_row.alert = has_error
            name_row.label(text=item.components if item.components else "(no components)")

        elif item.rule_type == 'DOMINATION':
            has_error = not item.dominator_names or not item.suppressed_names
            dom_label = item.dominator_names[:24] + ("…" if len(item.dominator_names) > 24 else "") if item.dominator_names else "(no dominators)"
            sup_label = item.suppressed_names[:20] + ("…" if len(item.suppressed_names) > 20 else "") if item.suppressed_names else ""
            name_col = row.row(align=True)
            name_col.alert = has_error
            name_col.label(text=dom_label)
            if sup_label:
                right = row.row(align=True)
                right.alignment = 'RIGHT'
                right.label(text="-> " + sup_label)
        else:
            name_alert = False
            if item.rule_type == 'PASSTHROUGH':
                name_alert = not item.name or item.name not in ctrl_names
                has_error = name_alert
            elif item.rule_type == 'EXPRESSION':
                if not item.name:
                    name_alert = True
                else:
                    in_shapekeys = sk is not None and (
                        item.name in sk.key_blocks or
                        any(item.name in key.name.split('+') for key in sk.key_blocks)
                    )
                    name_alert = (not in_shapekeys and item.name not in localvar_names
                                  and item.name not in stereo_delta_names
                                  and item.name not in renamed_delta_names)
                if name_alert:
                    has_error = True
                elif item.expression:
                    d_errs, c_errs = validate_flex_expression(item.expression.strip(), sk_names, ctrl_names, localvar_names, stereo_delta_names, renamed_delta_names)
                    has_error = bool(d_errs or c_errs)
            elif item.rule_type == 'LOCALVAR':
                name_alert = not item.name
                has_error = name_alert

            name_row = row.row(align=True)
            name_row.alert = name_alert
            display_name = item.name if item.name else ("(unnamed)" if item.rule_type != 'LOCALVAR' else "(local var)")
            name_row.label(text=display_name)

            if item.rule_type == 'EXPRESSION' and item.expression:
                expr_row = row.row(align=True)
                expr_row.alignment = 'RIGHT'
                truncated = item.expression[:28] + ("…" if len(item.expression) > 28 else "")
                expr_row.label(text=truncated)
            elif item.rule_type == 'PASSTHROUGH':
                pass_row = row.row(align=True)
                pass_row.alignment = 'RIGHT'
                pass_row.enabled = False
                pass_row.label(text="pass-through")

        if has_error:
            err_col = row.row(align=True)
            err_col.alert = True
            err_col.label(text="", icon='ERROR')


class SMD_UL_DmeDeltaOverrides(UIList):
    # Live text filter typed into the UIList search box; substring-matched against the
    # source shapekey and the override delta name.
    filter_name_search: bpy.props.StringProperty(
        name="Filter", default="",
        description=get_id("delta_override_filter_tip"),
    )

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob = context.object
        conflicts = get_dme_delta_override_conflicts(ob) if ob else set()
        split_conflicts = get_dme_split_delta_conflicts(ob) if ob else set()

        row = layout.row(align=True)
        sk = ob.data.shape_keys if ob and ob.data and hasattr(ob.data, 'shape_keys') else None
        sk_missing = sk is None or item.shapekey not in sk.key_blocks
        sk_row = row.row(align=True)
        sk_row.alert = bool(item.shapekey and sk_missing)
        sk_row.label(text=item.shapekey if item.shapekey else "(no key)", icon='SHAPEKEY_DATA')

        if getattr(item, 'split_lr', False):
            mirror = row.row(align=True)
            mirror.alert = index in split_conflicts
            mirror.label(text="", icon='MOD_MIRROR')

        right = row.row(align=True)
        right.alignment = 'RIGHT'
        is_conflict = index in conflicts or index in split_conflicts
        right.alert = is_conflict
        base = sanitize_string_for_delta(item.delta_name) if item.delta_name else ""
        # Show the L/R deltas that the split will actually produce.
        disp = f"{base}L / {base}R" if (base and getattr(item, 'split_lr', False)) else base
        right.label(text=disp, icon='ERROR' if is_conflict else 'NONE')

    def draw_filter(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "filter_name_search", text="", icon='VIEWZOOM')

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = []
        search = self.filter_name_search.strip().lower()
        if search:
            for item in items:
                hay = f"{item.shapekey} {item.delta_name}".lower()
                flags.append(self.bitflag_filter_item if search in hay else 0)
        else:
            flags = [self.bitflag_filter_item] * len(items)
        # No custom ordering; keep collection order.
        order = []
        return flags, order


class SMD_UL_VertexAnimationItem(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index): # pyright: ignore
        r = layout.row()
        r.alignment='LEFT'
        r.prop(item,"name",text="",emboss=False)
        r = layout.row(align=True)
        r.alignment='RIGHT'
        r.operator("smd.vertexanim_preview",text="",icon='PAUSE' if context.screen.is_animation_playing else 'PLAY')
        r.prop(item,"start",text="")
        r.prop(item,"end",text="")
        r.prop(item,"export_sequence",text="",icon='ACTION')


_HBOX_GROUP_LABELS = {ident: label for ident, label, *_ in hitbox_group}


class SMD_UL_Hitboxes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        is_capsule = item.scale > 0
        is_inverted = not is_capsule and any(item.vec_min[i] > item.vec_max[i] for i in range(3))
        shape_icon = 'META_CAPSULE' if is_capsule else 'MESH_CUBE'
        row.label(text='', icon=shape_icon)
        row.label(text=item.bone_name if item.bone_name else '—', icon='BONE_DATA')
        grp_label = _HBOX_GROUP_LABELS.get(item.group, item.group)
        row.label(text=grp_label)
        if is_capsule:
            row.label(text=f"r={item.scale:.2f}")
        if is_inverted:
            row.label(text='', icon='ERROR')


class SMD_UL_ArmatureItems(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        arm = get_armature(context.object)
        if active_propname == 'arm_attachment_index':
            obj = item.obj
            if obj:
                row = layout.row(align=True)
                row.label(text=obj.name, icon='EMPTY_DATA')
                if arm:
                    row.prop_search(obj, 'parent_bone', arm.data, 'bones', text='')
        else:  # arm_jigglebone_index
            bone = arm.data.bones.get(item.bone_name) if arm else None
            row = layout.row(align=True)
            row.label(text=item.bone_name or '?', icon='BONE_DATA')
            if bone:
                count = len(bone.collections)
                if count == 1:
                    row.label(text=bone.collections[0].name, icon='GROUP_BONE')
                elif count > 1:
                    row.label(text=get_id('label_in_multiple_collection', format_string=True), icon='GROUP_BONE')
                else:
                    row.label(text=get_id('label_not_in_collection', format_string=True), icon='GROUP_BONE')


class SMD_UL_ProcBones(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        proc_type = getattr(item, 'proc_type', 'TRIGGER')
        row.label(text='', icon='ACTION' if proc_type == 'TRIGGER' else 'CON_TRACKTO')
        row.label(text=item.helper_bone if item.helper_bone else "", icon='BONE_DATA')
        row.label(text=item.driver_bone if item.driver_bone else "", icon='DRIVER')
        if proc_type == 'TRIGGER':
            action_label = item.action.name if item.action else ""
            if action_label and item.action_slot_name:
                action_label = f"{item.action_slot_name} ({action_label})"
            row.label(text=action_label)


class SMD_UL_BoneNamePrefixes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.6, align=True)
        split.prop(item, "prefix", text="", emboss=True)
        split.prop(item, "shortcut", text="", emboss=True, icon='SYNTAX_OFF')


class SMD_UL_AttachmentDisplayMeshes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        mesh_name = item.mesh.name if item.mesh else "(No Mesh)"
        mesh_icon = 'MESH_DATA' if item.mesh else 'GHOST_DISABLED'
        row.label(text=mesh_name, icon=mesh_icon)
        is_rendered = data.attachment_display_mesh_render_index == index
        cam_icon = 'RESTRICT_RENDER_OFF' if is_rendered else 'RESTRICT_RENDER_ON'
        op = row.operator('smd.set_attachment_mesh_render', text="", icon=cam_icon, emboss=False)
        op.index = index

