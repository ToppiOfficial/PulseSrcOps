"""DMX -> IR. Parse only: this module builds no Blender data.

Extracted from readDMX. Everything here is a faithful port; behaviour
changes belong in the build half.
"""

from dataclasses import dataclass, field
from typing import Any, cast

from mathutils import Matrix, Vector, Quaternion

from .. import datamodel, ordered_set
from ..utils import (REF, ANIM, PHYS, axes_lookup, implicit_bone_name, dmx_version,
                     Compiler, getDmxKeywords)
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

    return parsed


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

def _is_cloth_enable_map(name: str) -> bool:
    return name.startswith("cloth_enable$")


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
        if isinstance(values[0], float) and _is_cloth_enable_map(vertexMap):
            continue  # imported as vertex groups instead

        kind = _classify_vertex_map(values)
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
            parsed.version_bumps.append(dmx_version(9, 22))

    _read_weights(parsed, DmeVertexData, mesh)
    _read_faces(DmeMesh, mesh)

    for cloth_name in [n for n in vertex_format if _is_cloth_enable_map(n)]:
        mesh.cloth_groups.append((
            cloth_name,
            DmeVertexData.get(cloth_name),
            DmeVertexData.get(cloth_name + "Indices"),
        ))

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
