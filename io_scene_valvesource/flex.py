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

import bpy, re
from . import datamodel, utils
from .utils import get_id, getCorrectiveShapeSeparator, sanitize_string_for_delta, sanitize_flex_expression_deltas, get_dme_corrective_delta_names, get_dme_delta_name_map

class DmxWriteFlexControllers(bpy.types.Operator):
    bl_idname = "export_scene.dmx_flex_controller"
    bl_label = get_id("gen_block")
    bl_description = get_id("gen_block_tip")
    bl_options = {'UNDO','INTERNAL'}
    
    @classmethod
    def poll(cls, context):
        return utils.hasShapes(context.object, valid_only=False)
    
    @classmethod
    def make_controllers(cls,id):
        dm = datamodel.DataModel("model",1)
        
        objects = []
        shapes = set()
        seen_controller_names = set()
        seen_rule_names = set()
        seen_dom_rules = set()
        
        if type(id) == bpy.types.Collection:
            objects.extend(list([ob for ob in id.objects if ob.data and ob.type in utils.shape_types and ob.data.shape_keys]))
        else:
            objects.append(id)
        
        name = "flex_{}".format(id.name)
        root = dm.add_element(name,id=name)
        DmeCombinationOperator = dm.add_element("combinationOperator","DmeCombinationOperator",id=id.name+"controllers")
        root["combinationOperator"] = DmeCombinationOperator
        controls = DmeCombinationOperator["controls"] = datamodel.make_array([],datamodel.Element)
        # Initialize dominators and targets early so DME branch can populate them
        DmeCombinationOperator["dominators"] = datamodel.make_array([],datamodel.Element)
        targets = DmeCombinationOperator["targets"] = datamodel.make_array([],datamodel.Element)

        def createController(namespace, name, deltas, shape_key=None, flexcontroller=None, normalize_shapekeys=False, flex_min=0.0, flex_max=1.0):
            if flexcontroller is not None:
                shapekey_name, eyelid, stereo, raw_delta_name, controller_name, flextype = flexcontroller
                raw_control_names = [raw_delta_name] if raw_delta_name else []
            else:
                controller_name = name
                eyelid = False
                stereo = False
                flextype = "default"
                raw_control_names = deltas

            DmeCombinationInputControl = dm.add_element(controller_name, "DmeCombinationInputControl", id=namespace + controller_name + "inputcontrol")
            controls.append(DmeCombinationInputControl)

            DmeCombinationInputControl["rawControlNames"] = datamodel.make_array(raw_control_names, str)
            DmeCombinationInputControl["stereo"] = bool(stereo)
            DmeCombinationInputControl["eyelid"] = bool(eyelid)
            DmeCombinationInputControl["flexgroup"] = flextype.lower()

            DmeCombinationInputControl["flexMin"] = float(flex_min)
            DmeCombinationInputControl["flexMax"] = float(flex_max)

        for ob in objects:
            normalize = ob.data.vs.normalize_shapekeys if ob.data.shape_keys else False

            if ob.vs.flex_controller_mode == 'DME':
                corrective_names = get_dme_corrective_delta_names(ob)
                delta_name_map = get_dme_delta_name_map(ob)

                for fc in ob.vs.dme_flexcontrollers:
                    if not fc.controller_name or not fc.controller_name.strip():
                        continue
                    if fc.controller_name in seen_controller_names:
                        print(f"- Skipping duplicate flex controller '{fc.controller_name}' on '{ob.name}' (already defined by another mesh)")
                        continue
                    seen_controller_names.add(fc.controller_name)
                    shape = ob.data.shape_keys.key_blocks.get(fc.shapekey) if (ob.data.shape_keys and fc.shapekey) else None
                    ctrl = dm.add_element(fc.controller_name, "DmeCombinationInputControl",
                                         id=ob.name + fc.controller_name + "inputcontrol")
                    controls.append(ctrl)
                    shapekey_ref = fc.shapekey or ''
                    if shapekey_ref in corrective_names:
                        raw = shapekey_ref
                    elif shapekey_ref:
                        raw = delta_name_map.get(shapekey_ref, sanitize_string_for_delta(shapekey_ref))
                    else:
                        raw = ''
                    ctrl["rawControlNames"] = datamodel.make_array([raw] if raw else [], str)
                    ctrl["stereo"]   = bool(fc.stereo)
                    ctrl["eyelid"]   = bool(fc.eyelid)
                    ctrl["flexgroup"] = fc.resolved_flexgroup()
                    ctrl["flexMin"]  = float(fc.flex_min)
                    ctrl["flexMax"]  = float(fc.flex_max)
                    if shape:
                        shapes.add(shape.name)

                # Domination rules - dominator_names are controller names (not sanitized),
                # suppressed_names are delta references (remapped then sanitized)
                dom_array = DmeCombinationOperator["dominators"]
                for rule in ob.vs.dme_flex_rules:
                    if rule.rule_type != 'DOMINATION':
                        continue
                    d_names = [n.strip() for n in rule.dominator_names.split(',') if n.strip()]
                    s_names = [delta_name_map.get(n.strip(), sanitize_string_for_delta(n.strip())) for n in rule.suppressed_names.split(',') if n.strip()]
                    if not d_names or not s_names:
                        continue
                    dom_key = (tuple(d_names), tuple(s_names))
                    if dom_key in seen_dom_rules:
                        continue
                    seen_dom_rules.add(dom_key)
                    dom_elem = dm.add_element("", "DmeCombinationDominationRule",
                                             id=ob.name + rule.dominator_names + "dom")
                    dom_elem["dominators"] = datamodel.make_array(d_names, str)
                    dom_elem["supressed"]  = datamodel.make_array(s_names, str)
                    dom_array.append(dom_elem)

                # Flex rules - exclude DOMINATION and CORRECTIVE (correctives are pure deltas)
                non_dom = [r for r in ob.vs.dme_flex_rules if r.rule_type not in ('DOMINATION', 'CORRECTIVE') and r.name]
                if non_dom:
                    flex_rules_elem = None
                    for rule in non_dom:
                        delta_name = delta_name_map.get(rule.name, sanitize_string_for_delta(rule.name))
                        if delta_name in seen_rule_names:
                            print(f"- Skipping duplicate flex rule '{delta_name}' on '{ob.name}' (already defined by another mesh)")
                            continue
                        seen_rule_names.add(delta_name)

                        if flex_rules_elem is None:
                            flex_rules_elem = dm.add_element("flexRules", "DmeFlexRules",
                                                             id=ob.name + "flexrules")
                            rule_deltas  = flex_rules_elem["deltaStates"]      = datamodel.make_array([], datamodel.Element)
                            rule_weights = flex_rules_elem["deltaStateWeights"] = datamodel.make_array([], datamodel.Vector2)
                            targets.append(flex_rules_elem)

                        if rule.rule_type == 'PASSTHROUGH':
                            rule_elem = dm.add_element(delta_name, "DmeFlexRulePassThrough",
                                                       id=ob.name + delta_name + "passthrough")
                            rule_elem["result"] = 0.0
                        elif rule.rule_type == 'EXPRESSION':
                            rule_elem = dm.add_element(delta_name, "DmeFlexRuleExpression",
                                                       id=ob.name + delta_name + "expression")
                            rule_elem["result"] = 0.0
                            rule_elem["expr"]   = sanitize_flex_expression_deltas(rule.expression.strip(), delta_name_map)
                        else:  # LOCALVAR - sanitize the name the same way as expression
                            # names and %-references, so the declaration stays consistent
                            # with how the variable is referenced (e.g. "ud_norm" -> "udnorm").
                            rule_elem = dm.add_element(delta_name, "DmeFlexRuleLocalVar",
                                                       id=ob.name + delta_name + "localvar")
                            rule_elem["result"] = 0.0
                        rule_deltas.append(rule_elem)
                        rule_weights.append(datamodel.Vector2([0.0, 0.0]))

            else:
                if not ob.data.shape_keys:
                    continue

                corrective_separator = getCorrectiveShapeSeparator()
                for shape in ob.data.shape_keys.key_blocks[1:]:
                    if corrective_separator in shape.name or shape.name in shapes:
                        continue

                    createController(ob.name, shape.name, [shape.name], shape_key=shape, flexcontroller=None, normalize_shapekeys=normalize)
                    shapes.add(shape.name)

        for vca in id.vs.vertex_animations:
            createController(id.name, vca.name, ["{}-{}".format(vca.name,i) for i in range(vca.end - vca.start)])

        controlValues = DmeCombinationOperator["controlValues"] = datamodel.make_array( [ [0.0,0.0,0.5] ] * len(controls), datamodel.Vector3)
        DmeCombinationOperator["controlValuesLagged"] = datamodel.make_array( controlValues, datamodel.Vector3)
        DmeCombinationOperator["usesLaggedValues"] = False

        return dm

    def execute(self, context) -> set:
        utils.State.update_scene()

        id = context.object
        dm = self.make_controllers(id)

        text_name = dm.root.name  # e.g. "flex_<ObjectName>"

        # Check if a text block already exists
        if text_name in bpy.data.texts:
            text = bpy.data.texts[text_name]
            text.clear()  # clear previous contents
        else:
            text = bpy.data.texts.new(text_name)

        # Write DMX contents
        text.from_string(dm.echo("keyvalues2", 1))

        # Save to file if flex_controller_source points to a path
        if id.vs.flex_controller_source and id.vs.flex_controller_source.endswith(".dmx"):
            filepath = bpy.path.abspath(id.vs.flex_controller_source)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(dm.echo("keyvalues2", 1))
        else:
            # Store reference to text block if no file is set
            id.vs.flex_controller_source = text.name

        self.report({'INFO'}, get_id("gen_block_success", True).format(text.name))
        return {'FINISHED'}

class ActiveDependencyShapes(bpy.types.Operator):
    bl_idname = "object.shape_key_activate_dependents"
    bl_label = get_id("activate_dep_shapes")
    bl_description = get_id("activate_dep_shapes_tip")
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_shape_key and context.active_object.active_shape_key.name.find(getCorrectiveShapeSeparator()) != -1

    def execute(self, context):
        context.active_object.show_only_shape_key = False
        active_key = context.active_object.active_shape_key		
        subkeys = set(getCorrectiveShapeKeyDrivers(active_key) or active_key.name.split(getCorrectiveShapeSeparator()))
        num_activated = 0
        for key in context.active_object.data.shape_keys.key_blocks:
            if key == active_key or set(key.name.split(getCorrectiveShapeSeparator())) <= subkeys:
                key.value = 1
                num_activated += 1
            else:
                key.value = 0
        self.report({'INFO'},get_id("activate_dep_shapes_success", True).format(num_activated - 1))
        return {'FINISHED'}

class AddCorrectiveShapeDrivers(bpy.types.Operator):
    bl_idname = "object.sourcetools_generate_corrective_drivers"
    bl_label = get_id("gen_drivers")
    bl_description = get_id("gen_drivers_tip")
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_shape_key

    def execute(self, context):
        keys = context.active_object.data.shape_keys
        for key in keys.key_blocks:
            subkeys = getCorrectiveShapeKeyDrivers(key) or []
            if key.name.find(getCorrectiveShapeSeparator()) != -1:
                name_subkeys = [subkey for subkey in key.name.split(getCorrectiveShapeSeparator()) if subkey in keys.key_blocks]
                subkeys = set([*subkeys, *name_subkeys])
            if subkeys:
                sorted = list(subkeys)
                sorted.sort()
                self.addDrivers(key, sorted)
        return {'FINISHED'}

    @classmethod
    def addDrivers(cls, key, driver_names):
        key.driver_remove("value")
        fcurve = key.driver_add("value")
        fcurve.modifiers.remove(fcurve.modifiers[0])
        fcurve.driver.type = 'MIN'
        for driver_key in driver_names:
            var = fcurve.driver.variables.new()
            var.name = driver_key
            var.targets[0].id_type = 'KEY'
            var.targets[0].id = key.id_data
            var.targets[0].data_path = "key_blocks[\"{}\"].value".format(driver_key)

class RenameShapesToMatchCorrectiveDrivers(bpy.types.Operator):
    bl_idname = "object.sourcetools_rename_to_corrective_drivers"
    bl_label = get_id("apply_drivers")
    bl_description = get_id("apply_drivers_tip")
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_shape_key

    def execute(self, context):
        renamed = 0
        for key in context.active_object.data.shape_keys.key_blocks:
            driver_shapes = getCorrectiveShapeKeyDrivers(key)
            if driver_shapes:
                generated_name = getCorrectiveShapeSeparator().join(driver_shapes)
                if key.name != generated_name:
                    key.name = generated_name
                    renamed += 1

        self.report({'INFO'},get_id("apply_drivers_success", True).format(renamed))
        return {'FINISHED'}

class InsertUUID(bpy.types.Operator):
    bl_idname = "text.insert_uuid"
    bl_label = get_id("insert_uuid")
    bl_description = get_id("insert_uuid_tip")

    @classmethod
    def poll(cls,context):
        return context.space_data.type == 'TEXT_EDITOR' and context.space_data.text

    def execute(self,context):
        text = context.space_data.text
        line = text.current_line
        if 0 and len(line.body) >= 36: # 2.69 https://developer.blender.org/T38386
            sel_range = [max(0,text.current_character - 36),min(len(line.body),text.current_character + 36)]
            sel_range.sort()

            m = re.search(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",line.body[sel_range[0]:sel_range[1]],re.I)
            if m:
                line.body = line.body[:m.start()] + str(datamodel.uuid.uuid4()) + line.body[m.end():]
                return {'FINISHED'}
        
        text.write(str(datamodel.uuid.uuid4()))
        return {'FINISHED'}

class InvalidDriverError(LookupError):
    def __init__(self, key, target_key):
        LookupError(self, "Shape key '{}' has an invalid corrective driver targeting key '{}'".format(key, target_key))
        self.key = key
        self.target_key = target_key

def getCorrectiveShapeKeyDrivers(shape_key, raise_on_invalid = False):
    owner = shape_key.id_data
    drivers = owner.animation_data.drivers if owner.animation_data else None
    if not drivers: return None

    def shapeName(path):
        m = re.match(r'key_blocks\["(.*?)"\].value', path)
        return m[1] if m else None

    fcurve = next((fc for fc in drivers if shapeName(fc.data_path) == shape_key.name), None)
    if not fcurve or not fcurve.driver or not fcurve.driver.type == 'MIN': return None

    keys = []
    for variable in (v for v in fcurve.driver.variables if v.type == 'SINGLE_PROP' and v.id_data == owner and v.targets):
        target_key = shapeName(variable.targets[0].data_path)
        if target_key:
            if raise_on_invalid and not variable.is_valid:
                raise InvalidDriverError(shape_key, target_key)
            keys.append(target_key)

    return keys
