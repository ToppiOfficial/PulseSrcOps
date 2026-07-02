"""Prefab serialization for jigglebones, hitboxes and procedural bones.

Each feature module owns both directions (import + export) for all three on-disk
formats (QC text, DME model-DMX, KV3 .vmdl), so a format's writer and reader can
no longer drift apart across files. ``export_smd`` calls the ``*_kwargs`` /
``qc_*`` / ``write_dme_*`` builders; ``import_smd`` (via ``utils``) calls the
``import_*`` entry points.

``proceduralbone`` is export-only for now (DME model-DMX): it shares its trigger
transform math with ``export_smd.PrefabExporter._write_proc_vrd`` so the VRD and
DME procedural-bone paths can't drift.
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
