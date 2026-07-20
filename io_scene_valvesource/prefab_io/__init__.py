"""Prefab serialization for jigglebones, hitboxes and procedural bones.

Each feature module owns both directions (import + export) for all three on-disk
formats (QC text, DME model-DMX, KV3 .vmdl), so a format's writer and reader can
no longer drift apart across files. ``export.prefab`` calls the ``*_kwargs`` /
``qc_*`` / ``write_dme_*`` builders; ``imports`` (via ``utils``) calls the
``import_*`` entry points.

``proceduralbone`` shares its trigger transform math with
``export.prefab.PrefabExporter._write_proc_vrd`` so the VRD and DME procedural-bone
export paths can't drift; its ``import_*`` readers invert that same math to
rebuild ``vs.proc_bones`` entries + slot actions from a DME or VRD source.
"""

from . import jigglebone, hitbox, proceduralbone

from .jigglebone import (
    import_jigglebones_from_dmx_elements,
    import_jigglebones_from_content,
    import_jigglebones_from_kv3,
)
from .hitbox import (
    import_hitboxes_from_dmx_root,
    import_hitboxes_from_content,
    import_hitboxes_from_kv3,
)
from .proceduralbone import (
    import_proc_bones_from_dmx_elements,
    import_proc_bones_from_vrd_content,
)
