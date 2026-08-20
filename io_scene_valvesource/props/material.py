__all__ = ['ValveSource_MaterialProps', 'material_path_items', '_on_blend_load_migrate_material_paths']

import bpy
from bpy.props import StringProperty, EnumProperty
from bpy.app.handlers import persistent
from ..utils import get_id


# Cached so the strings we return stay referenced (dynamic enum items must not be
# garbage-collected while Blender holds them).
_material_path_items_cache = []

def material_path_items(self, context):
    global _material_path_items_cache
    paths = context.scene.vs.material_paths if context and context.scene else []
    items = [(str(i), item.path if item.path else get_id("dmx_mat_path_none"), "", i)
             for i, item in enumerate(paths)]
    _material_path_items_cache = items or [('0', get_id("dmx_mat_path_none"), "", 0)]
    return _material_path_items_cache


class ValveSource_MaterialProps(bpy.types.PropertyGroup):
    material_path_index : EnumProperty(name=get_id("dmx_mat_path"), description=get_id("prop_material_path_index_tip"), items=material_path_items)
    # Legacy per-material override. Kept registered so pre-collection .blend files still
    # load their value for _on_blend_load_migrate_material_paths to pick up; not in the UI.
    override_dmx_export_path : StringProperty(name='Material Path', description=get_id("prop_override_dmx_export_path_tip"), default='')


def _normalize(path: str) -> str:
    return path.strip().replace('\\', '/').strip('/')


@persistent
def _on_blend_load_migrate_material_paths(filepath):
    """Fold the pre-collection single material path (and any per-material overrides) into
    scene.vs.material_paths. Runs once per file - a scene that already has entries is left
    alone."""
    overrides = []
    for mat in bpy.data.materials:
        path = _normalize(mat.vs.override_dmx_export_path)
        if path and path not in overrides:
            overrides.append(path)

    for scene in bpy.data.scenes:
        vs = scene.vs
        if len(vs.material_paths):
            continue
        legacy = _normalize(vs.material_path)
        if not legacy and not overrides:
            continue
        # Entry 0 is always the scene default, even when blank, so an override doesn't
        # silently claim the slot every material points at by default.
        for path in [legacy] + [p for p in overrides if p != legacy]:
            vs.material_paths.add().path = path

    if not overrides:
        return

    # Every migrated scene gets the same entries in the same order, so one lookup serves.
    reference = next((s.vs.material_paths for s in bpy.data.scenes if len(s.vs.material_paths)), None)
    if reference is None:
        return
    lookup = {item.path: i for i, item in enumerate(reference)}
    for mat in bpy.data.materials:
        path = _normalize(mat.vs.override_dmx_export_path)
        if not path:
            continue
        try:
            mat.vs.material_path_index = str(lookup[path])
        except (KeyError, TypeError):
            continue
        mat.vs.override_dmx_export_path = ''
