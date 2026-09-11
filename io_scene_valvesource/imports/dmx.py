"""DMX -> IR. Parse only: this module builds no Blender data.

Extracted from readDMX. Everything here is a faithful port; behaviour
changes belong in the build half.
"""

import re
from dataclasses import dataclass, field
from typing import Any, cast

from mathutils import Matrix, Vector, Quaternion

from .. import datamodel, ordered_set
from ..utils import (REF, ANIM, PHYS, axes_lookup, implicit_bone_name, dmx_version,
                     Compiler, getDmxKeywords, vertex_float_maps)
from .records import (ImportedAnim, ImportedAttachment, ImportedBone, ImportedChannel,
                      ImportedFace, ImportedFile, ImportedLoopLayer, ImportedMesh,
                      ImportedShape, ImportedSkeleton)


@dataclass
class ParsedDmx:
    dm: Any
    root: Any
    DmeModel: Any
    transforms: Any
    format_ver: int
    keywords: dict
    corrective_separator: str = '_'
    jobType: Any = None
    upAxis: str = 'Z'
    # Scene dmx_format/encoding bumps the parse implies; applied by the caller so
    # this module stays free of scene writes.
    version_bumps: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Load / detect
# ---------------------------------------------------------------------------

def load_dmx(filepath: str, smd_type=None, upAxis: str | None = None) -> ParsedDmx:
    """Raises IOError if the file cannot be read, datamodel.AttributeError if malformed."""
    dm = datamodel.load(filepath)

    parsed = ParsedDmx(
        dm=dm,
        root=dm.root,
        DmeModel=dm.root["skeleton"],
        transforms=None,
        format_ver=dm.format_ver,
        keywords=getDmxKeywords(dm.format_ver),
        upAxis=upAxis or 'Z',
    )

    DmeModel = parsed.DmeModel
    if DmeModel.get("baseStates") and len(DmeModel["baseStates"]) > 0:
        parsed.transforms = DmeModel["baseStates"][0]["transforms"]

    if dm.format_ver >= 22 and any(
            elem for elem in dm.elements
            if elem.type == "DmeVertexDeltaData" and '__' in elem.name):
        parsed.corrective_separator = '__'
        parsed.version_bumps.append(dmx_version(9, 22, compiler=Compiler.MODELDOC))

    if smd_type:
        parsed.jobType = smd_type
    elif dm.root.get("model"):
        parsed.jobType = REF
    elif dm.root.get("animationList") or dm.root.get("channels"):
        parsed.jobType = ANIM
    else:
        parsed.jobType = REF

    DmeAxisSystem = DmeModel.get("axisSystem")
    if DmeAxisSystem:
        for axis in axes_lookup.items():
            if axis[1] == DmeAxisSystem["upAxis"] - 1:
                parsed.upAxis = axis[0]
                break

    _fix_vrf_nmskel_axis(parsed)
    _fix_vrf_nmclip_axis(parsed)

    return parsed


# ---------------------------------------------------------------------------
# ValveResourceFormat NM (vskel / vnmclip) axis fixup
#
# VRF re-rolls root_motion by F - a 120 degree turn about (-1,-1,-1) - and compensates
# its children so nothing else moves. Both halves are broken:
#   - vskel:   the child compensation composes on the wrong side (`orientation * F`
#              instead of `F * orientation`), rotating every subtree under root_motion.
#   - vnmclip: the fixup lands on the animation frames but not on the skeleton they are
#              keyed against, so the animated pose comes out rotated by F.
# Both are undone here, which leaves the raw NM skeleton and keeps clips lined up with
# skeletons imported from the matching vskel.
# ---------------------------------------------------------------------------

_NM_FIXUP = Quaternion((0.5, -0.5, -0.5, -0.5))  # VRF's NmSkelRotationFixup
_NM_FIXUP_INV = _NM_FIXUP.inverted()


def _nm_unrotate(v):
    """F^-1 applied to a vector. F cycles the axes, so this is a swizzle."""
    return datamodel.Vector3([v[2], v[0], v[1]])


def _dmx_quat(q: Quaternion):
    return datamodel.Quaternion([q.x, q.y, q.z, q.w])


def _nm_root(parsed: ParsedDmx):
    """The root_motion joint of a ValveResourceFormat NM export, or None."""
    tags = parsed.root.get("exportTags")
    if not tags or "Source 2 Viewer" not in (tags.get("source") or ""):
        return None
    return next((c for c in cast(list, parsed.DmeModel.get("children") or [])
                 if c.name == "root_motion"), None)


def _fix_vrf_nmskel_axis(parsed: ParsedDmx) -> None:
    root = _nm_root(parsed)
    if not root:
        return
    q = root["transform"]["orientation"]
    if any(abs(q[i] - 0.5) > 1e-3 for i in range(4)):  # not the fixup quaternion
        return
    root["transform"]["orientation"] = _dmx_quat(blender_quat(q) @ _NM_FIXUP)
    for child in cast(list, root.get("children") or []):
        trfm = child["transform"]
        trfm["position"] = _nm_unrotate(trfm["position"])
        trfm["orientation"] = _dmx_quat(blender_quat(trfm["orientation"]) @ _NM_FIXUP_INV)
    print("- Removed ValveResourceFormat's broken root_motion axis fixup from this skeleton")


def _fix_vrf_nmclip_axis(parsed: ParsedDmx) -> None:
    root = _nm_root(parsed)
    anim_list = parsed.root.get("animationList")
    if not root or not anim_list:
        return

    binds = {c["transform"].id: c["transform"]["position"]
             for c in cast(list, root.get("children") or [])}
    channels = [ch for ch in anim_list["animations"][0]["channels"]
                if ch["toElement"] and ch["toElement"].id in binds]

    # Bone offsets barely move over an animation, so the first keyed position sits near
    # the bind position - unless the frames carry the fixup and the skeleton doesn't.
    def error(rotated: bool) -> float:
        total = 0.0
        for ch in channels:
            if ch["toAttribute"] != "position":
                continue
            values = ch["log"]["layers"][0]["values"]
            if not values:
                continue
            first = _nm_unrotate(values[0]) if rotated else values[0]
            bind = binds[ch["toElement"].id]
            total += sum((first[i] - bind[i]) ** 2 for i in range(3))
        return total

    raw = error(False)
    if raw < 1e-6 or error(True) * 4 > raw:
        return

    for ch in channels:
        values = ch["log"]["layers"][0]["values"]
        if ch["toAttribute"] == "position":
            values[:] = [_nm_unrotate(v) for v in values]
        elif ch["toAttribute"] == "orientation":
            values[:] = [_dmx_quat(_NM_FIXUP_INV @ blender_quat(v)) for v in values]
    print("- Removed ValveResourceFormat's root_motion axis fixup from this animation")


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------

def blender_quat(datamodel_quat) -> Quaternion:
    return Quaternion([datamodel_quat[3], datamodel_quat[0],
                       datamodel_quat[1], datamodel_quat[2]])


def transform_matrix(elem, transforms) -> Matrix:
    out = Matrix()
    if not elem:
        return out
    trfm = elem.get("transform")
    if transforms:
        for e in transforms:
            if e.name == elem.name:
                trfm = e
    if not trfm:
        return out
    out @= Matrix.Translation(Vector(trfm["position"]))
    out @= blender_quat(trfm["orientation"]).to_matrix().to_4x4()
    return out


# DmeQuatInterpBone (TRIGGER) and DmeAimAtBone (LOOKAT) are the procedural-bone joint
# types the DME exporter promotes helper joints to. They are DmeJoint subclasses, so
# they must count as bones or their joints (and any children) are skipped on import.
BONE_TYPES = ("DmeDag", "DmeJoint", "DmeJiggleBone", "DmeQuatInterpBone", "DmeAimAtBone")
JOINT_TYPES = ("DmeJoint", "DmeJiggleBone", "DmeQuatInterpBone", "DmeAimAtBone")


def is_bone(elem) -> bool:
    return elem.type in BONE_TYPES


def enumerate_bones_and_attachments(elem):
    """Yields (element, parent_element). Parent is None at the root."""
    parent = elem if is_bone(elem) else None
    for child in cast(list, elem.get("children") or []):
        if child.type == "DmeDag" and child.get("shape") and child["shape"].type == "DmeAttachment":
            yield (cast(Any, child["shape"]), parent)
        elif is_bone(child) and child.name != implicit_bone_name:
            boneShape = child.get("shape")
            if not boneShape or boneShape.get("currentState") is None:
                yield (child, parent)
            yield from enumerate_bones_and_attachments(child)
        elif child.type == "DmeModel":
            yield from enumerate_bones_and_attachments(child)


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

def read_skeleton(parsed: ParsedDmx) -> ImportedSkeleton:
    skel = ImportedSkeleton()
    bone_index_by_elem: dict[Any, int] = {}

    for (elem, parent) in enumerate_bones_and_attachments(parsed.DmeModel):
        if elem.name is None:
            continue
        parent_index = bone_index_by_elem.get(parent.id) if parent else None
        matrix = transform_matrix(elem, parsed.transforms)

        if elem.type == "DmeAttachment":
            skel.attachments.append(ImportedAttachment(
                name=elem.name, parent=parent_index, matrix=matrix, element=elem))
        else:
            bone_index_by_elem[elem.id] = len(skel.bones)
            skel.bones.append(ImportedBone(
                name=elem.name,
                parent=parent_index,
                matrix=matrix,
                source_id=elem.id,
                transform_id=elem["transform"].id,
                element=elem,
            ))

    return skel


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

_CLOTH_STREAM_SUFFIX = re.compile(r"\$[0-9]+$")
_CLOTH_COLLISION_LAYER = re.compile(r"cloth_collision_layer_([0-9]|1[0-5])$")


def _cloth_map_base(name: str) -> str | None:
    """Vertex group name for a cloth vertex stream ('cloth_mass$0' -> 'cloth_mass'),
    or None when the stream isn't a cloth map. Mirrors findDmxClothVertexGroups."""
    base = _CLOTH_STREAM_SUFFIX.sub("", name)
    if (base in vertex_float_maps
            or base.startswith("cloth_vertex_set_")
            or _CLOTH_COLLISION_LAYER.match(base)):
        return base
    return None


def _classify_vertex_map(values) -> str | None:
    """Returns an ImportedLoopLayer kind, or None if unsupported."""
    sample = values[0]
    if isinstance(sample, float):
        return 'FLOAT'
    if isinstance(sample, int):
        return 'INT'
    if isinstance(sample, str):
        return 'STRING'
    if isinstance(sample, datamodel.Vector2):
        return 'UV'
    if isinstance(sample, (datamodel.Vector4, datamodel.Color)):
        return 'COLOR'
    return None


def read_meshes(parsed: ParsedDmx) -> list[ImportedMesh]:
    """Walks the DmeModel tree and returns every DmeMesh it contains."""
    meshes: list[ImportedMesh] = []

    def walk(elem, matrix=Matrix(), last_bone=None):
        if elem.type in ("DmeModel",) + BONE_TYPES:
            if elem.type == "DmeDag":
                matrix = matrix @ transform_matrix(elem, parsed.transforms)
            if elem.get("children") and elem["children"]:
                if elem.type in JOINT_TYPES:
                    last_bone = elem
                subelems = elem["children"]
            elif elem.get("shape"):
                subelems = [elem["shape"]]
            else:
                return
            for subelem in subelems:
                walk(subelem, matrix, last_bone)
        elif elem.type == "DmeMesh":
            meshes.append(_read_mesh(parsed, elem, matrix, last_bone))

    walk(parsed.DmeModel)
    return meshes


def _read_mesh(parsed: ParsedDmx, DmeMesh, matrix: Matrix, last_bone) -> ImportedMesh:
    keywords = parsed.keywords
    DmeVertexData = DmeMesh["currentState"]
    vertex_format = DmeVertexData["vertexFormat"]

    mesh = ImportedMesh(name=DmeMesh.name, matrix=matrix)
    mesh.positions = DmeVertexData[keywords['pos']]
    mesh.position_indices = DmeVertexData[keywords['pos'] + "Indices"]
    mesh.has_weightmap = keywords["weight"] in vertex_format
    # VRF decompiles write flipVCoordinates=False for engine (top-down) V.
    # True/absent means Blender-oriented V that reads raw.
    flip_v = DmeVertexData.get("flipVCoordinates") is False

    if last_bone is not None and not mesh.has_weightmap:
        mesh.parent_bone = last_bone.name

    # Normals always come first; the build phase relies on finding a NORMAL layer.
    mesh.loop_layers.append(ImportedLoopLayer(
        name="__bst_normal",
        kind='NORMAL',
        values=DmeVertexData[keywords['norm']],
        indices=DmeVertexData[keywords['norm'] + "Indices"],
    ))

    for vertexMap in [p for p in vertex_format if p not in keywords.values()]:
        indices = DmeVertexData.get(vertexMap + "Indices")
        if not indices:
            continue
        values = DmeVertexData.get(vertexMap)
        if not isinstance(values, list) or len(values) == 0:
            continue
        if isinstance(values[0], float) and _cloth_map_base(vertexMap):
            continue  # imported as vertex groups instead

        kind = _classify_vertex_map(values)
        if kind == 'UV' and flip_v:
            values = [datamodel.Vector2((uv[0], 1.0 - uv[1])) for uv in values]
        if kind is None:
            parsed.warnings.append(
                f"Could not import vertex data '{vertexMap}'; "
                f"unsupported type {type(values[0]).__name__}")
            continue

        # The primary Source 2 colour stream (color$0) maps back to Blender's default
        # "Color" attribute, mirroring the export naming so the layer round-trips.
        layer_name = vertexMap
        if kind == 'COLOR' and vertexMap.lower() == "color$0":
            layer_name = "Color"

        mesh.loop_layers.append(ImportedLoopLayer(
            name=layer_name, kind=kind, values=values, indices=indices))

        if vertexMap != "textureCoordinates":
            parsed.version_bumps.append(dmx_version(9, 22, compiler=Compiler.RESOURCECOMPILER))

    _read_weights(parsed, DmeVertexData, mesh)
    _read_faces(DmeMesh, mesh)

    for stream in vertex_format:
        base = _cloth_map_base(stream)
        if base is None:
            continue
        mesh.cloth_groups.append((
            base,
            DmeVertexData.get(stream),
            DmeVertexData.get(stream + "Indices"),
        ))
        parsed.version_bumps.append(dmx_version(9, 22, compiler=Compiler.RESOURCECOMPILER))  # cloth streams are Source 2 only

    if keywords['balance'] in vertex_format:
        mesh.balance = (DmeVertexData[keywords['balance']],
                        DmeVertexData[keywords['balance'] + "Indices"])

    if DmeMesh.get("deltaStates"):
        for delta in DmeMesh["deltaStates"]:
            shape = ImportedShape(name=delta.name, indices=[], offsets=[])
            if keywords['pos'] in delta["vertexFormat"]:
                shape.indices = delta[keywords['pos'] + "Indices"]
                shape.offsets = delta[keywords['pos']]
            mesh.shapes.append(shape)

    return mesh


def _read_weights(parsed: ParsedDmx, DmeVertexData, mesh: ImportedMesh) -> None:
    if not mesh.has_weightmap:
        return

    keywords = parsed.keywords
    weighted_bone_indices = ordered_set.OrderedSet()
    jointWeights = DmeVertexData[keywords["weight"]]
    jointIndices = DmeVertexData[keywords["weight_indices"]]
    jointCount = DmeVertexData["jointCount"]

    joint_index = 0
    for _ in range(len(mesh.positions)):
        vert_weights = []
        for _i in range(jointCount):
            weight = jointWeights[joint_index]
            if weight > 0:
                vg_index = weighted_bone_indices.add(jointIndices[joint_index])
                vert_weights.append((vg_index, weight))
            joint_index += 1
        mesh.weights.append(vert_weights)

    # Resolve joint index -> name. jointList may be absent for armature-less Source 2 DMXs.
    joints_list = None
    try:
        key = "jointList" if parsed.format_ver >= 11 else "jointTransforms"
        joints_list = parsed.DmeModel.get(key)
    except Exception:
        pass

    for jidx in weighted_bone_indices:
        jname = None
        try:
            if joints_list:
                jname = joints_list[jidx].name or None
        except (IndexError, KeyError, TypeError):
            pass
        mesh.group_names.append(jname if jname else f"joint_{jidx}")


def _read_faces(DmeMesh, mesh: ImportedMesh) -> None:
    for face_set in DmeMesh["faceSets"]:
        set_index = len(mesh.materials)
        mesh.materials.append(face_set["material"]["mtlName"])

        face_loops: list[int] = []
        for vert in face_set["faces"]:
            if vert != -1:
                face_loops.append(vert)
                continue
            mesh.faces.append(ImportedFace(loops=list(face_loops), face_set=set_index))
            face_loops.clear()


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

def read_anim(parsed: ParsedDmx) -> ImportedAnim | None:
    anim_list = parsed.root.get("animationList")
    if anim_list is not None:
        animation = anim_list["animations"][0]
    elif parsed.root.get("channels") is not None:
        animation = parsed.root
    else:
        return None

    timeFrame = animation["timeFrame"]
    duration = timeFrame.get("duration") or timeFrame.get("durationTime")
    offset = timeFrame.get("offset") or timeFrame.get("offsetTime", 0.0)

    if type(duration) == int:
        duration = datamodel.Time.from_int(duration)
    if type(offset) == int:
        offset = datamodel.Time.from_int(offset)

    out = ImportedAnim(
        frame_rate=animation.get("frameRate", 30),
        start=timeFrame.get("start", 0),
        duration=duration,
    )

    for channel in animation["channels"]:
        toElement = channel["toElement"]
        if not toElement:
            continue
        attribute = channel["toAttribute"]
        if attribute not in ("position", "orientation", "scale"):
            continue

        frame_log = channel["log"]["layers"][0]
        out.channels.append(ImportedChannel(
            transform_id=toElement.id,
            name_hint=toElement.name,
            attribute=attribute,
            times=frame_log["times"],
            values=frame_log["values"],
        ))

    return out


def read_file(parsed: ParsedDmx) -> ImportedFile:
    """Everything the file yields, in one pass. Skeleton is always read; meshes only
    for REF/PHYS and animation only for ANIM, matching readDMX."""
    out = ImportedFile(
        jobName='',
        jobType=parsed.jobType,
        upAxis=parsed.upAxis,
        format_ver=parsed.format_ver,
        corrective_separator=parsed.corrective_separator,
        skeleton=read_skeleton(parsed),
    )
    if parsed.jobType in (REF, PHYS):
        out.meshes = read_meshes(parsed)
    elif parsed.jobType == ANIM:
        out.anim = read_anim(parsed)
    return out
