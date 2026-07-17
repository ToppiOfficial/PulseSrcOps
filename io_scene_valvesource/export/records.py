import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from .. import datamodel, ordered_set, flex


class BakedVertexAnimation(list):
    def __init__(self):
        super().__init__()
        self.export_sequence = False
        self.bone_id = -1
        self.num_frames = 0


class BakeResult:
    def __init__(self, name: str):
        self.name = name
        self.object: bpy.types.Object = None
        self.matrix = Matrix()
        self.envelope = None
        self.bone_parent_matrix = None
        self.src: bpy.types.Object = None
        self.armature: "BakeResult" = None
        self.balance_vg = None
        self.shapes = collections.OrderedDict()
        self.vertex_animations = collections.defaultdict(BakedVertexAnimation)


class ExportTask:
    def __init__(self, source_id, export_name: str, allowed_uids: set = None, companions: list = None):
        self.source_id = source_id
        self.export_name = export_name
        self.allowed_uids = allowed_uids if allowed_uids is not None else set()
        self.companions = companions if companions is not None else []

    def __repr__(self):
        return f"<ExportTask {self.export_name!r}>"


@dataclasses.dataclass
class _SplitPart:
    ob:       bpy.types.Object
    name:     str
    edgeline: typing.Optional[bpy.types.Object]
    backface: typing.Optional[bpy.types.Object]


@dataclasses.dataclass
class _MeshPlan:
    source:        bpy.types.Object
    target:        bpy.types.Object
    lod_source:    typing.Optional[bpy.types.Object]
    base_edgeline: typing.Optional[bpy.types.Object]
    base_backface: typing.Optional[bpy.types.Object]
    split_parts:   list
