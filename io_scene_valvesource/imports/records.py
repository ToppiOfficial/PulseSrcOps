from dataclasses import dataclass, field
from typing import Any

import bpy
from mathutils import Matrix


class ImportInfo:
    """Per-file import state. Replaces import's use of SmdInfo, which is shared with
    the exporter and carries export-only fields (amod, bakeInfo, dmxShapes)."""

    isDMX = 0  # version number, or 0 for SMD
    a: bpy.types.Object | None = None  # armature
    m: bpy.types.Object | None = None  # last mesh built
    g: bpy.types.Collection | None = None
    atch: bpy.types.Object | None = None
    jobType = None
    startTime = 0.0
    rotMode = 'EULER'
    layer = 0

    def __init__(self, jobName: str):
        self.jobName = jobName
        self.upAxis = bpy.context.scene.vs.up_axis
        # Source element id -> Blender bone name
        self.boneIDs: dict[Any, str] = {}
        # DmeTransform id -> Blender bone name; animation channels resolve through this
        self.boneTransformIDs: dict[Any, str] = {}


# ---------------------------------------------------------------------------
# Skeleton
# ---------------------------------------------------------------------------

@dataclass
class ImportedBone:
    name: str
    parent: int | None  # index into ImportedSkeleton.bones
    matrix: Matrix
    source_id: Any = None
    transform_id: Any = None
    # DmeJiggleBone / DmeQuatInterpBone / DmeAimAtBone are DmeJoint subclasses that
    # import as ordinary bones; the prefab pass re-reads them off `element`.
    element: Any = None


@dataclass
class ImportedAttachment:
    name: str
    parent: int | None
    matrix: Matrix
    element: Any = None


@dataclass
class ImportedSkeleton:
    bones: list[ImportedBone] = field(default_factory=list)
    attachments: list[ImportedAttachment] = field(default_factory=list)

    @property
    def has_bones(self) -> bool:
        return bool(self.bones)


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

@dataclass
class ImportedLoopLayer:
    """A per-loop data stream. `indices` maps loop index -> position in `values`."""
    name: str
    kind: str  # NORMAL | UV | COLOR | FLOAT | INT | STRING
    values: list
    indices: list

    @property
    def uneditable(self) -> bool:
        # Blender can hold these but offers no editing UI - the importer warns
        return self.kind in ('FLOAT', 'INT', 'STRING')


@dataclass
class ImportedFace:
    loops: list  # loop indices; resolve to verts via ImportedMesh.position_indices
    # Index into ImportedMesh.materials, which is per-face-set and may contain
    # duplicate paths. Resolving to a Blender material slot dedupes by name, so the
    # build phase maps face-set index -> slot index rather than using this directly.
    face_set: int


@dataclass
class ImportedShape:
    name: str
    indices: list
    offsets: list


@dataclass
class ImportedMesh:
    name: str
    positions: list = field(default_factory=list)
    position_indices: list = field(default_factory=list)  # loop -> vertex index
    faces: list[ImportedFace] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)  # material path per face set
    loop_layers: list[ImportedLoopLayer] = field(default_factory=list)

    # Per-vertex [(group index, weight)]; group index refers to `group_names`
    weights: list = field(default_factory=list)
    group_names: list[str] = field(default_factory=list)
    has_weightmap: bool = False

    # Weight-like streams that become vertex groups rather than loop layers
    cloth_groups: list = field(default_factory=list)  # [(name, values, indices)]
    balance: Any = None  # (values, indices) or None

    shapes: list[ImportedShape] = field(default_factory=list)

    matrix: Matrix = field(default_factory=Matrix)
    parent_bone: str | None = None

    # SMD gives a duplicate face its own vertices (readPolys createFace(use_cache=False));
    # DMX drops it.
    split_duplicate_faces: bool = False
    # DMX material entries are paths: the directory joins scene.vs.material_paths and the
    # basename names the material. SMD names a material per triangle and uses the whole
    # string verbatim.
    materials_are_paths: bool = True


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

@dataclass
class ImportedChannel:
    transform_id: Any
    name_hint: str  # toElement.name, for the "unrecognised bone" warning
    attribute: str  # position | orientation | scale
    times: list
    values: list


@dataclass
class ImportedAnim:
    """Raw channel data. KeyFrames are assembled in the build phase, not here:
    line 2186 branches on whether the resolved pose bone has a parent, which is a
    Blender fact the parser cannot know."""
    channels: list[ImportedChannel] = field(default_factory=list)
    frame_rate: float = 30.0
    start: float = 0.0
    duration: Any = None


@dataclass
class ImportedFile:
    """Everything one source file yields, before any of it touches Blender."""
    jobName: str
    jobType: Any = None
    upAxis: str = 'Z'
    format_ver: int = 0
    corrective_separator: str = '_'
    skeleton: ImportedSkeleton = field(default_factory=ImportedSkeleton)
    meshes: list[ImportedMesh] = field(default_factory=list)
    anim: ImportedAnim | None = None
