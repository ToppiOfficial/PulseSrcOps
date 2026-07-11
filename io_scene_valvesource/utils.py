#  Copyright (c) 2014 Tom Edwards contact@steamreview.org
#
# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy, struct, time, collections, os, sys, builtins, itertools, dataclasses, typing, mathutils, re, math, bmesh
from typing import Optional, Any
from bpy.app.translations import pgettext
from contextlib import contextmanager
from bpy.app.handlers import depsgraph_update_post, load_post, persistent
from mathutils import Matrix, Vector
from math import radians, pi, ceil, floor
from io import TextIOWrapper
from . import datamodel
from . import keyvalues3
import numpy as np

intsize = struct.calcsize("i")
floatsize = struct.calcsize("f")

rx90 = Matrix.Rotation(radians(90),4,'X')
ry90 = Matrix.Rotation(radians(90),4,'Y')
rz90 = Matrix.Rotation(radians(90),4,'Z')
ryz90 = ry90 @ rz90

rx90n = Matrix.Rotation(radians(-90),4,'X')
ry90n = Matrix.Rotation(radians(-90),4,'Y')
rz90n = Matrix.Rotation(radians(-90),4,'Z')

epsilon = Vector([0.0001] * 3)

implicit_bone_name = "blender_implicit"

# SMD types
REF = 0x1 # $body, $model, $bodygroup->studio (if before a $body or $model), $bodygroup, $lod->replacemodel
PHYS = 0x3 # $collisionmesh, $collisionjoints
ANIM = 0x4 # $sequence, $animation
FLEX = 0x6 # $model VTA

MAX_MESH_SPLIT = 16

mesh_compatible = ('MESH', 'TEXT', 'FONT', 'SURFACE', 'META', 'CURVE')
modifier_compatible = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'LATTICE'}
shape_types = ('MESH' , 'SURFACE', 'CURVE')
MODE_MAP = {
    "OBJECT": "OBJECT",
    "EDIT_ARMATURE": "EDIT",
    "POSE": "POSE",
    "EDIT_MESH": "EDIT",
    "SCULPT": "SCULPT",
    "VERTEX_PAINT": "VERTEX_PAINT",
    "PAINT_VERTEX": "VERTEX_PAINT",
    "PAINT_WEIGHT": "WEIGHT_PAINT",
    "WEIGHT_PAINT": "WEIGHT_PAINT",
    "PAINT_TEXTURE": "TEXTURE_PAINT",
    "TEXTURE_PAINT": "TEXTURE_PAINT"
}

exportable_types = list((*mesh_compatible, 'ARMATURE'))
exportable_types = tuple(exportable_types)

axes = (('X','X',''),('Y','Y',''),('Z','Z',''))
axes_forward = (('-X','-X',''),('-Y','-Y',''),('-Z','-Z',''),('X','X',''),('Y','Y',''),('Z','Z',''))
axes_lookup = { 'X':0, 'Y':1, 'Z':2 }
axes_lookup_source2 = { 'X':1, 'Y':2, 'Z':3 }

bonename_direction_map = {
    '.L': '.R', '_L': '_R', 'Left': 'Right', '_Left': '_Right', '.Left': '.Right', 'L_': 'R_', 'L.': 'R.', 'L ': 'R ',
    '.R': '.L', '_R': '_L', 'Right': 'Left', '_Right': '_Left', '.Right': '.Left', 'R_': 'L_', 'R.': 'L.', 'R ': 'L '
}

hitbox_group = [
    ('0', 'Generic', 'the default group of hitboxes, appears White in HLMV'),
    ('1', 'Head', 'Used for human NPC heads and to define where the player sits on the vehicle.mdl, appears Red in HLMV'),
    ('2', 'Chest', 'Used for human NPC midsection and chest, appears Green in HLMV'),
    ('3', 'Stomach', 'Used for human NPC stomach and pelvis, appears Yellow in HLMV'),
    ('4', 'Left Arm', 'Used for human Left Arm, appears Deep Blue in HLMV'),
    ('5', 'Right Arm', 'Used for human Right Arm, appears Bright Violet in HLMV'),
    ('6', 'Left Leg', 'Used for human Left Leg, appears Bright Cyan in HLMV'),
    ('7', 'Right Leg', 'Used for human Right Leg, appears White like the default group in HLMV (Orange in Garry\'s Mod'),
    ('8', 'Neck', 'Used for human neck (to fix penetration to head from behind), appears Orange in HLMV (In all games since CS:GO)'),
]

kitsune_data_keys: list[str] = []

class ExportFormat:
    SMD = 1
    DMX = 2

class Compiler:
    UNKNOWN = 0
    STUDIOMDL = 1 # Source 1
    RESOURCECOMPILER = 2 # Source 2
    MODELDOC = 3 # Source 2 post-Alyx

@dataclasses.dataclass(frozen = True)
class dmx_version:
    encoding : int
    format : int
    title : str = dataclasses.field(default="Unnamed", hash=False, compare=False)

    compiler : int = Compiler.STUDIOMDL

    @property
    def format_enum(self): return str(self.format) + ("_modeldoc" if self.compiler == Compiler.MODELDOC else "")
    @property
    def format_title(self): return f"Model {self.format}" + (" (ModelDoc)" if self.compiler == Compiler.MODELDOC else "")

dmx_versions_source1 = {
'Ep1': dmx_version(0,0, "Half-Life 2: Episode One"),
'Source2007': dmx_version(2,1, "Source 2007"),
'Source2009': dmx_version(2,1, "Source 2009"),
'Garrysmod': dmx_version(2,1, "Garry's Mod"),
'Orangebox': dmx_version(5,18, "OrangeBox / Source MP"),
'nmrih': dmx_version(2,1, "No More Room In Hell"),
}

dmx_versions_source1.update({version.title:version for version in [
dmx_version(2,1, 'Team Fortress 2'),
dmx_version(0,0, 'Left 4 Dead'), # wants model 7, but it's not worth working out what that is when L4D2 in far more popular and SMD export works
dmx_version(4,15, 'Left 4 Dead 2'),
dmx_version(5,18, 'Alien Swarm'),
dmx_version(5,18, 'Portal 2'),
dmx_version(5,18, 'Source Filmmaker'),
# and now back to 2/1 for some reason...
dmx_version(2,1, 'Half-Life 2'),
dmx_version(2,1, 'Source SDK Base 2013 Singleplayer'),
dmx_version(2,1, 'Source SDK Base 2013 Multiplayer'),
]})

dmx_versions_source2 = {
'dota2': dmx_version(9,22, "Dota 2", Compiler.RESOURCECOMPILER),
'steamtours': dmx_version(9,22, "SteamVR", Compiler.RESOURCECOMPILER),
'hlvr': dmx_version(9,22, "Half-Life: Alyx", Compiler.MODELDOC), # format is still declared as 22, but modeldoc introduces breaking changes
'cs2': dmx_version(9,22, 'Counter-Strike 2', Compiler.MODELDOC),
}

def getAllDataNameTranslations(string : str) -> set[str]:
    if not bpy.app.translations.locales:
        return { string } # Blender was compiled without translations
    
    translations = set()
        
    view_prefs = bpy.context.preferences.view
    user_language = view_prefs.language
    user_dataname_translate = view_prefs.use_translate_new_dataname
        
    try:
        view_prefs.use_translate_new_dataname = True
        for language in bpy.app.translations.locales:
            if language == "hr_HR" and bpy.app.version < (4,5,3):
                continue # enabling Croatian generates a C error message in the console, and it's very sparsely translated anyway
            try:
                view_prefs.language = language
                translations.add(bpy.app.translations.pgettext_data(string))
            except:
                pass
    finally:
        view_prefs.language = user_language
        view_prefs.use_translate_new_dataname = user_dataname_translate
    
    return translations

class _StateMeta(type): # class properties are not supported below Python 3.9, so we use a metaclass instead
    def __init__(cls, *args, **kwargs):
        cls._exportableObjects = set()
        cls.last_export_refresh = 0
        cls._engineBranch = None
        cls._gamePathValid = False
        cls._legacySlotTranslations = getAllDataNameTranslations("Legacy Slot")

    @property
    def exportableObjects(cls) -> set[int]: return cls._exportableObjects

    @property
    def engineBranch(cls) -> dmx_version | None: return cls._engineBranch

    @property
    def datamodelEncoding(cls):
        if cls._engineBranch: return cls._engineBranch.encoding
        enc = bpy.context.scene.vs.dmx_encoding
        return 1 if enc == 'kv2' else int(enc)

    @property
    def use_kv2(cls):
        return not cls._engineBranch and bpy.context.scene.vs.dmx_encoding == 'kv2'

    @property
    def datamodelFormat(cls): return cls._engineBranch.format if cls._engineBranch else int(bpy.context.scene.vs.dmx_format.split("_")[0])

    @property
    def engineBranchTitle(cls): return cls._engineBranch.title if cls._engineBranch else None

    @property
    def compiler(cls): return cls._engineBranch.compiler if cls._engineBranch else Compiler.MODELDOC if "modeldoc" in bpy.context.scene.vs.dmx_format else Compiler.UNKNOWN

    @property
    def exportFormat(cls): return ExportFormat.DMX if bpy.context.scene.vs.export_format == 'DMX' and cls.datamodelEncoding != 0 else ExportFormat.SMD

    @property
    def gamePath(cls):
        return cls._rawGamePath if cls._gamePathValid else None

    @property
    def legacySlotNames(cls): return cls._legacySlotTranslations

    @property
    def _rawGamePath(cls):
        if bpy.context.scene.vs.game_path:
            return os.path.abspath(os.path.join(bpy.path.abspath(bpy.context.scene.vs.game_path),''))
        else:
            return os.getenv('vproject')

class State(metaclass=_StateMeta):
    @classmethod
    def update_scene(cls, scene : bpy.types.Scene | None = None):
        scene = scene or bpy.context.scene
        assert(scene)
        cls._exportableObjects = set([ob.session_uid for ob in scene.objects if ob.type in exportable_types and not (ob.type == 'CURVE' and ob.data.bevel_depth == 0 and ob.data.extrude == 0)])
        make_export_list(scene)
        for arm_obj in (ob for ob in scene.objects if ob.type == 'ARMATURE'):
            avs = arm_obj.data.vs
            if _sync_object_entries(avs.arm_attachment_entries, get_attachments(arm_obj)):
                avs.arm_attachment_index = min(avs.arm_attachment_index, len(avs.arm_attachment_entries) - 1)
            if _sync_bone_entries(avs.arm_jigglebone_entries, get_jigglebones(arm_obj)):
                avs.arm_jigglebone_index = min(avs.arm_jigglebone_index, len(avs.arm_jigglebone_entries) - 1)
        for col in bpy.data.collections:
            seen = set()
            members = []
            for ob in get_collection_export_objects(col):
                if ob and ob.session_uid not in seen:
                    seen.add(ob.session_uid)
                    members.append(ob)
            members.sort(key=lambda o: o.name.lower())
            _sync_object_entries(col.vs.export_object_entries, members)
        cls.last_export_refresh = time.time()
    
    @staticmethod
    @persistent
    def _onDepsgraphUpdate(scene : bpy.types.Scene):
        if scene == bpy.context.scene:
            # Export list refresh
            if time.time() - State.last_export_refresh > 0.25:
                State.update_scene(scene)

    @staticmethod
    @persistent
    def _onLoad(_):
        State.update_scene()
        State._updateEngineBranch()
        State._validateGamePath()

    @classmethod
    def hook_events(cls):
        if not cls.update_scene in depsgraph_update_post:
            depsgraph_update_post.append(cls._onDepsgraphUpdate)
            load_post.append(cls._onLoad)

    @classmethod
    def unhook_events(cls):
        if cls.update_scene in depsgraph_update_post:
            depsgraph_update_post.remove(cls._onDepsgraphUpdate)
            load_post.remove(cls._onLoad)

    @staticmethod
    def onEnginePathChanged(props,context):
        if props == context.scene.vs:
            State._updateEngineBranch()

    @classmethod
    def _updateEngineBranch(cls):
        try:
            cls._engineBranch = getEngineBranch()
        except:
            cls._engineBranch = None

    @staticmethod
    def onGamePathChanged(props,context):
        if props == context.scene.vs:
            State._validateGamePath()

    @classmethod
    def _validateGamePath(cls):
        if cls._rawGamePath:
            for anchor in ["gameinfo.txt", "addoninfo.txt", "gameinfo.gi"]:
                if os.path.exists(os.path.join(cls._rawGamePath,anchor)):
                    cls._gamePathValid = True
                    return
        cls._gamePathValid = False

def print(*args, newline=True, debug_only=False):
    if not debug_only or bpy.app.debug_value > 0:
        builtins.print(" ".join([str(a) for a in args]).encode(sys.getdefaultencoding()).decode(sys.stdout.encoding or sys.getdefaultencoding()), end= "\n" if newline else "", flush=True)

def get_id(str_id: str, format_string: bool = False, data: bool = False) -> str:
    from . import translations
    out = translations.ids.get(str_id, "")
    if out is None:
        return ""
    if format_string or (data and bpy.context.preferences.view.use_translate_new_dataname):
        return typing.cast(str, pgettext(out))
    else:
        return out

def get_active_exportable(context = None):
    if not context: context = bpy.context
    
    if not context.scene.vs.export_list_active < len(context.scene.vs.export_list):
        return None

    return context.scene.vs.export_list[context.scene.vs.export_list_active]

class BenchMarker:
    def __init__(self,indent = 0, prefix = None):
        self._indent = indent * 4
        self._prefix = "{}{}".format(" " * self._indent,prefix if prefix else "")
        self.quiet = bpy.app.debug_value <= 0
        self.reset()

    def reset(self):
        self._last = self._start = time.time()
        
    def report(self,label = None, threshold = 0.0):
        now = time.time()
        elapsed = now - self._last
        if threshold and elapsed < threshold: return

        if not self.quiet:
            prefix = "{} {}:".format(self._prefix, label if label else "")
            pad = max(0, 10 - len(prefix) + self._indent)
            print("{}{}{:.4f}".format(prefix," " * pad, now - self._last))
        self._last = now

    def current(self):
        return time.time() - self._last
    def total(self):
        return time.time() - self._start

def smdBreak(line):
    line = line.rstrip('\n')
    return line == "end" or line == ""
    
def smdContinue(line):
    return line.startswith("//")

def getDatamodelQuat(blender_quat):
    return datamodel.Quaternion([blender_quat[1], blender_quat[2], blender_quat[3], blender_quat[0]])

def getEngineBranch() -> dmx_version | None:
    if not bpy.context.scene.vs.engine_path: return None
    path = os.path.abspath(bpy.path.abspath(bpy.context.scene.vs.engine_path))

    # Source 2: search for executable name
    engine_path_files = set(name[:-4] if name.endswith(".exe") else name for name in os.listdir(path))
    if "resourcecompiler" in engine_path_files: # Source 2
        for executable,dmx_version in dmx_versions_source2.items():
            if executable in engine_path_files:
                return dmx_version

    # Source 1 SFM special case
    if path.lower().find("sourcefilmmaker") != -1:
        return dmx_versions_source1["Source Filmmaker"] # hack for weird SFM folder structure, add a space too
    
    # Source 1 standard: use parent dir's name
    name = os.path.basename(os.path.dirname(bpy.path.abspath(path))).title().replace("Sdk","SDK")
    return dmx_versions_source1.get(name)

def getCorrectiveShapeSeparator(): return '__' if State.compiler == Compiler.MODELDOC else '_'

vertex_maps = {
    "valvesource_vertex_paint":  "VertexPaintTintColor$0",
    "valvesource_vertex_blend":  "VertexPaintBlendParams$0",
    "valvesource_vertex_blend1": "VertexPaintBlendParams1$0", # ???
}

# Per vertex Source 2 DMX maps
vertex_float_maps = [
    "cloth_enable",
    "cloth_animation_attract",
    "cloth_animation_force_attract",
    "cloth_goal_strength",
    "cloth_goal_strength_v2",
    "cloth_goal_damping",
    "cloth_drag",
    "cloth_drag_v2",
    "cloth_mass",
    "cloth_gravity",
    "cloth_gravity_z",
    "cloth_collision_radius",
    "cloth_ground_collision",
    "cloth_ground_friction",
    "cloth_use_rods",
    "cloth_make_rods",
    "cloth_anchor_free_rotate",
    "cloth_volumetric",
    "cloth_suspenders",
    "cloth_bend_stiffness",
    "cloth_stray_radius_inv",
    "cloth_stray_radius",
    "cloth_stray_radius_stretchiness",
    "cloth_antishrink",
    "cloth_shear_resistance",
    "cloth_stretch",
    "cloth_friction"

    # TODO add way to set up groups manually
    # cloth_collision_layer_%d - 0 through 15
    # cloth_vertex_set_%s - name
]

# Cloth map grouping for UI organization (based on Valve's Source 2 cloth attribute documentation)
cloth_map_groups = {
    "Enable": ["cloth_enable"],
    "Simulation Control": ["cloth_mass", "cloth_drag", "cloth_drag_v2", "cloth_gravity", "cloth_gravity_z", "cloth_friction"],
    "Physical Properties": ["cloth_bend_stiffness", "cloth_stretch", "cloth_shear_resistance", "cloth_antishrink"],
    "Constraints & Goals": ["cloth_goal_strength", "cloth_goal_strength_v2", "cloth_goal_damping"],
    "Animation": ["cloth_animation_attract", "cloth_animation_force_attract"],
    "Collision & Interaction": ["cloth_collision_radius", "cloth_ground_collision", "cloth_ground_friction"],
    "Structure": ["cloth_use_rods", "cloth_make_rods", "cloth_volumetric", "cloth_suspenders"],
    "Advanced": ["cloth_stray_radius_inv", "cloth_stray_radius", "cloth_stray_radius_stretchiness", "cloth_anchor_free_rotate"],
}

def findDmxClothVertexGroups(ob):
    groups = []
    for vgroup in ob.vertex_groups:
        if vgroup.name in vertex_float_maps:
            groups.append(vgroup)

        elif vgroup.name.startswith("cloth_collision_layer_"):
            for n in range(16):
                if vgroup.name == f"cloth_collision_layer_{n}":
                    groups.append(vgroup)
                    break

        elif vgroup.name.startswith("cloth_vertex_set_"):
            groups.append(vgroup)

    return groups

def getDmxKeywords(format_version):
    if format_version >= 22:
        return {
          'pos': "position$0", 'norm': "normal$0", 'wrinkle':"wrinkle$0",
          'balance':"balance$0", 'weight':"blendweights$0", 'weight_indices':"blendindices$0"
          }
    else:
        return { 'pos': "positions", 'norm': "normals", 'wrinkle':"wrinkle",
          'balance':"balance", 'weight':"jointWeights", 'weight_indices':"jointIndices" }

def count_exports(context):
    num = 0
    for exportable in context.scene.vs.export_list:
        if exportable.prefab_type:
            continue
        item = exportable.item
        if item and item.vs.export and (type(item) != bpy.types.Collection or (not item.vs.mute and not is_bypassed_into_parent(item))):
            num += 1
    return num

def _iter_action_keyframe_times(ad : bpy.types.AnimData):
    if not ad or not ad.action:
        return []
    try:
        channelbag = ad.action.layers[0].strips[0].channelbag(ad.action_slot)
    except (IndexError, AttributeError):
        return []
    if channelbag is None:
        return []
    return [kf.co.x for fcurve in channelbag.fcurves for kf in fcurve.keyframe_points]

def animationFrameRange(ad : bpy.types.AnimData):
    # (first_frame, length) for the current action/slot. `first_frame` is the frame the
    # action's earliest keyframe sits on; `length` is the whole-frame span from first to
    # last keyframe. Callers must sample scene frames first_frame .. first_frame+length
    # (NOT 0 .. length) so actions that don't start on frame 0 export their real motion
    # instead of a held first frame. Returns (0, 0) when there are no keyframes.
    times = _iter_action_keyframe_times(ad)
    if not times:
        return 0, 0
    first = floor(min(times))
    return first, ceil(max(times)) - first

def animationLength(ad : bpy.types.AnimData):
    return animationFrameRange(ad)[1]
    
def getFileExt(flex=False):
    if State.datamodelEncoding != 0 and bpy.context.scene.vs.export_format == 'DMX':
        return ".dmx"
    else:
        if flex: return ".vta"
        else: return ".smd"

# rounds to 6 decimal places, converts between "1e-5" and "0.000001", outputs str
def getSmdFloat(fval):
    return "{:.6f}".format(float(fval))

def getSmdVec(iterable):
    return " ".join([getSmdFloat(val) for val in iterable])

def appendExt(path,ext):
    if not path.lower().endswith("." + ext) and not path.lower().endswith(".dmx"):
        path += "." + ext
    return path

def printTimeMessage(start_time,name,job,type="SMD"):
    elapsedtime = int(time.time() - start_time)
    if elapsedtime == 1:
        elapsedtime = "1 second"
    elif elapsedtime > 1:
        elapsedtime = str(elapsedtime) + " seconds"
    else:
        elapsedtime = "under 1 second"

    print(type,name,"{}ed in".format(job),elapsedtime,"\n")

def getUpAxisMat(axis):
    match axis.upper():
        case 'X':
            return Matrix.Rotation(-pi/2, 4, 'Y')
        case 'Y':
            return Matrix.Rotation(pi/2, 4, 'X')
        case 'Z':
            return Matrix()
        case _:
            raise AttributeError("getUpAxisMat got invalid axis argument '{}'".format(axis))
    
def getUpAxisOffsetMat(axis, offset):
    match axis.upper():
        case 'X':
            return Matrix.Translation((0, 0, offset))
        case 'Y':
            return Matrix.Translation((0, 0, offset))
        case 'Z':
            return Matrix.Translation((0, 0, offset))
        case _:
            raise AttributeError("getUpAxisOffsetMat got invalid axis argument '{}'".format(axis))
    
def getForwardAxisMat(axis: str) -> Matrix:
    """Return a rotation matrix that orients an object to face the specified forward direction."""
    match axis.upper():
        case 'X':
            return Matrix.Rotation(-pi / 2, 4, 'Z')
        case 'Y':
            return Matrix.Rotation(pi, 4, 'Z')
        case '-Y':
            return Matrix()
        case 'Z':
            return Matrix.Rotation(-pi / 2, 4, 'X')
        case '-X':
            return Matrix.Rotation(pi / 2, 4, 'Z')
        case '-Z':
            return Matrix.Rotation(pi / 2, 4, 'X')
        case _:
            raise AttributeError(f"getForwardAxisMat got invalid axis argument '{axis}'")

def MakeObjectIcon(object,prefix=None,suffix=None):
    if not (prefix or suffix):
        raise TypeError("A prefix or suffix is required")

    if object.type == 'TEXT':
        type = 'FONT'
    else:
        type = object.type

    out = ""
    if prefix:
        out += prefix
    out += type
    if suffix:
        out += suffix
    return out

def get_flexcontrollers(ob: bpy.types.Object) -> list[tuple[str, bool, bool, str, str, str]]:
    """Return list of (shapekey, eyelid, stereo, raw_delta, controller_name, flexgroup) from object,
    only including entries with a valid controller name. Shapekey is optional."""

    if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_flexcontrollers"):
        return []

    valid_keys = set(ob.data.shape_keys.key_blocks.keys()[1:]) if ob.data.shape_keys else set()

    result = []

    for fc in ob.vs.dme_flexcontrollers:
        controller_name = fc.controller_name.strip() if fc.controller_name and fc.controller_name.strip() else ""

        if not controller_name:
            if not fc.shapekey or fc.shapekey not in valid_keys:
                continue
            controller_name = fc.shapekey

        shapekey = fc.shapekey if fc.shapekey and fc.shapekey in valid_keys else ""

        raw = fc.raw_delta_name.strip() if fc.raw_delta_name and fc.raw_delta_name.strip() else shapekey
        delta_name = sanitize_string_for_delta(raw)

        flexgroup = fc.resolved_flexgroup()

        result.append((shapekey, fc.eyelid, fc.stereo, delta_name, controller_name, flexgroup))

    return result

def removeObject(obj):
    d = obj.data
    type = obj.type

    if type == "ARMATURE":
        for child in obj.children:
            if child.type == 'EMPTY':
                removeObject(child)

    for collection in obj.users_collection:
        collection.objects.unlink(obj)
    if obj.users == 0:
        if type == 'ARMATURE' and obj.animation_data:
            obj.animation_data.action = None # avoid horrible Blender bug that leads to actions being deleted

        bpy.data.objects.remove(obj)
        if d and d.users == 0:
            if type == 'MESH':
                bpy.data.meshes.remove(d)
            if type == 'ARMATURE':
                bpy.data.armatures.remove(d)

    return None if d else type
    
def select_only(ob):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='OBJECT')
    if bpy.context.selected_objects:
        bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)

def hasShapes(id, valid_only = True):
    def _test(id_):
        return bool(id_.type in shape_types and id_.data.shape_keys and len(id_.data.shape_keys.key_blocks))
    
    if type(id) == bpy.types.Collection:
        for _ in [ob for ob in get_collection_export_objects(id) if ob.vs.export and (not valid_only or ob.session_uid in State.exportableObjects) and _test(ob)]:
            return True
        return False
    else:
        return _test(id)

def countShapes(*objects):
    num_shapes = 0
    num_correctives = 0
    flattened_objects = []

    for ob in objects:
        if isinstance(ob, bpy.types.Collection):
            flattened_objects.extend(get_collection_export_objects(ob))
        elif hasattr(ob, '__iter__'):
            flattened_objects.extend(ob)
        else:
            flattened_objects.append(ob)

    for ob in [o for o in flattened_objects if o.vs.export and hasShapes(o)]:
        if ob.vs.flex_controller_mode == 'DME':
            num_shapes += sum(1 for fc in ob.vs.dme_flexcontrollers if fc.controller_name and fc.controller_name.strip())
            num_correctives += sum(1 for r in ob.vs.dme_flex_rules if r.rule_type == 'CORRECTIVE' and r.components.strip())
        else:
            if ob.data.shape_keys:
                for shape in ob.data.shape_keys.key_blocks[1:]:
                    if getCorrectiveShapeSeparator() in shape.name:
                        num_correctives += 1
                    else:
                        num_shapes += 1

    return num_shapes, num_correctives

def hasCurves(id):
    def _test(id_):
        return id_.type in ['CURVE','SURFACE','FONT']

    if type(id) == bpy.types.Collection:
        for _ in [ob for ob in get_collection_export_objects(id) if ob.vs.export and ob.session_uid in State.exportableObjects and _test(ob)]:
            return True
        return False
    else:
        return _test(id)

def is_mesh_compatible(ob : bpy.types.Object | None) -> bool:
    return bool(ob and hasattr(ob,'type') and ob.type in mesh_compatible)

def valvesource_vertex_maps(id) -> set[str]:
    """Returns all vertex colour maps which are recognised by the Tools."""
    def test(id_):
        if hasattr(id_.data,"vertex_colors"):
            return set(id_.data.vertex_colors.keys()).intersection(vertex_maps)
        else:
            return set()

    if type(id) == bpy.types.Collection:
        return set(itertools.chain(*(test(ob) for ob in get_collection_export_objects(id))))
    elif id.type == 'MESH':
        return test(id)
    else:
        return set()

def actionSlotsForFilter(obj : bpy.types.Object):
    from fnmatch import fnmatch
    if not obj.animation_data:
        return list()
    return list([slot for slot in obj.animation_data.action_suitable_slots if fnmatch(slot.name_display, obj.vs.action_filter)] if obj.vs.action_filter else obj.animation_data.action_suitable_slots)

def actionsForFilter(filter):
    import fnmatch
    return list([action for action in bpy.data.actions if action.users and fnmatch.fnmatch(action.name, filter)])

def actionSlotExportName(animData : bpy.types.AnimData):
    """For use only when exporting a single action slot"""
    slot_name = animData.action_slot.name_display
    return animData.action.name if slot_name in State.legacySlotNames else slot_name

def shouldExportGroup(group):
    return group.vs.export and not group.vs.mute and not is_bypassed_into_parent(group)

def get_collection_parent_collection(col) -> bpy.types.Collection | None:
    """Return the collection that directly contains `col`, or None if `col` is
    only linked to the scene root (i.e. it is a top-level group)."""
    for parent in bpy.data.collections:
        if parent != col and col.name in parent.children:
            return parent
    return None

def is_bypassed_into_parent(col) -> bool:
    """True when `col` has 'bypass' enabled and there is a parent collection to
    fold it into. Top-level bypassed collections behave as normal groups."""
    return col.vs.bypass and get_collection_parent_collection(col) is not None

def get_collection_export_objects(col) -> list[bpy.types.Object]:
    """The objects that belong to `col` for export purposes: its own objects
    plus, recursively, the objects of any child collections marked 'bypass'
    (those fold into this group instead of exporting separately)."""
    result = list(col.objects)
    for child in col.children:
        if child.vs.bypass:
            result.extend(get_collection_export_objects(child))
    return result

def hasFlexControllerSource(source):
    return bpy.data.texts.get(source) or os.path.exists(bpy.path.abspath(source))

def channelBagForNewActionSlot(obj : bpy.types.Object, name : str):
    ad = obj.animation_data_create()
    if not ad.action:
        ad.action = bpy.data.actions.new(obj.name)
    slot = ad.action.slots.new(id_type='OBJECT', name=name)
    ad.action_slot = slot

    layer = ad.action.layers.new(name) if not ad.action.layers else ad.action.layers[0]
    strip = layer.strips.new(type='KEYFRAME') if not layer.strips else layer.strips[0]
    return typing.cast(bpy.types.ActionChannelbag, strip.channelbag(slot, ensure=True))

def getExportablesForObject(ob):
    # objects can be reallocated between yields, so capture the ID locally
    ob_session_uid = ob.session_uid
    seen = set()

    # Prefab rows are synthetic and share their armature's session_uid, so they are
    # excluded from both the iteration and the termination count.
    def _real_count():
        return sum(1 for e in bpy.context.scene.vs.export_list if not e.prefab_type)

    while len(seen) < _real_count():
        # Handle the exportables list changing between yields by re-evaluating the whole thing
        for exportable in bpy.context.scene.vs.export_list:
            if exportable.prefab_type:
                continue
            if not exportable.item:
                continue # Observed only in Blender release builds without a debugger attached

            if exportable.session_uid in seen:
                continue
            seen.add(exportable.session_uid)

            if exportable.ob_type == 'COLLECTION' and not exportable.item.vs.mute and not is_bypassed_into_parent(exportable.item) and any(collection_item.session_uid == ob_session_uid for collection_item in get_collection_export_objects(exportable.item)):
                yield exportable
                break

            if exportable.session_uid == ob_session_uid:
                yield exportable
                break

# How to handle the selected object appearing in multiple collections?
# How to handle an armature with animation only appearing within a collection?
def getSelectedExportables():
    seen = set()
    for ob in bpy.context.selected_objects:
        for exportable in getExportablesForObject(ob):
            if not exportable.name in seen:
                seen.add(exportable.name)
                yield exportable

    if len(seen) == 0 and bpy.context.active_object:
        for exportable in getExportablesForObject(bpy.context.active_object):
            yield exportable

def _sync_object_entries(coll, objects) -> bool:
    current = {ob.session_uid for ob in objects if ob}
    stored  = {e.obj.session_uid for e in coll if e.obj}
    has_stale = any(not e.obj for e in coll)
    if current != stored or has_stale:
        coll.clear()
        for ob in objects:
            coll.add().obj = ob
        return True
    return False

def _sync_bone_entries(coll, bones) -> bool:
    current = {b.name for b in bones}
    stored  = {e.bone_name for e in coll}
    if current != stored:
        coll.clear()
        for b in bones:
            coll.add().bone_name = b.name
        return True
    return False

def make_export_list(scene: bpy.types.Scene):
    scene.vs.export_list.clear()

    def makeDisplayName(item, name=None):
        return (name if name else item.name) + getFileExt()

    if not State.exportableObjects:
        return

    pending = []

    # Collections
    ungrouped_object_ids = State.exportableObjects.copy()

    scene_groups = []
    for group in sorted(bpy.data.collections, key=lambda g: g.name.lower()):
        valid = False
        for obj in [obj for obj in get_collection_export_objects(group) if obj.session_uid in State.exportableObjects]:
            if not group.vs.mute and obj.type != 'ARMATURE' and obj.session_uid in ungrouped_object_ids:
                ungrouped_object_ids.remove(obj.session_uid)
            valid = True
        if valid:
            scene_groups.append(group)

    for g in scene_groups:
        if g.vs.mute:
            label = "{} {}".format(g.name, pgettext(get_id("exportables_group_mute_suffix", True)))
        elif is_bypassed_into_parent(g):
            label = "{} {}".format(g.name, pgettext(get_id("exportables_group_bypass_suffix", True)))
        else:
            label = makeDisplayName(g)
        pending.append((0, label, "COLLECTION", "GROUP", None, g))

    # Ungrouped objects
    ungrouped_objects = sorted(
        (ob for ob in scene.objects if ob.session_uid in ungrouped_object_ids),
        key=lambda ob: ob.name.lower()
    )

    TYPE_BUCKET = {"ACTION_SLOT": 1, "ACTION": 2, "OBJECT": 3}

    for ob in ungrouped_objects:
        if ob.type == 'FONT':
            ob.vs.triangulate = True

        i_name = i_type = i_icon = None
        if ob.type == 'ARMATURE':
            ad = ob.animation_data
            if ad:
                i_icon = i_type = "ACTION_SLOT"
                if ob.data.vs.action_selection != 'CURRENT':
                    export_slots = ob.data.vs.action_selection == 'FILTERED'
                    exportables_count = len(actionSlotsForFilter(ob) if export_slots else actionsForFilter(ob.vs.action_filter))
                    if exportables_count > 0:
                        if not export_slots or (ob.vs.action_filter and ob.vs.action_filter != "*"):
                            i_name = get_id("exportables_arm_filter_result", True).format(ob.vs.action_filter, exportables_count)
                        else:
                            i_name = get_id("exportables_arm_no_slot_filter", True).format(exportables_count, ob.name)
                elif ad.action_slot:
                    i_name = makeDisplayName(ob, actionSlotExportName(ad))
        else:
            i_name = makeDisplayName(ob)
            i_icon = MakeObjectIcon(ob, prefix="OUTLINER_OB_")
            i_type = "OBJECT"

        if i_name:
            pending.append((TYPE_BUCKET[i_type], i_name, i_type, i_icon, ob, None))

    # Sort: type bucket first, then display name
    pending.sort(key=lambda p: (p[0], p[1].lower()))

    for _, name, ob_type, icon, obj, collection in pending:
        i = scene.vs.export_list.add()
        i.name = name
        i.ob_type = ob_type
        i.icon = icon
        if obj:
            i.obj = obj
        if collection:
            i.collection = collection

    # Prefab rows: one per available prefab type, for every armature in the scene
    # that has matching content. The persistent settings live on the armature data;
    # these rows are rebuilt every refresh.
    for arm in sorted((ob for ob in scene.objects if ob.type == 'ARMATURE'),
                      key=lambda a: a.name.lower()):
        avs = getattr(arm.data, 'vs', None)
        if avs is None:
            continue
        available = prefab_available_types(arm, scene)
        sync_prefab_items(avs, [t for t, _ in available])
        for ptype, count in available:
            icon, label = prefab_type_info[ptype]
            row = scene.vs.export_list.add()
            row.name = get_id("exportables_prefab_row", True).format(label, arm.name)
            row.ob_type = 'PREFAB'
            row.icon = icon
            row.obj = arm
            row.prefab_type = ptype
            row.prefab_count = count

def update_vmdl_container(container_class: str, nodes: list[keyvalues3.KVNode] | keyvalues3.KVNode, export_path: str | None = None,
                          to_clipboard: bool = False) -> keyvalues3.KVDocument | bool:
    """
    Insert or update node(s) into a container inside a KV3 RootNode.
    Folders are overwritten if they exist; other nodes are appended.

    Args:
        container_class: _class of container (e.g., "JiggleBoneList" or "AnimConstraintList"/"ScratchArea").
        nodes: Single KVNode or list of KVNodes to insert.
        export_path: Filepath to load existing KV3 document if not clipboard.
        to_clipboard: If True, uses ScratchArea container instead of a file.

    Returns:
        KVDocument ready for writing or clipboard.
    """

    def open_and_parse_vmdl(filepath: str) -> keyvalues3.KVNode | None:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return None
        
        try:
            parser = keyvalues3.KVParser(text)
            doc = parser.parse()

            root_node = doc.roots.get("rootNode")
            if not root_node or root_node.properties.get("_class") != "RootNode":
                return None
            return root_node

        except Exception:
            return None

    if not isinstance(nodes, list):
        nodes = [nodes]

    root = None
    if to_clipboard:
        root = keyvalues3.KVNode(_class="RootNode")
    else:
        if export_path and os.path.exists(export_path):
            root = open_and_parse_vmdl(export_path)

            if root is None:
                return False
        else:
            root = keyvalues3.KVNode(_class="RootNode")

    container = root.get(_class=container_class)
    if not container:
        container = keyvalues3.KVNode(_class=container_class)
        root.add_child(container)

    for node in nodes:
        node_name = node.properties.get("name")
        if node_name:
            existing = next(
                (c for c in container.children if c.properties.get("name") == node_name and c.properties.get("_class") == node.properties.get("_class")),
                None
            )
            if existing:
                existing.children.clear()
                for child in node.children:
                    existing.add_child(child)
                continue

        container.add_child(node)

    kv_doc = keyvalues3.KVDocument()
    kv_doc.add_root("rootNode", root)
    return kv_doc

class Logger:
    def __init__(self):
        self.log_warnings = []
        self.log_errors = []
        self.startTime = time.time()

    def warning(self, *string):
        message = " ".join(str(s) for s in string)
        print(" WARNING:",message)
        self.log_warnings.append(message)

    def error(self, *string):
        message = " ".join(str(s) for s in string)
        print(" ERROR:",message)
        self.log_errors.append(message)
    
    def list_errors(self, menu, context):
        l = menu.layout
        if len(self.log_errors):
            for msg in self.log_errors:
                l.label(text="{}: {}".format(pgettext("Error").upper(), msg))
            l.separator()
        if len(self.log_warnings):
            for msg in self.log_warnings:
                l.label(text="{}: {}".format(pgettext("Warning").upper(), msg))

    def elapsed_time(self):
        return round(time.time() - self.startTime, 1)

    def errorReport(self,message):
        if len(self.log_errors) or len(self.log_warnings):
            message += get_id("exporter_report_suffix",True).format(len(self.log_errors),len(self.log_warnings))
            if not bpy.app.background:
                bpy.context.window_manager.popup_menu(self.list_errors,title=get_id("exporter_report_menu"))
            
            print("{} Errors and {} Warnings".format(len(self.log_errors),len(self.log_warnings)))
            for msg in self.log_errors: print("Error:",msg)
            for msg in self.log_warnings: print("Warning:",msg)
        
        self.report({'INFO'},message)
        print(message)

class SmdInfo:
    isDMX = 0 # version number, or 0 for SMD
    a : bpy.types.Object | None = None
    m : bpy.types.Object | None = None
    shapes = None
    g : bpy.types.Collection | None = None # Group being exported
    file : TextIOWrapper
    jobType = None
    startTime = 0.0
    in_block_comment = False
    rotMode = 'EULER' # for creating keyframes during import
    shapeNames : dict | None = None
    
    def __init__(self, jobName : str):
        self.jobName = jobName
        self.upAxis = bpy.context.scene.vs.up_axis
        self.amod = {} # Armature modifiers
        self.materials_used = set() # printed to the console for users' benefit

        # DMX stuff
        self.attachments = []
        self.meshes = []
        self.parent_chain = []
        self.dmxShapes = collections.defaultdict(list)
        self.boneTransformIDs = {}

        self.frameData = []
        self.bakeInfo = []

        # boneIDs contains the ID-to-name mapping of *this* SMD's bones.
        # - Key: integer ID
        # - Value: bone name (storing object itself is not safe)
        self.boneIDs = {}
        self.boneNameToID = {} # for convenience during export
        self.phantomParentIDs = {} # for bones in animation SMDs but not the ref skeleton

class QcInfo:
    startTime = 0
    ref_mesh = None # for VTA import
    a = None
    origin = None
    upAxis = 'Z'
    upAxisMat = None
    numSMDs = 0
    makeCamera = False
    in_block_comment = False
    jobName = ""
    root_filedir = ""
    pending_combo_op = None  # deferred DmeCombinationOperator from a DMX $model import
    no_auto_dmx_rules = False  # $model noautodmxrules: ignore DMX flex, QC is sole source
    hboxset_name: str = ''  # first $hboxset encountered; '' means none seen yet
    # Flex accumulation  shared across all recursive readQC calls; applied once by outer call
    flex_target_mesh = None
    flex_target_combo_op = None
    flex_controllers_pending: list = None
    localvars_pending: list = None
    expressions_pending: list = None
    stereo_flex_names_pending: set = None

    def __init__(self):
        self.imported_smds = []
        self.vars = {}
        self.dir_stack = []
        # Every imported mesh with shape keys; global flex data is applied to all of them.
        self.flex_meshes = []

    def cd(self):
        return os.path.join(self.root_filedir,*self.dir_stack)
        
class KeyFrame:
    def __init__(self):
        self.frame = None
        self.pos = self.rot = self.scale = False
        self.matrix = Matrix()

#
#   SCENE
#

def unhide_all(layer_col: bpy.types.LayerCollection):
    if layer_col is None:
        return

    layer_col.exclude = False
    layer_col.hide_viewport = False

    col = layer_col.collection
    col.hide_viewport = False
    col.hide_render = False

    for obj in col.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False
        
        if obj.hide_get():
            obj.hide_set(False)

    for child in layer_col.children:
        unhide_all(child)

#
#   VERTEX GROUPS
#

class VertexGroupNormalizer:
    def __init__(self, ob: bpy.types.Object, vgroup_limit: int = 4, clean_tolerance: float = 0.001):
        self.ob = ob
        self.vgroup_limit = vgroup_limit
        self.clean_tolerance = clean_tolerance
        self.arm = get_armature(ob)
        self.bone_names = {b.name for b in self.arm.data.bones if b.use_deform} if self.arm else set()

    def run(self):
        if not self.arm:
            return
        self._clean_weights()
        self._limit_influence()
        self._normalize_weights()

    def _clean_weights(self):
        # Collect all vertices to remove per group, then batch-remove in one call per group
        # instead of one Blender C-API call per vertex.
        to_remove: dict[int, list[int]] = collections.defaultdict(list)
        for v in self.ob.data.vertices:
            for g in v.groups:
                if g.group < len(self.ob.vertex_groups) and self.ob.vertex_groups[g.group].name in self.bone_names:
                    if g.weight < self.clean_tolerance:
                        to_remove[g.group].append(v.index)

        for group_idx, vert_indices in to_remove.items():
            if group_idx < len(self.ob.vertex_groups):
                self.ob.vertex_groups[group_idx].remove(vert_indices)

    def _limit_influence(self):
        to_remove: dict[int, list[int]] = collections.defaultdict(list)

        for v in self.ob.data.vertices:
            groups = sorted(
                (g for g in v.groups if g.group < len(self.ob.vertex_groups) and self.ob.vertex_groups[g.group].name in self.bone_names),
                key=lambda g: -g.weight
            )
            for g in groups[self.vgroup_limit:]:
                to_remove[g.group].append(v.index)

        for group_idx, vert_indices in to_remove.items():
            if group_idx < len(self.ob.vertex_groups):
                self.ob.vertex_groups[group_idx].remove(vert_indices)

    def _normalize_weights(self):
        for v in self.ob.data.vertices:
            groups = [
                (self.ob.vertex_groups[g.group], g.weight)
                for g in v.groups
                if g.group < len(self.ob.vertex_groups) and self.ob.vertex_groups[g.group].name in self.bone_names
            ]
            total = sum(w for _, w in groups)
            if total > 0:
                for vg, w in groups:
                    vg.add([v.index], w / total, 'REPLACE')

_ORDER_VG_RE = re.compile(r"^mesh split (\d+)$", re.IGNORECASE)
 
def parse_order_vg_name(name: str) -> int | None:
    """Return the integer n for a 'mesh split {n}' vgroup name, or None."""
    m = _ORDER_VG_RE.match(name.strip())
    if m is None:
        return None
    n = int(m.group(1))
    if n < 0:
        return None
    return n

#
#   IMPORT
#


def parse_hitbox_line(line: str):
    """Parse a $hbox line. Returns dict with group, bone, min, max, rotation (degrees), scale or None."""
    import re
    pattern = (r'\$hbox\s+(\d+)\s+"([^"]+)"\s+'
               r'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+'
               r'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
               r'(?:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+))?'
               r'(?:\s+([-\d.]+))?')
    match = re.match(pattern, line.strip())
    if not match:
        return None
    g = match.groups()
    return {
        'group':    int(g[0]),
        'bone':     g[1],
        'min':      mathutils.Vector((float(g[2]), float(g[3]), float(g[4]))),
        'max':      mathutils.Vector((float(g[5]), float(g[6]), float(g[7]))),
        'rotation': (float(g[8] or 0), float(g[9] or 0), float(g[10] or 0)),
        'scale':    float(g[11]) if g[11] is not None else -1.0,
    }


#
#   GET
#

def get_hitboxes(ob):
    armature = get_armature(ob) if ob is not None else get_armature()
    if armature is None:
        return []
    avs = getattr(armature.data, 'vs', None)
    if avs is None:
        return []
    return list(avs.hitboxes)

def get_jigglebones(ob : bpy.types.Object | None) -> list[bpy.types.Bone | None]:
    armature = None
    if ob is None:
        armature = get_armature()
    else:
        armature = get_armature(ob)
        
    if armature is None: return []
    
    return [b for b in armature.data.bones if b.vs.bone_is_jigglebone]

def get_attachments(ob : bpy.types.Object | None) -> list[bpy.types.Object | None]:
    armature = None
    if ob is None:
        armature = get_armature()
    else:
        if ob.type == 'ARMATURE':
            armature = ob
        else:
            armature = get_armature(ob)
        
    if armature is None: return []
    
    attchs = []
    for ob in bpy.data.objects:
        if ob.type != 'EMPTY' or ob.parent is None or ob.parent != armature: continue
        if ob.parent_type != 'BONE' or not ob.parent_bone.strip(): continue
        if not ob.vs.dmx_attachment: continue
        
        attchs.append(ob)
        
    return attchs

# I forgot what I even made this for??? Unused function
#def get_collision_cloth_bone_uses(arm_ob: bpy.types.Object, weight_threshold: float) -> set[str]:
#    """Return names of bones that have at least one vertex with weight > weight_threshold
#    in any COLLISION or CLOTHPROXY mesh associated with arm_ob (via Armature modifier
#    or direct parenting).  Uses the evaluated (post-modifier) mesh so Mirror and other
#    modifiers that affect vertex group assignments are respected."""
#    result: set[str] = set()
#    bone_names = {b.name for b in arm_ob.data.bones}
#
#    try:
#        depsgraph = bpy.context.evaluated_depsgraph_get()
#    except Exception:
#        depsgraph = None
#
#    for obj in bpy.data.objects:
#        if obj.type != 'MESH':
#            continue
#        if getattr(getattr(obj, 'vs', None), 'mesh_type', 'DEFAULT') not in ('COLLISION', 'CLOTHPROXY'):
#            continue
#        associated = obj.parent is arm_ob
#        if not associated:
#            for mod in obj.modifiers:
#                if mod.type == 'ARMATURE' and mod.object is arm_ob:
#                    associated = True
#                    break
#        if not associated:
#            continue
#
#        eval_obj = obj.evaluated_get(depsgraph) if depsgraph is not None else None
#        mesh_data = eval_obj.to_mesh() if eval_obj is not None else obj.data
#        vg_source = eval_obj if eval_obj is not None else obj
#
#        try:
#            vg_to_bone = {
#                vg.index: vg.name
#                for vg in vg_source.vertex_groups
#                if vg.name in bone_names
#            }
#            if not vg_to_bone:
#                continue
#            for vert in mesh_data.vertices:
#                for ge in vert.groups:
#                    if ge.group in vg_to_bone and ge.weight > weight_threshold:
#                        result.add(vg_to_bone[ge.group])
#        finally:
#            if eval_obj is not None:
#                eval_obj.to_mesh_clear()
#
#    return result


# Display metadata for each prefab type: (icon, singular label). The default
# output filename suffix and file extension are resolved in export_smd.py.
prefab_type_info = {
    'JIGGLEBONES':   ('BONE_DATA',        'Jigglebones'),
    'ATTACHMENTS':   ('EMPTY_ARROWS',     'Attachments'),
    'HITBOXES':      ('MESH_CUBE',        'Hitboxes'),
    'PROCEDURAL':    ('CON_TRACKTO',      'Procedural'),
}


def prefab_mode_is_dme(scene) -> bool:
    """True when prefabs should be encoded into the model DMX (DME mode) rather than
    written to .qci files. DME embedding is a Source 1 concept only - it is always
    False for ModelDoc / Source 2, which keeps jigglebones and hitboxes in .vmdl."""
    return (State.compiler != Compiler.MODELDOC
            and getattr(scene.vs, 'prefab_export_mode', 'QCI') == 'DME')


def prefab_available_types(arm: bpy.types.Object, scene=None) -> list[tuple[str, int]]:
    """Prefab types that the given armature currently has content for.

    Returns a list of (prefab_type, count) in display order. PROCEDURAL is only
    offered for Source 1 (.vrd); Source 2 procedural export is not implemented.
    """
    if arm is None or arm.type != 'ARMATURE':
        return []

    if scene is None:
        scene = bpy.context.scene

    avs = getattr(arm.data, 'vs', None)
    proc_entries = list(getattr(avs, 'proc_bones', [])) if avs else []

    result: list[tuple[str, int]] = []

    jiggles = get_jigglebones(arm)
    if jiggles:
        result.append(('JIGGLEBONES', len(jiggles)))

    # LOOKAT proc bones can surface as attachments, but only where the exporter
    # actually writes one: MODELDOC never does; DME mode writes one only for a
    # non-zero offset (a zero offset aims the bone directly, no attachment);
    # QCI mode writes one per unique (driver, offset). Match that so a 0,0,0
    # aim-at doesn't add a phantom attachment row. Mirrors the writer logic in
    # writeDMX / PrefabExporter._collect_lookat_attachments.
    attachments = get_attachments(arm)
    lookat_pairs: set[tuple[str, tuple]] = set()
    if State.compiler != Compiler.MODELDOC:
        dme = prefab_mode_is_dme(scene)
        for e in proc_entries:
            if getattr(e, 'proc_type', 'TRIGGER') != 'LOOKAT':
                continue
            dn = e.driver_bone
            if not dn or not arm.data.bones.get(dn):
                continue
            off = tuple(e.lookat_offset)
            if dme and off == (0.0, 0.0, 0.0):
                continue
            lookat_pairs.add((dn, off))
    if attachments or lookat_pairs:
        result.append(('ATTACHMENTS', len(attachments) + len(lookat_pairs)))

    hitboxes = get_hitboxes(arm)
    if hitboxes:
        result.append(('HITBOXES', len(hitboxes)))

    if State.compiler != Compiler.MODELDOC:
        valid_proc = [e for e in proc_entries if e.helper_bone and arm.data.bones.get(e.helper_bone)]
        if valid_proc:
            result.append(('PROCEDURAL', len(valid_proc)))

    return result


def sync_prefab_items(arm_vs, types: list[str]) -> None:
    """Sync an armature's prefab_items collection to exactly `types`,
    preserving each entry's export toggle and filepath across syncs."""
    want = set(types)
    for i in range(len(arm_vs.prefab_items) - 1, -1, -1):
        if arm_vs.prefab_items[i].prefab_type not in want:
            arm_vs.prefab_items.remove(i)
    have = {p.prefab_type for p in arm_vs.prefab_items}
    for t in types:
        if t not in have:
            arm_vs.prefab_items.add().prefab_type = t


def get_armature(ob: bpy.types.Object | bpy.types.Bone | bpy.types.EditBone | bpy.types.PoseBone | None = None) -> bpy.types.Object | None:
    if isinstance(ob, bpy.types.Object):
        if ob.type == 'ARMATURE':
            return ob
        
        arm = ob.find_armature()
        if arm:
            return arm
        
        parent = ob.parent
        while parent:
            if parent.type == 'ARMATURE':
                return parent
            parent = parent.parent
        
        return None

    elif isinstance(ob, bpy.types.Bone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.data.bones.get(ob.name) == ob:
                return o

    elif isinstance(ob, bpy.types.EditBone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.data.edit_bones.get(ob.name) == ob:
                return o

    elif isinstance(ob, bpy.types.PoseBone):
        for o in bpy.data.objects:
            if o.type == 'ARMATURE' and o.pose.bones.get(ob.name) == ob:
                return o

    else:
        ctx_obj = bpy.context.active_object
        if ctx_obj:
            return get_armature(ctx_obj)
        return None

def get_collection_parent(ob, scene) -> bpy.types.Collection | None:
    for collection in scene.collection.children_recursive:
        if ob.name in collection.objects:
            return collection
    
    if ob.name in scene.collection.objects:
        return None
    
    return None

def get_valid_vertexanimation_object(ob : bpy.types.Object | None) -> bpy.types.Object | bpy.types.Collection | None:
    if not is_mesh_compatible(ob): return None
    
    collection = get_collection_parent(ob, bpy.context.scene)
    if collection is None or collection.vs.mute: return ob
    else: return collection

#
#   DATA
#

def get_addon_prefs():
    """Return this add-on's AddonPreferences, or None if it can't be resolved."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None

def get_preserved_bone_prefixes() -> list:
    """Bone-name prefixes (with trailing '.') kept verbatim during Source 2 sanitization.
    'ValveBiped' is always present; users add more in the add-on preferences."""
    prefixes = ["ValveBiped."]
    prefs = get_addon_prefs()
    if prefs:
        for item in prefs.bone_name_prefixes:
            p = item.prefix.strip().rstrip('.').strip()
            if p and (p + ".") not in prefixes:
                prefixes.append(p + ".")
    return prefixes

def get_prefix_shortcut_map() -> dict:
    """Map of shortcut token -> 'Prefix.' expansion for the !shortcut! export-name syntax.
    ValveBiped is always available; its shortcut and any custom prefixes come from prefs."""
    result = {}
    prefs = get_addon_prefs()
    vb_sc = re.sub(r'\W', '', (prefs.valvebiped_shortcut if prefs else 'vbip').strip())
    if vb_sc:
        result[vb_sc] = "ValveBiped.Bip01"
    if prefs:
        for item in prefs.bone_name_prefixes:
            p = item.prefix.strip().rstrip('.').strip()
            sc = re.sub(r'\W', '', item.shortcut.strip())
            if p and sc and sc not in result:
                result[sc] = p + "."
    return result

def sanitize_string(data: typing.Union[str, list], allow_unicode: bool = False, force_modeldoc: bool = False) -> typing.Union[str, list]:
    if isinstance(data, list):
        return [sanitize_string(item, allow_unicode, force_modeldoc) for item in data]

    _data = data.strip()

    if (State.compiler == Compiler.MODELDOC or force_modeldoc) and not allow_unicode:
        matched = next((p for p in get_preserved_bone_prefixes() if _data.startswith(p)), None)
        if matched:
            _data = matched + re.sub(r'[^a-zA-Z0-9_]+', '_', _data[len(matched):])
        else:
            _data = re.sub(r'[^a-zA-Z0-9_]+', '_', _data)
    else:
        _data = re.sub(r'[^\w.]+', '_', _data, flags=re.UNICODE)

    _data = re.sub(r'_+', '_', _data)
    _data = _data.strip('_')

    if not _data:
        return 'unnamed'

    return _data

def sanitize_string_for_delta(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '', name)


def get_dme_corrective_delta_names(ob) -> set:
    """Return the set of corrective delta names derived from an object's DME CORRECTIVE rules.
    Components (e.g. 'brow+anger+mouth') are joined with the corrective separator to produce
    the delta name the engine expects (e.g. 'brow_anger_mouth').
    """
    sep = getCorrectiveShapeSeparator()
    result = set()
    for rule in ob.vs.dme_flex_rules:
        if rule.rule_type == 'CORRECTIVE' and rule.components.strip():
            components = [c.strip() for c in rule.components.split('+') if c.strip()]
            if components:
                result.add(sep.join(components))
    return result


def sanitize_flex_expression_deltas(expr: str, delta_map: dict | None = None) -> str:
    """Sanitize %name delta tokens inside a DME flex expression string.
    If delta_map is provided, shapekey names are remapped to their export delta names first."""
    def _replace(m):
        name = m.group(1)
        if delta_map and name in delta_map:
            return '%' + delta_map[name]
        return '%' + sanitize_string_for_delta(name)
    return re.sub(r'%(\w+)', _replace, expr)


def get_dme_delta_name_map(ob) -> dict:
    """Return a mapping of shapekey_name -> sanitized export delta name.
    Standalone dme_delta_overrides take precedence over per-controller raw_delta_name."""
    if not hasattr(ob, "vs"):
        return {}
    result = {}
    vs = ob.vs
    if hasattr(vs, "dme_flexcontrollers"):
        for fc in vs.dme_flexcontrollers:
            if not fc.shapekey:
                continue
            raw = fc.raw_delta_name.strip() if fc.raw_delta_name and fc.raw_delta_name.strip() else fc.shapekey
            result[fc.shapekey] = sanitize_string_for_delta(raw)
    if hasattr(vs, "dme_delta_overrides"):
        for ov in vs.dme_delta_overrides:
            if ov.shapekey and ov.delta_name and ov.delta_name.strip():
                result[ov.shapekey] = sanitize_string_for_delta(ov.delta_name.strip())
    return result


def get_dme_controller_shapekeys(ob) -> set:
    """Return the set of shape key names referenced directly by a DME flex controller.
    A controller cannot take a split (L/R) delta, so split overrides on these are invalid."""
    if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_flexcontrollers"):
        return set()
    return {fc.shapekey for fc in ob.vs.dme_flexcontrollers if fc.shapekey}


def get_dme_split_delta_map(ob) -> dict:
    """Return {shapekey: sanitized base delta name} for delta overrides that request an
    L/R split AND are eligible (the shape key is not assigned to a flex controller).

    The exporter splits each such shape key into '<base>L' and '<base>R' deltas using the
    mesh stereo balance, doing ahead of time what the compiler would do."""
    if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_delta_overrides"):
        return {}
    controller_keys = get_dme_controller_shapekeys(ob)
    result = {}
    for ov in ob.vs.dme_delta_overrides:
        if not (getattr(ov, "split_lr", False) and ov.shapekey and ov.delta_name and ov.delta_name.strip()):
            continue
        if ov.shapekey in controller_keys:
            continue  # ineligible: controllers can't take split deltas (exporter warns + falls back)
        base = sanitize_string_for_delta(ov.delta_name.strip())
        if base:
            result[ov.shapekey] = base
    return result


def get_dme_split_delta_conflicts(ob) -> set:
    """Return indices of dme_delta_overrides that request split_lr but whose shape key is
    assigned to a flex controller (so the split cannot be performed)."""
    conflicts = set()
    if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_delta_overrides"):
        return conflicts
    controller_keys = get_dme_controller_shapekeys(ob)
    for i, ov in enumerate(ob.vs.dme_delta_overrides):
        if getattr(ov, "split_lr", False) and ov.shapekey and ov.shapekey in controller_keys:
            conflicts.add(i)
    return conflicts


def get_dme_renamed_delta_names(ob) -> set:
    """Return the set of sanitized export delta names produced by renaming (per-controller
    raw_delta_name and dme_delta_overrides). These are valid %delta references in expressions
    even though they don't match a raw shape key name.

    For overrides flagged to split into L/R, the base name is replaced by its '<base>L' and
    '<base>R' variants (the names the split actually produces on export)."""
    split_map = get_dme_split_delta_map(ob)
    names = set()
    for shapekey, delta in get_dme_delta_name_map(ob).items():
        if shapekey in split_map:
            base = split_map[shapekey]
            names.add(f"{base}L")
            names.add(f"{base}R")
        else:
            names.add(delta)
    return names


def get_dme_delta_override_conflicts(ob) -> set:
    """Return the set of dme_delta_overrides indices that conflict.

    An override conflicts when its (sanitized) delta_name either:
      - collides with an existing shape key name that is NOT its own source shapekey, or
      - is shared as a rename target by another override (two overrides -> same name).
    """
    conflicts = set()
    if not hasattr(ob, "vs") or not hasattr(ob.vs, "dme_delta_overrides"):
        return conflicts

    sk = ob.data.shape_keys if (ob.data and hasattr(ob.data, 'shape_keys')) else None
    sk_names = set(sk.key_blocks.keys()) if sk else set()

    # Collect (index, source shapekey, sanitized target) for valid override entries.
    entries = []
    target_counts = collections.Counter()
    for i, ov in enumerate(ob.vs.dme_delta_overrides):
        if not (ov.shapekey and ov.delta_name and ov.delta_name.strip()):
            continue
        target = sanitize_string_for_delta(ov.delta_name.strip())
        if not target:
            continue
        entries.append((i, ov.shapekey, target))
        target_counts[target] += 1

    for i, src, target in entries:
        # Renaming onto an existing shape key that isn't this entry's own source.
        if target in sk_names and target != src:
            conflicts.add(i)
        # Two or more overrides resolving to the same delta name.
        if target_counts[target] > 1:
            conflicts.add(i)

    return conflicts

def on_delta_override_index_changed(self, context):
    """Sync the mesh's active shape key to the selected Delta Map entry's source shapekey."""
    ob = context.active_object
    if not ob:
        return

    mesh : bpy.types.Object = ob if ob.type == 'MESH' else next(
        (child for child in ob.children if child.type == 'MESH'), None
    )
    if not mesh or not mesh.data.shape_keys:
        return

    items = ob.vs.dme_delta_overrides
    idx = ob.vs.dme_delta_overrides_index
    if idx < 0 or idx >= len(items):
        return

    shapekey_name = items[idx].shapekey
    if not shapekey_name:
        return

    sk_idx = mesh.data.shape_keys.key_blocks.find(shapekey_name)
    if sk_idx != -1:
        mesh.active_shape_key_index = sk_idx

_FLEX_MATH_KEYWORDS = frozenset({
    'min', 'max', 'sqrt', 'abs', 'pow', 'clamp', 'atan2', 'log', 'sin', 'cos',
})


def validate_corrective_components(components_str: str, sk_names: set) -> list:
    """Return unknown component names from a '+'-separated components string."""
    if not components_str.strip():
        return []
    return [c for c in (t.strip() for t in components_str.split('+')) if c and c not in sk_names]


def validate_flex_expression(expr: str, sk_names: set, ctrl_names: set, localvar_names: set = frozenset(), stereo_delta_names: set = frozenset(), renamed_delta_names: set = frozenset()) -> tuple:
    """Parse a DME flex expression and return (delta_errors, controller_errors).

    %name  -> must match a shape key, a component of a compound "L+R" shape key, a local var,
              a stereo-generated delta name, or a renamed (override) delta name
    name   -> must match a flex controller, ignoring math keywords
    """
    expanded_sk = sk_names | {part for name in sk_names for part in name.split('+')} | stereo_delta_names | renamed_delta_names
    delta_errors = []
    controller_errors = []
    # $name$ is a compiler definevariable reference - always valid, strip before parsing
    expr_no_defvar = re.sub(r'\$\w+\$', '', expr)
    for m in re.finditer(r'%(\w+)', expr_no_defvar):
        name = m.group(1)
        if name not in expanded_sk and name not in localvar_names:
            delta_errors.append(name)
    stripped = re.sub(r'%\w+', '', expr_no_defvar)
    for m in re.finditer(r'\b([a-zA-Z_]\w*)\b', stripped):
        name = m.group(1)
        if name not in _FLEX_MATH_KEYWORDS and name not in ctrl_names:
            controller_errors.append(name)
    return delta_errors, controller_errors


def _build_dme_ctrl_names(vs) -> set:
    """Build the full set of valid controller name variants (including stereo) for an object's vs."""
    ctrl_names = set()
    for fc in vs.dme_flexcontrollers:
        if not fc.controller_name or not fc.controller_name.strip():
            continue
        name = fc.controller_name.strip()
        ctrl_names.add(name)
        if fc.stereo:
            ctrl_names |= {f"left_{name}", f"right_{name}", f"{name}L", f"{name}R"}
    return ctrl_names


def _build_stereo_delta_names(vs) -> set:
    """Return stereo-generated delta name variants for all stereo flex controllers.

    Uses the sanitized shapekey/raw_delta_name as the base (same as the exporter),
    so eye_shrink controller with eyeshrink shapekey produces eyeshrinkL/eyeshrinkR.
    """
    names = set()
    for fc in vs.dme_flexcontrollers:
        if not fc.stereo or not fc.shapekey:
            continue
        raw = fc.raw_delta_name.strip() if fc.raw_delta_name and fc.raw_delta_name.strip() else fc.shapekey
        base = sanitize_string_for_delta(raw)
        names |= {f"{base}L", f"{base}R", f"left_{base}", f"right_{base}"}
    return names


def validate_dme_flex_for_export(ob) -> list:
    """Return a list of error strings for DME flex data on *ob*. Empty list = valid."""
    errors = []
    vs = ob.vs
    sk = ob.data.shape_keys if ob.data and hasattr(ob.data, 'shape_keys') else None
    sk_names = set(sk.key_blocks.keys()) if sk else set()

    ctrl_names = set()
    for fc in vs.dme_flexcontrollers:
        if not fc.controller_name or not fc.controller_name.strip():
            errors.append(get_id('exporter_err_dme_no_ctrl_name', True).format(ob.name))
            continue
        name = fc.controller_name.strip()
        ctrl_names.add(name)
        if fc.stereo:
            ctrl_names |= {f"left_{name}", f"right_{name}", f"{name}L", f"{name}R"}

    localvar_names = {r.name for r in vs.dme_flex_rules if r.rule_type == 'LOCALVAR' and r.name}
    stereo_delta_names = _build_stereo_delta_names(vs)
    renamed_delta_names = get_dme_renamed_delta_names(ob)

    for rule in vs.dme_flex_rules:
        if rule.rule_type == 'PASSTHROUGH':
            if not rule.name:
                errors.append(get_id('exporter_err_dme_passthrough_no_name', True).format(ob.name))
            elif rule.name not in ctrl_names:
                errors.append(get_id('exporter_err_dme_passthrough_unknown', True).format(ob.name, rule.name))

        elif rule.rule_type == 'EXPRESSION':
            if not rule.name:
                errors.append(get_id('exporter_err_dme_expression_no_name', True).format(ob.name))
            elif rule.name not in sk_names and rule.name not in localvar_names and rule.name not in stereo_delta_names and rule.name not in renamed_delta_names:
                errors.append(get_id('exporter_err_dme_expression_unknown_target', True).format(ob.name, rule.name))
            expr = rule.expression.strip()
            if expr:
                delta_errs, ctrl_errs = validate_flex_expression(expr, sk_names, ctrl_names, localvar_names, stereo_delta_names, renamed_delta_names)
                for n in delta_errs:
                    errors.append(get_id('exporter_err_dme_expression_unknown_delta', True).format(ob.name, n))
                for n in ctrl_errs:
                    errors.append(get_id('exporter_err_dme_expression_unknown_ctrl', True).format(ob.name, n))

        elif rule.rule_type == 'LOCALVAR':
            if not rule.name:
                errors.append(get_id('exporter_err_dme_localvar_no_name', True).format(ob.name))

        elif rule.rule_type == 'CORRECTIVE':
            comp_errs = validate_corrective_components(rule.components, sk_names)
            if not rule.components.strip():
                errors.append(get_id('exporter_err_dme_corrective_no_components', True).format(ob.name))
            else:
                for comp in comp_errs:
                    errors.append(get_id('exporter_err_dme_corrective_unknown_component', True).format(ob.name, comp))

        elif rule.rule_type == 'DOMINATION':
            if not rule.dominator_names.strip():
                errors.append(get_id('exporter_err_dme_domination_no_dominators', True).format(ob.name))
            if not rule.suppressed_names.strip():
                errors.append(get_id('exporter_err_dme_domination_no_suppressed', True).format(ob.name))

    return errors


def sort_bone_by_hierarchy(bones: typing.Iterable[bpy.types.Bone]) -> list[bpy.types.Bone]:
    bone_set = set(bones)
    sorted_bones = []
    visited = set()
    
    def dfs(bone):
        if bone in visited or bone not in bone_set:
            return
        visited.add(bone)
        sorted_bones.append(bone)
        
        for child in sorted(bone.children, key=lambda b: b.name):
            if child in bone_set:
                dfs(child)
    
    roots = [b for b in bone_set if b.parent is None or b.parent not in bone_set]
    
    for root in sorted(roots, key=lambda b: b.name):
        dfs(root)
    
    return sorted_bones

def get_bone_exportname(bone: bpy.types.Bone | bpy.types.PoseBone | None, for_write = False) -> str:
    """Generate the export name for a bone or posebone, respecting custom naming rules."""
    
    if bone is None: 
        return "None"
    elif not isinstance(bone, (bpy.types.Bone, bpy.types.PoseBone)):
        return bone.name if hasattr(bone, "name") else str(bone)

    data_bone = bone.bone if isinstance(bone, bpy.types.PoseBone) else bone
    armature = get_armature(data_bone)
    
    if armature is None: 
        return bone.name
    
    arm_prop = armature.data.vs
    
    if arm_prop.ignore_bone_exportnames and not for_write:
        return bone.name

    def get_bone_side(b: bpy.types.Bone) -> str:
        bone_x = b.matrix_local.to_translation().x
        return (arm_prop.bone_direction_naming_right if bone_x < 0 
                else arm_prop.bone_direction_naming_left)

    scene = bpy.context.scene
    force_s2 = bool(scene and scene.vs.force_source2_bone_sanitize)
    prefix_shortcuts = get_prefix_shortcut_map()

    ordered_bones = sort_bone_by_hierarchy(armature.data.bones)
    name_count = collections.defaultdict(lambda: arm_prop.bone_name_startcount)
    export_names = {}

    for b in ordered_bones:
        b_side = get_bone_side(b)
        raw_name = b.vs.export_name.strip() or b.name
        raw_name = raw_name.replace("*", b_side)

        # Prefix shortcuts: !name! expands to the assigned prefix (e.g. !vbip! -> ValveBiped.Bip01)
        raw_name = re.sub(r'!(\w+)!', lambda m: prefix_shortcuts.get(m.group(1), m.group(0)), raw_name)

        if "$" in raw_name:
            key = (raw_name, b_side)
            final_name = raw_name.replace("$", str(name_count[key])).strip()
            name_count[key] += 1
        else:
            final_name = raw_name

        final_name = sanitize_string(final_name, force_modeldoc=force_s2)
        export_names[b.name] = final_name

    return export_names[data_bone.name]

def get_bone_matrix(data: bpy.types.PoseBone | mathutils.Matrix, bone: bpy.types.PoseBone | None = None,
                    rest_space : bool = False) -> mathutils.Matrix:
    """
    Returns the effective matrix of a PoseBone or matrix with applied export offsets.

    Args:
        data: PoseBone or a 4x4 Matrix.
        bone: Optional PoseBone reference (required for offset properties).
              If not provided and `data` is a PoseBone, it's automatically used.

    Returns:
        Matrix: The final transform matrix with translation and rotation offsets applied.
    """
    # Resolve matrix and bone
    if isinstance(data, bpy.types.PoseBone):
        matrix = data.matrix if not rest_space else data.bone.matrix_local
        bone = data
    elif isinstance(data, mathutils.Matrix):
        matrix = data

    if bone is None:
        return matrix

    b_props = bone.bone.vs

    # Rotation offsets
    rot_x = 0.0 if b_props.ignore_rotation_offset else b_props.export_rotation_offset_x
    rot_y = 0.0 if b_props.ignore_rotation_offset else b_props.export_rotation_offset_y
    rot_z = 0.0 if b_props.ignore_rotation_offset else b_props.export_rotation_offset_z

    rot_offset_matrix = (
        mathutils.Matrix.Rotation(rot_z, 4, 'Z') @ # type: ignore
        mathutils.Matrix.Rotation(rot_y, 4, 'Y') @ # type: ignore
        mathutils.Matrix.Rotation(rot_x, 4, 'X')  # type: ignore
    )

    # Location offsets
    loc_x = 0.0 if b_props.ignore_location_offset else b_props.export_location_offset_x
    loc_y = 0.0 if b_props.ignore_location_offset else b_props.export_location_offset_y
    loc_z = 0.0 if b_props.ignore_location_offset else b_props.export_location_offset_z

    loc_offset_matrix = mathutils.Matrix.Translation((loc_x, loc_y, loc_z))

    # Translation after rotation
    offset_matrix = loc_offset_matrix @ rot_offset_matrix

    # Apply offsets in bone space
    return matrix @ offset_matrix

#
#   BOOL
#

def is_mesh(ob : bpy.types.Object | None) -> bool:
    return ob is not None and ob.type == 'MESH'

def is_armature(ob : bpy.types.Object | None) -> bool:
    return ob is not None and ob.type == 'ARMATURE'

def is_empty(ob : bpy.types.Object | None) -> bool:
    return ob is not None and ob.type == 'EMPTY'

def is_curve(ob : bpy.types.Object | None) -> bool:
    return ob is not None and ob.type == 'CURVE'


KST_ATTACHMENT_COLL = "KST Attachment References"


def _find_layer_collection(layer_coll, name: str):
    if layer_coll.name == name:
        return layer_coll
    for child in layer_coll.children:
        found = _find_layer_collection(child, name)
        if found:
            return found
    return None


def ensure_kst_collection_at_top(scene, view_layer):
    """Get (or create) the hidden KST attachment collection and move it to the
    very top of the outliner, above all other scene children."""
    scene_coll = scene.collection
    coll = bpy.data.collections.get(KST_ATTACHMENT_COLL)
    if coll is None:
        coll = bpy.data.collections.new(KST_ATTACHMENT_COLL)

    children = list(scene_coll.children)
    if not (children and children[0] == coll):
        if coll in children:
            scene_coll.children.unlink(coll)
            children.remove(coll)
        for c in children:
            scene_coll.children.unlink(c)
        scene_coll.children.link(coll)
        for c in children:
            scene_coll.children.link(c)

    coll.hide_render = True
    lc = _find_layer_collection(view_layer.layer_collection, KST_ATTACHMENT_COLL)
    if lc:
        lc.exclude = True

    return coll


# Jigglebone / hitbox serialization lives in the prefab_io subpackage (both import
# and export, co-located per format). Re-exported here so existing
# `from .utils import *` call sites in import_smd keep working unchanged.
from .prefab_io import (
    import_jigglebones_from_dmx_elements,
    import_jigglebones_from_content,
    import_jigglebones_from_kv3,
    import_hitboxes_from_dmx_root,
    import_hitboxes_from_content,
    import_hitboxes_from_kv3,
    import_proc_bones_from_dmx_elements,
    import_proc_bones_from_vrd_content,
)