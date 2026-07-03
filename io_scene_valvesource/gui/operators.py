import bpy, math, re as _re
from bpy.types import Operator, MeshLoopColorLayer, LoopColors
from bpy.props import FloatProperty, BoolProperty, IntProperty, EnumProperty, StringProperty
from ..utils import (get_id, get_armature, is_mesh, is_armature, vertex_maps, vertex_float_maps,
                     get_bone_exportname, getFileExt, get_valid_vertexanimation_object, sanitize_string_for_delta)
from .. import procbones_sim as _procbones_sim
from .helpers import _get_or_create_proc_tol_fcurve, _get_entry_proc_tol


SMD_OT_CreateVertexMap_idname : str = "smd.vertex_map_create_"
SMD_OT_SelectVertexMap_idname : str = "smd.vertex_map_select_"
SMD_OT_RemoveVertexMap_idname : str = "smd.vertex_map_remove_"

for map_name in vertex_maps:

    class SelectVertexColorMap(Operator):
        bl_idname = SMD_OT_SelectVertexMap_idname + map_name
        bl_label = get_id("vertmap_select")
        bl_description = get_id("vertmap_select")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            if not is_mesh(context.object):
                return False
            vc_loop : MeshLoopColorLayer | None = context.object.data.vertex_colors.get(cls.vertex_map)
            return bool(vc_loop and not vc_loop.active)

        def execute(self, context) -> set:
            context.object.data.vertex_colors[self.vertex_map].active = True
            return {'FINISHED'}

    class CreateVertexColorMap(Operator):
        bl_idname = SMD_OT_CreateVertexMap_idname + map_name
        bl_label = get_id("vertmap_create")
        bl_description = get_id("vertmap_create")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            return bool(is_mesh(context.object) and cls.vertex_map not in context.object.data.vertex_colors)

        def execute(self, context) -> set:
            vc : MeshLoopColorLayer = context.object.data.vertex_colors.new(name=self.vertex_map)
            vc.data.foreach_set("color", [1.0] * len(vc.data) * 4)
            SelectVertexColorMap.execute(self, context)
            return {'FINISHED'}

    class RemoveVertexColorMap(Operator):
        bl_idname = SMD_OT_RemoveVertexMap_idname + map_name
        bl_label = get_id("vertmap_remove")
        bl_description = get_id("vertmap_remove")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            return bool(is_mesh(context.object) and cls.vertex_map in context.object.data.vertex_colors)

        def execute(self, context) -> set:
            vcs : LoopColors  = context.object.data.vertex_colors
            vcs.remove(vcs[self.vertex_map])
            return {'FINISHED'}

    bpy.utils.register_class(SelectVertexColorMap)
    bpy.utils.register_class(CreateVertexColorMap)
    bpy.utils.register_class(RemoveVertexColorMap)


SMD_OT_CreateVertexFloatMap_idname : str = "smd.vertex_float_map_create_"
SMD_OT_SelectVertexFloatMap_idname : str = "smd.vertex_float_map_select_"
SMD_OT_RemoveVertexFloatMap_idname : str = "smd.vertex_float_map_remove_"

for map_name in vertex_float_maps:

    class SelectVertexFloatMap(Operator):
        bl_idname = SMD_OT_SelectVertexFloatMap_idname + map_name
        bl_label = get_id("vertmap_select")
        bl_description = get_id("vertmap_select")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            vg_loop = context.object.vertex_groups.get(cls.vertex_map)
            return bool(vg_loop and not context.object.vertex_groups.active == vg_loop)

        def execute(self, context) -> set:
            context.object.vertex_groups.active_index = context.object.vertex_groups[self.vertex_map].index
            return {'FINISHED'}

    class CreateVertexFloatMap(Operator):
        bl_idname = SMD_OT_CreateVertexFloatMap_idname + map_name
        bl_label = get_id("vertmap_create")
        bl_description = get_id("vertmap_create")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            return bool(context.object and context.object.type == 'MESH' and cls.vertex_map not in context.object.vertex_groups)

        def execute(self, context) -> set:
            vc = context.object.vertex_groups.new(name=self.vertex_map)

            found : bool = False
            for remap in context.object.vs.vertex_map_remaps:
                if remap.group == map_name:
                    found = True
                    break

            if not found:
                remap = context.object.vs.vertex_map_remaps.add()
                remap.group = map_name
                remap.min : float = 0.0
                remap.max : float = 1.0

            SelectVertexFloatMap.execute(self, context)
            return {'FINISHED'}

    class RemoveVertexFloatMap(Operator):
        bl_idname = SMD_OT_RemoveVertexFloatMap_idname + map_name
        bl_label = get_id("vertmap_remove")
        bl_description = get_id("vertmap_remove")
        bl_options = {'INTERNAL'}
        vertex_map = map_name

        @classmethod
        def poll(cls, context) -> bool:
            return bool(context.object and context.object.type == 'MESH' and cls.vertex_map in context.object.vertex_groups)

        def execute(self, context) -> set:
            vgs = context.object.vertex_groups
            vgs.remove(vgs[self.vertex_map])
            return {'FINISHED'}

    bpy.utils.register_class(SelectVertexFloatMap)
    bpy.utils.register_class(CreateVertexFloatMap)
    bpy.utils.register_class(RemoveVertexFloatMap)


class SMD_OT_AddFlexController(Operator):
    bl_idname = "smd.add_flexcontroller"
    bl_label = "Add Flex Controller"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        ob  = context.object

        new_item = ob.vs.dme_flexcontrollers.add()
        ob.vs.dme_flexcontrollers_index = len(ob.vs.dme_flexcontrollers) - 1

        if hasattr(ob.data, 'shape_keys') and ob.active_shape_key_index is not None and ob.active_shape_key_index > 0:
            new_item.shapekey = ob.data.shape_keys.key_blocks[ob.active_shape_key_index].name
            new_item.raw_delta_name = new_item.shapekey
        else:
            new_item.shapekey = ""

        return {'FINISHED'}


class SMD_OT_AddAllFlexControllers(Operator):
    bl_idname = "smd.add_all_flexcontrollers"
    bl_label = "Add All Flex Controllers"
    bl_options = {'INTERNAL', 'UNDO'}

    mode: EnumProperty(
        name="Add Mode",
        items=[
            ('ALL', "Add All", "Add all shape keys, replacing existing entries"),
            ('MISSING', "Add Missing", "Only add shape keys not already in the list"),
        ],
        default='MISSING',
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context) -> set:
        ob = context.object

        if not hasattr(ob.data, 'shape_keys') or ob.data.shape_keys is None:
            self.report({'WARNING'}, "No shape keys found on active object")
            return {'CANCELLED'}

        key_blocks = ob.data.shape_keys.key_blocks
        existing = {item.shapekey for item in ob.vs.dme_flexcontrollers}

        added = 0
        for key in key_blocks[1:]:  # skip Basis
            if self.mode == 'MISSING' and key.name in existing:
                continue
            new_item = ob.vs.dme_flexcontrollers.add()
            new_item.shapekey = key.name
            new_item.raw_delta_name = key.name
            added += 1

        if added:
            ob.vs.dme_flexcontrollers_index = len(ob.vs.dme_flexcontrollers) - 1

        self.report({'INFO'}, f"Added {added} flex controller(s)")
        return {'FINISHED'}


class SMD_OT_ImportFlexControllersFromText(Operator):
    bl_idname = "smd.import_flex_from_text"
    bl_label = "Import from Text Block"
    bl_description = get_id("op_import_flex_text_tip")
    bl_options = {'INTERNAL', 'UNDO'}

    text_block: StringProperty(name="Text Block", description=get_id("op_import_flex_text_block_tip"))

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and hasattr(context.object, "vs")
                    and hasattr(context.object.vs, "dme_flexcontrollers") and len(bpy.data.texts))

    def invoke(self, context, event):
        # Prefill with the text block shown in an open Text Editor, if any.
        if not self.text_block:
            for area in context.screen.areas:
                if area.type == 'TEXT_EDITOR' and area.spaces.active.text:
                    self.text_block = area.spaces.active.text.name
                    break
            if not self.text_block and len(bpy.data.texts) == 1:
                self.text_block = bpy.data.texts[0].name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, "text_block", bpy.data, "texts", text="")

    def execute(self, context) -> set:
        from ..import_smd import parse_flex_text, apply_flex_text_to_object

        text = bpy.data.texts.get(self.text_block)
        if not text:
            self.report({'ERROR'}, "Select a text block to import from")
            return {'CANCELLED'}

        parsed = parse_flex_text(text.as_string())
        n_controllers, n_rules = apply_flex_text_to_object(context.object, parsed)

        if context.object.vs.dme_flexcontrollers:
            context.object.vs.dme_flexcontrollers_index = len(context.object.vs.dme_flexcontrollers) - 1

        if not n_controllers and not n_rules:
            self.report({'WARNING'}, "No flex controllers or rules found in the text block")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Imported {n_controllers} controller(s), {n_rules} rule(s)")
        return {'FINISHED'}


class SMD_OT_RemoveFlexController(Operator):
    bl_idname = "smd.remove_flexcontroller"
    bl_label = "Remove Flex Controller"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(len(context.object.vs.dme_flexcontrollers) > 0)

    def execute(self, context) -> set:
        context.object.vs.dme_flexcontrollers.remove(context.object.vs.dme_flexcontrollers_index)
        context.object.vs.dme_flexcontrollers_index = min(max(0, context.object.vs.dme_flexcontrollers_index - 1),
                                                 len(context.object.vs.dme_flexcontrollers) - 1)
        return {'FINISHED'}


class SMD_OT_MoveFlexController(Operator):
    bl_idname = "smd.move_flexcontroller"
    bl_label = "Move Flex Controller"
    bl_options = {'INTERNAL', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context) -> set:
        ob = context.object
        controllers = ob.vs.dme_flexcontrollers
        index = ob.vs.dme_flexcontrollers_index

        if self.direction == 'UP' and index > 0:
            controllers.move(index, index - 1)
            ob.vs.dme_flexcontrollers_index -= 1
        elif self.direction == 'DOWN' and index < len(controllers) - 1:
            controllers.move(index, index + 1)
            ob.vs.dme_flexcontrollers_index += 1

        return {'FINISHED'}


class SMD_OT_CombineStereoFlexControllers(Operator):
    bl_idname = "smd.combine_stereo_flexcontrollers"
    bl_label = "Combine L/R into Stereo"
    bl_description = get_id("op_combine_stereo_tip")
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and hasattr(context.object, "vs")
                    and len(context.object.vs.dme_flexcontrollers) > 0)

    def execute(self, context) -> set:
        ob = context.object
        controllers = ob.vs.dme_flexcontrollers

        # Map controller_name -> index for sibling lookup. Names are already lowercased
        # and sanitized by update_sanitize_name, so prefixes are reliably "left_"/"right_".
        by_name = {fc.controller_name: i for i, fc in enumerate(controllers) if fc.controller_name}

        to_remove = []   # indices of the redundant "left_" entries
        merged = 0
        for i, fc in enumerate(controllers):
            name = fc.controller_name
            if not name or not name.startswith("right_"):
                continue
            base = name[len("right_"):]
            if not base:
                continue
            left_idx = by_name.get("left_" + base)
            if left_idx is None or left_idx == i:
                continue
            # Promote the right_ entry to the base-named stereo controller; the left_
            # entry is dropped. Shape key references are left untouched (no rename).
            fc.controller_name = base
            fc.stereo = True
            to_remove.append(left_idx)
            merged += 1

        # Remove the redundant left_ entries from the end so earlier indices stay valid.
        for idx in sorted(set(to_remove), reverse=True):
            controllers.remove(idx)

        if controllers:
            ob.vs.dme_flexcontrollers_index = min(ob.vs.dme_flexcontrollers_index, len(controllers) - 1)

        if not merged:
            self.report({'WARNING'}, "No left_/right_ controller pairs found to combine")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Combined {merged} L/R pair(s) into stereo controllers")
        return {'FINISHED'}


class SMD_OT_AutoAssignFlexGroups(Operator):
    bl_idname = "smd.auto_assign_flexgroups"
    bl_label = "Auto Assign Flex Groups"
    bl_description = "Automatically categorize flex controllers based on keywords"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and
                hasattr(context.object, "vs") and
                len(context.object.vs.dme_flexcontrollers) > 0)

    def execute(self, context) -> set:
        ob = context.object
        controllers = ob.vs.dme_flexcontrollers

        mapping = [
            ('EYELID', ['lid', 'blink', 'wink']),
            ('EYES', ['eye']),
            ('BROW', ['brow']),
            ('MOUTH', ['mouth', 'phoneme', 'smile', 'frown', 'jaw', 'lip', 'tongue']),
            ('CHEEK', ['cheek', 'puff']),
        ]

        assigned_count = 0

        for item in controllers:
            search_name = ""
            if item.controller_name:
                search_name = item.controller_name.lower()
            elif hasattr(item, 'raw_delta_name') and item.raw_delta_name:
                search_name = item.raw_delta_name.lower()
            elif hasattr(item, 'shapekey') and item.shapekey:
                search_name = item.shapekey.lower()

            if not search_name:
                continue

            for group_id, keywords in mapping:
                if any(kw in search_name for kw in keywords):
                    item.flexgroup = group_id
                    assigned_count += 1
                    break

        self.report({'INFO'}, f"Categorized {assigned_count} controllers")
        return {'FINISHED'}


class SMD_OT_CopyFlexControllers(Operator):
    bl_idname = "smd.copy_flexcontrollers"
    bl_label = "Copy Flex Data to Selected"
    bl_description = "Copy flex controllers, rules, and delta overrides from the active object to other selected mesh objects"
    bl_options = {'REGISTER', 'UNDO'}

    copy_flexcontrollers: BoolProperty(name="Flex Controllers", default=True)
    copy_flex_rules: BoolProperty(name="Flex Rules", default=True)
    copy_delta_overrides: BoolProperty(name="Delta Overrides", default=True)

    @classmethod
    def poll(cls, context) -> bool:
        ob = context.active_object
        if not (ob and hasattr(ob, "vs") and len(context.selected_objects) > 1):
            return False
        vs = ob.vs
        return bool(vs.dme_flexcontrollers or vs.dme_flex_rules or vs.dme_delta_overrides)
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context) -> set:
        active_ob = context.active_object
        targets = [ob for ob in context.selected_objects if ob != active_ob]
        src_vs = active_ob.vs

        for target in targets:
            tvs = target.vs
            missing_keys = []

            if self.copy_flexcontrollers:
                tvs.dme_flexcontrollers.clear()
                for src in src_vs.dme_flexcontrollers:
                    dst = tvs.dme_flexcontrollers.add()
                    dst.controller_name = src.controller_name
                    dst.raw_delta_name  = src.raw_delta_name
                    dst.shapekey        = src.shapekey
                    dst.eyelid          = src.eyelid
                    dst.stereo          = src.stereo
                    dst.flexgroup       = src.flexgroup
                    dst.flexgroup_custom = src.flexgroup_custom
                    dst.flex_min        = src.flex_min
                    dst.flex_max        = src.flex_max

                    if src.shapekey:
                        if not (hasattr(target.data, "shape_keys") and target.data.shape_keys and src.shapekey in target.data.shape_keys.key_blocks):
                            missing_keys.append(src.shapekey)

            if self.copy_flex_rules:
                tvs.dme_flex_rules.clear()
                for src in src_vs.dme_flex_rules:
                    dst = tvs.dme_flex_rules.add()
                    dst.rule_type       = src.rule_type
                    dst.name            = src.name
                    dst.expression      = src.expression
                    dst.components      = src.components
                    dst.dominator_names = src.dominator_names
                    dst.suppressed_names = src.suppressed_names

            if self.copy_delta_overrides:
                tvs.dme_delta_overrides.clear()
                for src in src_vs.dme_delta_overrides:
                    dst = tvs.dme_delta_overrides.add()
                    dst.shapekey   = src.shapekey
                    dst.delta_name = src.delta_name
                    dst.split_lr   = src.split_lr

            if missing_keys:
                self.report({'WARNING'}, f"'{target.name}' is missing shape keys: {', '.join(missing_keys)}")

        self.report({'INFO'}, f"Copied data to {len(targets)} object(s)")
        return {'FINISHED'}


class SMD_OT_SortFlexControllers(Operator):
    bl_idname = "smd.sort_flexcontrollers"
    bl_label = "Sort Flex Controllers"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        ob = context.object
        controllers = ob.vs.dme_flexcontrollers

        def sort_key(fc):
            name = fc.controller_name.strip() if fc.controller_name and fc.controller_name.strip() else None
            delta = fc.raw_delta_name.strip() if fc.raw_delta_name and fc.raw_delta_name.strip() else None
            return (name or delta or fc.shapekey or "").lower()

        sorted_controllers = sorted(controllers, key=sort_key)

        temp = [(fc.controller_name, fc.shapekey, fc.raw_delta_name, fc.stereo, fc.eyelid) for fc in sorted_controllers]

        controllers.clear()
        for controller_name, shapekey, raw_delta_name, stereo, eyelid in temp:
            item = controllers.add()
            item.controller_name = controller_name
            item.shapekey = shapekey
            item.raw_delta_name = raw_delta_name
            item.stereo = stereo
            item.eyelid = eyelid

        ob.vs.dme_flexcontrollers_index = 0
        return {'FINISHED'}


class SMD_OT_PreviewFlexController(Operator):
    bl_idname= "dme.preview_flexcontroller"
    bl_label= "Preview Flex Controller"
    bl_options: set = {'INTERNAL', 'UNDO'}

    reset_others: BoolProperty(
        name="Reset Others",
        description="Reset all other shape keys to 0",
        default=True
    )

    @classmethod
    def poll(cls, context) -> bool:
        ob = context.object
        return bool(ob and ob.type == 'MESH' and ob.data.shape_keys and len(ob.vs.dme_flexcontrollers) > 0)

    def execute(self, context) -> set:
        ob = context.object
        shape_keys = ob.data.shape_keys
        current_index = ob.vs.dme_flexcontrollers_index

        if current_index >= len(ob.vs.dme_flexcontrollers):
            return {'CANCELLED'}

        current_flex = ob.vs.dme_flexcontrollers[current_index]
        target_shapekey_name = current_flex.shapekey

        for i, key_block in enumerate(shape_keys.key_blocks):
            if i == 0:
                continue
            if key_block.name == target_shapekey_name:
                ob.active_shape_key_index = i
                key_block.value = 1.0
            elif self.reset_others:
                key_block.value = 0.0

        return {'FINISHED'}


class SMD_OT_ClearFlexControllers(Operator):
    bl_idname= "dme.clear_flexcontrollers"
    bl_label= "Clear All Flex Controllers"
    bl_options: set = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(len(context.object.vs.dme_flexcontrollers) > 0)

    def invoke(self, context, event) -> set:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context) -> set:
        context.object.vs.dme_flexcontrollers.clear()
        context.object.vs.dme_flexcontrollers_index = 0
        return {'FINISHED'}


class SMD_OT_MigrateQCDeltasToOverrides(Operator):
    bl_idname = "smd.migrate_qc_deltas_to_overrides"
    bl_label = "Migrate QC Deltas to Overrides"
    bl_description = "Convert unnamed controller entries that have a delta name set into standalone delta name overrides and remove them from the controllers list"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        ob = context.object
        if not ob:
            return False
        return any(
            not (fc.controller_name and fc.controller_name.strip()) and fc.raw_delta_name and fc.raw_delta_name.strip()
            for fc in ob.vs.dme_flexcontrollers
        )

    def execute(self, context) -> set:
        ob = context.object
        converted = 0
        to_remove = []
        existing_overrides = {ov.shapekey for ov in ob.vs.dme_delta_overrides}

        for i, fc in enumerate(ob.vs.dme_flexcontrollers):
            if fc.controller_name and fc.controller_name.strip():
                continue
            if not (fc.raw_delta_name and fc.raw_delta_name.strip()):
                continue
            to_remove.append(i)
            if fc.shapekey and fc.shapekey not in existing_overrides:
                ov = ob.vs.dme_delta_overrides.add()
                ov.shapekey = fc.shapekey
                ov.delta_name = fc.raw_delta_name.strip()
                existing_overrides.add(fc.shapekey)
                converted += 1

        for i in reversed(to_remove):
            ob.vs.dme_flexcontrollers.remove(i)

        ob.vs.dme_flexcontrollers_index = min(
            max(0, ob.vs.dme_flexcontrollers_index),
            len(ob.vs.dme_flexcontrollers) - 1
        )
        self.report({'INFO'}, f"Migrated {converted} delta name(s) to overrides, removed {len(to_remove)} controller entries")
        return {'FINISHED'}


class SMD_OT_AddFlexRule(Operator):
    bl_idname = "smd.add_flex_rule"
    bl_label = "Add Flex Rule"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        ob = context.object
        new_item = ob.vs.dme_flex_rules.add()
        ob.vs.dme_flex_rules_index = len(ob.vs.dme_flex_rules) - 1
        new_item.rule_type = 'EXPRESSION'
        if ob.data and hasattr(ob.data, 'shape_keys') and ob.data.shape_keys and ob.active_shape_key_index > 0:
            new_item.name = ob.data.shape_keys.key_blocks[ob.active_shape_key_index].name
        return {'FINISHED'}


class SMD_OT_RemoveFlexRule(Operator):
    bl_idname = "smd.remove_flex_rule"
    bl_label = "Remove Flex Rule"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and len(context.object.vs.dme_flex_rules) > 0)

    def execute(self, context) -> set:
        ob = context.object
        ob.vs.dme_flex_rules.remove(ob.vs.dme_flex_rules_index)
        ob.vs.dme_flex_rules_index = min(
            max(0, ob.vs.dme_flex_rules_index - 1),
            len(ob.vs.dme_flex_rules) - 1
        )
        return {'FINISHED'}


class SMD_OT_ClearFlexRules(Operator):
    bl_idname = "smd.clear_flex_rules"
    bl_label = "Clear All Flex Rules"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and len(context.object.vs.dme_flex_rules) > 0)

    def invoke(self, context, event) -> set:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context) -> set:
        context.object.vs.dme_flex_rules.clear()
        context.object.vs.dme_flex_rules_index = 0
        return {'FINISHED'}


class SMD_OT_MoveFlexRule(Operator):
    bl_idname = "smd.move_flex_rule"
    bl_label = "Move Flex Rule"
    bl_options = {'INTERNAL', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context) -> set:
        ob = context.object
        rules = ob.vs.dme_flex_rules
        index = ob.vs.dme_flex_rules_index
        if self.direction == 'UP' and index > 0:
            rules.move(index, index - 1)
            ob.vs.dme_flex_rules_index -= 1
        elif self.direction == 'DOWN' and index < len(rules) - 1:
            rules.move(index, index + 1)
            ob.vs.dme_flex_rules_index += 1
        return {'FINISHED'}


class SMD_OT_FlexRuleRegexReplace(Operator):
    bl_idname = "smd.flex_rule_regex_replace"
    bl_label = "Regex Find/Replace in Flex Rules"
    bl_description = "Apply a regex find/replace across all flex rule fields on the active object"
    bl_options = {'INTERNAL', 'UNDO'}

    pattern     : StringProperty(name="Pattern",     description="Regex pattern to search for")
    replacement : StringProperty(name="Replacement", description="Replacement string (supports back-references like \\1)")
    field_name         : BoolProperty(name="Name",           description="Apply to Name / Variable Name / Controller fields", default=True)
    field_expression   : BoolProperty(name="Expression",     description="Apply to Expression fields", default=True)
    field_components   : BoolProperty(name="Components",     description="Apply to Corrective Components fields", default=False)
    field_dominator    : BoolProperty(name="Dominators",     description="Apply to Dominator Names fields", default=False)
    field_suppressed   : BoolProperty(name="Suppressed",     description="Apply to Suppressed Names fields", default=False)

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and getattr(context.object, 'vs', None) and len(context.object.vs.dme_flex_rules) > 0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'pattern')
        layout.prop(self, 'replacement')
        layout.separator(factor=0.5)
        layout.label(text="Apply to fields:")
        col = layout.column(align=True)
        col.prop(self, 'field_name')
        col.prop(self, 'field_expression')
        col.prop(self, 'field_components')
        col.prop(self, 'field_dominator')
        col.prop(self, 'field_suppressed')

    def execute(self, context) -> set:
        if not self.pattern:
            self.report({'WARNING'}, "Pattern is empty")
            return {'CANCELLED'}
        try:
            compiled = _re.compile(self.pattern)
        except _re.error as e:
            self.report({'ERROR'}, f"Invalid regex: {e}")
            return {'CANCELLED'}

        fields = []
        if self.field_name:       fields.append('name')
        if self.field_expression: fields.append('expression')
        if self.field_components: fields.append('components')
        if self.field_dominator:  fields.append('dominator_names')
        if self.field_suppressed: fields.append('suppressed_names')

        if not fields:
            self.report({'WARNING'}, "No fields selected")
            return {'CANCELLED'}

        total = 0
        for rule in context.object.vs.dme_flex_rules:
            for field in fields:
                old_val = getattr(rule, field, '')
                new_val, n = compiled.subn(self.replacement, old_val)
                if n:
                    setattr(rule, field, new_val)
                    total += n

        if total:
            self.report({'INFO'}, f"Made {total} substitution(s) across flex rules")
        else:
            self.report({'INFO'}, "No matches found")
        return {'FINISHED'}


class SMD_OT_AddDeltaOverride(Operator):
    bl_idname = "smd.add_delta_override"
    bl_label = "Add Delta Override"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        ob = context.object
        new_item = ob.vs.dme_delta_overrides.add()
        ob.vs.dme_delta_overrides_index = len(ob.vs.dme_delta_overrides) - 1
        if ob.data and hasattr(ob.data, 'shape_keys') and ob.data.shape_keys and ob.active_shape_key_index > 0:
            new_item.shapekey = ob.data.shape_keys.key_blocks[ob.active_shape_key_index].name
        return {'FINISHED'}


class SMD_OT_RemoveDeltaOverride(Operator):
    bl_idname = "smd.remove_delta_override"
    bl_label = "Remove Delta Override"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and len(context.object.vs.dme_delta_overrides) > 0)

    def execute(self, context) -> set:
        ob = context.object
        ob.vs.dme_delta_overrides.remove(ob.vs.dme_delta_overrides_index)
        ob.vs.dme_delta_overrides_index = min(
            max(0, ob.vs.dme_delta_overrides_index - 1),
            len(ob.vs.dme_delta_overrides) - 1
        )
        return {'FINISHED'}


class SMD_OT_ClearDeltaOverrides(Operator):
    bl_idname = "smd.clear_delta_overrides"
    bl_label = "Clear All Delta Overrides"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return bool(context.object and len(context.object.vs.dme_delta_overrides) > 0)

    def invoke(self, context, event) -> set:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context) -> set:
        context.object.vs.dme_delta_overrides.clear()
        context.object.vs.dme_delta_overrides_index = 0
        return {'FINISHED'}


class SMD_OT_AddVertexMapRemap(Operator):
    bl_idname = "smd.add_vertex_map_remap"
    bl_label = "Apply Remap Range"

    map_name: StringProperty()

    def execute(self, context) -> set:
        active_object = context.object
        if active_object and active_object.type == 'MESH':
            group = active_object.vs.vertex_map_remaps.add()
            group.group = self.map_name
            group.min = 0.0
            group.max = 1.0
        return {'FINISHED'}


class SMD_OT_AddVertexAnimation(Operator):
    bl_idname = "smd.vertexanim_add"
    bl_label = get_id("vca_add")
    bl_description = get_id("vca_add_tip")
    bl_options = {'INTERNAL', 'UNDO'}

    index: IntProperty()

    def execute(self,context) -> set:
        item = get_valid_vertexanimation_object(context.object)
        item.vs.vertex_animations.add()
        item.vs.active_vertex_animation = len(item.vs.vertex_animations) - 1
        return {'FINISHED'}


class SMD_OT_RemoveVertexAnimation(Operator):
    bl_idname = "smd.vertexanim_remove"
    bl_label = get_id("vca_remove")
    bl_description = get_id("vca_remove_tip")
    bl_options = {'INTERNAL', 'UNDO'}

    index : IntProperty(min=0)
    vertexindex : IntProperty(min=0)

    def execute(self, context) -> set:
        item = get_valid_vertexanimation_object(context.object)
        if len(item.vs.vertex_animations) > self.vertexindex:
            item.vs.vertex_animations.remove(self.vertexindex)
            item.vs.active_vertex_animation = max(
                0, min(self.vertexindex, len(item.vs.vertex_animations) - 1)
            )
        return {'FINISHED'}


class SMD_OT_PreviewVertexAnimation(Operator):
    bl_idname = "smd.vertexanim_preview"
    bl_label = get_id("vca_preview")
    bl_description = get_id("vca_preview_tip")
    bl_options = {'INTERNAL'}

    index: IntProperty(min=0)
    vertexindex: IntProperty(min=0)

    def execute(self, context) -> set:
        scene = context.scene

        item = get_valid_vertexanimation_object(context.object)
        if self.vertexindex >= len(item.vs.vertex_animations):
            self.report({'ERROR'}, "Invalid vertex animation index")
            return {'CANCELLED'}

        anim = item.vs.vertex_animations[self.vertexindex]

        scene.use_preview_range = True
        scene.frame_preview_start = anim.start
        scene.frame_preview_end = anim.end

        if not context.screen.is_animation_playing:
            scene.frame_set(anim.start)
        bpy.ops.screen.animation_play()

        return {'FINISHED'}


class SMD_OT_GenerateVertexAnimationQCSnippet(Operator):
    bl_idname = "smd.vertexanim_generate_qc"
    bl_label = get_id("vca_qcgen")
    bl_description = get_id("vca_qcgen_tip")
    bl_options = {'INTERNAL'}

    index: IntProperty(min=0)

    @classmethod
    def poll(cls, context) -> bool:
        return len(context.scene.vs.export_list) > 0

    def execute(self, context) -> set:
        scene = context.scene

        item = get_valid_vertexanimation_object(context.object)
        fps = scene.render.fps / scene.render.fps_base
        wm = context.window_manager

        wm.clipboard = '$model "merge_me" {0}{1}'.format(item.name, getFileExt())
        if scene.vs.export_format == 'SMD':
            wm.clipboard += ' {{\n{0}\n}}\n'.format(
                "\n".join([f"\tvcafile {vca.name}.vta" for vca in item.vs.vertex_animations])
            )
        else:
            wm.clipboard += '\n'

        wm.clipboard += "\n// vertex animation block begins\n$upaxis Y\n"
        wm.clipboard += "\n".join([
            f'''
$boneflexdriver "vcabone_{vca.name}" tx "{vca.name}" 0 1
$boneflexdriver "vcabone_{vca.name}" ty "multi_{vca.name}" 0 1
$sequence "{vca.name}" "vcaanim_{vca.name}{getFileExt()}" fps {fps}
'''.strip()
            for vca in item.vs.vertex_animations if vca.export_sequence
        ])
        wm.clipboard += "\n// vertex animation block ends\n"

        self.report({'INFO'}, "QC segment copied to clipboard.")
        return {'FINISHED'}


class SMD_OT_CopyBoneExportName(Operator):
    bl_idname = "smd.copy_bone_export_name"
    bl_label = 'Copy Name to Clipboard'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.mode == 'POSE' and context.object.type == 'ARMATURE' and (context.selected_pose_bones or context.selected_bones))

    def execute(self, context) -> set:
        bones = context.object.data.bones
        selected = context.selected_bones or context.selected_pose_bones or []
        names = [
            get_bone_exportname(bones[b.name], for_write=True)
            for b in selected
            if b.name in bones
        ]
        bpy.context.window_manager.clipboard = " ".join(names)
        self.report({'INFO'}, "Name copied to clipboard.")
        return {'FINISHED'}


class SMD_OT_AssignBoneRotExportOffset(Operator):
    bl_idname = 'smd.assign_bone_rot_export_offset'
    bl_label = 'Assign Bone Target Forward'
    bl_options: set = {'REGISTER', 'UNDO'}
    bl_description = "Target Bone Forward: Sets the bone's forward direction for export. Blender bones use Y-forward by default in edit mode (check with 'normal' gizmo). This property specifies which axis will be forward in the target engine/application. Example: Setting 'X-forward' rotates the bone +90° around Z on export, converting Y-forward → X-forward. Rotation order on export: Z→Y→X (translation: X→Y→Z)"

    export_rot_target : EnumProperty(
        name='Rotation Target',
        description="Target Bone Forward (Assuming the bone is currently on Blender's Y-forward format)",
        items=[
            ('X', '+X', ''),
            ('Y', '+Y', ''),
            ('Z', '+Z', ''),
            ('X_INVERT', '-X', ''),
            ('Y_INVERT', '-Y', ''),
            ('Z_INVERT', '-Z', ''),
        ], default='X'
    )

    only_active_bone : BoolProperty(
        name='Only Active Bone',
        default=False
    )

    @classmethod
    def poll(cls, context) -> bool:
        selected_arms = [ob for ob in context.selected_objects if is_armature(ob)]
        return bool(selected_arms and context.mode not in {'EDIT', 'EDIT_ARMATURE', 'OBJECT'} and context.active_bone and context.active_bone.select == True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Y to...')
        row = layout.row(align=True)
        row.prop(self,'export_rot_target',expand=True)

    def execute(self, context) -> set:
        selected_arms = [ob for ob in context.selected_objects if is_armature(ob)]

        if not selected_arms:
            return {'CANCELLED'}

        any_bones_found = False

        for arm in selected_arms:
            if self.only_active_bone:
                selected_bones = [arm.data.bones.active] if arm.data.bones.active else []
            else:
                selected_bones = [b for b in arm.data.bones if not b.hide_select and b.select]

            if not selected_bones:
                continue

            any_bones_found = True

            for bone in selected_bones:
                if not bone.vs:
                    continue

                bone.vs.export_rotation_offset_x = 0
                bone.vs.export_rotation_offset_y = 0
                bone.vs.export_rotation_offset_z = 0

                match self.export_rot_target:
                    case 'X':
                        bone.vs.export_rotation_offset_z = math.radians(90)
                    case 'Z':
                        bone.vs.export_rotation_offset_x = math.radians(-90)
                    case 'X_INVERT':
                        bone.vs.export_rotation_offset_z = math.radians(-90)
                    case 'Y_INVERT':
                        bone.vs.export_rotation_offset_y = math.radians(180)
                    case 'Z_INVERT':
                        bone.vs.export_rotation_offset_x = math.radians(-90)

        if not any_bones_found:
            self.report({'ERROR'}, 'No active or selected bones')
            return {'CANCELLED'}

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        return {'FINISHED'}


class SMD_OT_HitboxAdd(Operator):
    bl_idname  = "smd.hitbox_add"
    bl_label   = get_id('op_hitbox_add')
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        entry = avs.hitboxes.add()
        if context.active_pose_bone:
            entry.bone_name = context.active_pose_bone.name
        avs.hitboxes_index = len(avs.hitboxes) - 1
        return {'FINISHED'}


class SMD_OT_HitboxRemove(Operator):
    bl_idname  = "smd.hitbox_remove"
    bl_label   = get_id('op_hitbox_remove')
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.hitboxes_index
        if 0 <= idx < len(avs.hitboxes):
            avs.hitboxes.remove(idx)
            avs.hitboxes_index = max(0, min(idx, len(avs.hitboxes) - 1))
        return {'FINISHED'}


class SMD_OT_HitboxFromBone(Operator):
    bl_idname   = "smd.hitbox_from_bone"
    bl_label    = get_id('op_hitbox_from_bone')
    bl_options  = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.selected_pose_bones

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        for pb in context.selected_pose_bones:
            entry = avs.hitboxes.add()
            entry.bone_name = pb.name
        avs.hitboxes_index = len(avs.hitboxes) - 1
        return {'FINISHED'}


_hitbox_clipboard: list[dict] | None = None


def _hitbox_entry_to_dict(entry) -> dict:
    return {
        'bone_name': entry.bone_name,
        'group':     entry.group,
        'vec_min':   tuple(entry.vec_min),
        'vec_max':   tuple(entry.vec_max),
        'rotation':  tuple(entry.rotation),
        'scale':     entry.scale,
    }


def _hitbox_entry_from_dict(entry, d: dict):
    entry.bone_name   = d['bone_name']
    entry.group       = d['group']
    entry.vec_min[:]  = d['vec_min']
    entry.vec_max[:]  = d['vec_max']
    entry.rotation[:] = d['rotation']
    entry.scale       = d['scale']


class SMD_OT_HitboxDuplicate(Operator):
    bl_idname  = "smd.hitbox_duplicate"
    bl_label   = get_id('op_hitbox_duplicate')
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.hitboxes_index < len(avs.hitboxes)

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.hitboxes_index
        if not (0 <= idx < len(avs.hitboxes)):
            return {'CANCELLED'}
        _hitbox_entry_from_dict(avs.hitboxes.add(), _hitbox_entry_to_dict(avs.hitboxes[idx]))
        avs.hitboxes_index = len(avs.hitboxes) - 1
        return {'FINISHED'}


class SMD_OT_HitboxCopyEntry(Operator):
    bl_idname      = "smd.hitbox_copy_entry"
    bl_label       = get_id('op_hitbox_copy_entry')
    bl_description = get_id('op_hitbox_copy_entry_tip')
    bl_options     = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.hitboxes_index < len(avs.hitboxes)

    def execute(self, context):
        global _hitbox_clipboard
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        _hitbox_clipboard = [_hitbox_entry_to_dict(avs.hitboxes[avs.hitboxes_index])]
        self.report({'INFO'}, "Copied 1 hitbox entry")
        return {'FINISHED'}


class SMD_OT_HitboxCopyAll(Operator):
    bl_idname      = "smd.hitbox_copy_all"
    bl_label       = get_id('op_hitbox_copy_all')
    bl_description = get_id('op_hitbox_copy_all_tip')
    bl_options     = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        return len(arm_ob.data.vs.hitboxes) > 0

    def execute(self, context):
        global _hitbox_clipboard
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        _hitbox_clipboard = [_hitbox_entry_to_dict(e) for e in avs.hitboxes]
        n = len(_hitbox_clipboard)
        self.report({'INFO'}, f"Copied {n} hitbox entr{'y' if n == 1 else 'ies'}")
        return {'FINISHED'}


class SMD_OT_HitboxPasteEntries(Operator):
    bl_idname      = "smd.hitbox_paste_entries"
    bl_label       = get_id('op_hitbox_paste_entries')
    bl_description = get_id('op_hitbox_paste_entries_tip')
    bl_options     = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(_hitbox_clipboard) and bool(get_armature(context.object))

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        for d in _hitbox_clipboard:
            _hitbox_entry_from_dict(avs.hitboxes.add(), d)
        avs.hitboxes_index = len(avs.hitboxes) - 1
        n = len(_hitbox_clipboard)
        self.report({'INFO'}, f"Pasted {n} hitbox entr{'y' if n == 1 else 'ies'}")
        return {'FINISHED'}


class SMD_OT_HitboxPasteValues(Operator):
    bl_idname      = "smd.hitbox_paste_values"
    bl_label       = get_id('op_hitbox_paste_values')
    bl_description = get_id('op_hitbox_paste_values_tip')
    bl_options     = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if not _hitbox_clipboard:
            return False
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.hitboxes_index < len(avs.hitboxes)

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        entry  = avs.hitboxes[avs.hitboxes_index]
        d = _hitbox_clipboard[0]
        entry.group       = d['group']
        entry.vec_min[:]  = d['vec_min']
        entry.vec_max[:]  = d['vec_max']
        entry.rotation[:] = d['rotation']
        entry.scale       = d['scale']
        return {'FINISHED'}


class SMD_OT_HitboxCopyToArmature(Operator):
    bl_idname      = "smd.hitbox_copy_to_armature"
    bl_label       = get_id('op_hitbox_copy_to_armature')
    bl_description = get_id('op_hitbox_copy_to_armature_tip')
    bl_options     = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        active_arm = get_armature(context.object)
        if not active_arm or len(active_arm.data.vs.hitboxes) == 0:
            return False
        return any(is_armature(ob) and ob != active_arm for ob in context.selected_objects)

    def execute(self, context):
        active_arm = get_armature(context.object)
        if not active_arm:
            return {'CANCELLED'}
        src_dicts  = [_hitbox_entry_to_dict(e) for e in active_arm.data.vs.hitboxes]
        if not src_dicts:
            return {'CANCELLED'}
        targets = [ob for ob in context.selected_objects if is_armature(ob) and ob != active_arm]
        if not targets:
            return {'CANCELLED'}
        for target in targets:
            target.data.vs.hitboxes.clear()
            for d in src_dicts:
                _hitbox_entry_from_dict(target.data.vs.hitboxes.add(), d)
        n = len(src_dicts)
        self.report({'INFO'}, f"Copied {n} hitbox entr{'y' if n == 1 else 'ies'} to {len(targets)} armature(s)")
        return {'FINISHED'}


class SMD_OT_HitboxMirror(Operator):
    bl_idname      = "smd.hitbox_mirror"
    bl_label       = "Mirror Hitbox"
    bl_description = "Mirror the active hitbox along an axis"
    bl_options     = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
        default='X',
    )

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.hitboxes_index < len(avs.hitboxes)

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        entry  = avs.hitboxes[avs.hitboxes_index]
        mn = list(entry.vec_min)
        mx = list(entry.vec_max)
        i  = 'XYZ'.index(self.axis)
        if entry.scale < 0:
            # Box: swap negated extents on the chosen axis to preserve min/max convention
            mn[i], mx[i] = -mx[i], -mn[i]
        else:
            # Capsule: negate each endpoint independently on the chosen axis
            mn[i] = -mn[i]
            mx[i] = -mx[i]
        entry.vec_min = mn
        entry.vec_max = mx
        return {'FINISHED'}


# Keep old idname as a thin shim so any existing keymap/operator calls still work
SMD_OT_HitboxMirrorX = SMD_OT_HitboxMirror


class SMD_OT_ProcBoneAdd(Operator):
    bl_idname  = "smd.proc_bone_add"
    bl_label   = "Add Procedural Bone"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        avs.proc_bones.add()
        avs.proc_bones_index = len(avs.proc_bones) - 1
        return {'FINISHED'}


class SMD_OT_ProcBoneAddFromSelected(Operator):
    bl_idname       = "smd.proc_bone_add_from_selected"
    bl_label        = get_id('op_proc_bone_add_from_selected')
    bl_description  = get_id('op_proc_bone_add_from_selected_tip')
    bl_options      = {'UNDO'}

    driver_bone      : StringProperty(name=get_id('prop_proc_bone_driver'),
                                      description=get_id('prop_proc_bone_driver_tip'))
    action_name      : StringProperty(name=get_id('prop_proc_bone_action'),
                                      description=get_id('prop_proc_bone_action_tip'))
    action_slot_name : StringProperty(name=get_id('prop_proc_bone_slot'),
                                      description=get_id('prop_proc_bone_slot_tip'))

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE'
                and bool(get_armature(context.object))
                and bool(context.selected_pose_bones))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        arm_ob  = get_armature(context.object)
        arm_data = arm_ob.data if arm_ob else None
        n = len(context.selected_pose_bones)

        layout.label(
            text=f"{n} bone{'s' if n != 1 else ''} will be added as helper{'s' if n != 1 else ''}",
            icon='BONE_DATA')

        col = layout.column(align=True)
        col.label(text=get_id('op_proc_bone_add_optional_hint'), icon='INFO')

        if arm_data:
            col.prop_search(self, 'driver_bone', arm_data, 'bones')
        else:
            col.prop(self, 'driver_bone')

        col.prop_search(self, 'action_name', bpy.data, 'actions')

        action = bpy.data.actions.get(self.action_name)
        if action and not getattr(action, 'is_action_legacy', True):
            col.prop_search(self, 'action_slot_name', action, 'slots',
                            text=get_id('prop_proc_bone_slot'))

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs    = arm_ob.data.vs
        bones  = context.selected_pose_bones
        if not bones:
            return {'CANCELLED'}

        action = bpy.data.actions.get(self.action_name) if self.action_name else None

        for pb in bones:
            entry                  = avs.proc_bones.add()
            entry.helper_bone      = pb.name
            entry.driver_bone      = self.driver_bone
            if action:
                entry.action       = action
            entry.action_slot_name = self.action_slot_name

        avs.proc_bones_index = len(avs.proc_bones) - 1
        self.report({'INFO'},
                    f"Added {len(bones)} proc bone entr{'y' if len(bones) == 1 else 'ies'}")
        return {'FINISHED'}


_lookat_axis_items = [
    ('+X', "+X", "Positive X",  1),
    ('+Y', "+Y", "Positive Y",  2),
    ('+Z', "+Z", "Positive Z",  4),
    ('-X', "-X", "Negative X",  8),
    ('-Y', "-Y", "Negative Y", 16),
    ('-Z', "-Z", "Negative Z", 32),
]


class SMD_OT_ProcBoneAddLookAt(Operator):
    bl_idname       = "smd.proc_bone_add_lookat"
    bl_label        = get_id('op_proc_bone_add_lookat')
    bl_description  = get_id('op_proc_bone_add_lookat_tip')
    bl_options      = {'UNDO'}

    target_type : EnumProperty(
        name=get_id('prop_proc_bone_lookat_target_type'),
        description=get_id('prop_proc_bone_lookat_target_type_tip'),
        items=[
            ('BONE',       "Bone",       "Aim at another bone",          'BONE_DATA',    0),
            ('ATTACHMENT', "Attachment", "Aim at an attachment (Empty)", 'EMPTY_ARROWS', 1),
        ],
        default='BONE',
    )
    target_bone       : StringProperty(name=get_id('prop_proc_bone_lookat_target'))
    target_attachment : StringProperty(name=get_id('prop_proc_bone_lookat_target_attachment'),
                                       description=get_id('prop_proc_bone_lookat_target_attachment_tip'))
    aim_axis : EnumProperty(
        name=get_id('prop_proc_bone_lookat_aim_axis'),
        description=get_id('prop_proc_bone_lookat_aim_axis_tip'),
        items=_lookat_axis_items, default={'+X'}, options={'ENUM_FLAG'},
    )
    up_axis : EnumProperty(
        name=get_id('prop_proc_bone_lookat_up_axis'),
        description=get_id('prop_proc_bone_lookat_up_axis_tip'),
        items=_lookat_axis_items, default={'+Z'}, options={'ENUM_FLAG'},
    )

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE'
                and bool(get_armature(context.object))
                and bool(context.selected_pose_bones))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def _attachment_object(self, arm_ob) -> bpy.types.Object | None:
        ob = bpy.data.objects.get(self.target_attachment)
        if (ob and ob.type == 'EMPTY' and ob.parent == arm_ob
                and ob.parent_type == 'BONE' and ob.parent_bone.strip()
                and ob.parent_bone in arm_ob.data.bones):
            return ob
        return None

    def draw(self, context):
        layout = self.layout
        arm_ob   = get_armature(context.object)
        arm_data = arm_ob.data if arm_ob else None
        n = len(context.selected_pose_bones)

        layout.label(
            text=f"{n} bone{'s' if n != 1 else ''} will be added as LookAt helper{'s' if n != 1 else ''}",
            icon='CON_TRACKTO')

        col = layout.column(align=True)
        col.prop(self, 'target_type', expand=True)

        if self.target_type == 'BONE':
            if arm_data:
                col.prop_search(self, 'target_bone', arm_data, 'bones',
                                text=get_id('prop_proc_bone_lookat_target'))
            else:
                col.prop(self, 'target_bone')
        else:
            col.prop_search(self, 'target_attachment', bpy.data, 'objects',
                            text=get_id('prop_proc_bone_lookat_target_attachment'))
            if self.target_attachment and arm_ob and not self._attachment_object(arm_ob):
                col.label(text=get_id('warn_lookat_attachment_invalid'), icon='ERROR')

        col.separator()

        split = col.split(factor=0.22)
        split.label(text=get_id('prop_proc_bone_lookat_aim_axis'))
        split.row().prop(self, 'aim_axis', expand=True)

        split = col.split(factor=0.22)
        split.label(text=get_id('prop_proc_bone_lookat_up_axis'))
        split.row().prop(self, 'up_axis', expand=True)

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        bones = context.selected_pose_bones
        if not bones:
            return {'CANCELLED'}
        avs = arm_ob.data.vs

        offset = None
        if self.target_type == 'BONE':
            target_bone_name = self.target_bone
            if not target_bone_name or target_bone_name not in arm_ob.data.bones:
                self.report({'ERROR'}, "Pick a valid target bone")
                return {'CANCELLED'}
        else:
            att_ob = self._attachment_object(arm_ob)
            if not att_ob:
                self.report({'ERROR'}, get_id('warn_lookat_attachment_invalid'))
                return {'CANCELLED'}
            target_bone_name = att_ob.parent_bone
            driver_pb  = arm_ob.pose.bones[target_bone_name]
            driver_mat = (arm_ob.matrix_world @ driver_pb.matrix
                         @ _procbones_sim._get_export_offset_mat(driver_pb))
            from mathutils import Vector
            world_t = att_ob.matrix_world.translation
            local   = (driver_mat.inverted_safe()
                      @ Vector((world_t.x, world_t.y, world_t.z, 1.0))).to_3d()
            offset  = (local.x, local.y, local.z)

        added = 0
        for pb in bones:
            if pb.name == target_bone_name:
                continue
            entry             = avs.proc_bones.add()
            entry.proc_type   = 'LOOKAT'
            entry.helper_bone = pb.name
            entry.driver_bone = target_bone_name
            entry.lookat_aim_axis = self.aim_axis
            entry.lookat_up_axis  = self.up_axis
            if offset is not None:
                entry.lookat_offset = offset
            added += 1

        if added == 0:
            self.report({'WARNING'}, "No entries added, target bone can't look at itself")
            return {'CANCELLED'}

        avs.proc_bones_index = len(avs.proc_bones) - 1
        self.report({'INFO'},
                    f"Added {added} LookAt proc bone entr{'y' if added == 1 else 'ies'}")
        return {'FINISHED'}


class SMD_OT_ProcBoneDuplicate(Operator):
    bl_idname  = "smd.proc_bone_duplicate"
    bl_label   = "Duplicate Procedural Bone"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if not (0 <= idx < len(avs.proc_bones)):
            return {'CANCELLED'}
        src = avs.proc_bones[idx]
        dst = avs.proc_bones.add()
        dst.proc_type          = src.proc_type
        dst.helper_bone        = src.helper_bone
        dst.driver_bone        = src.driver_bone
        dst.reference_armature = src.reference_armature
        dst.action             = src.action
        dst.action_slot_name   = src.action_slot_name
        dst.lookat_aim_axis  = src.lookat_aim_axis
        dst.lookat_up_axis   = src.lookat_up_axis
        dst.lookat_offset[:] = src.lookat_offset[:]
        avs.proc_bones_index = len(avs.proc_bones) - 1
        return {'FINISHED'}


class SMD_OT_ProcBoneRemove(Operator):
    bl_idname  = "smd.proc_bone_remove"
    bl_label   = "Remove Procedural Bone"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context) -> set:
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if 0 <= idx < len(avs.proc_bones):
            _procbones_sim.invalidate_proc_cache(arm_ob.name)
            avs.proc_bones.remove(idx)
            avs.proc_bones_index = max(0, min(idx, len(avs.proc_bones) - 1))
        return {'FINISHED'}


class SMD_OT_ProcBoneSetTolerance(Operator):
    bl_idname  = "smd.proc_bone_set_tolerance"
    bl_label   = "Set Proc Bone Tolerance"
    bl_options = {'REGISTER', 'UNDO'}

    value: FloatProperty(
        name=get_id('prop_pose_bone_proc_tolerance'),
        description=get_id('prop_pose_bone_proc_tolerance_tip'),
        default=math.pi / 2, min=0.01, max=math.pi, subtype='ANGLE', precision=2,
    )

    def invoke(self, context, event):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if not (0 <= idx < len(avs.proc_bones)):
            return {'CANCELLED'}
        entry = avs.proc_bones[idx]
        self.value = _get_entry_proc_tol(entry, context.scene.frame_current, arm_ob)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, 'value')

    def execute(self, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return {'CANCELLED'}
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if not (0 <= idx < len(avs.proc_bones)):
            return {'CANCELLED'}
        entry = avs.proc_bones[idx]
        if not entry.action or not entry.driver_bone:
            return {'CANCELLED'}

        # Paths on bpy.types.Bone are relative to the Armature data-block.
        dp    = f'bones["{entry.driver_bone}"].vs.proc_tolerance'
        frame = context.scene.frame_current
        fc    = _get_or_create_proc_tol_fcurve(entry, dp)
        if fc is None:
            return {'CANCELLED'}

        fc.keyframe_points.insert(frame, self.value, options={'NEEDED', 'FAST'})
        fc.update()
        _procbones_sim.invalidate_proc_cache(arm_ob.name)
        return {'FINISHED'}


class SMD_OT_ProcBoneNavigateFrame(Operator):
    bl_idname  = "smd.proc_bone_navigate_frame"
    bl_label   = "Navigate Proc Bone Frame"
    bl_options = {'REGISTER', 'INTERNAL'}

    direction: EnumProperty(items=[
        ('FIRST', "First",    "Jump to the first frame of the trigger range"),
        ('PREV',  "Previous", "Go one frame back"),
        ('NEXT',  "Next",     "Go one frame forward"),
        ('LAST',  "Last",     "Jump to the last frame of the trigger range"),
    ], default='FIRST')

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        return 0 <= idx < len(avs.proc_bones)

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        entry  = avs.proc_bones[avs.proc_bones_index]
        fs, fe, valid = _procbones_sim._get_proc_trigger_frame_range(entry, arm_ob)
        if not valid:
            return {'CANCELLED'}
        pf = max(fs, min(fe, entry.trigger_preview_frame))
        if   self.direction == 'FIRST': pf = fs
        elif self.direction == 'PREV':  pf = max(fs, pf - 1)
        elif self.direction == 'NEXT':  pf = min(fe, pf + 1)
        elif self.direction == 'LAST':  pf = fe
        entry.trigger_preview_frame = pf
        return {'FINISHED'}


_tolerance_clipboard: list[tuple[float, float]] | None = None


class SMD_OT_ProcBoneCopyTolerance(Operator):
    bl_idname   = "smd.proc_bone_copy_tolerance"
    bl_label    = get_id('op_proc_bone_copy_tolerance')
    bl_description = get_id('op_proc_bone_copy_tolerance_tip')
    bl_options  = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if not (0 <= idx < len(avs.proc_bones)):
            return False
        e = avs.proc_bones[idx]
        return (getattr(e, 'proc_type', 'TRIGGER') == 'TRIGGER'
                and bool(e.action) and bool(e.driver_bone))

    def execute(self, context):
        global _tolerance_clipboard
        arm_ob = get_armature(context.object)
        avs = arm_ob.data.vs
        entry = avs.proc_bones[avs.proc_bones_index]
        fcurves = _procbones_sim._get_action_fcurves(entry.action, entry.action_slot_name)
        dp = f'bones["{entry.driver_bone}"].vs.proc_tolerance'
        fc = next((f for f in fcurves if f.data_path == dp and f.array_index == 0), None)
        if fc is None:
            self.report({'WARNING'}, "No tolerance keyframes to copy")
            return {'CANCELLED'}
        _tolerance_clipboard = [(kp.co[0], kp.co[1]) for kp in fc.keyframe_points]
        self.report({'INFO'}, f"Copied {len(_tolerance_clipboard)} tolerance keyframe(s)")
        return {'FINISHED'}


class SMD_OT_ProcBonePasteTolerance(Operator):
    bl_idname   = "smd.proc_bone_paste_tolerance"
    bl_label    = get_id('op_proc_bone_paste_tolerance')
    bl_description = get_id('op_proc_bone_paste_tolerance_tip')
    bl_options  = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _tolerance_clipboard:
            return False
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        idx = avs.proc_bones_index
        if not (0 <= idx < len(avs.proc_bones)):
            return False
        e = avs.proc_bones[idx]
        return (getattr(e, 'proc_type', 'TRIGGER') == 'TRIGGER'
                and bool(e.action) and bool(e.driver_bone))

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs = arm_ob.data.vs
        entry = avs.proc_bones[avs.proc_bones_index]
        dp = f'bones["{entry.driver_bone}"].vs.proc_tolerance'
        fc = _get_or_create_proc_tol_fcurve(entry, dp)
        if fc is None:
            self.report({'ERROR'}, "Could not access tolerance fcurve")
            return {'CANCELLED'}
        for i in range(len(fc.keyframe_points) - 1, -1, -1):
            fc.keyframe_points.remove(fc.keyframe_points[i], fast=True)
        for frame, value in _tolerance_clipboard:
            fc.keyframe_points.insert(frame, value, options={'NEEDED', 'FAST'})
        fc.update()
        _procbones_sim.invalidate_proc_cache(arm_ob.name)
        self.report({'INFO'}, f"Pasted {len(_tolerance_clipboard)} tolerance keyframe(s)")
        return {'FINISHED'}


_proc_bone_clipboard: list[dict] | None = None


def _proc_entry_to_dict(entry) -> dict:
    return {
        'proc_type':          entry.proc_type,
        'helper_bone':        entry.helper_bone,
        'driver_bone':        entry.driver_bone,
        'reference_armature': entry.reference_armature.name if entry.reference_armature else '',
        'action':             entry.action.name if entry.action else '',
        'action_slot_name':   entry.action_slot_name,
        'lookat_aim_axis':  set(entry.lookat_aim_axis),
        'lookat_up_axis':   set(entry.lookat_up_axis),
        'lookat_offset':    tuple(entry.lookat_offset),
    }


def _proc_entry_from_dict(entry, d: dict):
    entry.proc_type        = d['proc_type']
    entry.helper_bone      = d['helper_bone']
    entry.driver_bone      = d['driver_bone']
    ref_name = d.get('reference_armature', '')
    if ref_name and ref_name in bpy.data.objects:
        entry.reference_armature = bpy.data.objects[ref_name]
    action_name = d['action']
    if action_name and action_name in bpy.data.actions:
        entry.action = bpy.data.actions[action_name]
    entry.action_slot_name = d['action_slot_name']
    entry.lookat_aim_axis  = d['lookat_aim_axis']
    entry.lookat_up_axis   = d['lookat_up_axis']
    entry.lookat_offset[:] = d['lookat_offset']


class SMD_OT_ProcBoneCopyActive(Operator):
    bl_idname      = "smd.proc_bone_copy_active"
    bl_label       = get_id('op_proc_bone_copy_active')
    bl_description = get_id('op_proc_bone_copy_active_tip')
    bl_options     = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.proc_bones_index < len(avs.proc_bones)

    def execute(self, context):
        global _proc_bone_clipboard
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        entry  = avs.proc_bones[avs.proc_bones_index]
        _proc_bone_clipboard = [_proc_entry_to_dict(entry)]
        self.report({'INFO'}, "Copied 1 proc bone entry")
        return {'FINISHED'}
    

class SMD_OT_ProcBoneCopyByDriverBone(Operator):
    bl_idname      = "smd.proc_bone_copy_by_driver_bone"
    bl_label       = get_id('op_proc_bone_copy_by_driver_bone')
    bl_description = get_id('op_proc_bone_copy_by_driver_bone_tip')
    bl_options     = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        avs = arm_ob.data.vs
        return 0 <= avs.proc_bones_index < len(avs.proc_bones)

    def execute(self, context):
        global _proc_bone_clipboard
        arm_ob      = get_armature(context.object)
        avs         = arm_ob.data.vs
        driver_bone = avs.proc_bones[avs.proc_bones_index].driver_bone
        _proc_bone_clipboard = [
            _proc_entry_to_dict(e)
            for e in avs.proc_bones
            if e.driver_bone == driver_bone
        ]
        n = len(_proc_bone_clipboard)
        self.report({'INFO'}, f"Copied {n} proc bone entr{'y' if n == 1 else 'ies'} for driver bone '{driver_bone}'")
        return {'FINISHED'}


class SMD_OT_ProcBoneCopyAll(Operator):
    bl_idname      = "smd.proc_bone_copy_all"
    bl_label       = get_id('op_proc_bone_copy_all')
    bl_description = get_id('op_proc_bone_copy_all_tip')
    bl_options     = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        arm_ob = get_armature(context.object)
        if not arm_ob:
            return False
        return len(arm_ob.data.vs.proc_bones) > 0

    def execute(self, context):
        global _proc_bone_clipboard
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        _proc_bone_clipboard = [_proc_entry_to_dict(e) for e in avs.proc_bones]
        n = len(_proc_bone_clipboard)
        self.report({'INFO'}, f"Copied {n} proc bone entr{'y' if n == 1 else 'ies'}")
        return {'FINISHED'}


class SMD_OT_ProcBonePasteEntries(Operator):
    bl_idname      = "smd.proc_bone_paste_entries"
    bl_label       = get_id('op_proc_bone_paste_entries')
    bl_description = get_id('op_proc_bone_paste_entries_tip')
    bl_options     = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(_proc_bone_clipboard) and bool(get_armature(context.object))

    def execute(self, context):
        arm_ob = get_armature(context.object)
        avs    = arm_ob.data.vs
        for d in _proc_bone_clipboard:
            _proc_entry_from_dict(avs.proc_bones.add(), d)
        avs.proc_bones_index = len(avs.proc_bones) - 1
        n = len(_proc_bone_clipboard)
        self.report({'INFO'}, f"Pasted {n} proc bone entr{'y' if n == 1 else 'ies'}")
        return {'FINISHED'}


class SMD_OT_CopySourceBoneProps(Operator):
    bl_idname = "smd.copy_bone_props"
    bl_label = "Copy Source Bone Properties"
    bl_options = {"REGISTER", "UNDO"}

    copy_name: BoolProperty(name="Export Name", default=False)
    copy_rotation: BoolProperty(name="Export Rotation Offset", default=True)
    copy_location: BoolProperty(name="Export Location Offset", default=True)
    copy_jigglebone: BoolProperty(name="Jigglebone", default=False)
    to_invoke : BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'POSE'
            and context.active_pose_bone is not None
            and len(context.selected_pose_bones) > 1
        )

    def invoke(self, context, event):
        if self.to_invoke:
            self.copy_jigglebone = context.active_pose_bone.bone.vs.bone_is_jigglebone
            return context.window_manager.invoke_props_dialog(self)
        else:
            return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Properties to copy:")
        layout.prop(self, "copy_name")
        layout.prop(self, "copy_rotation")
        layout.prop(self, "copy_location")
        row = layout.row()
        row.prop(self, "copy_jigglebone")
        row.enabled = context.active_pose_bone.bone.vs.bone_is_jigglebone

    def _copy_rotation(self, src, pb, arm_ob):
        """Copy rotation-offset settings to target bone pb.

        rotation_copy_target and the offset values are rest-pose dependent, so when the
        source follows a copy target we recompute the offset for pb's own rest matrix
        instead of copying the source's numbers verbatim. Uses dict-style assignment to
        bypass the update callbacks (which would recompute against the active bone)."""
        from ..props.armature import _compute_rotation_sync
        bvs = pb.bone.vs
        bvs.ignore_rotation_offset = src.ignore_rotation_offset
        tgt_name = src.rotation_copy_target
        target_pb = arm_ob.pose.bones.get(tgt_name) if (tgt_name and arm_ob) else None
        if target_pb is not None and target_pb != pb:
            bvs['rotation_copy_target'] = tgt_name
            euler = _compute_rotation_sync(pb, target_pb)
            bvs['export_rotation_offset_x'] = euler.x
            bvs['export_rotation_offset_y'] = euler.y
            bvs['export_rotation_offset_z'] = euler.z
        else:
            bvs['rotation_copy_target'] = ""
            bvs['export_rotation_offset_x'] = src.export_rotation_offset_x
            bvs['export_rotation_offset_y'] = src.export_rotation_offset_y
            bvs['export_rotation_offset_z'] = src.export_rotation_offset_z

    def _copy_location(self, src, pb):
        """Copy location-offset settings to target bone pb.

        When the source uses armature space, the same armature-space offset is applied to
        each target and its local-space equivalent is recomputed from the target's own rest
        matrix. Local and armature-space values are kept in sync. Dict-style assignment
        bypasses the _sync_* update callbacks so we control the conversion explicitly."""
        from mathutils import Vector
        bvs = pb.bone.vs
        rot = pb.bone.matrix_local.to_3x3()
        bvs.ignore_location_offset = src.ignore_location_offset
        bvs['location_offset_in_armature_space'] = src.location_offset_in_armature_space
        if src.location_offset_in_armature_space:
            arm_vec = Vector((src.export_location_offset_arm_x,
                              src.export_location_offset_arm_y,
                              src.export_location_offset_arm_z))
            local_vec = rot.inverted() @ arm_vec
        else:
            local_vec = Vector((src.export_location_offset_x,
                                src.export_location_offset_y,
                                src.export_location_offset_z))
            arm_vec = rot @ local_vec
        bvs['export_location_offset_x'] = local_vec.x
        bvs['export_location_offset_y'] = local_vec.y
        bvs['export_location_offset_z'] = local_vec.z
        bvs['export_location_offset_arm_x'] = arm_vec.x
        bvs['export_location_offset_arm_y'] = arm_vec.y
        bvs['export_location_offset_arm_z'] = arm_vec.z

    def execute(self, context) -> set:
        src = context.active_pose_bone.bone.vs
        arm_ob = context.active_object

        props = []
        if self.copy_name:
            props.append('export_name')
        if self.copy_jigglebone:
            if not src.bone_is_jigglebone:
                self.report({'WARNING'}, "Active bone is not a jigglebone")
                return {'CANCELLED'}
            props += [
                'bone_is_jigglebone',
                'jiggle_flex_type',
                'jiggle_base_type',
                'use_bone_length_for_jigglebone_length',
                'jiggle_length',
                'jiggle_tip_mass',
                'jiggle_yaw_stiffness',
                'jiggle_yaw_damping',
                'jiggle_pitch_stiffness',
                'jiggle_pitch_damping',
                'jiggle_allow_length_flex',
                'jiggle_along_stiffness',
                'jiggle_along_damping',
                'jiggle_has_angle_constraint',
                'jiggle_has_yaw_constraint',
                'jiggle_has_pitch_constraint',
                'jiggle_angle_constraint',
                'jiggle_yaw_constraint_min',
                'jiggle_yaw_constraint_max',
                'jiggle_yaw_friction',
                'jiggle_pitch_constraint_min',
                'jiggle_pitch_constraint_max',
                'jiggle_pitch_friction',
                'jiggle_base_stiffness',
                'jiggle_base_damping',
                'jiggle_base_mass',
                'jiggle_has_left_constraint',
                'jiggle_has_up_constraint',
                'jiggle_has_forward_constraint',
                'jiggle_left_constraint_min',
                'jiggle_left_constraint_max',
                'jiggle_left_friction',
                'jiggle_up_constraint_min',
                'jiggle_up_constraint_max',
                'jiggle_up_friction',
                'jiggle_forward_constraint_min',
                'jiggle_forward_constraint_max',
                'jiggle_forward_friction',
                'jiggle_impact_speed',
                'jiggle_impact_angle',
                'jiggle_damping_rate',
                'jiggle_frequency',
                'jiggle_amplitude',
            ]

        if not props and not self.copy_rotation and not self.copy_location:
            self.report({'WARNING'}, "Nothing selected to copy")
            return {'CANCELLED'}

        targets = [pb for pb in context.selected_pose_bones if pb != context.active_pose_bone]
        for pb in targets:
            for prop in props:
                try:
                    setattr(pb.bone.vs, prop, getattr(src, prop))
                except AttributeError:
                    continue
            if self.copy_rotation:
                self._copy_rotation(src, pb, arm_ob)
            if self.copy_location:
                self._copy_location(src, pb)

        self.report({'INFO'}, f"Copied bone properties to {len(targets)} bone(s)")
        return {'FINISHED'}


class SMD_OT_ResetJiggleSimulation(Operator):
    bl_idname  = "smd.reset_simulation"
    bl_label   = get_id("op_reset_jiggle_simulation")
    bl_description = get_id('op_reset_jiggle_simulation_tip')

    def execute(self, context) -> set:
        _procbones_sim._states.clear()
        _procbones_sim._proc_trigger_cache.clear()
        _procbones_sim._restore_jiggle_bones()
        return {'FINISHED'}


class SMD_OT_AddAttachmentDisplayMesh(Operator):
    bl_idname = 'smd.add_attachment_display_mesh'
    bl_label = "Add Display Mesh Slot"
    bl_description = "Add an empty display mesh slot to this attachment"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == 'EMPTY' and ob.vs.dmx_attachment

    def execute(self, context):
        vs = context.object.vs
        was_empty = len(vs.attachment_display_meshes) == 0
        vs.attachment_display_meshes.add()
        new_idx = len(vs.attachment_display_meshes) - 1
        vs.attachment_display_meshes_index = new_idx
        if was_empty:
            vs.attachment_display_mesh_render_index = new_idx
        return {'FINISHED'}


class SMD_OT_RemoveAttachmentDisplayMesh(Operator):
    bl_idname = 'smd.remove_attachment_display_mesh'
    bl_label = "Remove Display Mesh Slot"
    bl_description = "Remove the selected display mesh slot from this attachment"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        if ob is None or ob.type != 'EMPTY' or not ob.vs.dmx_attachment:
            return False
        vs = ob.vs
        return 0 <= vs.attachment_display_meshes_index < len(vs.attachment_display_meshes)

    def execute(self, context):
        vs = context.object.vs
        idx = vs.attachment_display_meshes_index
        vs.attachment_display_meshes.remove(idx)
        new_len = len(vs.attachment_display_meshes)
        vs.attachment_display_meshes_index = min(idx, new_len - 1)
        if vs.attachment_display_mesh_render_index == idx:
            vs.attachment_display_mesh_render_index = -1
        elif vs.attachment_display_mesh_render_index > idx:
            vs.attachment_display_mesh_render_index -= 1
        return {'FINISHED'}


class SMD_OT_SetAttachmentMeshRender(Operator):
    bl_idname = 'smd.set_attachment_mesh_render'
    bl_label = "Toggle Render"
    bl_description = "Set this mesh as the one rendered in the viewport (click again to disable)"
    bl_options = {'REGISTER', 'UNDO'}

    index : IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == 'EMPTY' and ob.vs.dmx_attachment

    def execute(self, context):
        vs = context.object.vs
        if vs.attachment_display_mesh_render_index == self.index:
            vs.attachment_display_mesh_render_index = -1
        else:
            vs.attachment_display_mesh_render_index = self.index
        return {'FINISHED'}
