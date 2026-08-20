"""Flex controller / flex rule data, from QC text or a DMX combinationOperator,
onto an object's `vs.dme_flexcontrollers` and `vs.dme_flex_rules`.

Named flexdata rather than flex to stay distinct from the add-on's top-level
`flex.py`, which holds the shape-key operators.
"""

import bpy


# QC flex type keywords that map directly onto a real flexgroup enum option. Anything not
# in this set (e.g. 'phoneme', 'nose') becomes the CUSTOM flexgroup, preserving the raw QC
# type keyword in flexgroup_custom rather than being lumped into MISC.
# DEFAULT and CUSTOM are deliberately excluded - they are never produced by a direct match.
_VALID_FLEXGROUP_ENUMS = {'EYES', 'EYELID', 'BROW', 'MOUTH', 'MISC', 'CHEEK'}


def set_flexgroup_from_qc(item, fc_type: str) -> None:
    """Map a QC flexcontroller type keyword onto a FlexControllerItem's flexgroup.

    A type maps to its enum equivalent only when it directly matches a real flexgroup
    option; otherwise the controller is set to CUSTOM with the raw keyword preserved in
    flexgroup_custom. An empty/missing type stays at the DEFAULT flexgroup."""
    if not fc_type:
        item.flexgroup = 'DEFAULT'
        item.flexgroup_custom = ""
        return
    upper = fc_type.upper()
    if upper in _VALID_FLEXGROUP_ENUMS:
        item.flexgroup = upper
        item.flexgroup_custom = ""
    else:
        item.flexgroup = 'CUSTOM'
        item.flexgroup_custom = fc_type


def parse_flex_text(text: str) -> dict:
    """Parse QC-style flex text into intermediate lists.

    Recognises the same tokens as the QC importer:
      flexcontroller <type> [range <min> <max>] <name...>
      flexpair <name>        (marks <name> as stereo)
      localvar a b c
      %delta = expression

    Returns a dict: {'controllers': [(name, fc_type, fmin, fmax)],
                     'localvars': [name], 'expressions': [(delta, expr)],
                     'stereo_names': set()}.
    """
    controllers = []
    localvars = []
    expressions = []
    stereo_names = set()

    in_block_comment = False
    for raw_line in text.splitlines():
        line_str = raw_line

        # Strip block comments (best-effort; the QC flex syntax is line-comment based).
        if in_block_comment:
            end = line_str.find('*/')
            if end == -1:
                continue
            line_str = line_str[end + 2:]
            in_block_comment = False
        while '/*' in line_str:
            start = line_str.find('/*')
            end = line_str.find('*/', start + 2)
            if end == -1:
                line_str = line_str[:start]
                in_block_comment = True
                break
            line_str = line_str[:start] + line_str[end + 2:]

        # Strip line comments.
        for token in ('//', '#', ';'):
            idx = line_str.find(token)
            if idx != -1:
                line_str = line_str[:idx]

        line_str = line_str.strip()
        if not line_str:
            continue

        line = line_str.split()
        kw = line[0]

        if kw == "flexpair" and len(line) >= 2:
            stereo_names.add(line[1])
            continue

        if kw == "flexcontroller" and len(line) >= 3:
            try:
                fc_type = line[1]
                if len(line) >= 5 and line[2] == "range":
                    flex_min, flex_max = float(line[3]), float(line[4])
                    names = line[5:]
                else:
                    flex_min, flex_max = 0.0, 1.0
                    names = line[2:]
            except (ValueError, IndexError):
                continue
            for name in names:
                controllers.append((name, fc_type, flex_min, flex_max))
            continue

        if kw == "localvar" and len(line) >= 2:
            localvars.extend(line[1:])
            continue

        if line_str.startswith('%'):
            # Everything up to the first '=' is the delta name (which may contain spaces,
            # e.g. "eyes look up"); the rest is the expression.
            body = line_str[1:]
            if '=' in body:
                name, expr = body.split('=', 1)
                name, expr = name.strip(), expr.strip()
                if name and expr:
                    expressions.append((name, expr))
            continue

    return {
        'controllers': controllers,
        'localvars': localvars,
        'expressions': expressions,
        'stereo_names': stereo_names,
    }


def apply_flex_text_to_object(ob, parsed: dict) -> tuple[int, int]:
    """Merge parsed flex data (from parse_flex_text) into ob's DME flex collections.

    Controllers/expressions with a matching name are updated in place; everything else is
    appended. Returns (controllers_touched, rules_touched)."""
    controllers = parsed.get('controllers', [])
    localvars = parsed.get('localvars', [])
    expressions = parsed.get('expressions', [])
    stereo_names = parsed.get('stereo_names', set())

    n_controllers = 0
    for name, fc_type, flex_min, flex_max in controllers:
        stereo = name in stereo_names
        existing = next((i for i in ob.vs.dme_flexcontrollers if i.controller_name == name), None)
        item = existing if existing else ob.vs.dme_flexcontrollers.add()
        if not existing:
            item.controller_name = name
        item.flex_min = flex_min
        item.flex_max = flex_max
        set_flexgroup_from_qc(item, fc_type)
        item.eyelid = False
        item.stereo = stereo
        n_controllers += 1

    n_rules = 0
    existing_lv = {r.name for r in ob.vs.dme_flex_rules if r.rule_type == 'LOCALVAR'}
    for varname in localvars:
        if varname not in existing_lv:
            item = ob.vs.dme_flex_rules.add()
            item.rule_type = 'LOCALVAR'
            item.name = varname
            existing_lv.add(varname)
            n_rules += 1

    controller_by_name = {i.controller_name: i for i in ob.vs.dme_flexcontrollers if i.controller_name}
    shape_keys = ob.data.shape_keys if getattr(ob, "data", None) else None
    shape_key_names = {kb.name for kb in shape_keys.key_blocks} if shape_keys else set()
    for delta_name, expr in expressions:
        # A bare "%<shapekey> = <controller>" is a direct assignment: store it on the
        # controller's shapekey field rather than as a standalone expression rule - but
        # only when the delta actually names a shape key on this mesh. Otherwise keep it
        # a regular expression rule.
        target = expr.strip()
        if target in controller_by_name and delta_name in shape_key_names:
            controller_by_name[target].shapekey = delta_name
            n_controllers += 1
            continue

        existing = next(
            (r for r in ob.vs.dme_flex_rules if r.rule_type == 'EXPRESSION' and r.name == delta_name),
            None,
        )
        if existing:
            existing.expression = expr
        else:
            item = ob.vs.dme_flex_rules.add()
            item.rule_type = 'EXPRESSION'
            item.name = delta_name
            item.expression = expr
        n_rules += 1

    if ob.vs.dme_flexcontrollers:
        ob.vs.flex_controller_mode = 'DME'

    return n_controllers, n_rules


def _fmt_num(v: float) -> str:
    return f"{v:g}"


def serialize_flex_text(ob) -> tuple[str, int]:
    """Serialize ob's DME flex controllers and rules to QC-style text.

    Inverse of apply_flex_text_to_object: only the tokens parse_flex_text can read back
    (flexcontroller / flexpair / localvar / %expression) are emitted as live directives.
    Other rule types (passthrough, domination, corrective) are written as // comments so
    they stay visible for editing but are ignored on re-import.

    Returns (text, n_uneditable) where n_uneditable is how many rules were commented out
    because the text importer can't round-trip them."""
    lines = [f'// DME flex controllers and rules for "{ob.name}"']
    n_uneditable = 0

    # --- Flex controllers -----------------------------------------------------
    controllers = [fc for fc in ob.vs.dme_flexcontrollers if fc.controller_name]
    stereo = [fc.controller_name for fc in controllers if fc.stereo]
    direct = [(fc.shapekey, fc.controller_name) for fc in controllers if fc.shapekey]

    if controllers:
        # Pad the group column so the names line up in a readable table.
        gw = max(len(fc.resolved_flexgroup()) for fc in controllers)
        lines += ["", "// === Flex Controllers ==="]
        for fc in controllers:
            group = fc.resolved_flexgroup().ljust(gw)
            lines.append(
                f"flexcontroller {group} range {_fmt_num(fc.flex_min)} {_fmt_num(fc.flex_max)} {fc.controller_name}"
            )

    if stereo:
        lines += ["", "// === Stereo (L/R split) ==="]
        lines += [f"flexpair {name}" for name in stereo]

    # --- Flex rules -----------------------------------------------------------
    localvars = [r.name for r in ob.vs.dme_flex_rules if r.rule_type == 'LOCALVAR' and r.name]
    expressions = [r for r in ob.vs.dme_flex_rules if r.rule_type == 'EXPRESSION' and r.name]

    if direct:
        # Direct shapekey assignments: %<shapekey> = <controller> (a plain passthrough).
        lines += ["", "// === Direct Assignments (shapekey = controller) ==="]
        lines += [f"%{shapekey} = {name}" for shapekey, name in direct]

    if localvars:
        lines += ["", "// === Local Variables ==="]
        lines += [f"localvar {name}" for name in localvars]

    if expressions:
        lines += ["", "// === Expressions ==="]
        lines += [f"%{r.name} = {r.expression}" for r in expressions]

    # Rule types the text importer can't round-trip: keep them visible as comments.
    other = [r for r in ob.vs.dme_flex_rules if r.rule_type in ('PASSTHROUGH', 'DOMINATION', 'CORRECTIVE')]
    if other:
        lines += ["", "// === Not re-imported (edit in the UI) ==="]
        for r in other:
            n_uneditable += 1
            if r.rule_type == 'PASSTHROUGH':
                lines.append(f"// passthrough {r.name}")
            elif r.rule_type == 'DOMINATION':
                lines.append(f"// domination dominators=[{r.dominator_names}] suppressed=[{r.suppressed_names}]")
            else:
                lines.append(f"// corrective {r.name} components=[{r.components}]")

    return "\n".join(lines) + "\n", n_uneditable


def populate_dme_flex_from_dmx(ob: bpy.types.Object, combo_op) -> None:
    ob.vs.dme_flexcontrollers.clear()
    ob.vs.dme_flex_rules.clear()

    # A DMX combinationOperator is global model data, but when it was exported
    # from multiple meshes its "controls" list and its "targets" (one DmeFlexRules
    # per mesh) hold the same controllers/rules repeated once per mesh. Deduplicate
    # so each controller/rule is imported a single time.
    seen_controllers: set[str] = set()
    for ctrl in combo_op.get("controls", []):
        if ctrl.name in seen_controllers:
            continue
        seen_controllers.add(ctrl.name)
        item = ob.vs.dme_flexcontrollers.add()
        item.controller_name = ctrl.name
        raw = ctrl.get("rawControlNames", [])
        item.shapekey = raw[0] if raw else ''
        item.stereo = bool(ctrl.get("stereo", False)) or len(raw) >= 2
        item.eyelid = bool(ctrl.get("eyelid", False))
        item.flex_min = float(ctrl.get("flexMin", 0.0))
        item.flex_max = float(ctrl.get("flexMax", 1.0))

    seen_doms: set[tuple] = set()
    for dom in combo_op.get("dominators", []):
        d_names = dom.get("dominators", [])
        s_names = dom.get("supressed", [])  # note: "supressed" is Valve's typo in the DMX format
        if d_names or s_names:
            key = (tuple(d_names), tuple(s_names))
            if key in seen_doms:
                continue
            seen_doms.add(key)
            item = ob.vs.dme_flex_rules.add()
            item.rule_type = 'DOMINATION'
            item.dominator_names = ", ".join(d_names)
            item.suppressed_names = ", ".join(s_names)

    seen_rules: set[tuple] = set()
    for target in combo_op.get("targets", []):
        if target.type != "DmeFlexRules":
            continue
        for rule in target.get("deltaStates", []):
            rt = rule.type
            if rt not in ("DmeFlexRulePassThrough", "DmeFlexRuleExpression", "DmeFlexRuleLocalVar"):
                continue
            key = (rt, rule.name)
            if key in seen_rules:
                continue
            seen_rules.add(key)
            item = ob.vs.dme_flex_rules.add()
            if rt == "DmeFlexRulePassThrough":
                item.rule_type = 'PASSTHROUGH'
                item.name = rule.name
            elif rt == "DmeFlexRuleExpression":
                item.rule_type = 'EXPRESSION'
                item.name = rule.name
                item.expression = rule.get("expr", "")
            else:  # DmeFlexRuleLocalVar
                item.rule_type = 'LOCALVAR'
                item.name = rule.name

    if ob.vs.dme_flexcontrollers:
        ob.vs.flex_controller_mode = 'DME'
