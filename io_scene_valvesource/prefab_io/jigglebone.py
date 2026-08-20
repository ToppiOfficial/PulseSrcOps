"""Jigglebone serialization - import + export for all three prefab formats.

Converts between a bone's ``jiggle_*`` properties (``bone.vs``) and:

* **QC text** - ``$jigglebone`` blocks (Source 1 / studiomdl)
* **DME** - ``DmeJiggleBone`` element attributes (model-DMX)
* **KV3** - ``JiggleBone`` KVNodes (Source 2 / ModelDoc .vmdl)

Each format's writer sits next to its reader. KV3 is fully driven by
``_KV3_FIELDS`` both directions; QC and DME keep structured codecs (nested /
flag-gated formats).
"""

import math
import re

from .. import utils, keyvalues3
from ..keyvalues3 import KVBool, KVVector3


# -----------------------------------------------------------------------------
# KV3 (Source 2 / ModelDoc) - schema-driven both directions
# -----------------------------------------------------------------------------

# (kv3_key, vs_attr, kind). Order matters - KVNode serializes in insertion order.
# kinds: rootbone/type/basespringbool/length/coll* are write-only (set by the
# exporter or not read back); bool=KVBool, deg=radians<->degrees, int, raw=float.
_KV3_FIELDS = [
    ('jiggle_root_bone',     None,                            'rootbone'),
    ('jiggle_type',          None,                            'type'),
    ('has_yaw_constraint',   'jiggle_has_yaw_constraint',     'bool'),
    ('has_pitch_constraint', 'jiggle_has_pitch_constraint',   'bool'),
    ('has_angle_constraint', 'jiggle_has_angle_constraint',   'bool'),
    ('has_base_spring',      None,                            'basespringbool'),
    ('allow_flex_length',    'jiggle_allow_length_flex',      'bool'),
    ('length',               None,                            'length'),
    ('tip_mass',             'jiggle_tip_mass',               'raw'),
    ('angle_limit',          'jiggle_angle_constraint',       'deg'),
    ('min_yaw',              'jiggle_yaw_constraint_min',     'deg'),
    ('max_yaw',              'jiggle_yaw_constraint_max',     'deg'),
    ('yaw_friction',         'jiggle_yaw_friction',           'raw'),
    ('min_pitch',            'jiggle_pitch_constraint_min',   'deg'),
    ('max_pitch',            'jiggle_pitch_constraint_max',   'deg'),
    ('pitch_friction',       'jiggle_pitch_friction',         'raw'),
    ('base_mass',            'jiggle_base_mass',              'int'),
    ('base_stiffness',       'jiggle_base_stiffness',         'raw'),
    ('base_damping',         'jiggle_base_damping',           'raw'),
    ('base_left_min',        'jiggle_left_constraint_min',    'raw'),
    ('base_left_max',        'jiggle_left_constraint_max',    'raw'),
    ('base_left_friction',   'jiggle_left_friction',          'raw'),
    ('base_up_min',          'jiggle_up_constraint_min',      'raw'),
    ('base_up_max',          'jiggle_up_constraint_max',      'raw'),
    ('base_up_friction',     'jiggle_up_friction',            'raw'),
    ('base_forward_min',     'jiggle_forward_constraint_min', 'raw'),
    ('base_forward_max',     'jiggle_forward_constraint_max', 'raw'),
    ('base_forward_friction','jiggle_forward_friction',       'raw'),
    ('yaw_stiffness',        'jiggle_yaw_stiffness',          'raw'),
    ('yaw_damping',          'jiggle_yaw_damping',            'raw'),
    ('pitch_stiffness',      'jiggle_pitch_stiffness',        'raw'),
    ('pitch_damping',        'jiggle_pitch_damping',          'raw'),
    ('along_stiffness',      'jiggle_along_stiffness',        'raw'),
    ('along_damping',        'jiggle_along_damping',          'raw'),
    ('has_collision',        None,                            'collbool'),
    ('radius0',              None,                            'collraw0'),
    ('radius1',              None,                            'collraw1'),
    ('point0',               None,                            'collvec0'),
    ('point1',               None,                            'collvec1'),
]


def _kv3_type(vs):
    if vs.jiggle_flex_type not in ('FLEXIBLE', 'RIGID'):
        return 2
    return 1 if vs.jiggle_flex_type == 'FLEXIBLE' else 0


def kv3_kwargs(vs, s2name, jiggle_length) -> dict:
    """Build the ``JiggleBone`` KVNode property kwargs (excluding _class/name).

    ``s2name`` is the prefab-stripped bone name and ``jiggle_length`` the resolved
    length, both computed by the exporter (they need the bone, not just bone.vs).
    """
    kw = {}
    for key, attr, kind in _KV3_FIELDS:
        if kind == 'rootbone':
            kw[key] = s2name
        elif kind == 'type':
            kw[key] = _kv3_type(vs)
        elif kind == 'basespringbool':
            kw[key] = KVBool(vs.jiggle_base_type == 'BASESPRING')
        elif kind == 'length':
            kw[key] = jiggle_length
        elif kind == 'bool':
            kw[key] = KVBool(getattr(vs, attr))
        elif kind == 'deg':
            kw[key] = math.degrees(getattr(vs, attr))
        elif kind == 'collbool':
            kw[key] = KVBool(vs.jiggle_has_collision)
        elif kind == 'collraw0':
            kw[key] = vs.jiggle_collision_radius0
        elif kind == 'collraw1':
            kw[key] = vs.jiggle_collision_radius1
        elif kind == 'collvec0':
            kw[key] = KVVector3(*vs.jiggle_collision_point0)
        elif kind == 'collvec1':
            kw[key] = KVVector3(*vs.jiggle_collision_point1)
        else:  # 'int' / 'raw'
            kw[key] = getattr(vs, attr)
    return kw


def _read_kv3_props(vs, props) -> None:
    jt = props.get('jiggle_type')
    vs.jiggle_flex_type = 'RIGID' if jt == 0 else ('FLEXIBLE' if jt == 1 else 'NONE')

    has_bs = props.get('has_base_spring', False)
    vs.jiggle_has_base_spring = has_bs
    vs.jiggle_base_type = 'BASESPRING' if has_bs else 'NONE'

    vs.jiggle_length = float(props.get('length', 0.0))
    vs.use_bone_length_for_jigglebone_length = vs.jiggle_length == 0.0

    for key, attr, kind in _KV3_FIELDS:
        if attr is None:
            continue  # handled above, or write-only
        if kind == 'bool':
            setattr(vs, attr, props.get(key, False))
        elif kind == 'deg':
            setattr(vs, attr, math.radians(float(props.get(key, 0.0))))
        elif kind == 'int':
            setattr(vs, attr, int(float(props.get(key, 0))))
        else:  # 'raw'
            setattr(vs, attr, float(props.get(key, 0.0)))


def import_jigglebones_from_kv3(kv_doc, armature: 'object') -> 'tuple[int, list]':
    imported_count = 0
    missing_bones = []

    # Source bone names are case-insensitive; keep a lowercase fallback map.
    bone_map = {utils.get_bone_exportname(b): b for b in armature.data.bones}
    bone_map_lower = {utils.get_bone_exportname(b).lower(): b for b in armature.data.bones}
    for b in armature.data.bones:
        bone_map_lower.setdefault(b.name.lower(), b)

    def find_jigglebone_nodes(node):
        found = []
        if isinstance(node, keyvalues3.KVNode):
            if node.properties.get('_class') == "JiggleBone":
                found.append(node)
            for child in node.children:
                found.extend(find_jigglebone_nodes(child))
        elif isinstance(node, dict):
            for value in node.values():
                found.extend(find_jigglebone_nodes(value))
        elif isinstance(node, (list, tuple)):
            for item in node:
                found.extend(find_jigglebone_nodes(item))
        return found

    jigglebone_nodes = []
    for root_node in kv_doc.roots.values():
        jigglebone_nodes.extend(find_jigglebone_nodes(root_node))

    if not jigglebone_nodes:
        return 0, []

    for jb_node in jigglebone_nodes:
        props = jb_node.properties

        current_bone_name = props.get('jiggle_root_bone')
        if not current_bone_name:
            continue

        blender_bone = bone_map.get(current_bone_name) or bone_map_lower.get(current_bone_name.lower())
        if not blender_bone:
            missing_bones.append(current_bone_name)
            continue

        vs_bone = blender_bone.vs
        vs_bone.bone_is_jigglebone = True
        imported_count += 1
        _read_kv3_props(vs_bone, props)

    return imported_count, missing_bones


# -----------------------------------------------------------------------------
# DME (model-DMX / PulseMDL) - structured codec, writer + reader adjacent
# -----------------------------------------------------------------------------

def write_dme_attrs(elem, bone) -> None:
    """Populate a DmeJiggleBone element from a bone's jiggle_* properties.

    Mirrors the flag-gating and unit conversions of ``qc_block_lines`` so the DME
    encoding matches the .qci output. Attribute names/units match PulseMDL's
    CDmeJiggleBone. Inverse of ``import_jigglebones_from_dmx_elements`` below.
    """
    bvs = bone.vs
    jiggle_length = bone.length if bvs.use_bone_length_for_jigglebone_length else bvs.jiggle_length

    is_flexible = bvs.jiggle_flex_type == 'FLEXIBLE'
    is_rigid    = bvs.jiggle_flex_type == 'RIGID'

    elem["flexible"] = is_flexible
    elem["rigid"]    = is_rigid
    elem["length"]   = float(jiggle_length)
    elem["tipMass"]  = float(bvs.jiggle_tip_mass)

    if is_flexible:
        elem["yawStiffness"]   = float(bvs.jiggle_yaw_stiffness)
        elem["yawDamping"]     = float(bvs.jiggle_yaw_damping)
        elem["pitchStiffness"] = float(bvs.jiggle_pitch_stiffness)
        elem["pitchDamping"]   = float(bvs.jiggle_pitch_damping)

        elem["yawConstrained"] = bool(bvs.jiggle_has_yaw_constraint)
        if bvs.jiggle_has_yaw_constraint:
            elem["yawMin"]      = -abs(math.degrees(bvs.jiggle_yaw_constraint_min))
            elem["yawMax"]      =  abs(math.degrees(bvs.jiggle_yaw_constraint_max))
            elem["yawFriction"] = float(bvs.jiggle_yaw_friction)

        elem["pitchConstrained"] = bool(bvs.jiggle_has_pitch_constraint)
        if bvs.jiggle_has_pitch_constraint:
            elem["pitchMin"]      = -abs(math.degrees(bvs.jiggle_pitch_constraint_min))
            elem["pitchMax"]      =  abs(math.degrees(bvs.jiggle_pitch_constraint_max))
            elem["pitchFriction"] = float(bvs.jiggle_pitch_friction)

        # Flexible jigglebones constrain length by default; allow_length_flex releases
        # it, so lengthConstrained is the inverse.
        elem["lengthConstrained"] = not bvs.jiggle_allow_length_flex
        if bvs.jiggle_allow_length_flex:
            elem["alongStiffness"] = float(bvs.jiggle_along_stiffness)
            elem["alongDamping"]   = float(bvs.jiggle_along_damping)

        elem["angleConstrained"] = bool(bvs.jiggle_has_angle_constraint)
        if bvs.jiggle_has_angle_constraint:
            elem["angleLimit"] = math.degrees(bvs.jiggle_angle_constraint)

    if bvs.jiggle_base_type == 'BASESPRING':
        elem["baseSpring"]    = True
        elem["baseStiffness"] = float(bvs.jiggle_base_stiffness)
        elem["baseDamping"]   = float(bvs.jiggle_base_damping)
        elem["baseMass"]      = float(bvs.jiggle_base_mass)
        if bvs.jiggle_has_left_constraint:
            elem["baseYawMin"]      = -abs(bvs.jiggle_left_constraint_min)
            elem["baseYawMax"]      =  abs(bvs.jiggle_left_constraint_max)
            elem["baseYawFriction"] = float(bvs.jiggle_left_friction)
        if bvs.jiggle_has_up_constraint:
            elem["basePitchMin"]      = -abs(bvs.jiggle_up_constraint_min)
            elem["basePitchMax"]      =  abs(bvs.jiggle_up_constraint_max)
            elem["basePitchFriction"] = float(bvs.jiggle_up_friction)
        if bvs.jiggle_has_forward_constraint:
            elem["baseAlongMin"]      = -abs(bvs.jiggle_forward_constraint_min)
            elem["baseAlongMax"]      =  abs(bvs.jiggle_forward_constraint_max)
            elem["baseAlongFriction"] = float(bvs.jiggle_forward_friction)
    elif bvs.jiggle_base_type == 'BOING':
        elem["boing"]            = True
        elem["boingImpactSpeed"] = float(bvs.jiggle_impact_speed)
        elem["boingImpactAngle"] = math.degrees(bvs.jiggle_impact_angle)
        elem["boingDampingRate"] = float(bvs.jiggle_damping_rate)
        elem["boingFrequency"]   = float(bvs.jiggle_frequency)
        elem["boingAmplitude"]   = float(bvs.jiggle_amplitude)


def import_jigglebones_from_dmx_elements(elements, armature: 'object') -> 'tuple[int, list]':
    imported_count = 0
    missing_bones: list = []

    # Match by exported name first, then by raw bone name (case-insensitive fallback).
    bone_by_export = {utils.get_bone_exportname(b): b for b in armature.data.bones}
    bone_by_name = {b.name: b for b in armature.data.bones}
    bone_by_name_lower = {b.name.lower(): b for b in armature.data.bones}

    for elem, bone_name in elements:
        bone = None
        if bone_name:
            bone = bone_by_name.get(bone_name)
        if bone is None:
            name = elem.name or ""
            bone = (bone_by_export.get(name) or bone_by_name.get(name)
                    or bone_by_name_lower.get(name.lower()))
        if bone is None:
            missing_bones.append(elem.name or "<unnamed>")
            continue

        vs_bone = bone.vs
        vs_bone.bone_is_jigglebone = True
        imported_count += 1

        if elem.get("flexible"):
            vs_bone.jiggle_flex_type = 'FLEXIBLE'
        elif elem.get("rigid"):
            vs_bone.jiggle_flex_type = 'RIGID'
        else:
            vs_bone.jiggle_flex_type = 'NONE'

        vs_bone.jiggle_length = float(elem.get("length", 0.0))
        vs_bone.use_bone_length_for_jigglebone_length = vs_bone.jiggle_length == 0.0
        vs_bone.jiggle_tip_mass = float(elem.get("tipMass", 0.0))

        if vs_bone.jiggle_flex_type == 'FLEXIBLE':
            vs_bone.jiggle_yaw_stiffness = float(elem.get("yawStiffness", 0.0))
            vs_bone.jiggle_yaw_damping = float(elem.get("yawDamping", 0.0))
            vs_bone.jiggle_pitch_stiffness = float(elem.get("pitchStiffness", 0.0))
            vs_bone.jiggle_pitch_damping = float(elem.get("pitchDamping", 0.0))

            vs_bone.jiggle_has_yaw_constraint = bool(elem.get("yawConstrained"))
            if vs_bone.jiggle_has_yaw_constraint:
                vs_bone.jiggle_yaw_constraint_min = abs(math.radians(float(elem.get("yawMin", 0.0))))
                vs_bone.jiggle_yaw_constraint_max = abs(math.radians(float(elem.get("yawMax", 0.0))))
                vs_bone.jiggle_yaw_friction = float(elem.get("yawFriction", 0.0))

            vs_bone.jiggle_has_pitch_constraint = bool(elem.get("pitchConstrained"))
            if vs_bone.jiggle_has_pitch_constraint:
                vs_bone.jiggle_pitch_constraint_min = abs(math.radians(float(elem.get("pitchMin", 0.0))))
                vs_bone.jiggle_pitch_constraint_max = abs(math.radians(float(elem.get("pitchMax", 0.0))))
                vs_bone.jiggle_pitch_friction = float(elem.get("pitchFriction", 0.0))

            # Inverse of the lengthConstrained flag written on export.
            vs_bone.jiggle_allow_length_flex = not bool(elem.get("lengthConstrained", True))
            if vs_bone.jiggle_allow_length_flex:
                vs_bone.jiggle_along_stiffness = float(elem.get("alongStiffness", 0.0))
                vs_bone.jiggle_along_damping = float(elem.get("alongDamping", 0.0))

            vs_bone.jiggle_has_angle_constraint = bool(elem.get("angleConstrained"))
            if vs_bone.jiggle_has_angle_constraint:
                vs_bone.jiggle_angle_constraint = math.radians(float(elem.get("angleLimit", 0.0)))

        if elem.get("baseSpring"):
            vs_bone.jiggle_base_type = 'BASESPRING'
            vs_bone.jiggle_base_stiffness = float(elem.get("baseStiffness", 0.0))
            vs_bone.jiggle_base_damping = float(elem.get("baseDamping", 0.0))
            vs_bone.jiggle_base_mass = int(float(elem.get("baseMass", 0)))

            # Base constraints are written without degree conversion on export - read raw.
            has_left = elem.get("baseYawMin") is not None or elem.get("baseYawMax") is not None
            vs_bone.jiggle_has_left_constraint = has_left
            if has_left:
                vs_bone.jiggle_left_constraint_min = abs(float(elem.get("baseYawMin", 0.0)))
                vs_bone.jiggle_left_constraint_max = abs(float(elem.get("baseYawMax", 0.0)))
                vs_bone.jiggle_left_friction = float(elem.get("baseYawFriction", 0.0))

            has_up = elem.get("basePitchMin") is not None or elem.get("basePitchMax") is not None
            vs_bone.jiggle_has_up_constraint = has_up
            if has_up:
                vs_bone.jiggle_up_constraint_min = abs(float(elem.get("basePitchMin", 0.0)))
                vs_bone.jiggle_up_constraint_max = abs(float(elem.get("basePitchMax", 0.0)))
                vs_bone.jiggle_up_friction = float(elem.get("basePitchFriction", 0.0))

            has_forward = elem.get("baseAlongMin") is not None or elem.get("baseAlongMax") is not None
            vs_bone.jiggle_has_forward_constraint = has_forward
            if has_forward:
                vs_bone.jiggle_forward_constraint_min = abs(float(elem.get("baseAlongMin", 0.0)))
                vs_bone.jiggle_forward_constraint_max = abs(float(elem.get("baseAlongMax", 0.0)))
                vs_bone.jiggle_forward_friction = float(elem.get("baseAlongFriction", 0.0))
        elif elem.get("boing"):
            vs_bone.jiggle_base_type = 'BOING'
            vs_bone.jiggle_impact_speed = int(float(elem.get("boingImpactSpeed", 0)))
            vs_bone.jiggle_impact_angle = math.radians(float(elem.get("boingImpactAngle", 0.0)))
            vs_bone.jiggle_damping_rate = float(elem.get("boingDampingRate", 0.0))
            vs_bone.jiggle_frequency = float(elem.get("boingFrequency", 0.0))
            vs_bone.jiggle_amplitude = float(elem.get("boingAmplitude", 0.0))
        else:
            vs_bone.jiggle_base_type = 'NONE'

    return imported_count, missing_bones


# -----------------------------------------------------------------------------
# QC text ($jigglebone) - structured codec, writer + reader adjacent
# -----------------------------------------------------------------------------

def qc_block_lines(bone) -> list:
    """Return the QC text lines for one ``$jigglebone`` block. Inverse of
    ``import_jigglebones_from_content`` below. QC intentionally omits
    ``along_damping`` (DME/KV3 write it) to keep .qci output byte-identical.
    """
    d = []
    d.append(f'$jigglebone "{utils.get_bone_exportname(bone)}"')
    d.append('{')
    jiggle_length = bone.length if bone.vs.use_bone_length_for_jigglebone_length else bone.vs.jiggle_length

    if bone.vs.jiggle_flex_type in ['FLEXIBLE', 'RIGID']:
        d.append('\tis_flexible' if bone.vs.jiggle_flex_type == 'FLEXIBLE' else '\tis_rigid')
        d.append('\t{')
        d.append(f'\t\tlength {jiggle_length:.4f}')
        d.append(f'\t\ttip_mass {bone.vs.jiggle_tip_mass:.2f}')
        if bone.vs.jiggle_flex_type == 'FLEXIBLE':
            d.append(f'\t\tyaw_stiffness {bone.vs.jiggle_yaw_stiffness:.4f}')
            d.append(f'\t\tyaw_damping {bone.vs.jiggle_yaw_damping:.4f}')
            if bone.vs.jiggle_has_yaw_constraint:
                d.append(f'\t\tyaw_constraint {-abs(math.degrees(bone.vs.jiggle_yaw_constraint_min)):.4f} {abs(math.degrees(bone.vs.jiggle_yaw_constraint_max)):.4f}')
                d.append(f'\t\tyaw_friction {bone.vs.jiggle_yaw_friction:.3f}')
            d.append(f'\t\tpitch_stiffness {bone.vs.jiggle_pitch_stiffness:.4f}')
            d.append(f'\t\tpitch_damping {bone.vs.jiggle_pitch_damping:.4f}')
            if bone.vs.jiggle_has_pitch_constraint:
                d.append(f'\t\tpitch_constraint {-abs(math.degrees(bone.vs.jiggle_pitch_constraint_min)):.4f} {abs(math.degrees(bone.vs.jiggle_pitch_constraint_max)):.4f}')
                d.append(f'\t\tpitch_friction {bone.vs.jiggle_pitch_friction:.3f}')
            if bone.vs.jiggle_allow_length_flex:
                d.append('\t\tallow_length_flex')
                d.append(f'\t\talong_stiffness {bone.vs.jiggle_along_stiffness:.4f}')
            if bone.vs.jiggle_has_angle_constraint:
                d.append(f'\t\tangle_constraint {math.degrees(bone.vs.jiggle_angle_constraint):.4f}')
        d.append('\t}')

    if bone.vs.jiggle_base_type == 'BASESPRING':
        d.append('\thas_base_spring')
        d.append('\t{')
        d.append(f'\t\tstiffness {bone.vs.jiggle_base_stiffness:.4f}')
        d.append(f'\t\tdamping {bone.vs.jiggle_base_damping:.4f}')
        d.append(f'\t\tbase_mass {bone.vs.jiggle_base_mass}')
        if bone.vs.jiggle_has_left_constraint:
            d.append(f'\t\tleft_constraint {-abs(bone.vs.jiggle_left_constraint_min):.2f} {abs(bone.vs.jiggle_left_constraint_max):.2f}')
            d.append(f'\t\tleft_friction {bone.vs.jiggle_left_friction:.3f}')
        if bone.vs.jiggle_has_up_constraint:
            d.append(f'\t\tup_constraint {-abs(bone.vs.jiggle_up_constraint_min):.2f} {abs(bone.vs.jiggle_up_constraint_max):.2f}')
            d.append(f'\t\tup_friction {bone.vs.jiggle_up_friction:.3f}')
        if bone.vs.jiggle_has_forward_constraint:
            d.append(f'\t\tforward_constraint {-abs(bone.vs.jiggle_forward_constraint_min):.2f} {abs(bone.vs.jiggle_forward_constraint_max):.2f}')
            d.append(f'\t\tforward_friction {bone.vs.jiggle_forward_friction:.3f}')
        d.append('\t}')
    elif bone.vs.jiggle_base_type == 'BOING':
        d.append('\tis_boing')
        d.append('\t{')
        d.append(f'\t\timpact_speed {bone.vs.jiggle_impact_speed}')
        d.append(f'\t\timpact_angle {math.degrees(bone.vs.jiggle_impact_angle):.4f}')
        d.append(f'\t\tdamping_rate {bone.vs.jiggle_damping_rate:.3f}')
        d.append(f'\t\tfrequency {bone.vs.jiggle_frequency:.3f}')
        d.append(f'\t\tamplitude {bone.vs.jiggle_amplitude:.3f}')
        d.append('\t}')
    d.append('}')
    d.append('\n')
    return d


def import_jigglebones_from_content(content: str, armature: 'object') -> 'tuple[int, list]':
    """
    Import jigglebones from text content containing $jigglebone definitions.
    Returns (imported_count, missing_bones_list)
    """
    imported_count = 0
    missing_bones = []

    # Remove comments to simplify parsing
    content = re.sub(r"//.*", "", content)

    bone_map = {utils.get_bone_exportname(b): b for b in armature.data.bones}

    # Recursive descent parser for the QC-like key-value format (nested blocks).
    def parse_from_tokens(token_stream):
        result = {}
        tokens = list(token_stream)
        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token == "}":
                return result

            key = token.lower()
            i += 1

            if i >= len(tokens):
                result[key] = ""
                break

            value_token = tokens[i]

            if value_token == "{":
                brace_depth = 1
                j = i + 1
                while j < len(tokens):
                    if tokens[j] == '{': brace_depth += 1
                    elif tokens[j] == '}': brace_depth -= 1
                    if brace_depth == 0: break
                    j += 1

                sub_tokens = tokens[i+1:j]
                result[key] = parse_from_tokens(iter(sub_tokens))
                i = j + 1
            else:
                values = [value_token.strip('"')]
                i += 1
                while i < len(tokens):
                    next_token = tokens[i]

                    is_value = False
                    try:
                        float(next_token.strip('"'))
                        is_value = True
                    except ValueError:
                        pass

                    if next_token == "{" or next_token == "}":
                        is_value = False

                    if is_value:
                        values.append(next_token.strip('"'))
                        i += 1
                    else:
                        break
                result[key] = " ".join(values)
        return result

    for match in re.finditer(r'\$jigglebone\s+"([^"]+)"', content, re.IGNORECASE):
        current_bone_name = match.group(1)

        block_start_index = content.find('{', match.end())
        if block_start_index == -1:
            print(f"- Missing '{{' for jigglebone '{current_bone_name}'.")
            continue

        # Find the matching closing brace to extract the block content.
        brace_depth = 1
        block_end_index = -1
        for i in range(block_start_index + 1, len(content)):
            if content[i] == '{':
                brace_depth += 1
            elif content[i] == '}':
                brace_depth -= 1

            if brace_depth == 0:
                block_end_index = i
                break

        if block_end_index == -1:
            print(f"QC: Unmatched '{{' for jigglebone '{current_bone_name}'.")
            continue

        block_content = content[block_start_index + 1 : block_end_index]

        tokens = iter(re.findall(r'"[^"]+"|\S+', block_content))
        current_jigglebone_data = parse_from_tokens(tokens)

        if not current_bone_name or not current_jigglebone_data:
            continue

        blender_bone = bone_map.get(current_bone_name)
        if not blender_bone:
            print(f"- No matching Blender bone found for '{current_bone_name}'.")
            missing_bones.append(current_bone_name)
            continue

        vs_bone = blender_bone.vs
        vs_bone.bone_is_jigglebone = True
        imported_count += 1

        if 'is_flexible' in current_jigglebone_data:
            vs_bone.jiggle_flex_type = 'FLEXIBLE'
            flex_data = current_jigglebone_data['is_flexible']
            if isinstance(flex_data, dict):
                vs_bone.jiggle_length = float(flex_data.get('length', 0.0))
                vs_bone.jiggle_tip_mass = float(flex_data.get('tip_mass', 0.0))
                vs_bone.jiggle_yaw_stiffness = float(flex_data.get('yaw_stiffness', 0.0))
                vs_bone.jiggle_yaw_damping = float(flex_data.get('yaw_damping', 0.0))

                if 'yaw_constraint' in flex_data:
                    vs_bone.jiggle_has_yaw_constraint = True
                    yc_vals = [float(x) for x in flex_data['yaw_constraint'].split()]
                    vs_bone.jiggle_yaw_constraint_min = abs(math.radians(yc_vals[0]))
                    vs_bone.jiggle_yaw_constraint_max = abs(math.radians(yc_vals[1]))
                if 'yaw_friction' in flex_data:
                    vs_bone.jiggle_yaw_friction = float(flex_data['yaw_friction'])

                vs_bone.jiggle_pitch_stiffness = float(flex_data.get('pitch_stiffness', 0.0))
                vs_bone.jiggle_pitch_damping = float(flex_data.get('pitch_damping', 0.0))

                if 'pitch_constraint' in flex_data:
                    vs_bone.jiggle_has_pitch_constraint = True
                    pc_vals = [float(x) for x in flex_data['pitch_constraint'].split()]
                    vs_bone.jiggle_pitch_constraint_min = abs(math.radians(pc_vals[0]))
                    vs_bone.jiggle_pitch_constraint_max = abs(math.radians(pc_vals[1]))
                if 'pitch_friction' in flex_data:
                    vs_bone.jiggle_pitch_friction = float(flex_data['pitch_friction'])

                vs_bone.jiggle_allow_length_flex = 'allow_length_flex' in flex_data
                if vs_bone.jiggle_allow_length_flex and isinstance(flex_data['allow_length_flex'], dict):
                    along_data = flex_data['allow_length_flex']
                    vs_bone.jiggle_along_stiffness = float(along_data.get('along_stiffness', 0.0))
                    vs_bone.jiggle_along_damping = float(along_data.get('along_damping', 0.0))

                if 'angle_constraint' in flex_data:
                    vs_bone.jiggle_has_angle_constraint = True
                    vs_bone.jiggle_angle_constraint = math.radians(float(flex_data['angle_constraint']))

        elif 'is_rigid' in current_jigglebone_data:
            vs_bone.jiggle_flex_type = 'RIGID'
            rigid_data = current_jigglebone_data['is_rigid']
            if isinstance(rigid_data, dict):
                vs_bone.jiggle_length = float(rigid_data.get('length', 0.0))
                vs_bone.jiggle_tip_mass = float(rigid_data.get('tip_mass', 0.0))
        else:
            vs_bone.jiggle_flex_type = 'NONE'

        if 'has_base_spring' in current_jigglebone_data:
            vs_bone.jiggle_base_type = 'BASESPRING'
            base_data = current_jigglebone_data['has_base_spring']
            if isinstance(base_data, dict):
                vs_bone.jiggle_base_stiffness = float(base_data.get('stiffness', 0.0))
                vs_bone.jiggle_base_damping = float(base_data.get('damping', 0.0))
                vs_bone.jiggle_base_mass = int(float(base_data.get('base_mass', 0)))

                if 'left_constraint' in base_data:
                    vs_bone.jiggle_has_left_constraint = True
                    lc_vals = [float(x) for x in base_data['left_constraint'].split()]
                    vs_bone.jiggle_left_constraint_min = abs(lc_vals[0])
                    vs_bone.jiggle_left_constraint_max = abs(lc_vals[1])
                if 'left_friction' in base_data:
                    vs_bone.jiggle_left_friction = float(base_data['left_friction'])

                if 'up_constraint' in base_data:
                    vs_bone.jiggle_has_up_constraint = True
                    uc_vals = [float(x) for x in base_data['up_constraint'].split()]
                    vs_bone.jiggle_up_constraint_min = abs(uc_vals[0])
                    vs_bone.jiggle_up_constraint_max = abs(uc_vals[1])
                if 'up_friction' in base_data:
                    vs_bone.jiggle_up_friction = float(base_data['up_friction'])

                if 'forward_constraint' in base_data:
                    vs_bone.jiggle_has_forward_constraint = True
                    fc_vals = [float(x) for x in base_data['forward_constraint'].split()]
                    vs_bone.jiggle_forward_constraint_min = abs(fc_vals[0])
                    vs_bone.jiggle_forward_constraint_max = abs(fc_vals[1])
                if 'forward_friction' in base_data:
                    vs_bone.jiggle_forward_friction = float(base_data['forward_friction'])

        elif 'is_boing' in current_jigglebone_data:
            vs_bone.jiggle_base_type = 'BOING'
            boing_data = current_jigglebone_data['is_boing']
            if isinstance(boing_data, dict):
                vs_bone.jiggle_impact_speed = int(float(boing_data.get('impact_speed', 0)))
                vs_bone.jiggle_impact_angle = math.radians(float(boing_data.get('impact_angle', 0.0)))
                vs_bone.jiggle_damping_rate = float(boing_data.get('damping_rate', 0.0))
                vs_bone.jiggle_frequency = float(boing_data.get('frequency', 0.0))
                vs_bone.jiggle_amplitude = float(boing_data.get('amplitude', 0.0))
        else:
            vs_bone.jiggle_base_type = 'NONE'

        if 'length' in current_jigglebone_data and vs_bone.jiggle_flex_type == 'NONE':
            vs_bone.jiggle_length = float(current_jigglebone_data['length'])

        if vs_bone.jiggle_length > 0:
            vs_bone.use_bone_length_for_jigglebone_length = False

    return imported_count, missing_bones
