__all__ = [
    # items
    'ValveSource_FloatMapRemap',
    'PrefabItem',
    'FlexControllerItem',
    'DmeFlexRuleItem',
    'DmeDeltaNameOverride',
    'VertexAnimation',
    'ArmatureItemEntry',
    'HitboxEntry',
    'ProcBoneEntry',
    'AttachmentDisplayMeshItem',
    'BoneNamePrefixItem',
    'MaterialPathItem',
    # mixins
    'ShapeTypeProps',
    'CurveTypeProps',
    'JiggleBoneProps',
    'ExportableProps',
    # scene
    'ValveSource_Exportable',
    'ValveSource_SceneProps',
    # object
    'ValveSource_MeshProps',
    'ValveSource_SurfaceProps',
    'ValveSource_CurveProps',
    'ValveSource_TextProps',
    'ValveSource_ObjectProps',
    # armature
    'ValveSource_BoneProps',
    'ValveSource_ArmatureProps',
    '_on_armature_data_updated',
    '_on_blend_load_refresh_hitbox_snapshot',
    # collection
    'ValveSource_CollectionProps',
    # material
    'ValveSource_MaterialProps',
    '_on_blend_load_migrate_material_paths',
]

if "bpy" in dir():
    import importlib
    from . import items, mixins, scene, object, armature, collection, material
    for _mod in [items, mixins, scene, object, armature, collection, material]:
        importlib.reload(_mod)
else:
    from . import items, mixins, scene, object, armature, collection, material

from .items import *
from .mixins import *
from .scene import *
from .object import *
from .armature import *
from .collection import *
from .material import *
