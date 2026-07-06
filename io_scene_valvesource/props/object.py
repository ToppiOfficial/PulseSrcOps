__all__ = [
    'ValveSource_MeshProps',
    'ValveSource_SurfaceProps',
    'ValveSource_CurveProps',
    'ValveSource_TextProps',
    'ValveSource_ObjectProps',
]

import bpy
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       CollectionProperty, FloatProperty, FloatVectorProperty, PointerProperty)
from ..utils import get_id, hitbox_group, on_delta_override_index_changed
from .items import FlexControllerItem, DmeFlexRuleItem, DmeDeltaNameOverride, VertexAnimation, ValveSource_FloatMapRemap, AttachmentDisplayMeshItem
from .mixins import ShapeTypeProps, CurveTypeProps, ExportableProps
from .scene import on_flexcontroller_index_changed


class ValveSource_MeshProps(ShapeTypeProps, bpy.types.PropertyGroup):
    pass


class ValveSource_SurfaceProps(ShapeTypeProps, CurveTypeProps, bpy.types.PropertyGroup):
    pass


class ValveSource_CurveProps(ShapeTypeProps, CurveTypeProps, bpy.types.PropertyGroup):
    pass


class ValveSource_TextProps(CurveTypeProps, bpy.types.PropertyGroup):
    pass


class ValveSource_ObjectProps(ExportableProps, bpy.types.PropertyGroup):
    mesh_type : EnumProperty(
        name="Mesh Type",
        description="Controls export role and feature availability for this mesh",
        items=[
            ('DEFAULT',    "Default",    "Standard export with all features"),
            ('COLLISION',  "Collision",  "Physics mesh: no materials, no post-process, max 1 bone influence per vertex"),
            ('CLOTHPROXY', "Cloth Proxy", "Cloth proxy: no materials, cloth DMX attributes, min 4–max 8 bone influences, DMX format required"),
        ],
        default='DEFAULT',
    )
    action_filter : StringProperty(name=get_id("slot_filter"), description=get_id("slot_filter_tip"), default="*")
    triangulate : BoolProperty(name=get_id("triangulate"), description=get_id("triangulate_tip"), default=False)
    vertex_map_remaps : CollectionProperty(name="Vertes map remaps", type=ValveSource_FloatMapRemap)

    dme_flexcontrollers : CollectionProperty(name='Flex Controllers', type=FlexControllerItem)
    dme_flexcontrollers_index : IntProperty(default=-1, update=on_flexcontroller_index_changed)
    dme_flex_rules : CollectionProperty(name='Flex Rules', type=DmeFlexRuleItem)
    dme_flex_rules_index : IntProperty(default=-1)
    dme_delta_overrides : CollectionProperty(name='Delta Name Overrides', type=DmeDeltaNameOverride)
    dme_delta_overrides_index : IntProperty(default=-1, update=on_delta_override_index_changed)

    dmx_attachment : BoolProperty(name=get_id('prop_dmx_attachment'), description=get_id('prop_dmx_attachment_tip'), default=False)
    attachment_display_meshes : CollectionProperty(type=AttachmentDisplayMeshItem)
    attachment_display_meshes_index : IntProperty(default=-1)
    attachment_display_mesh_render_index : IntProperty(default=-1)
