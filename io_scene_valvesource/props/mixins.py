__all__ = ['ShapeTypeProps', 'CurveTypeProps', 'JiggleBoneProps', 'ExportableProps']

import bpy
from math import radians
from bpy.props import (StringProperty, BoolProperty, EnumProperty, IntProperty,
                       FloatProperty, FloatVectorProperty, CollectionProperty)
from ..utils import get_id, axes
from .items import VertexAnimation


class ShapeTypeProps():
    flex_stereo_sharpness : FloatProperty(name=get_id("shape_stereo_sharpness"), description=get_id("shape_stereo_sharpness_tip"), default=90, min=0, max=100, subtype='PERCENTAGE')
    flex_stereo_mode : EnumProperty(name=get_id("shape_stereo_mode"), description=get_id("shape_stereo_mode_tip"),
                                    items=tuple(list(axes) + [('VGROUP', 'Vertex Group', get_id("shape_stereo_mode_vgroup"))]), default='X')
    flex_stereo_vg : StringProperty(name=get_id("shape_stereo_vgroup"), description=get_id("shape_stereo_vgroup_tip"))
    bake_shapekey_as_basis_normals : BoolProperty(name=get_id("bake_shapekey_as_basis_normals"), description=get_id("bake_shapekey_as_basis_normals_tip"))
    normalize_shapekeys : BoolProperty(name=get_id('prop_normalize_shapekeys'), description=get_id('prop_normalize_shapekeys_tip'), default=True)


class CurveTypeProps():
    faces : EnumProperty(name=get_id("curve_poly_side"), description=get_id("curve_poly_side_tip"), default='FORWARD', items=(
        ('FORWARD', get_id("curve_poly_side_fwd"), ''),
        ('BACKWARD', get_id("curve_poly_side_back"), ''),
        ('BOTH', get_id("curve_poly_side_both"), '')))


class JiggleBoneProps():
    bone_is_jigglebone : BoolProperty(name=get_id('prop_bone_is_jigglebone'), description=get_id('prop_bone_is_jigglebone_tip'), default=False)
    use_bone_length_for_jigglebone_length : BoolProperty(name=get_id('prop_use_bone_length_for_jb'), description=get_id('prop_use_bone_length_for_jb_tip'), default=True)

    jiggle_flex_type : EnumProperty(name=get_id('prop_jiggle_flex_type'), description=get_id('prop_jiggle_flex_type_tip'), items=[('FLEXIBLE', 'Flexible', ''), ('RIGID', 'Rigid', ''), ('NONE', 'None', '')], default='FLEXIBLE')

    jiggle_length : FloatProperty(name=get_id('prop_jiggle_length'), description=get_id('prop_jiggle_length_tip'), default=0, min=0, precision=4)
    jiggle_tip_mass : FloatProperty(name=get_id('prop_jiggle_tip_mass'), description=get_id('prop_jiggle_tip_mass_tip'), precision=2, default=0, min=0, max=1000)
    jiggle_yaw_stiffness : FloatProperty(name=get_id('prop_jiggle_yaw_stiffness'), description=get_id('prop_jiggle_yaw_stiffness_tip'), default=100, min=0, soft_max=1000, precision=4)
    jiggle_yaw_damping : FloatProperty(name=get_id('prop_jiggle_yaw_damping'), description=get_id('prop_jiggle_yaw_damping_tip'), default=0, min=0, soft_max=20, precision=4)
    jiggle_pitch_stiffness : FloatProperty(name=get_id('prop_jiggle_pitch_stiffness'), description=get_id('prop_jiggle_pitch_stiffness_tip'), default=100, min=0, soft_max=1000, precision=4)
    jiggle_pitch_damping : FloatProperty(name=get_id('prop_jiggle_pitch_damping'), description=get_id('prop_jiggle_pitch_damping_tip'), default=0, min=0, soft_max=20, precision=4)

    jiggle_allow_length_flex : BoolProperty(name=get_id('prop_jiggle_allow_length_flex'), description=get_id('prop_jiggle_allow_length_flex_tip'), default=False)
    jiggle_along_stiffness : FloatProperty(name=get_id('prop_jiggle_along_stiffness'), description=get_id('prop_jiggle_along_stiffness_tip'), default=100, min=0, soft_max=1000, precision=4)
    jiggle_along_damping : FloatProperty(name=get_id('prop_jiggle_along_damping'), description=get_id('prop_jiggle_along_damping_tip'), default=0, min=0, soft_max=20, precision=4)

    jiggle_base_type : EnumProperty(name=get_id('prop_jiggle_base_type'), description=get_id('prop_jiggle_base_type_tip'), items=[('BASESPRING', 'Has Base Spring', ''), ('BOING', 'Is Boing', ''), ('NONE', 'None', '')], default='NONE')

    jiggle_base_stiffness : FloatProperty(name=get_id('prop_jiggle_base_stiffness'), description=get_id('prop_jiggle_base_stiffness_tip'), default=100, min=0, soft_max=1000, precision=4)
    jiggle_base_damping : FloatProperty(name=get_id('prop_jiggle_base_damping'), description=get_id('prop_jiggle_base_damping_tip'), default=0, min=0, soft_max=100, precision=4)
    jiggle_base_mass : IntProperty(name=get_id('prop_jiggle_base_mass'), description=get_id('prop_jiggle_base_mass_tip'), default=0, min=0)

    jiggle_has_left_constraint : BoolProperty(name=get_id('prop_jiggle_side_constraint'), description=get_id('prop_jiggle_side_constraint_tip'), default=False)
    jiggle_left_constraint_min : FloatProperty(name=get_id('prop_jiggle_side_constraint_min'), description=get_id('prop_jiggle_side_constraint_min_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_left_constraint_max : FloatProperty(name=get_id('prop_jiggle_side_constraint_max'), description=get_id('prop_jiggle_side_constraint_max_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_left_friction : FloatProperty(name=get_id('prop_jiggle_side_friction'), description=get_id('prop_jiggle_side_friction_tip'), precision=3, default=0.0, min=0, soft_max=20.0)

    jiggle_has_up_constraint : BoolProperty(name=get_id('prop_jiggle_up_constraint'), description=get_id('prop_jiggle_up_constraint_tip'), default=False)
    jiggle_up_constraint_min : FloatProperty(name=get_id('prop_jiggle_up_constraint_min'), description=get_id('prop_jiggle_up_constraint_min_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_up_constraint_max : FloatProperty(name=get_id('prop_jiggle_up_constraint_max'), description=get_id('prop_jiggle_up_constraint_max_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_up_friction : FloatProperty(name=get_id('prop_jiggle_up_friction'), description=get_id('prop_jiggle_up_friction_tip'), precision=3, default=0.0, min=0, soft_max=20.0)

    jiggle_has_forward_constraint : BoolProperty(name=get_id('prop_jiggle_forward_constraint'), description=get_id('prop_jiggle_forward_constraint_tip'), default=False)
    jiggle_forward_constraint_min : FloatProperty(name=get_id('prop_jiggle_forward_constraint_min'), description=get_id('prop_jiggle_forward_constraint_min_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_forward_constraint_max : FloatProperty(name=get_id('prop_jiggle_forward_constraint_max'), description=get_id('prop_jiggle_forward_constraint_max_tip'), unit='LENGTH', default=0.0, min=0, soft_max=15, precision=2)
    jiggle_forward_friction : FloatProperty(name=get_id('prop_jiggle_forward_friction'), description=get_id('prop_jiggle_forward_friction_tip'), precision=3, default=0.0, min=0, soft_max=20.0)

    jiggle_has_yaw_constraint : BoolProperty(name=get_id('prop_jiggle_yaw_constraint'), description=get_id('prop_jiggle_yaw_constraint_tip'), default=False)
    jiggle_yaw_constraint_min : FloatProperty(name=get_id('prop_jiggle_yaw_constraint_min'), description=get_id('prop_jiggle_yaw_constraint_min_tip'), unit='ROTATION', default=0.0, min=0, soft_max=radians(360), precision=2)
    jiggle_yaw_constraint_max : FloatProperty(name=get_id('prop_jiggle_yaw_constraint_max'), description=get_id('prop_jiggle_yaw_constraint_max_tip'), unit='ROTATION', default=0.0, min=0, soft_max=radians(360), precision=2)
    jiggle_yaw_friction : FloatProperty(name=get_id('prop_jiggle_yaw_friction'), description=get_id('prop_jiggle_yaw_friction_tip'), precision=3, default=0.0, min=0, soft_max=20.0)

    jiggle_has_pitch_constraint : BoolProperty(name=get_id('prop_jiggle_pitch_constraint'), description=get_id('prop_jiggle_pitch_constraint_tip'), default=False)
    jiggle_pitch_constraint_min : FloatProperty(name=get_id('prop_jiggle_pitch_constraint_min'), description=get_id('prop_jiggle_pitch_constraint_min_tip'), unit='ROTATION', default=0.0, min=0, soft_max=radians(360), precision=2)
    jiggle_pitch_constraint_max : FloatProperty(name=get_id('prop_jiggle_pitch_constraint_max'), description=get_id('prop_jiggle_pitch_constraint_max_tip'), unit='ROTATION', default=0.0, min=0, soft_max=radians(360), precision=2)
    jiggle_pitch_friction : FloatProperty(name=get_id('prop_jiggle_pitch_friction'), description=get_id('prop_jiggle_pitch_friction_tip'), precision=3, default=0.0, min=0, soft_max=20.0)

    jiggle_has_angle_constraint : BoolProperty(name=get_id('prop_jiggle_angle_constraint'), description=get_id('prop_jiggle_angle_constraint_tip'), default=False)
    jiggle_angle_constraint : FloatProperty(name=get_id('prop_jiggle_angular_constraint'), description=get_id('prop_jiggle_angular_constraint_tip'), precision=3, unit='ROTATION', default=0.0, min=0, soft_max=radians(360))

    jiggle_impact_speed : IntProperty(name=get_id('prop_jiggle_impact_speed'), description=get_id('prop_jiggle_impact_speed_tip'), min=0, soft_max=1000)
    jiggle_impact_angle : FloatProperty(name=get_id('prop_jiggle_impact_angle'), description=get_id('prop_jiggle_impact_angle_tip'), precision=3, unit='ROTATION', default=0.0, min=0, soft_max=radians(360))
    jiggle_damping_rate : FloatProperty(name=get_id('prop_jiggle_damping_rate'), description=get_id('prop_jiggle_damping_rate_tip'), precision=3, default=0.0, min=0, soft_max=10)
    jiggle_frequency : FloatProperty(name=get_id('prop_jiggle_frequency'), description=get_id('prop_jiggle_frequency_tip'), precision=3, default=0.0, min=0, soft_max=1000)
    jiggle_amplitude : FloatProperty(name=get_id('prop_jiggle_amplitude'), description=get_id('prop_jiggle_amplitude_tip'), precision=3, default=0.0, min=0, soft_max=1000)

    # Collider (Source 2): tapered collision capsule with independently-scaled endpoints
    jiggle_has_collision : BoolProperty(name=get_id('prop_jiggle_has_collision'), description=get_id('prop_jiggle_has_collision_tip'), default=False)
    jiggle_collision_radius0 : FloatProperty(name=get_id('prop_jiggle_collision_radius0'), description=get_id('prop_jiggle_collision_radius0_tip'), default=1.0, min=0.0, precision=4)
    jiggle_collision_radius1 : FloatProperty(name=get_id('prop_jiggle_collision_radius1'), description=get_id('prop_jiggle_collision_radius1_tip'), default=1.0, min=0.0, precision=4)
    jiggle_collision_point0 : FloatVectorProperty(name=get_id('prop_jiggle_collision_point0'), description=get_id('prop_jiggle_collision_point0_tip'), size=3, subtype='XYZ', default=(0.0, 0.0, 0.0), precision=4)
    jiggle_collision_point1 : FloatVectorProperty(name=get_id('prop_jiggle_collision_point1'), description=get_id('prop_jiggle_collision_point1_tip'), size=3, subtype='XYZ', default=(10.0, 0.0, 0.0), precision=4)


class ExportableProps():
    flex_controller_modes = (
        ('SIMPLE',   "Simple",   get_id("controllers_simple_tip")),
        ('ADVANCED', "Advanced", get_id("controllers_advanced_tip")),
        ('DME',      "DMX",      get_id("controllers_dme_tip")),
    )

    export : BoolProperty(name=get_id("scene_export"), description=get_id("use_scene_export_tip"), default=True)
    subdir : StringProperty(name=get_id("subdir"), description=get_id("subdir_tip"))
    flex_controller_mode : EnumProperty(name=get_id("controllers_mode"), description=get_id("controllers_mode_tip"), items=flex_controller_modes, default='DME')
    flex_controller_source : StringProperty(name=get_id("controller_source"), description=get_id("controllers_source_tip"), subtype='FILE_PATH', options={'PATH_SUPPORTS_BLEND_RELATIVE'})

    vertex_animations : CollectionProperty(name=get_id("vca_group_props"), type=VertexAnimation)
    active_vertex_animation : IntProperty(default=-1)

    use_toon_edgeline : BoolProperty(name="Use Toon Edge Line", description=get_id("prop_use_toon_edgeline_tip"), default=False)
    edgeline_per_material : BoolProperty(name="Edgeline Per Material", description=get_id("prop_edgeline_per_material_tip"), default=False)
    base_toon_edgeline_thickness : FloatProperty(name="Thickness", description=get_id("prop_edgeline_thickness_tip"), default=0.15, min=0.001, soft_max=1.0, precision=3)
    toon_edgeline_vertexgroup : StringProperty(name='Vertex Group Ratio', description=get_id("prop_edgeline_vgroup_tip"), default='')
    export_edgeline_separately : BoolProperty(name="Export Edgeline Separately", description=get_id("prop_export_edgeline_separately_tip"), default=False)
    edgeline_weld : BoolProperty(name="Weld", description=get_id("prop_edgeline_weld_tip"), default=True)

    non_exportable_vgroup : StringProperty(name=get_id('prop_non_exportable_vgroup'), description=get_id("prop_non_exportable_vgroup_tip"), default='')
    non_exportable_vgroup_tolerance : FloatProperty(name=get_id('prop_non_exportable_vgroup_tolerance'), description=get_id("prop_non_exportable_vgroup_tolerance_tip"), default=0.90, min=0.8, max=1.0, precision=2)

    use_mesh_split : BoolProperty(name='Separate Mesh Split', description=get_id("prop_use_mesh_split_tip"), default=False)
    export_mesh_split_separately : BoolProperty(name='Export Mesh Split Separately', description=get_id("prop_export_mesh_split_separately_tip"), default=False)
    mesh_split_threshold : FloatProperty(name='Mesh Split Threshold', description=get_id("prop_mesh_split_threshold_tip"), default=0.95, min=0.8, max=1.0, precision=2)
    max_mesh_split : IntProperty(name='Max Order Number', description=get_id("prop_max_mesh_split_tip"), default=16, max=16, min=1)

    show_vertexanim_items : BoolProperty()

    generate_backface : BoolProperty(name='Generate Backface', description=get_id("prop_generate_backface_tip"), default=False)
    backface_vgroup : StringProperty(name='Backface Group', description=get_id("prop_backface_vgroup_tip"), default='')
    backface_vgroup_tolerance : FloatProperty(name='Backface Tolerance', description=get_id("prop_backface_vgroup_tolerance_tip"), default=0.90, min=0.8, max=1.0, precision=2)

    generate_lods : BoolProperty(name='Generate LODs on Export', description=get_id("prop_generate_lods_tip"), default=False)
    lod_count : IntProperty(name='LOD count', description=get_id("prop_lod_count_tip"), default=1, min=1, soft_max=3)
    decimate_factor : FloatProperty(name='Decimation Per LOD', description=get_id("prop_decimate_factor_tip"), default=50.0, min=0, soft_max=100, precision=2)
