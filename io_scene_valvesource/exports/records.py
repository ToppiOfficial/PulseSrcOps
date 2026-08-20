import bpy, bmesh, collections, dataclasses, re, typing, os
from bpy import ops
from mathutils import Vector, Matrix, Euler
from math import *  # pyright: ignore
from bpy.types import Collection

from ..utils import *
from .. import datamodel, ordered_set, flex


def is_proxy_only(bake_results) -> bool:
    """True when every mesh in an export is a collision hull or cloth proxy. Such an
    export is not the reference model, so it carries no flexes and no DME prefab data
    (jigglebones, hitboxes, procedural bones). False for mesh-less exports (animations)."""
    mesh_types = [getattr(b.src.vs, 'mesh_type', 'DEFAULT')
                  for b in bake_results if b.src and b.object.type != 'ARMATURE']
    return bool(mesh_types) and all(mt in ('COLLISION', 'CLOTHPROXY') for mt in mesh_types)


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
        self.balance = None
        self.shapes = collections.OrderedDict()
        self.vertex_animations = collections.defaultdict(BakedVertexAnimation)

    # Per-vertex stereo balance for split flex deltas, cached on self.balance: the L delta is
    # weighted 1-balance, the R delta balance. Also latches balance_vg, which the writers read
    # as "this mesh has stereo data".
    def stereo_balance(self, ob, warn=None) -> list:
        balance = [0.0] * len(ob.data.vertices)
        self.balance = balance
        vs = getattr(getattr(self.src, "data", None), "vs", None)
        if not (self.shapes and vs):
            return balance

        if vs.flex_stereo_mode == 'VGROUP':
            vg_name = vs.flex_stereo_vg
            if not vg_name:
                if warn: warn(f"'{self.name}': stereo mode is VGROUP but no vertex group is specified")
                return balance
            self.balance_vg = ob.vertex_groups.get(vg_name)
            if self.balance_vg is None:
                if warn: warn(f"'{self.name}': stereo vertex group '{vg_name}' not found")
                return balance
            for v in ob.data.vertices:
                try:
                    balance[v.index] = self.balance_vg.weight(v.index)
                except RuntimeError:
                    pass  # vertex not in the balance group
        elif vs.flex_stereo_mode in axes_lookup:
            axis = axes_lookup[vs.flex_stereo_mode]
            width = ob.dimensions[axis] * (1 - (vs.flex_stereo_sharpness / 100))
            if width:
                for v in ob.data.vertices:
                    balance[v.index] = max(0.0, min(1.0, (-v.co[axis] / width / 2) + 0.5))
            self.balance_vg = True  # sentinel: no vertex group, balance[] is computed
        return balance


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
