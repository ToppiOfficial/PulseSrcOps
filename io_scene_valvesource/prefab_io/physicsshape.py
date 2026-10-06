"""Bone-attached physics primitives (capsule / box), embedded into the model DMX for
PulseModel (Source 1, model 22). Written under ``DmePhysicsPrimitiveList`` so they never
collide with mesh-based ``DmePhysicsShape`` collision. See PHYSICS_SHAPES_SPEC.md.
"""

import math

from mathutils import Vector, Euler

from .. import datamodel


def _capsule_points(entry):
    # Same convention as hitbox capsules: rotation spins the P0/P1 segment about its midpoint.
    mn  = Vector(entry.vec_min)
    mx  = Vector(entry.vec_max)
    ctr = (mn + mx) * 0.5
    rot = Euler((entry.rotation[0], entry.rotation[1], entry.rotation[2]), 'XYZ').to_matrix()
    return ctr + rot @ (mn - ctr), ctr + rot @ (mx - ctr)


def element_class(entry) -> str:
    return "DmePhysicsCapsule" if entry.shape_type == 'CAPSULE' else "DmePhysicsBox"


def write_dme_attrs(el, entry, bone_export: str) -> None:
    el["boneName"] = bone_export
    # True: one piece with every other merge=True primitive on the same bone.
    el["merge"]    = bool(entry.merge)
    if entry.shape_type == 'CAPSULE':
        p0, p1 = _capsule_points(entry)
        el["point0"]  = datamodel.Vector3(p0)
        el["point1"]  = datamodel.Vector3(p1)
        el["radius0"] = float(entry.radius0)
        el["radius1"] = float(entry.radius1)
        el["segments"] = max(3, min(64, int(entry.segments)))
    else:
        el["minBounds"]   = datamodel.Vector3(Vector(entry.vec_min))
        el["maxBounds"]   = datamodel.Vector3(Vector(entry.vec_max))
        # Euler degrees (pitch, yaw, roll) as Vector3, matching DmeHitbox.orientation.
        el["orientation"] = datamodel.Vector3(tuple(math.degrees(a) for a in entry.rotation))
