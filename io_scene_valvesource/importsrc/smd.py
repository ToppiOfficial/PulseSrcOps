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

from mathutils import Matrix, Euler, Vector

from ..utils import (REF, ANIM, PHYS, FLEX, KeyFrame, get_id, getUpAxisMat, rx90,
                     smdBreak, smdContinue)
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

def read_frames(ctx, smd, qc=None) -> ParsedFrames:
    """Reads the skeleton block into per-bone-id matrices. Shape names are harvested
    from the comment on each `time` line when this is a VTA."""
    if smd.jobType not in [REF, ANIM]:
        for line in smd.file:
            line = line.strip()
            if smdBreak(line):
                return ParsedFrames()
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
    # SMD applies the up-axis correction to mesh data rather than the object
    mesh.data_transform = rx90 if smd.upAxis == 'Y' else None
    # A duplicate face is resolved by giving it its own vertices, not by dropping it
    mesh.split_duplicate_faces = True

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

        mat_path = line if line else get_id("importer_name_nomat", data=True)
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


def _face_set_for(mesh: ImportedMesh, mat_path: str) -> int:
    """SMD names a material per triangle, so face sets are deduped by path here
    rather than being given by the file the way DMX face sets are."""
    try:
        return mesh.materials.index(mat_path)
    except ValueError:
        mesh.materials.append(mat_path)
        return len(mesh.materials) - 1
