"""QC / QCI -> Blender.

Like smd.py, this is a reader that touches Blender rather than a pure parser. QC
directives are order-dependent and stateful: `flex` / `flexcontroller` / `localvar` /
`%expr` only register once an earlier `$body` has produced `qc.ref_mesh`, `$hbox` and
`$sequence` resolve the armature mid-parse, `$upaxis` rewrites scene state for every
later import, and `$include` recurses immediately into shared accumulators. Parsing to
a job list would mean re-evaluating between jobs, which is the interpreter again.

Lexing does go through keyvalues1: `Cursor.block()` tracks `{ }` by position, replacing
the in_bodygroup / in_lod / in_sequence booleans and the num_words_to_skip counters.
A few directives still need the raw source line - tokens carry line numbers for that.
"""

import os
import re

import bpy
from bpy import ops
from mathutils import Vector
from math import pi

from .. import keyvalues1
from ..utils import (REF, ANIM, PHYS, FLEX, QcInfo, appendExt, get_id, getUpAxisMat,
                     hasShapes, printTimeMessage, import_jigglebones_from_content,
                     import_hitboxes_from_content, import_proc_bones_from_vrd_content)
from .build import find_armature
from .flexdata import apply_flex_text_to_object, populate_dme_flex_from_dmx
from .prefab import wants_prefab


# $sequence/$animation option keywords -> how many argument words follow.
_SEQUENCE_OPTIONS = {
    'hidden': 0, 'autolay': 0, 'realtime': 0, 'snap': 0, 'spline': 0,
    'xfade': 0, 'delta': 0, 'predelta': 0,
    'fadein': 1, 'fadeout': 1, 'addlayer': 1, 'blendwidth': 1, 'node': 1,
    'activity': 2, 'transision': 2, 'rtransition': 2,
    'blend': 3,
    'blendlayer': 5,
}


def _normalise_path(path: str) -> str:
    if os.path.sep == '/':
        path = path.replace('\\', '/')
    return os.path.normpath(path)


def _normalise_word(word: str, qc) -> str:
    """$var$ substitution, then the case/separator handling parseQuoteBlockedLine did:
    lowercase on Windows or for any $directive, and `/` -> `\\`."""
    for var in qc.vars.keys():
        kw = f"${var}$"
        pos = word.lower().find(kw)
        if pos != -1:
            word = word.replace(word[pos:pos + len(kw)], qc.vars[var])
    if word and (os.name == 'nt' or word[0] == "$"):
        word = word.lower()
    return word.replace("/", "\\")


class _Reader:
    """Holds the per-file cursor plus the raw lines, so a directive that needs its
    source line (localvar, %expr, $hbox, $definemacro) can reach it by line number."""

    def __init__(self, ctx, qc, filepath: str, text: str, rot_mode: str, do_anim: bool):
        self.ctx = ctx
        self.qc = qc
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.lines = text.splitlines()
        self.cur = keyvalues1.Cursor(keyvalues1.tokenize(text))
        self.rot_mode = rot_mode
        self.do_anim = do_anim
        self.lod = 0  # per-file, as the original's local `lod` was

    # -- source access -------------------------------------------------------
    def raw_line(self, line_no: int) -> str:
        return self.lines[line_no - 1] if 0 < line_no <= len(self.lines) else ""

    def word(self) -> str | None:
        """Next token, normalised. None at EOF or at a brace."""
        tok = self.cur.peek()
        if tok is None or (not tok.quoted and tok.text in '{}'):
            return None
        self.cur.next()
        return _normalise_word(tok.text, self.qc)

    def rest_of_line(self, line_no: int) -> list[str]:
        """Remaining normalised tokens on this source line, brace-terminated."""
        out = []
        while True:
            tok = self.cur.peek()
            if tok is None or tok.line != line_no or (not tok.quoted and tok.text in '{}'):
                break
            self.cur.next()
            out.append(_normalise_word(tok.text, self.qc))
        return out

    def skip_to_line_after(self, line_no: int) -> None:
        while True:
            tok = self.cur.peek()
            if tok is None or tok.line > line_no:
                break
            self.cur.next()

    # -- child imports -------------------------------------------------------
    def import_file(self, path_word: str, default_ext: str, smd_type,
                    append: str = 'APPEND', layer: int = 0) -> None:
        qc = self.qc
        path = os.path.join(qc.cd(), appendExt(_normalise_path(path_word), default_ext))
        if not os.path.exists(path):
            # QC conventionally omits the extension; fall back to DMX exactly once,
            # then import whatever that resolves to so a genuine miss still errors.
            path = os.path.join(qc.cd(), appendExt(_normalise_path(path_word), "dmx"))
        if path in qc.imported_smds:
            return
        qc.imported_smds.append(path)
        self.ctx.append = append if qc.a else 'NEW_ARMATURE'
        reader = self.ctx.readDMX if path.endswith("dmx") else self.ctx.readSMD
        self.ctx.num_files_imported += reader(
            path, qc.upAxis, self.rot_mode, False, smd_type, target_layer=layer)

    def pin_flex_target(self) -> None:
        qc = self.qc
        if qc.flex_target_mesh is None and qc.ref_mesh:
            qc.flex_target_mesh = qc.ref_mesh
            qc.flex_target_combo_op = qc.pending_combo_op

    def require_armature(self, warning: str | None = None) -> bool:
        qc = self.qc
        if not qc.a:
            qc.a = find_armature()
        if not qc.a and warning:
            self.ctx.warning(warning)
        return bool(qc.a)


def read_qc(ctx, filepath: str, newscene: bool, do_anim: bool, make_camera: bool,
            rot_mode: str, outer_qc: bool = False) -> int:
    filename = os.path.basename(filepath)
    filedir = os.path.dirname(filepath)

    if outer_qc:
        print(f"\nQC IMPORTER: now working on {filename}")
        qc = ctx.qc = QcInfo()
        qc.startTime = __import__('time').time()
        qc.jobName = filename
        qc.root_filedir = filedir
        qc.makeCamera = make_camera
        qc.animation_names = []
        qc.flex_controllers_pending = []
        qc.localvars_pending = []
        qc.expressions_pending = []
        qc.stereo_flex_names_pending = set()
        qc.flex_target_mesh = None
        qc.flex_target_combo_op = None
        if newscene:
            bpy.context.screen.scene = bpy.data.scenes.new(filename)
        elif filename.lower().endswith('.qc'):
            bpy.context.scene.name = filename
    else:
        qc = ctx.qc

    if filepath.lower().endswith(('.vmdl', '.vmdl_prefab')):
        from .vmdl import read_vmdl
        return read_vmdl(ctx, filepath, qc, rot_mode)

    try:
        with open(filepath, 'r') as f:
            text = f.read()
    except IOError:
        text = ""

    if text and '$jigglebone' in text.lower() and wants_prefab(ctx, 'JIGGLEBONES'):
        if not qc.a:
            qc.a = find_armature()
        if qc.a:
            imported_count, missing_bones = import_jigglebones_from_content(text, qc.a)
            if imported_count > 0:
                ctx.imported_jigglebones += imported_count
                print(f"- Imported {imported_count} jigglebone(s) from {filename}")
            if missing_bones:
                ctx.warning(f"Could not find bones for {len(missing_bones)} jigglebone(s) "
                            f"in {filename}: {', '.join(missing_bones)}")

    r = _Reader(ctx, qc, filepath, text, rot_mode, do_anim)
    _run(r)

    if qc.origin:
        qc.origin.parent = qc.a
        if qc.ref_mesh:
            size = min(qc.ref_mesh.dimensions) / 15
            if qc.makeCamera:
                qc.origin.data.display_size = size
            else:
                qc.origin.empty_display_size = size

    if outer_qc:
        _flush_flex(ctx, qc)
        printTimeMessage(qc.startTime, filename, "import", "QC")
    return ctx.num_files_imported


def _run(r: _Reader) -> None:
    ctx, qc, cur = r.ctx, r.qc, r.cur

    while not cur.eof():
        tok = cur.next()
        if tok.quoted or tok.text in '{}':
            continue
        line_no = tok.line
        kw = _normalise_word(tok.text, qc)

        if kw == "$definemacro":
            ctx.warning(get_id("importer_qc_macroskip", True).format(r.filename))
            last = line_no
            while r.raw_line(last).rstrip().endswith("\\\\"):
                last += 1
            r.skip_to_line_after(last)
            continue

        if kw == "$definevariable":
            words = r.rest_of_line(line_no)
            if len(words) >= 2:
                qc.vars[words[0]] = words[1].lower()
            continue

        if kw == "$pushd":
            words = r.rest_of_line(line_no)
            if words:
                path = words[0]
                if path[-1] != "\\":
                    path += "\\"
                qc.dir_stack.append(path)
            continue

        if kw == "$popd":
            try:
                qc.dir_stack.pop()
            except IndexError:
                pass
            continue

        if kw == "$upaxis":
            words = r.rest_of_line(line_no)
            if words:
                qc.upAxis = bpy.context.scene.vs.up_axis = words[0].upper()
                qc.upAxisMat = getUpAxisMat(words[0])
            continue

        if kw == "$hboxset":
            words = r.rest_of_line(line_no)
            if words:
                new_set = words[0].strip('"')
                if not qc.hboxset_name:
                    qc.hboxset_name = new_set
                elif qc.hboxset_name != new_set:
                    ctx.warning(f"Multiple $hboxset values found; using first "
                                f"(\"{qc.hboxset_name}\"), ignoring \"{new_set}\"")
            continue

        if kw == "$hbox":
            r.rest_of_line(line_no)
            if not wants_prefab(ctx, 'HITBOXES'):
                continue
            if not r.require_armature(get_id("qc_warn_noarmature_hbox", True).format(r.filename)):
                continue
            prev_pose_position = qc.a.data.pose_position
            qc.a.data.pose_position = 'REST'
            bpy.context.view_layer.update()
            created, skipped, bones = import_hitboxes_from_content(
                r.raw_line(line_no), qc.a, bpy.context, ctx.createCollections,
                hboxset_name=qc.hboxset_name)
            qc.a.data.pose_position = prev_pose_position
            bpy.context.view_layer.update()
            if created > 0:
                ctx.imported_hitboxes += created
                print(f"- Imported {created} hitbox(es) from QC")
            if skipped > 0:
                print(f"  Warning: Skipped {skipped} hitbox(es) with missing bones: "
                      f"{', '.join(bones)}")
            continue

        if kw in ("$proceduralbones", "$procbones"):
            words = r.rest_of_line(line_no)
            if not words or not wants_prefab(ctx, 'PROCEDURAL'):
                continue
            if not r.require_armature(f"$proceduralbones in {r.filename} but no armature to bind to"):
                continue
            _import_vrd(r, words[0])
            continue

        if kw in ("$body", "$model"):
            words = r.rest_of_line(line_no)
            if len(words) >= 2:
                r.import_file(words[1], "smd", REF)
            continue

        if kw == "$lod":
            _read_lod(r, line_no)
            continue

        if kw == "$bodygroup":
            _read_bodygroup(r, line_no)
            continue

        if kw in ("$sequence", "$animation"):
            _read_sequence(r, line_no, kw)
            continue

        if kw == "flexfile":
            words = r.rest_of_line(line_no)
            if words:
                r.import_file(words[0], "vta", FLEX, 'VALIDATE')
            continue

        if kw in ("flex", "flexpair") and qc.ref_mesh:
            words = r.rest_of_line(line_no)
            for i, w in enumerate(words):
                if w == "frame" and i + 1 < len(words):
                    shape = qc.ref_mesh.data.shape_keys.key_blocks.get(words[i + 1])
                    if shape and shape.name.startswith("Key") and words:
                        shape.name = words[0]
                    break
            if kw == "flexpair" and words:
                qc.stereo_flex_names_pending.add(words[0])
            continue

        if kw == "flexcontroller" and qc.ref_mesh:
            words = r.rest_of_line(line_no)
            if len(words) < 2:
                continue
            try:
                fc_type = words[0]
                if len(words) >= 4 and words[1] == "range":
                    flex_min, flex_max = float(words[2]), float(words[3])
                    names = words[4:]
                else:
                    flex_min, flex_max = 0.0, 1.0
                    names = words[1:]
            except (ValueError, IndexError):
                continue
            if not names:
                continue
            r.pin_flex_target()
            for name in names:
                qc.flex_controllers_pending.append((name, fc_type, flex_min, flex_max))
            continue

        if kw == "localvar" and qc.ref_mesh:
            r.rest_of_line(line_no)
            r.pin_flex_target()
            m = re.match(r'(?i)localvar\s+(.+?)(?:\s*//.*)?$', r.raw_line(line_no).strip())
            if m:
                qc.localvars_pending.extend(m.group(1).split())
            continue

        if kw.startswith('%') and qc.ref_mesh:
            r.rest_of_line(line_no)
            m = re.match(r'^\s*%(\w+)\s*=\s*(.+?)(?:\s*//.*)?$', r.raw_line(line_no).rstrip())
            if m:
                r.pin_flex_target()
                qc.expressions_pending.append((m.group(1), m.group(2).strip()))
            continue

        if kw == "noautodmxrules":
            r.pin_flex_target()
            qc.no_auto_dmx_rules = True
            continue

        if kw in ("$collisionmodel", "$collisionjoints"):
            words = r.rest_of_line(line_no)
            if words:
                r.import_file(words[0], "smd", PHYS, 'VALIDATE', layer=10)
            continue

        if kw == "$origin":
            _make_origin(r, r.rest_of_line(line_no))
            continue

        if kw == "$include":
            words = r.rest_of_line(line_no)
            if not words:
                continue
            path = os.path.join(qc.root_filedir, _normalise_path(words[0]))
            if not path.endswith(".qc") and not path.endswith(".qci"):
                if os.path.exists(appendExt(path, ".qci")):
                    path = appendExt(path, ".qci")
                elif os.path.exists(appendExt(path, ".qc")):
                    path = appendExt(path, ".qc")
            try:
                read_qc(ctx, path, False, r.do_anim, qc.makeCamera, r.rot_mode)
            except IOError:
                ctx.warning(get_id("importer_err_qci", True).format(path))
            continue


# ---------------------------------------------------------------------------
# Block directives - nesting comes from Cursor.block(), not a boolean flag
# ---------------------------------------------------------------------------

def _read_lod(r: _Reader, line_no: int) -> None:
    """$lod <n> { replacemodel <a> <b> ... }"""
    r.rest_of_line(line_no)
    qc = r.qc
    r.lod += 1
    body = [t for t in r.cur.block()]
    i = 0
    while i < len(body):
        if not body[i].quoted and _normalise_word(body[i].text, qc) == "replacemodel":
            if i + 2 < len(body):
                r.import_file(_normalise_word(body[i + 2].text, qc), "smd", REF,
                              'VALIDATE', layer=r.lod)
            i += 3
            continue
        i += 1


def _read_bodygroup(r: _Reader, line_no: int) -> None:
    """$bodygroup <name> { studio <file> | blank ... }"""
    r.rest_of_line(line_no)
    qc = r.qc
    body = [t for t in r.cur.block()]
    i = 0
    while i < len(body):
        if not body[i].quoted and _normalise_word(body[i].text, qc) == "studio":
            if i + 1 < len(body):
                r.import_file(_normalise_word(body[i + 1].text, qc), "smd", REF)
            i += 2
            continue
        i += 1


def _read_sequence(r: _Reader, line_no: int, kw: str) -> None:
    """$sequence <name> [file] [options] , inline or braced.

    Replaces the num_words_to_skip walk. The first token on a line that is not a
    recognised option is the animation source; the rest of that line is dropped,
    which is what the original's `break` did.
    """
    qc = r.qc
    name_words = r.rest_of_line(line_no)
    seq_name = name_words[0] if name_words else ""

    # Inline remainder first, then any braced body - a sequence can use either.
    groups: list[list] = []
    if len(name_words) > 1:
        groups.append([(w, line_no) for w in name_words[1:]])
    body = [t for t in r.cur.block()]
    if body:
        by_line: dict[int, list] = {}
        for t in body:
            by_line.setdefault(t.line, []).append(_normalise_word(t.text, qc))
        for ln in sorted(by_line):
            groups.append([(w, ln) for w in by_line[ln]])

    if not r.do_anim:
        return

    for group in groups:
        i = 0
        while i < len(group):
            word = group[i][0]
            skip = _SEQUENCE_OPTIONS.get(word)
            if skip is not None:
                i += 1 + skip
                continue
            if not r.require_armature(get_id("qc_warn_noarmature", True).format(word)):
                return
            if word.lower() not in qc.animation_names:
                if not qc.a.animation_data:
                    qc.a.animation_data_create()
                r.import_file(word, "smd", ANIM, 'VALIDATE')
                if kw == "$animation" and seq_name:
                    qc.animation_names.append(seq_name.lower())
            break  # rest of this line is options for the file just taken


def _import_vrd(r: _Reader, path_word: str) -> None:
    ctx, qc = r.ctx, r.qc
    vrd_path = os.path.join(qc.cd(), _normalise_path(path_word))
    if not os.path.splitext(vrd_path)[1]:
        vrd_path = appendExt(vrd_path, "vrd")
    try:
        with open(vrd_path, 'r') as vf:
            vrd_content = vf.read()
    except IOError:
        ctx.warning(f"Could not read procedural bone file '{vrd_path}'")
        return
    prev_pose_position = qc.a.data.pose_position
    qc.a.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    pb_count, pb_missing = import_proc_bones_from_vrd_content(vrd_content, qc.a, bpy.context.scene)
    qc.a.data.pose_position = prev_pose_position
    bpy.context.view_layer.update()
    if pb_count > 0:
        ctx.imported_procbones += pb_count
        print(f"- Imported {pb_count} procedural bone(s) from {os.path.basename(vrd_path)}")
    if pb_missing:
        ctx.warning(f"Could not find bones for {len(pb_missing)} procedural entr(y/ies) "
                    f"in {os.path.basename(vrd_path)}: {', '.join(pb_missing)}")


def _make_origin(r: _Reader, words: list[str]) -> None:
    qc = r.qc
    if qc.makeCamera:
        data = bpy.data.cameras.new(qc.jobName + "_origin")
        name = "camera"
    else:
        data = None
        name = "empty object"
    print(f"QC IMPORTER: created {name} at $origin\n")

    origin = bpy.data.objects.new(qc.jobName + "_origin", data)
    bpy.context.scene.collection.objects.link(origin)
    origin.rotation_euler = Vector([pi / 2, 0, pi]) + Vector(getUpAxisMat(qc.upAxis).inverted().to_euler())
    ops.object.select_all(action="DESELECT")
    origin.select_set(True)
    ops.object.transform_apply(rotation=True)

    for i in range(min(3, len(words))):
        origin.location[i] = float(words[i])
    origin.matrix_world = getUpAxisMat(qc.upAxis) @ origin.matrix_world

    if qc.makeCamera:
        bpy.context.scene.camera = origin
        origin.data.lens_unit = 'DEGREES'
        origin.data.lens = 31.401752
        origin.data.shift_y = -0.27
        origin.data.shift_x = 0.36
        origin.data.passepartout_alpha = 1
    else:
        origin.empty_display_type = 'PLAIN_AXES'

    qc.origin = origin


def _flush_flex(ctx, qc) -> None:
    """Apply accumulated flex data from this file and any $include children. Flex
    controllers/rules are global model data, so they go onto every imported mesh that
    has shape keys - not just a single target mesh."""
    target_meshes = [m for m in qc.flex_meshes if m and hasShapes(m)]
    if qc.flex_target_mesh and qc.flex_target_mesh not in target_meshes:
        if hasShapes(qc.flex_target_mesh) or not target_meshes:
            target_meshes.append(qc.flex_target_mesh)

    if not (target_meshes and (qc.flex_controllers_pending or qc.localvars_pending
                               or qc.expressions_pending or qc.no_auto_dmx_rules
                               or qc.flex_target_combo_op)):
        return

    for ob in target_meshes:
        if qc.no_auto_dmx_rules:
            ob.vs.dme_flexcontrollers.clear()
            ob.vs.dme_flex_rules.clear()
            # Rename stereo shape keys from the DMX combo op: a controller with stereo=True
            # and exactly one rawControlName means a single shape key that should use the
            # compound L+R naming convention (e.g. "AU15" = "AU15L+AU15R").
            # Controllers with two rawControlNames already have separate L/R shape keys.
            if (qc.flex_target_combo_op
                    and ob.data and hasattr(ob.data, 'shape_keys') and ob.data.shape_keys):
                _key_blocks = ob.data.shape_keys.key_blocks
                for _ctrl in qc.flex_target_combo_op.get("controls", []):
                    _raw = _ctrl.get("rawControlNames", [])
                    if bool(_ctrl.get("stereo", False)) and len(_raw) == 1:
                        _sk = _key_blocks.get(_raw[0])
                        if _sk:
                            _sk.name = f"{_raw[0]}L+{_raw[0]}R"
        elif qc.flex_target_combo_op:
            populate_dme_flex_from_dmx(ob, qc.flex_target_combo_op)

        apply_flex_text_to_object(ob, {
            'controllers': qc.flex_controllers_pending,
            'localvars': qc.localvars_pending,
            'expressions': qc.expressions_pending,
            'stereo_names': qc.stereo_flex_names_pending,
        })

        if ob.vs.dme_flexcontrollers:
            print(f"- Imported {len(ob.vs.dme_flexcontrollers)} flex controllers and "
                  f"{len(ob.vs.dme_flex_rules)} flex rules from QC/DMX into '{ob.name}'")
