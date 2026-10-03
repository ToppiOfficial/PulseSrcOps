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

import itertools
import os
from dataclasses import dataclass, field

import bpy
import numpy as np
from bpy.app.translations import pgettext
from mathutils import Matrix, Euler, Vector, kdtree

from ..utils import (REF, ANIM, PHYS, FLEX, get_id, hasShapes,
                     removeObject, shape_types, smdBreak, smdContinue, BenchMarker)
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
    # A duplicate face is resolved by giving it its own vertices, not by dropping it
    mesh.split_duplicate_faces = True
    mesh.materials_are_paths = False

    group_index = {name: i for i, name in enumerate(group_names)}
    normals: list = []
    uvs: list = []

    lines = []
    end_line = None
    for line in smd.file:
        if line.rstrip("\n") == "end":
            end_line = line
            break
        lines.append(line)

    if _is_plain_triangle_block(lines):
        count_polys, bad_weights = _read_plain_triangles(
            lines, smd, mesh, group_index, normals, uvs)
    else:
        rest = itertools.chain(lines, [end_line] if end_line else [], smd.file)
        count_polys, bad_weights = _read_triangle_lines(
            rest, smd, mesh, group_index, normals, uvs)

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


def _is_plain_triangle_block(lines) -> bool:
    """A material line then exactly three vertex lines per face, with no comments or
    blank vertex lines - the shape every exporter writes."""
    if len(lines) % 4:
        return False
    text = "".join(lines)
    if text.startswith("//") or "\n//" in text:
        return False
    if "\n\n" not in text and not text.startswith("\n"):
        return True
    for i, line in enumerate(lines):
        if line.startswith("//") or (i % 4 and line == "\n"):
            return False
    return True


def _read_plain_triangles(lines, smd, mesh, group_index, normals, uvs):
    nomat = pgettext(get_id("importer_name_nomat", data=True))
    face_sets: dict = {}
    vert_map: dict = {}
    # (parent id, weight tokens) -> (weights, bad link count); vertices repeat per face
    weight_cache: dict = {}
    bone_ids = smd.boneIDs
    positions = mesh.positions
    position_indices = mesh.position_indices
    faces = mesh.faces
    bad_weights = 0

    for f in range(0, len(lines), 4):
        mat_path = lines[f].rstrip("\n") or nomat
        face_set = face_sets.get(mat_path)
        if face_set is None:
            face_set = face_sets[mat_path] = _face_set_for(mesh, mat_path)

        first_loop = len(position_indices)
        for line in lines[f + 1:f + 4]:
            values = line.split()
            co = (float(values[1]), float(values[2]), float(values[3]))
            normals.append((float(values[4]), float(values[5]), float(values[6])))
            uvs.append((float(values[7]), float(values[8])))

            wkey = (values[0], tuple(values[9:]))
            cached = weight_cache.get(wkey)
            if cached is None:
                cached = weight_cache[wkey] = _parse_weights(values, bone_ids, group_index)
            weights, bad = cached
            bad_weights += bad

            key = (co, weights)
            vert_index = vert_map.get(key)
            if vert_index is None:
                vert_index = vert_map[key] = len(positions)
                positions.append(co)
                mesh.weights.append(list(weights))
            position_indices.append(vert_index)

        faces.append(ImportedFace(loops=[first_loop, first_loop + 1, first_loop + 2], face_set=face_set))

    return len(lines) // 4, bad_weights


def _parse_weights(values, bone_ids, group_index):
    weights = []
    bad = 0
    if len(values) > 10 and values[9] != "0":
        for i in range(10, 10 + (int(values[9]) * 2), 2):
            name = bone_ids.get(int(values[i]))
            if name is None or name not in group_index:
                bad += 1
                continue
            weights.append((group_index[name], float(values[i + 1])))
    else:
        name = bone_ids.get(int(values[0]))
        if name is None or name not in group_index:
            bad += 1
        else:
            weights.append((group_index[name], 1.0))
    return tuple(weights), bad


def _read_triangle_lines(lines, smd, mesh, group_index, normals, uvs):
    vert_map: dict = {}
    bad_weights = 0
    count_polys = 0

    for line in lines:
        line = line.rstrip("\n")
        if line and smdBreak(line):
            break
        if smdContinue(line):
            continue

        mat_path = line if line else pgettext(get_id("importer_name_nomat", data=True))
        face_set = _face_set_for(mesh, mat_path)

        vertex_count = 0
        face_loops: list[int] = []
        for line in lines:
            if smdBreak(line):
                break
            if smdContinue(line):
                continue
            values = line.split()

            vertex_count += 1
            co = tuple(float(v) for v in values[1:4])
            normals.append(tuple(float(v) for v in values[4:7]))
            uvs.append((float(values[7]), float(values[8])))

            weights, bad = _parse_weights(values, smd.boneIDs, group_index)
            bad_weights += bad

            key = (co, weights)
            vert_index = vert_map.get(key)
            if vert_index is None:
                vert_index = len(mesh.positions)
                mesh.positions.append(co)
                mesh.weights.append(list(weights))
                vert_map[key] = vert_index

            face_loops.append(len(mesh.position_indices))
            mesh.position_indices.append(vert_index)

            if vertex_count == 3:
                mesh.faces.append(ImportedFace(loops=face_loops, face_set=face_set))
                count_polys += 1
                break

    return count_polys, bad_weights


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
    base_cos: list = []
    base_name = None
    pending_name = None
    co_map: dict = {}
    frame_keys: dict = {}
    touched: set = set()
    making_base_shape = True
    num_shapes = 0
    axis_mat = smd.axisMat
    bench = BenchMarker(2)

    for header, lines in _read_vta_frames(smd.file):
        if header is not None:
            shape_name = smd.shapeNames.get(header[1])
            if base_name is None:
                base_name = shape_name or "Basis"
            elif making_base_shape:
                bench.report("base frame")
                cos = np.concatenate(base_cos) if base_cos else np.empty((0, 3), np.float32)
                co_map = (_map_vta_by_loop(targets, base_ids, cos)
                          or _match_vta(ctx, smd, targets, base_ids, cos))
                bench.report("match")
                if co_map is None:
                    return
                making_base_shape = False
            if not making_base_shape:
                frame_keys = {}
                pending_name = shape_name or header[1]
                num_shapes += 1

        ids, cos = _parse_vta_rows(lines, axis_mat)

        if making_base_shape:
            base_ids += ids
            base_cos.append(cos)
            continue

        for cur_id, co in zip(ids, cos.tolist()):
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
            key_block.data[vert_index].co = co

    bench.report(f"shapes ({num_shapes})")
    print(f"- Imported {num_shapes} flex shapes across {len(touched)} mesh(es)")


def _read_vta_frames(file) -> list:
    """[(split `time` line or None, [data lines])] up to the end of the block."""
    frames = [(None, [])]
    append = frames[-1][1].append
    # smdBreak / smdContinue inlined; they are per-line calls on 100k+ line blocks
    for line in file:
        line = line.rstrip("\n")
        if not line or line == "end":
            break
        if line.startswith("//"):
            continue
        if "time" in line:
            values = line.split()
            if values[0] == "time":
                frames.append((values, []))
                append = frames[-1][1].append
                continue
        append(line)
    return frames


def _parse_vta_rows(lines, axis_mat):
    rows = [line.split() for line in lines]
    ids = [int(r[0]) for r in rows]
    cos = np.array([(float(r[1]), float(r[2]), float(r[3])) for r in rows], np.float32).reshape(-1, 3)
    return ids, _transform_points(axis_mat, cos)


def _transform_points(mat, points):
    # Bit-exact with mathutils `mat @ Vector(p)`: float32 products summed in float64.
    m = np.array(mat, np.float32)
    out = np.empty_like(points)
    for row in range(3):
        dot = np.zeros(len(points))
        for col in range(3):
            dot += m[row, col] * points[:, col]
        dot += np.float64(m[row, 3])
        out[:, row] = dot
    return out


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


def _vertex_cos(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _map_vta_by_loop(targets, ids, cos) -> dict | None:
    """A VTA exported alongside its SMD lists one base vertex per SMD face corner, in
    file order, so id i is loop i of the imported mesh. Exact for vertices that share a
    position, which a nearest-vertex search cannot tell apart."""
    if len(targets) != 1 or not ids:
        return None
    me = targets[0].data
    if len(me.loops) != len(ids) or ids != list(range(len(ids))):
        return None
    loop_verts = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loop_verts)
    if np.abs(_vertex_cos(me)[loop_verts] - cos).max() >= _MATCH_TOLERANCE:
        return None  # same corner count, different mesh or edited since import
    ob = targets[0]
    return {i: (ob, v) for i, v in enumerate(loop_verts.tolist())}


def _match_vta(ctx, smd, targets, ids, cos):
    """Map each base-frame VTA id onto (mesh, vertex index).

    Each point goes to the nearest vertex across all candidate meshes, so a body vertex
    is never claimed by whichever face vertex happens to be closest within one mesh.

    Returns None when nothing matched, having already reported the error.
    """
    count = len(ids)
    best: list = [None] * count
    best_dist = [_MATCH_TOLERANCE] * count
    points = cos.tolist()

    for ob in targets:
        mesh_cos = _vertex_cos(ob.data).tolist()
        tree = kdtree.KDTree(len(mesh_cos))
        for i, co in enumerate(mesh_cos):
            tree.insert(co, i)
        tree.balance()
        find = tree.find
        for i, co in enumerate(points):
            _, index, dist = find(co)
            if index is not None and dist < best_dist[i]:
                best_dist[i] = dist
                best[i] = (ob, index)

    unmatched = [i for i in range(count) if best[i] is None]
    if unmatched:
        vd = bpy.data.meshes.new(name="VTA vertices")
        vd.vertices.add(count)
        vd.vertices.foreach_set("co", cos.ravel())
        ref = smd.vta_ref = bpy.data.objects.new(name=vd.name, object_data=vd)
        ref.matrix_world = targets[0].matrix_world
        (smd.g if smd.g else bpy.context.scene.collection).objects.link(ref)
        err_group = ref.vertex_groups.new(name=get_id("importer_name_unmatchedvta"))
        err_group.add(unmatched, 1.0, 'REPLACE')
        ratio = len(unmatched) / count
        message = get_id("importer_err_unmatched_mesh", True).format(
            len(unmatched), int(ratio * 100))
        if ratio == 1:
            ctx.error(message)
            return None
        ctx.warning(message)

    return {ids[i]: best[i] for i in range(count) if best[i] is not None}


def _face_set_for(mesh: ImportedMesh, mat_path: str) -> int:
    """SMD names a material per triangle, so face sets are deduped by path here
    rather than being given by the file the way DMX face sets are."""
    try:
        return mesh.materials.index(mat_path)
    except ValueError:
        mesh.materials.append(mat_path)
        return len(mesh.materials) - 1
