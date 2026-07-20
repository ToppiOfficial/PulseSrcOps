"""SMD / VTA text -> IR.

Unlike dmx.py this module is not a pure parser. SMD is a streaming text format and
weight resolution depends on the target armature: vertex groups are created for every
bone on `smd.a` (which may be a pre-existing armature with bones the file never
mentions), and a triangle's weights reference bone IDs that only mean something once
the node block has been reconciled against it. So the node block must be built before
the triangle block can be read, exactly as the original reader did.

What this module does guarantee is that mesh construction goes through
build.build_mesh, so SMD and DMX share one bmesh path.
"""

import os
from dataclasses import dataclass, field

import bpy
from bpy.app.translations import pgettext
from mathutils import Matrix, Euler, Vector

from ..utils import (REF, ANIM, PHYS, FLEX, get_id, getUpAxisMat, hasShapes,
                     removeObject, shape_types, smdBreak, smdContinue)
from .records import ImportedFace, ImportedLoopLayer, ImportedMesh


@dataclass
class SmdNode:
    id: int
    name: str
    parent: int


@dataclass
class ParsedFrames:
    """Raw skeleton-block data, keyed by SMD bone id."""
    frames: dict = field(default_factory=dict)  # bone id -> [(frame, Matrix)]
    num_frames: int = 0


# ---------------------------------------------------------------------------
# Lexing
# ---------------------------------------------------------------------------

def parse_quote_blocked_line(line, qc=None, lower=True):
    if len(line) == 0:
        return ["\n"]

    words = []
    last_word_start = 0
    in_quote = False

    if line[-1] != "\n":
        line += "\n"

    for i in range(len(line)):
        char = line[i]
        nchar = line[i + 1] if i < len(line) - 1 else None
        pchar = line[i - 1] if i > 0 else None

        if not in_quote and ((char == "/" and nchar == "/") or char in ['#', ';']):
            if i > 0:
                i = i - 1
            break

        if qc:
            if qc.in_block_comment:
                if char == "/" and pchar == "*":
                    qc.in_block_comment = False
                continue
            elif char == "/" and nchar == "*":
                qc.in_block_comment = True
                continue

        if char == "\"" and pchar != "\\":
            in_quote = not in_quote
        if not in_quote:
            if char in [" ", "\t"]:
                cur_word = line[last_word_start:i].strip("\"")
                if len(cur_word) > 0:
                    if (lower and os.name == 'nt') or cur_word[0] == "$":
                        cur_word = cur_word.lower()
                    words.append(cur_word)
                last_word_start = i + 1

    needBracket = False
    cur_word = line[last_word_start:i]
    if cur_word.endswith("{"):
        needBracket = True
    cur_word = cur_word.strip("\"{")
    if len(cur_word) > 0:
        words.append(cur_word)
    if needBracket:
        words.append("{")
    if line.endswith("\\\\\n") and (len(words) == 0 or words[-1] != "\\\\"):
        words.append("\\\\")
    return words


def scan_smd(smd) -> None:
    """Determines jobType by looking ahead for a section header, then rewinds."""
    for line in smd.file:
        if line == "triangles\n":
            smd.jobType = REF
            print("- This is a mesh")
            break
        if line == "vertexanimation\n":
            print("- This is a flex animation library")
            smd.jobType = FLEX
            break
    if smd.jobType is None:
        print("- This is a skeletal animation or pose")
        smd.jobType = ANIM
    smd.file.seek(0, 0)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def read_nodes(smd, qc=None) -> list[SmdNode]:
    nodes: list[SmdNode] = []
    for line in smd.file:
        if smdBreak(line):
            break
        if smdContinue(line):
            continue
        id, name, parent = parse_quote_blocked_line(line, qc, lower=False)[:3]
        nodes.append(SmdNode(id=int(id), name=name, parent=int(parent)))
    return nodes


# ---------------------------------------------------------------------------
# Skeleton block
# ---------------------------------------------------------------------------

def read_frames(ctx, smd, qc=None) -> ParsedFrames | None:
    """Reads the skeleton block into per-bone-id matrices, or None when the block
    carries no pose to apply. Shape names are harvested from the comment on each
    `time` line when this is a VTA."""
    if smd.jobType not in [REF, ANIM]:
        for line in smd.file:
            line = line.strip()
            if smdBreak(line):
                return None
            if smd.jobType == FLEX and line.startswith("time"):
                smd.shapeNames = smd.shapeNames or {}
                for c in line:
                    if c in ['#', ';', '/']:
                        pos = line.index(c)
                        frame = line[:pos].split()[1]
                        if c == '/':
                            pos += 1
                        smd.shapeNames[frame] = line[pos + 1:].strip()

    out = ParsedFrames()
    for line in smd.file:
        if smdBreak(line):
            break
        if smdContinue(line):
            continue

        values = line.split()
        if values[0] == "time":
            if out.num_frames > 0 and smd.jobType == REF:
                ctx.warning(get_id("importer_err_refanim", True).format(smd.jobName))
                for line in smd.file:
                    if smdBreak(line):
                        break
                    if smdContinue(line):
                        continue
            out.num_frames += 1
            continue

        pos = Vector([float(values[1]), float(values[2]), float(values[3])])
        rot = Euler([float(values[4]), float(values[5]), float(values[6])])
        matrix = Matrix.Translation(pos) @ rot.to_matrix().to_4x4()

        out.frames.setdefault(int(values[0]), []).append((out.num_frames - 1, matrix))

    return out


# ---------------------------------------------------------------------------
# Triangles
# ---------------------------------------------------------------------------

def read_polys(ctx, smd, group_names: list[str], qc=None) -> ImportedMesh | None:
    """Reads the triangle block into an ImportedMesh.

    `group_names` is the vertex-group list in armature bone order - SMD creates a
    group for every bone, weighted or not, which is why it is passed in rather than
    derived from the weights.
    """
    if smd.jobType not in [REF, PHYS]:
        return None

    mesh_name = smd.jobName
    if smd.jobType == REF and "reference" not in smd.jobName.lower() and not smd.jobName.lower().endswith("ref"):
        mesh_name += " ref"

    mesh = ImportedMesh(name=mesh_name)
    mesh.has_weightmap = True
    mesh.group_names = list(group_names)
    # SMD applies the up-axis correction to mesh data rather than the object.
    # readPolys only ever handled Y (hardcoded rx90), so an X-up SMD came in unrotated
    # while its bones and any VTA - both of which go through getUpAxisMat - did not.
    # getUpAxisMat('Y') is rx90 and getUpAxisMat('Z') is identity, so Y and Z are
    # unchanged by using it here.
    mesh.data_transform = getUpAxisMat(smd.upAxis)
    # A duplicate face is resolved by giving it its own vertices, not by dropping it
    mesh.split_duplicate_faces = True
    mesh.materials_are_paths = False

    group_index = {name: i for i, name in enumerate(group_names)}
    normals: list = []
    uvs: list = []
    vert_map: dict = {}
    bad_weights = 0
    count_polys = 0

    for line in smd.file:
        line = line.rstrip("\n")
        if line and smdBreak(line):
            break
        if smdContinue(line):
            continue

        mat_path = line if line else pgettext(get_id("importer_name_nomat", data=True))
        face_set = _face_set_for(mesh, mat_path)

        vertex_count = 0
        face_loops: list[int] = []
        for line in smd.file:
            if smdBreak(line):
                break
            if smdContinue(line):
                continue
            values = line.split()

            vertex_count += 1
            co = tuple(float(v) for v in values[1:4])
            normals.append(tuple(float(v) for v in values[4:7]))
            uvs.append((float(values[7]), float(values[8])))

            weights: list = []
            if len(values) > 10 and values[9] != "0":
                for i in range(10, 10 + (int(values[9]) * 2), 2):
                    name = smd.boneIDs.get(int(values[i]))
                    if name is None or name not in group_index:
                        bad_weights += 1
                        continue
                    weights.append((group_index[name], float(values[i + 1])))
            else:
                name = smd.boneIDs.get(int(values[0]))
                if name is None or name not in group_index:
                    bad_weights += 1
                else:
                    weights.append((group_index[name], 1.0))

            key = (co, tuple(weights))
            vert_index = vert_map.get(key)
            if vert_index is None:
                vert_index = len(mesh.positions)
                mesh.positions.append(co)
                mesh.weights.append(weights)
                vert_map[key] = vert_index

            face_loops.append(len(mesh.position_indices))
            mesh.position_indices.append(vert_index)

            if vertex_count == 3:
                mesh.faces.append(ImportedFace(loops=face_loops, face_set=face_set))
                count_polys += 1
                break

    mesh.loop_layers.append(ImportedLoopLayer(
        name="__bst_normal", kind='NORMAL',
        values=normals, indices=list(range(len(normals)))))
    mesh.loop_layers.append(ImportedLoopLayer(
        name="UVMap", kind='UV',
        values=uvs, indices=list(range(len(uvs)))))

    if bad_weights:
        ctx.warning(get_id("importer_err_badweights", True).format(bad_weights, smd.jobName))
    print(f"- Imported {count_polys} polys")

    return mesh


# ---------------------------------------------------------------------------
# VTA shapes
# ---------------------------------------------------------------------------

# Snap distance above which a VTA vertex is considered to belong to no imported mesh.
# Genuine members snap to distance ~0; the VTA and the SMD carry the same coordinates
# through the same up-axis matrix, so anything beyond this is a different bodypart.
_MATCH_TOLERANCE = 0.01


def read_shapes(ctx, smd) -> None:
    """Reads a VTA vertex-animation block into shape keys.

    Not routed through build_mesh: VTA carries no topology, only positions in an id
    space that belongs to no single mesh. A decompiled VTA is indexed against the whole
    model - its base frame lists every vertex in the model while the deltas stay sparse -
    so the base frame is matched against every imported reference mesh and each delta is
    applied to the mesh that owns that vertex.
    """
    if smd.jobType is not FLEX:
        return

    targets = _shape_targets(ctx, smd)
    if not targets:
        ctx.error(get_id("importer_err_shapetarget"))
        return

    smd.m = smd.m or targets[0]
    for ob in targets:
        if hasShapes(ob):
            ob.active_shape_key_index = 0
        ob.show_only_shape_key = True

    smd.vta_ref = None
    base_ids: list[int] = []
    base_cos: list[float] = []
    base_name = None
    pending_name = None
    co_map: dict = {}
    frame_keys: dict = {}
    touched: set = set()
    making_base_shape = True
    num_shapes = 0

    for line in smd.file:
        line = line.rstrip("\n")
        if smdBreak(line):
            break
        if smdContinue(line):
            continue

        values = line.split()

        if values[0] == "time":
            shape_name = smd.shapeNames.get(values[1])
            if base_name is None:
                base_name = shape_name or "Basis"
            elif making_base_shape:
                co_map = _match_vta(ctx, smd, targets, base_ids, base_cos)
                if co_map is None:
                    return
                making_base_shape = False
            if not making_base_shape:
                frame_keys = {}
                pending_name = shape_name or values[1]
                num_shapes += 1
            continue

        cur_id = int(values[0])
        vta_co = getUpAxisMat(smd.upAxis) @ Vector([float(values[1]), float(values[2]), float(values[3])])

        if making_base_shape:
            base_ids.append(cur_id)
            base_cos.extend(vta_co)
            continue

        entry = co_map.get(cur_id)
        if entry is None:
            continue
        ob, vert_index = entry
        key_block = frame_keys.get(ob)
        if key_block is None:
            # Created lazily so a frame only adds a shape key to the meshes it moves.
            if not hasShapes(ob, False):
                ob.shape_key_add(name=base_name)
            key_block = ob.shape_key_add(name=pending_name)
            key_block.value = 0.0
            frame_keys[ob] = key_block
            touched.add(ob)
        key_block.data[vert_index].co = vta_co

    print(f"- Imported {num_shapes} flex shapes across {len(touched)} mesh(es)")


def _shape_targets(ctx, smd) -> list:
    """Meshes a VTA may write into. Under a QC that is every reference mesh imported so
    far, because the VTA's ids span the whole model rather than one bodygroup."""
    if smd.m:
        return [smd.m]
    qc = getattr(ctx, 'qc', None)
    if qc:
        meshes = [m for m in qc.ref_meshes if m and m.type in shape_types]
        if meshes:
            return meshes
        return [qc.ref_mesh] if qc.ref_mesh else []
    active = bpy.context.active_object
    if active and active.type in shape_types:
        return [active]
    return [o for o in bpy.context.selected_objects if o.type in shape_types]


def _round_key(co):
    return (round(co.x, 3), round(co.y, 3), round(co.z, 3))


def _vertex_lookup(ob) -> dict:
    lookup: dict = {}
    for i, v in enumerate(ob.data.vertices):
        lookup.setdefault(_round_key(v.co), i)  # first wins, as list.index did
    return lookup


def _match_vta(ctx, smd, targets, ids, cos):
    """Map each base-frame VTA id onto (mesh, vertex index).

    Shrinkwrap NEAREST_VERTEX snaps every point to the closest vertex on its target, so
    the snap distance says whether the point belongs to that mesh at all. The original
    shrinkwrapped against a single mesh and accepted any snap, which silently mapped a
    body vertex onto whichever face vertex happened to be nearest. Taking the smallest
    snap distance across all candidate meshes gives each vertex its real owner.

    Returns None when nothing matched, having already reported the error.
    """
    count = len(ids)
    vd = bpy.data.meshes.new(name="VTA vertices")
    vd.vertices.add(count)
    vd.vertices.foreach_set("co", cos)
    ref = smd.vta_ref = bpy.data.objects.new(name=vd.name, object_data=vd)
    (smd.g if smd.g else bpy.context.scene.collection).objects.link(ref)
    err_group = ref.vertex_groups.new(name=get_id("importer_name_unmatchedvta"))

    origin = [Vector(cos[i * 3:i * 3 + 3]) for i in range(count)]
    best: list = [None] * count
    best_dist = [_MATCH_TOLERANCE] * count

    for ob in targets:
        lookup = _vertex_lookup(ob)
        ref.matrix_world = ob.matrix_world
        mod = ref.modifiers.new(name="VTA Shrinkwrap", type='SHRINKWRAP')
        mod.target = ob
        mod.wrap_method = 'NEAREST_VERTEX'
        bpy.context.view_layer.update()
        snapped = bpy.data.meshes.new_from_object(
            ref.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        ref.modifiers.remove(mod)

        for i, v in enumerate(snapped.vertices):
            dist = (v.co - origin[i]).length
            if dist >= best_dist[i]:
                continue
            index = lookup.get(_round_key(v.co))
            if index is not None:
                best_dist[i] = dist
                best[i] = (ob, index)

        bpy.data.meshes.remove(snapped)

    unmatched = [i for i in range(count) if best[i] is None]
    if unmatched:
        ratio = len(unmatched) / count
        err_group.add(unmatched, 1.0, 'REPLACE')
        message = get_id("importer_err_unmatched_mesh", True).format(
            len(unmatched), int(ratio * 100))
        if ratio == 1:
            ctx.error(message)
            return None
        ctx.warning(message)
    else:
        removeObject(ref)
        smd.vta_ref = None

    return {ids[i]: best[i] for i in range(count) if best[i] is not None}


def _face_set_for(mesh: ImportedMesh, mat_path: str) -> int:
    """SMD names a material per triangle, so face sets are deduped by path here
    rather than being given by the file the way DMX face sets are."""
    try:
        return mesh.materials.index(mat_path)
    except ValueError:
        mesh.materials.append(mat_path)
        return len(mesh.materials) - 1
