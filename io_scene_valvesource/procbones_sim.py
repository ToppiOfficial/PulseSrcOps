#   MIT License
#   
#   Copyright (c) 2024 Jakob
#   Copyright (c) 2026 Toppi
#   
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#   
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#   
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.

import re as _re
import time
import math
import bpy
from mathutils import Vector, Matrix, Quaternion

# FIXME: When the driver bone is also the parent of the helper, the procedural simulation just borked. WHY!?

# -- Per-bone simulation state -------------------------------------------------

class BoneSimState:
    __slots__ = (
        'tip_position', 'tip_velocity',
        'base_offset', 'base_velocity',
        'boing_time', 'boing_direction', 'prev_speed',
        'last_sim_time',
        'base_abs_pos',
    )

    def __init__(self):
        self.tip_position: Vector | None = None
        self.tip_velocity: Vector = Vector()
        self.base_offset:  Vector = Vector()
        self.base_velocity: Vector = Vector()
        self.boing_time: float = 0.0
        self.boing_direction: Vector | None = None
        self.prev_speed: float = 0.0
        self.last_sim_time: float | None = None
        self.base_abs_pos: Vector | None = None


_states: dict[tuple[str, str], BoneSimState] = {}
_tick_sim_world: dict[tuple[str, str], Matrix] = {}
_timer_handle = None
_last_real_time: float = 0.0

# Live counts of bones simulated in the most recent pass, for the viewport HUD.
# Accumulated across all armatures per pass; _live_* are the published totals.
_jiggle_count_acc: int = 0
_proc_count_acc: int = 0
_live_jiggle_count: int = 0
_live_proc_count: int = 0


def get_sim_counts() -> tuple[int, int]:
    """(jiggle_bones, proc_bones) actually simulated in the latest tick."""
    return _live_jiggle_count, _live_proc_count

# arm_name -> depth-sorted list of jiggle bone names (rebuilt on reset / rig change)
_jiggle_bone_cache: dict[str, list[str]] = {}
# scene_name -> list of armature object names that have jiggle/proc bones
_sim_arm_cache: dict[str, list[str]] = {}

# key: (arm_name, entry_index, action_name, slot_name)
# value: list of (driver_quat, helper_quat) or None (empty = no triggers found)
_proc_trigger_cache: dict[tuple, list] = {}
_building_proc_cache: bool = False

# Tracks helper bones whose constraints/drivers are currently muted by the sim.
# key: (arm_name, bone_name)
_overridden_helpers: set[tuple] = set()
# Saved original mute states before we overrode them.
# 'C' keys = constraints, 'D' keys = driver fcurves.
_helper_saved_mutes: dict[tuple, bool] = {}


# -- Helpers -------------------------------------------------------------------

def _is_source2(scene) -> bool:
    try:
        vs = scene.vs
        return vs.export_format == 'DMX' and vs.dmx_format in ('22', '22_modeldoc')
    except Exception:
        return False


def _get_state(arm_ob, pb) -> BoneSimState:
    key = (arm_ob.name, pb.name)
    if key not in _states:
        _states[key] = BoneSimState()
    return _states[key]


def _bone_depth(pb) -> int:
    d, p = 0, pb.parent
    while p:
        d += 1
        p = p.parent
    return d


def _get_length(pb, jvs) -> float:
    if jvs.use_bone_length_for_jigglebone_length:
        return pb.bone.length
    return jvs.jiggle_length if jvs.jiggle_length > 0.0 else pb.bone.length


def _get_cols(is_s2: bool) -> tuple[int, int, int]:
    # (fwd_col, yaw_perp_col, pitch_perp_col)
    # Source 2: X-forward (col 0); Source 1: Z-forward (col 2)
    return (0, 1, 2) if is_s2 else (2, 0, 1)


def _get_export_offset_mat(pb) -> Matrix:
    bvs = pb.bone.vs
    if bvs.ignore_rotation_offset:
        return Matrix.Identity(4)
    return (Matrix.Rotation(bvs.export_rotation_offset_z, 4, 'Z') @
            Matrix.Rotation(bvs.export_rotation_offset_y, 4, 'Y') @
            Matrix.Rotation(bvs.export_rotation_offset_x, 4, 'X'))


def _get_animated_goal(arm_ob, pb, arm_world_inv: Matrix) -> tuple:
    """Compute goal matrices from the parent chain, bypassing this bone's matrix_basis.

    pb.matrix is contaminated by our own simulation writes on non-keyframed bones.
    For jiggle parents already processed this tick, _tick_sim_world holds the fresh
    simulation result - using it gives zero lag so child goals track the parent's
    current simulated rotation instantly, eliminating the per-level cascade jitter.
    For non-jiggle parents, pb.parent.matrix is the clean animated pose.
    arm_world_inv is pre-computed by simulate_armature and passed in to avoid
    recomputing it per bone.
    """
    if pb.parent:
        parent_key = (arm_ob.name, pb.parent.name)
        if pb.parent.bone.vs.bone_is_jigglebone and parent_key in _tick_sim_world:
            parent_arm = arm_world_inv @ _tick_sim_world[parent_key]
        else:
            parent_arm = pb.parent.matrix
        bone_in_parent = pb.parent.bone.matrix_local.inverted_safe() @ pb.bone.matrix_local
        arm_mat = parent_arm @ bone_in_parent
    else:
        arm_mat = pb.bone.matrix_local.copy()
    anim_world = arm_ob.matrix_world @ arm_mat
    goal_world  = anim_world @ _get_export_offset_mat(pb)
    return anim_world, goal_world


def _constrain_axis(state: BoneSimState, axis: Vector,
                    enabled: bool, lo: float, hi: float,
                    friction: float, dt: float) -> None:
    if not enabled:
        return
    proj = state.base_offset.dot(axis)
    if proj < lo:
        state.base_offset += axis * (lo - proj)
        comp = state.base_velocity.dot(axis)
        if comp < 0.0 and friction > 0.0:
            state.base_velocity -= axis * comp * min(1.0, friction * dt)
    elif proj > hi:
        state.base_offset += axis * (hi - proj)
        comp = state.base_velocity.dot(axis)
        if comp > 0.0 and friction > 0.0:
            state.base_velocity -= axis * comp * min(1.0, friction * dt)


# -- Per-bone simulation step --------------------------------------------------

def _sim_bone(arm_ob, pb, dt: float, is_s2: bool, arm_world_inv: Matrix) -> None:
    jvs  = pb.bone.vs
    state = _get_state(arm_ob, pb)

    now   = time.perf_counter()
    stale = state.last_sim_time is None or (now - state.last_sim_time) > 0.5
    state.last_sim_time = now

    fwd_col, yp_col, pp_col = _get_cols(is_s2)

    # Compute goal from parent chain - NOT from pb.matrix, which retains our own
    # simulation writes on non-keyframed bones and would make the goal chase the sim.
    anim_world, goal_world = _get_animated_goal(arm_ob, pb, arm_world_inv)

    _fwd_vec  = Vector([goal_world[r][fwd_col] for r in range(3)])
    _yp_vec   = Vector([goal_world[r][yp_col]  for r in range(3)])
    _pp_vec   = Vector([goal_world[r][pp_col]  for r in range(3)])
    fwd_scale = _fwd_vec.length
    yp_scale  = _yp_vec.length
    pp_scale  = _pp_vec.length
    export_fwd   = _fwd_vec.normalized() if fwd_scale > 1e-9 else Vector((0.0, 0.0, 1.0))
    export_perp1 = _yp_vec.normalized()  if yp_scale  > 1e-9 else Vector((1.0, 0.0, 0.0))
    export_perp2 = _pp_vec.normalized()  if pp_scale  > 1e-9 else Vector((0.0, 1.0, 0.0))
    goal_base    = goal_world.to_translation()
    length       = _get_length(pb, jvs) * fwd_scale
    goal_tip     = goal_base + export_fwd * length

    # Start from animated matrix; may be overwritten below
    new_world = anim_world.copy()

    # -- Boing: squash-and-stretch scale jiggle triggered by impact ------------
    if jvs.jiggle_base_type == 'BOING':
        anim_base = anim_world.to_translation()

        if stale or state.tip_position is None:
            state.tip_position = anim_base.copy()
            state.tip_velocity = Vector((0.0, 0.0, 1.0))
            state.prev_speed   = 0.0
        else:
            raw_delta = anim_base - state.tip_position
            state.tip_position = anim_base.copy()

            speed    = raw_delta.length / dt if dt > 1e-6 else 0.0
            velocity = raw_delta.normalized() if raw_delta.length > 1e-6 else state.tip_velocity.copy()

            state.boing_time += dt

            _MIN_SPEED       = 5.0
            _MIN_REBOING_GAP = 0.5

            if ((speed > _MIN_SPEED or state.prev_speed > _MIN_SPEED)
                    and state.boing_time > _MIN_REBOING_GAP):
                speed_triggered = abs(state.prev_speed - speed) > float(jvs.jiggle_impact_speed)
                angle_triggered = velocity.dot(state.tip_velocity) < math.cos(jvs.jiggle_impact_angle)
                if speed_triggered or angle_triggered:
                    state.boing_time      = 0.0
                    state.boing_direction = -velocity

            state.tip_velocity = velocity
            state.prev_speed   = speed

            damping = max(0.0, 1.0 - jvs.jiggle_damping_rate * state.boing_time)
            damping *= damping
            damping *= damping

            if damping > 0.0 and state.boing_direction is not None:
                flex      = (jvs.jiggle_amplitude
                             * math.cos(jvs.jiggle_frequency * state.boing_time)
                             * damping)
                new_rot   = anim_world.to_3x3() * (1.0 + flex)
                new_world = new_rot.to_4x4()
                new_world.translation = anim_world.to_translation()

    # -- Tip flex: FLEXIBLE or RIGID -------------------------------------------
    elif jvs.jiggle_flex_type in ('FLEXIBLE', 'RIGID'):
        # RIGID always locks length regardless of allow_length_flex
        allow_length_flex = (jvs.jiggle_allow_length_flex
                             and jvs.jiggle_flex_type == 'FLEXIBLE')

        if stale or state.tip_position is None:
            state.tip_position = goal_tip.copy()
            state.tip_velocity = Vector()

        vel = state.tip_velocity
        error = goal_tip - state.tip_position

        yaw_acc   = (jvs.jiggle_yaw_stiffness  * error.dot(export_perp1)
                     - jvs.jiggle_yaw_damping   * vel.dot(export_perp1))
        pitch_acc = (jvs.jiggle_pitch_stiffness * error.dot(export_perp2)
                     - jvs.jiggle_pitch_damping  * vel.dot(export_perp2))
        if allow_length_flex:
            along_acc = (jvs.jiggle_along_stiffness * error.dot(export_fwd)
                         - jvs.jiggle_along_damping  * vel.dot(export_fwd))
        else:
            along_acc = 0.0

        gravity   = Vector((0.0, 0.0, -jvs.jiggle_tip_mass))
        total_acc = (export_perp1 * yaw_acc
                     + export_perp2 * pitch_acc
                     + export_fwd * along_acc
                     + gravity)

        state.tip_velocity += total_acc * dt
        state.tip_position += state.tip_velocity * dt

        # Angle constraint (global cone)
        if jvs.jiggle_has_angle_constraint and jvs.jiggle_angle_constraint > 0.0:
            to_tip = state.tip_position - goal_base
            if to_tip.length > 1e-6:
                sim_dir = to_tip.normalized()
                cos_a   = max(-1.0, min(1.0, sim_dir.dot(export_fwd)))
                angle   = math.acos(cos_a)
                if angle > jvs.jiggle_angle_constraint:
                    axis = export_fwd.cross(sim_dir)
                    if axis.length > 1e-6:
                        axis.normalize()
                        clamped_fwd = (
                            Quaternion(axis, jvs.jiggle_angle_constraint).to_matrix()
                            @ export_fwd
                        ).normalized()
                        rad = to_tip.length if allow_length_flex else length
                        state.tip_position = goal_base + clamped_fwd * rad
                        # Damp velocity component pointing outside cone
                        excess = sim_dir - clamped_fwd
                        out_v  = state.tip_velocity.dot(excess)
                        if out_v > 0.0:
                            state.tip_velocity -= excess.normalized() * out_v

        # Yaw constraint - min stored positive, represents negative limit (user spec)
        if jvs.jiggle_has_yaw_constraint:
            yaw_min = -jvs.jiggle_yaw_constraint_min
            yaw_max =  jvs.jiggle_yaw_constraint_max
            to_tip  = state.tip_position - goal_base
            pf = to_tip.dot(export_fwd)
            py = to_tip.dot(export_perp1)
            yaw_angle = math.atan2(py, max(pf, 1e-8))
            clamped   = max(yaw_min, min(yaw_max, yaw_angle))
            if abs(clamped - yaw_angle) > 1e-6:
                dist  = math.hypot(pf, py)
                pp_c  = to_tip.dot(export_perp2)
                state.tip_position = (goal_base
                    + export_fwd   * (dist * math.cos(clamped))
                    + export_perp1 * (dist * math.sin(clamped))
                    + export_perp2 * pp_c)
                if jvs.jiggle_yaw_friction > 0.0:
                    yv = state.tip_velocity.dot(export_perp1)
                    state.tip_velocity -= export_perp1 * yv * min(1.0, jvs.jiggle_yaw_friction * dt)

        # Pitch constraint
        if jvs.jiggle_has_pitch_constraint:
            pitch_min = -jvs.jiggle_pitch_constraint_min
            pitch_max =  jvs.jiggle_pitch_constraint_max
            to_tip    = state.tip_position - goal_base
            pf  = to_tip.dot(export_fwd)
            pp  = to_tip.dot(export_perp2)
            pitch_angle = math.atan2(pp, max(pf, 1e-8))
            clamped     = max(pitch_min, min(pitch_max, pitch_angle))
            if abs(clamped - pitch_angle) > 1e-6:
                dist = math.hypot(pf, pp)
                py_c = to_tip.dot(export_perp1)
                state.tip_position = (goal_base
                    + export_fwd   * (dist * math.cos(clamped))
                    + export_perp2 * (dist * math.sin(clamped))
                    + export_perp1 * py_c)
                if jvs.jiggle_pitch_friction > 0.0:
                    pv = state.tip_velocity.dot(export_perp2)
                    state.tip_velocity -= export_perp2 * pv * min(1.0, jvs.jiggle_pitch_friction * dt)

        # Along constraint: lock length for RIGID and when allow_length_flex is False
        if not allow_length_flex:
            to_tip = state.tip_position - goal_base
            if to_tip.length > 1e-6:
                state.tip_position = goal_base + to_tip.normalized() * length

            # Use the post-clamp direction so radial velocity is removed correctly
            sim_dir = to_tip.normalized() if to_tip.length > 1e-6 else export_fwd
            along_v = state.tip_velocity.dot(sim_dir)
            state.tip_velocity -= sim_dir * along_v

        # Reconstruct rotation from simulated tip direction
        to_tip  = state.tip_position - goal_base
        sim_fwd = to_tip.normalized() if to_tip.length > 1e-6 else export_fwd

        # delta_q is a world-space rotation: export_fwd -> sim_fwd.
        # Composing it with the animated rotation correctly carries the export
        # offset along (see plan - at rest delta_q = identity, no visual jump).
        delta_q  = export_fwd.rotation_difference(sim_fwd)
        new_rot  = delta_q.to_matrix() @ anim_world.to_3x3() # DO NOT NORMALIZED THIS!!
        new_world = new_rot.to_4x4()
        new_world.translation = anim_world.to_translation()

        # -- Base spring (may layer on top of tip flex rotation) ---------------
        if jvs.jiggle_base_type == 'BASESPRING':
            anim_base = anim_world.to_translation()
            if stale or state.base_abs_pos is None:
                state.base_abs_pos  = anim_base.copy()
                state.base_velocity = Vector()

            error = anim_base - state.base_abs_pos
            grav  = Vector((0.0, 0.0, -float(jvs.jiggle_base_mass)))
            acc   = (jvs.jiggle_base_stiffness * error
                     - jvs.jiggle_base_damping  * state.base_velocity
                     + grav)
            state.base_velocity += acc * dt
            state.base_abs_pos  += state.base_velocity * dt
            state.base_offset    = state.base_abs_pos - anim_base

            # Axis constraints (lo = stored positive -> treated as negative)
            _constrain_axis(state, export_perp1,
                            jvs.jiggle_has_left_constraint,
                            -jvs.jiggle_left_constraint_min  * yp_scale,
                             jvs.jiggle_left_constraint_max  * yp_scale,
                            jvs.jiggle_left_friction, dt)
            _constrain_axis(state, export_perp2,
                            jvs.jiggle_has_up_constraint,
                            -jvs.jiggle_up_constraint_min  * pp_scale,
                             jvs.jiggle_up_constraint_max  * pp_scale,
                            jvs.jiggle_up_friction, dt)
            _constrain_axis(state, export_fwd,
                            jvs.jiggle_has_forward_constraint,
                            -jvs.jiggle_forward_constraint_min  * fwd_scale,
                             jvs.jiggle_forward_constraint_max  * fwd_scale,
                            jvs.jiggle_forward_friction, dt)
            state.base_abs_pos = anim_base + state.base_offset

            new_world = new_world.copy()
            new_world.translation = anim_world.to_translation() + state.base_offset

    # -- Standalone base spring (flex_type NONE + base BASESPRING) -------------
    elif jvs.jiggle_base_type == 'BASESPRING':
        anim_base = anim_world.to_translation()
        if stale or state.base_abs_pos is None:
            state.base_abs_pos  = anim_base.copy()
            state.base_velocity = Vector()

        error = anim_base - state.base_abs_pos
        grav  = Vector((0.0, 0.0, -float(jvs.jiggle_base_mass)))
        acc   = (jvs.jiggle_base_stiffness * error
                 - jvs.jiggle_base_damping  * state.base_velocity
                 + grav)
        state.base_velocity += acc * dt
        state.base_abs_pos  += state.base_velocity * dt
        state.base_offset    = state.base_abs_pos - anim_base

        _constrain_axis(state, export_perp1,
                        jvs.jiggle_has_left_constraint,
                        -jvs.jiggle_left_constraint_min  * yp_scale,
                         jvs.jiggle_left_constraint_max  * yp_scale,
                        jvs.jiggle_left_friction, dt)
        _constrain_axis(state, export_perp2,
                        jvs.jiggle_has_up_constraint,
                        -jvs.jiggle_up_constraint_min  * pp_scale,
                         jvs.jiggle_up_constraint_max  * pp_scale,
                        jvs.jiggle_up_friction, dt)
        _constrain_axis(state, export_fwd,
                        jvs.jiggle_has_forward_constraint,
                        -jvs.jiggle_forward_constraint_min  * fwd_scale,
                         jvs.jiggle_forward_constraint_max  * fwd_scale,
                        jvs.jiggle_forward_friction, dt)
        state.base_abs_pos = anim_base + state.base_offset

        new_world = anim_world.copy()
        new_world.translation = anim_world.to_translation() + state.base_offset

    # Cache this tick's result so child jiggle bones can use it
    _tick_sim_world[(arm_ob.name, pb.name)] = new_world

    # -- Write simulated matrix back to the bone -------------------------------
    # Direct mathutils equivalent of convert_space(WORLD->LOCAL) avoids the
    # C-API call that forces a depsgraph evaluation per bone (which would re-skin
    # all attached meshes N times per tick instead of once).
    pose_mat = arm_world_inv @ new_world
    if pb.parent:
        parent_key = (arm_ob.name, pb.parent.name)
        parent_pose = (arm_world_inv @ _tick_sim_world[parent_key]
                       if parent_key in _tick_sim_world else pb.parent.matrix)
        bone_rest = pb.parent.bone.matrix_local.inverted_safe() @ pb.bone.matrix_local
        local_mat = (parent_pose @ bone_rest).inverted_safe() @ pose_mat
    else:
        local_mat = pb.bone.matrix_local.inverted_safe() @ pose_mat
    if jvs.jiggle_base_type == 'BASESPRING':
        pb.matrix_basis = local_mat
    else:
        pb.matrix_basis = local_mat.to_3x3().to_4x4()


# -- Procedural bone simulation ------------------------------------------------

def _find_action_slot(action, slot_name: str):
    """Return the ActionSlot matching slot_name (by any name form), or first slot."""
    if not slot_name:
        return action.slots[0] if action.slots else None
    for s in action.slots:
        if (s.identifier == slot_name
                or s.name_display == slot_name
                or getattr(s, 'name', '') == slot_name):
            return s
    return action.slots[0] if action.slots else None


def _get_action_fcurves(action, slot_name: str) -> list:
    if getattr(action, 'is_action_legacy', True):
        return list(action.fcurves)
    target_slot = _find_action_slot(action, slot_name)
    if target_slot is None:
        return []
    for layer in action.layers:
        for strip in layer.strips:
            # Blender 4.5: strip.channelbag(slot) or iterate strip.channelbags
            cb_fn = getattr(strip, 'channelbag', None)
            if cb_fn and callable(cb_fn):
                try:
                    bag = cb_fn(target_slot)
                    if bag is not None:
                        return list(bag.fcurves)
                except Exception:
                    pass
            # Iterate all channelbags and match by slot handle
            for bag in getattr(strip, 'channelbags', ()):
                if getattr(bag, 'slot_handle', None) == target_slot.handle:
                    return list(bag.fcurves)
    return []


def _bone_keyframes(fcurves, bone_name: str) -> list[float]:
    """Return sorted unique keyframe times for ANY channel on the given bone."""
    prefix = f'pose.bones["{bone_name}"].'
    frames: set[float] = set()
    for fc in fcurves:
        if fc.data_path.startswith(prefix):
            for kp in fc.keyframe_points:
                frames.add(kp.co[0])
    return sorted(frames)


_BONE_XFORM_RE = _re.compile(r'^pose\.bones\["([^"]+)"\]\.(?:rotation|location|scale)')

def _get_proc_trigger_frame_range(entry, arm_ob) -> tuple[int, int, bool]:
    """Return (frame_start, frame_end, is_valid) for a TRIGGER proc bone entry.

    Manual mode uses the stored frame range props.  Auto mode scans the action
    for the first/last keyframe of any transform channel on a bone that still
    exists in the armature (excluding property paths like vs.proc_tolerance)."""
    if getattr(entry, 'use_manual_frame_range', False):
        fs = entry.trigger_frame_start
        fe = entry.trigger_frame_end
        return fs, fe, (fs < fe)

    action = entry.action
    if not action:
        return 0, 0, False
    fcurves  = _get_action_fcurves(action, entry.action_slot_name)
    existing = {b.name for b in arm_ob.data.bones} if arm_ob else set()
    frames: list[float] = []
    for fc in fcurves:
        m = _BONE_XFORM_RE.match(fc.data_path)
        if m and m.group(1) in existing:
            for kp in fc.keyframe_points:
                frames.append(kp.co[0])
    if not frames:
        return 0, 0, False
    return int(min(frames)), int(max(frames)), True


def _get_or_create_proc_tol_fcurve(entry, dp: str):
    """Find or create the proc_tolerance fcurve in entry.action. Returns None on failure."""
    action = entry.action
    if getattr(action, 'is_action_legacy', True):
        fc = action.fcurves.find(dp, index=0)
        return fc if fc is not None else action.fcurves.new(dp, index=0)
    target_slot = _find_action_slot(action, entry.action_slot_name)
    if target_slot is None:
        return None
    for layer in action.layers:
        for strip in layer.strips:
            cb_fn = getattr(strip, 'channelbag', None)
            if cb_fn and callable(cb_fn):
                try:
                    bag = cb_fn(target_slot)
                    if bag is not None:
                        fc = bag.fcurves.find(dp, index=0)
                        return fc if fc is not None else bag.fcurves.new(dp, index=0)
                except Exception:
                    pass
            for bag in getattr(strip, 'channelbags', ()):
                if getattr(bag, 'slot_handle', None) == target_slot.handle:
                    fc = bag.fcurves.find(dp, index=0)
                    return fc if fc is not None else bag.fcurves.new(dp, index=0)
    return None


def _set_helper_mute(arm_ob, bone_name: str, mute: bool) -> None:
    """Mute or restore constraints and driver fcurves on a helper bone.
    Original states are saved on first mute and restored on unmute."""
    pb = arm_ob.pose.bones.get(bone_name)
    if pb:
        for c in pb.constraints:
            key = (arm_ob.name, bone_name, 'C', c.name)
            if mute:
                if key not in _helper_saved_mutes:
                    _helper_saved_mutes[key] = c.mute
                c.mute = True
            else:
                c.mute = _helper_saved_mutes.pop(key, False)
    anim = arm_ob.animation_data
    if anim:
        prefix = f'pose.bones["{bone_name}"].'
        for fc in anim.drivers:
            if fc.data_path.startswith(prefix):
                key = (arm_ob.name, bone_name, 'D', fc.data_path, fc.array_index)
                if mute:
                    if key not in _helper_saved_mutes:
                        _helper_saved_mutes[key] = fc.mute
                    fc.mute = True
                else:
                    fc.mute = _helper_saved_mutes.pop(key, False)


def _temp_unmute_helper(arm_ob, bone_name: str) -> None:
    """Directly unmute constraints and drivers on a helper bone WITHOUT
    touching saved-state dicts. Used during cache building so frame_set
    captures their effect, while saved states from a previous mute are preserved."""
    pb = arm_ob.pose.bones.get(bone_name)
    if pb:
        for c in pb.constraints:
            c.mute = False
    anim = arm_ob.animation_data
    if anim:
        prefix = f'pose.bones["{bone_name}"].'
        for fc in anim.drivers:
            if fc.data_path.startswith(prefix):
                fc.mute = False


def _iter_layer_collections(layer_coll):
    yield layer_coll
    for child in layer_coll.children:
        yield from _iter_layer_collections(child)


def _force_evaluatable(obj):
    """Temporarily pull ``obj`` into the active depsgraph even when it (or a
    collection it belongs to) is disabled/excluded in the view layer, so
    ``scene.frame_set`` drives its pose. Returns a ``restore()`` callable.

    Only the settings that actually remove an object from depsgraph evaluation are
    touched: object / collection "Disable in Viewports" (``hide_viewport``) and the
    view-layer ``exclude`` checkbox. The eye icon (``hide_get`` / ``LayerCollection.
    hide_viewport``) and ``hide_render`` leave the object evaluated, so they are left
    alone. Needed because a reference armature is commonly kept hidden/excluded."""
    restores = []

    if obj.hide_viewport:
        obj.hide_viewport = False
        restores.append(lambda o=obj: setattr(o, 'hide_viewport', True))

    obj_colls = list(obj.users_collection)
    for coll in obj_colls:
        if coll.hide_viewport:
            coll.hide_viewport = False
            restores.append(lambda c=coll: setattr(c, 'hide_viewport', True))

    view_layer = getattr(bpy.context, 'view_layer', None)
    if view_layer is not None:
        coll_names = {c.name for c in obj_colls}
        for lc in _iter_layer_collections(view_layer.layer_collection):
            if lc.collection.name in coll_names and lc.exclude:
                lc.exclude = False
                restores.append(lambda l=lc: setattr(l, 'exclude', True))

    def restore():
        for fn in reversed(restores):
            try:
                fn()
            except Exception:
                pass
    return restore


def _build_proc_triggers(arm_ob, entry, entry_idx: int, scene, export_print = False) -> list:
    """Sample trigger-target pairs from the action by evaluating the scene at each
    driver bone keyframe frame. Returns list of (driver_quat, helper_quat)."""
    global _building_proc_cache
    if _building_proc_cache:
        return []

    action = entry.action
    if not action:
        return []

    # A reference armature lets near-identical rigs (e.g. the same character with a
    # different outfit / IK setup) reuse a base rig's computed triggers. When set,
    # the action is sampled on the reference; only the trigger *deltas* come from it
    # - the exported armature (arm_ob) still supplies rest orientation and basePos
    # in build_trigger_transforms, so different rest positions are expected/fine.
    sample_arm = arm_ob
    ref = getattr(entry, 'reference_armature', None)
    if ref is not None and ref is not arm_ob and getattr(ref, 'type', None) == 'ARMATURE':
        if entry.driver_bone in ref.pose.bones and entry.helper_bone in ref.pose.bones:
            sample_arm = ref
        else:
            print(f"[ProcBones] Reference armature '{ref.name}' is missing bone "
                  f"'{entry.driver_bone}' or '{entry.helper_bone}' - sampling '{arm_ob.name}' instead")

    anim = sample_arm.animation_data
    if anim is None:
        anim = sample_arm.animation_data_create()

    # Determine frame range via shared helper (respects manual vs auto mode). Auto
    # mode scans for keyframes on bones that exist in the sampled armature.
    fs, fe, valid = _get_proc_trigger_frame_range(entry, sample_arm)
    if not valid:
        print(f"[ProcBones] No valid frame range for '{entry.helper_bone}' "
              f"in action '{action.name}' : check action has bone keyframes")
        return []
    frames = list(range(fs, fe + 1))

    # Find per-trigger tolerance fcurve once (keyed on driver bone).
    fcurves = _get_action_fcurves(action, entry.action_slot_name)
    _tol_dp = f'bones["{entry.driver_bone}"].vs.proc_tolerance'
    _tol_fc = next((fc for fc in fcurves
                    if fc.data_path == _tol_dp and fc.array_index == 0), None)

    # Resolve target slot for assignment
    is_legacy     = getattr(action, 'is_action_legacy', True)
    target_slot   = None if is_legacy else _find_action_slot(action, entry.action_slot_name)

    # Save state
    orig_frame   = scene.frame_current
    orig_action  = anim.action
    orig_use_nla = anim.use_nla
    orig_slot_handle = getattr(anim, 'action_slot_handle', None)

    was_overridden = (sample_arm.name, entry.helper_bone) in _overridden_helpers
    # Snapshot the current pose so it can be restored exactly after cache build.
    # The identity-then-frame_set approach in the finally block only restores
    # keyframed bones; this snapshot preserves manually posed (non-keyframed) bones.
    # frame_set re-evaluates the whole scene, so when sampling from a reference
    # armature the exported armature (arm_ob) must be snapshot/restored too.
    restore_arms = [sample_arm] if sample_arm is arm_ob else [sample_arm, arm_ob]
    saved_pose = {a.name: {pb.name: pb.matrix_basis.copy() for pb in a.pose.bones}
                  for a in restore_arms}

    # When sampling a reference armature it may be hidden/excluded from the view
    # layer; force it evaluatable so frame_set actually drives its pose, else every
    # trigger would collapse to the rest pose. Restored in the finally block.
    restore_visibility = _force_evaluatable(sample_arm) if sample_arm is not arm_ob else None

    _building_proc_cache = True
    try:
        # Unmute constraints/drivers on the helper so frame_set captures their
        # effect. If sim was already running (was_overridden), saved states are
        # preserved in _helper_saved_mutes - _temp_unmute_helper doesn't touch them.
        _temp_unmute_helper(sample_arm, entry.helper_bone)

        anim.use_nla = False
        anim.action  = action
        if target_slot is not None:
            try:
                anim.action_slot_handle = target_slot.handle
            except AttributeError:
                try:
                    anim.action_slot = target_slot
                except Exception:
                    pass

        triggers = []
        for frame in frames:
            scene.frame_set(int(frame), subframe=frame - int(frame))
            d_pb = sample_arm.pose.bones.get(entry.driver_bone)
            h_pb = sample_arm.pose.bones.get(entry.helper_bone)
            if d_pb and h_pb:
                d_local = sample_arm.convert_space(
                    pose_bone=d_pb, matrix=d_pb.matrix,
                    from_space='POSE', to_space='LOCAL')
                dq   = d_local.to_quaternion().normalized()
                dloc = d_local.to_translation()
                # Read the full constraint/driver-evaluated pose in local space.
                h_local = sample_arm.convert_space(
                    pose_bone=h_pb, matrix=h_pb.matrix,
                    from_space='POSE', to_space='LOCAL')
                hloc = h_local.to_translation()
                hq   = h_local.to_quaternion().normalized()
                tol = (_tol_fc.evaluate(frame) if _tol_fc is not None
                       else d_pb.bone.vs.proc_tolerance)
                triggers.append((dq, dloc, hloc, hq, tol))
    finally:
        anim.action  = orig_action
        anim.use_nla = orig_use_nla
        if orig_slot_handle is not None:
            try:
                anim.action_slot_handle = orig_slot_handle
            except Exception:
                pass
        # Restore the pre-build pose. frame_set re-evaluates the original action
        # and updates scene.frame_current; the snapshot then restores ALL bones
        # (including non-keyframed ones that frame_set would leave at identity).
        scene.frame_set(orig_frame)
        for a in restore_arms:
            for pb in a.pose.bones:
                if pb.name in saved_pose[a.name]:
                    pb.matrix_basis = saved_pose[a.name][pb.name]
        # Re-mute the helper if it was already overridden before this build.
        if was_overridden:
            _set_helper_mute(sample_arm, entry.helper_bone, True)
        if restore_visibility is not None:
            restore_visibility()
        _building_proc_cache = False

    if not export_print:
        print(f"[ProcBones] Cached {len(triggers)} triggers for '{entry.helper_bone}' "
            f"driven by '{entry.driver_bone}' via '{action.name}'")
    else:
        print(f"  - Cached {len(triggers)} triggers for '{entry.helper_bone}' "
            f"driven by '{entry.driver_bone}' via '{action.name}'")
    return triggers


def invalidate_proc_cache(arm_name: str) -> None:
    for k in [k for k in _proc_trigger_cache if k[0] == arm_name]:
        del _proc_trigger_cache[k]
    # Restore overrides so the next cache build can sample with constraints/drivers active.
    arm_ob = bpy.data.objects.get(arm_name)
    for key in [k for k in _overridden_helpers if k[0] == arm_name]:
        if arm_ob and arm_ob.pose:
            _set_helper_mute(arm_ob, key[1], False)
        _overridden_helpers.discard(key)


_AXIS_TO_VEC = {
    '+X': Vector(( 1,  0,  0)), '+Y': Vector(( 0,  1,  0)), '+Z': Vector(( 0,  0,  1)),
    '-X': Vector((-1,  0,  0)), '-Y': Vector(( 0, -1,  0)), '-Z': Vector(( 0,  0, -1)),
}


def _axis_to_vec(axes) -> Vector:
    if isinstance(axes, str):
        return _AXIS_TO_VEC.get(axes, Vector((1.0, 0.0, 0.0)))
    result = Vector((0.0, 0.0, 0.0))
    for a in axes:
        result += _AXIS_TO_VEC.get(a, Vector((0.0, 0.0, 0.0)))
    return result.normalized() if result.length > 1e-9 else Vector((1.0, 0.0, 0.0))


def _sim_lookat_entry(arm_ob, entry, is_s2: bool, arm_world_inv: Matrix) -> None:
    """Mirror of the engine's DoAimAtBone (source-sdk-2013 bone_setup.cpp).

    The orientation is built absolutely, not as a delta from the animated pose:
    aimRotation takes the constant aim axis onto the world aim direction, then a
    twist about that direction aligns the up axis with the helper's rest
    orientation carried by the parent's animation (the engine's boneLocalToWorld
    reference) - NOT world Z. The helper's own animated local rotation is
    ignored, exactly like the engine.
    """
    helper_pb = arm_ob.pose.bones.get(entry.helper_bone)
    driver_pb = arm_ob.pose.bones.get(entry.driver_bone)
    if not helper_pb or not driver_pb:
        return

    # anim_world = parent chain @ rest local (engine: parentSpace @ basepos/quat);
    # goal_world adds the export offset, making it the helper's Source-frame
    # boneLocalToWorld - the frame the engine rotates userUpVector by.
    anim_world, goal_world = _get_animated_goal(arm_ob, helper_pb, arm_world_inv)
    goal_rot = goal_world.to_3x3()
    aim_world_position = anim_world.to_translation()

    user_aim = _axis_to_vec(getattr(entry, 'lookat_aim_axis', '+X'))
    user_up  = _axis_to_vec(getattr(entry, 'lookat_up_axis',  '+Z'))

    # Aim target (engine: aimAtSpace) - the attachment sits on the driver bone
    # with lookat_offset as its local translation.
    # NOTE: _get_animated_goal must NOT be used here it rebuilds from bone.matrix_local
    # (rest pose), giving wrong positions whenever the driver bone is animated/posed. too bad!
    driver_key = (arm_ob.name, entry.driver_bone)
    if driver_key in _tick_sim_world:
        driver_mat = _tick_sim_world[driver_key]
    else:
        driver_mat = arm_ob.matrix_world @ driver_pb.matrix @ _get_export_offset_mat(driver_pb)

    loff = getattr(entry, 'lookat_offset', None)
    if loff is not None:
        target_pos = (driver_mat @ Vector((loff[0], loff[1], loff[2], 1.0))).to_3d()
    else:
        target_pos = driver_mat.to_translation()

    aim_vector = target_pos - aim_world_position
    if aim_vector.length > 1e-6:
        aim_vector.normalize()
    else:
        av = goal_rot @ user_aim
        aim_vector = av.normalized() if av.length > 1e-9 else Vector((0.0, 0.0, 1.0))

    # aimRotation: shortest arc taking the aim axis onto the aim direction.
    aim_rotation = user_aim.rotation_difference(aim_vector)

    bone_rotation = aim_rotation
    # Engine skips the up correction entirely when the user aim and up axes are
    # parallel (degenerate configuration).
    if 1.0 - abs(user_up.dot(user_aim)) > 1e-6:
        # pUp: up axis after aimRotation, projected perpendicular to the aim.
        tmp_up = aim_rotation @ user_up
        p_up = tmp_up - aim_vector * aim_vector.dot(tmp_up)

        # pParentUp: reference up = userUpVector rotated by boneLocalToWorld,
        # projected perpendicular to the aim.
        tmp_parent_up = goal_rot @ user_up
        p_parent_up = tmp_parent_up - aim_vector * aim_vector.dot(tmp_parent_up)

        if p_up.length > 1e-6 and p_parent_up.length > 1e-6:
            p_up.normalize()
            p_parent_up.normalize()
            # Engine quirk kept: zero twist when the projected ups are parallel
            # OR anti-parallel (angle forced to 0, never a 180 degree flip).
            if 1.0 - abs(p_up.dot(p_parent_up)) > 1e-7:
                up_rotation = p_up.rotation_difference(p_parent_up)
                bone_rotation = up_rotation @ aim_rotation

    # bone_rotation is the Source-frame world orientation; strip the export
    # offset to get back to the Blender bone frame (goal = anim @ offset).
    off_rot   = _get_export_offset_mat(helper_pb).to_3x3()
    new_rot   = bone_rotation.to_matrix() @ off_rot.inverted()
    new_world = new_rot.to_4x4()
    new_world.translation = aim_world_position

    _tick_sim_world[(arm_ob.name, helper_pb.name)] = new_world
    pose_mat = arm_world_inv @ new_world
    pb = helper_pb
    if pb.parent:
        parent_key = (arm_ob.name, pb.parent.name)
        parent_pose = (arm_world_inv @ _tick_sim_world[parent_key]
                       if parent_key in _tick_sim_world else pb.parent.matrix)
        bone_rest = pb.parent.bone.matrix_local.inverted_safe() @ pb.bone.matrix_local
        local_mat = (parent_pose @ bone_rest).inverted_safe() @ pose_mat
    else:
        local_mat = pb.bone.matrix_local.inverted_safe() @ pose_mat
    helper_pb.matrix_basis = local_mat.to_3x3().to_4x4()


def _sim_proc_entries(arm_ob, scene, is_s2: bool, arm_world_inv: Matrix) -> int:
    """Drive procedural (helper) bones. Returns the number of helpers simulated."""
    jiggle_helpers: set[str] = {
        pb.name for pb in arm_ob.pose.bones if pb.bone.vs.bone_is_jigglebone
    }
    seen_helpers: set[str] = set()
    sim_count = 0

    for entry_idx, entry in enumerate(arm_ob.data.vs.proc_bones):
        is_lookat = getattr(entry, 'proc_type', 'TRIGGER') == 'LOOKAT'
        if not entry.helper_bone or not entry.driver_bone:
            continue
        if not is_lookat and not entry.action:
            continue
        if entry.helper_bone not in arm_ob.pose.bones:
            continue
        if entry.driver_bone not in arm_ob.pose.bones:
            continue
        if entry.helper_bone in jiggle_helpers:
            continue

        # First entry for a given helper wins; warn once on subsequent duplicates.
        if entry.helper_bone in seen_helpers:
            if not is_lookat:
                cache_key = (arm_ob.name, entry_idx,
                             entry.action.name, entry.action_slot_name)
                if cache_key not in _proc_trigger_cache:
                    print(f"[ProcBones] Warning: helper '{entry.helper_bone}' in entry "
                          f"{entry_idx} is already controlled by an earlier entry - skipping")
                    _proc_trigger_cache[cache_key] = []
            continue
        seen_helpers.add(entry.helper_bone)

        if is_lookat:
            _sim_lookat_entry(arm_ob, entry, is_s2, arm_world_inv)
            sim_count += 1
            continue

        cache_key = (arm_ob.name, entry_idx,
                     entry.action.name, entry.action_slot_name)
        if cache_key not in _proc_trigger_cache:
            if _building_proc_cache:
                continue
            _proc_trigger_cache[cache_key] = _build_proc_triggers(
                arm_ob, entry, entry_idx, scene)

        triggers = _proc_trigger_cache[cache_key]
        if not triggers:
            continue

        # Mute constraints and driver fcurves on the helper so our matrix_basis
        # write is the final result. Called every tick - undo can restore c.mute
        # to its original value (undo stack is Blender data, not our Python dicts),
        # so we re-assert the mute each tick to catch that case.
        # _set_helper_mute saves original state only once (key not in dict guard),
        # so repeated calls are safe and don't overwrite the saved value.
        override_key = (arm_ob.name, entry.helper_bone)
        _set_helper_mute(arm_ob, entry.helper_bone, True)
        _overridden_helpers.add(override_key)

        driver_pb    = arm_ob.pose.bones[entry.driver_bone]
        d_local      = arm_ob.convert_space(
            pose_bone=driver_pb, matrix=driver_pb.matrix,
            from_space='POSE', to_space='LOCAL')
        current_quat = d_local.to_quaternion().normalized()

        weights = []
        for trig_q, _dloc, _loc, _rot, trig_tol in triggers:
            dot   = abs(current_quat.dot(trig_q))
            dot   = max(-1.0, min(1.0, dot))
            angle = 2.0 * math.acos(dot)
            weights.append(max(0.0, 1.0 - angle / trig_tol))

        total = sum(weights)
        if total <= 1e-4:
            blended_loc = Vector(triggers[0][2])
            blended_rot = Quaternion(triggers[0][3])
        else:
            blended_loc = Vector((0.0, 0.0, 0.0))
            blended_rot = Quaternion((0.0, 0.0, 0.0, 0.0))
            for w, (_drv, _dloc, tloc, trot, _tol) in zip(weights, triggers):
                nw = w / total
                blended_loc        += nw * tloc
                blended_rot.x      += nw * trot.x
                blended_rot.y      += nw * trot.y
                blended_rot.z      += nw * trot.z
                blended_rot.w      += nw * trot.w
            blended_rot.normalize()

        helper_pb = arm_ob.pose.bones[entry.helper_bone]
        mat = blended_rot.to_matrix().to_4x4()
        mat.translation = blended_loc
        helper_pb.matrix_basis = mat
        sim_count += 1

    return sim_count


# -- Armature-level simulation -------------------------------------------------

def simulate_armature(arm_ob, scene, dt: float, skip_selected: bool = False) -> None:
    global _jiggle_count_acc, _proc_count_acc
    if not arm_ob.pose or _building_proc_cache:
        return
    _tick_sim_world.clear()
    is_s2 = _is_source2(scene)
    arm_world_inv = arm_ob.matrix_world.inverted_safe()

    if getattr(scene.vs, 'sim_jiggle_bones', True):
        arm_name = arm_ob.name
        if arm_name not in _jiggle_bone_cache:
            pbs = [pb for pb in arm_ob.pose.bones if pb.bone.vs.bone_is_jigglebone]
            pbs.sort(key=_bone_depth)
            _jiggle_bone_cache[arm_name] = [pb.name for pb in pbs]
        jiggle_pbs = [arm_ob.pose.bones[n] for n in _jiggle_bone_cache[arm_name]
                      if n in arm_ob.pose.bones]
        # In Pose Mode, selected jiggle bones are skipped so the user can pose
        # them manually. Stale detection resumes sim cleanly after deselection.
        if skip_selected and bpy.context.mode == 'POSE':
            jiggle_pbs = [pb for pb in jiggle_pbs if not pb.bone.select]
        for pb in jiggle_pbs:
            try:
                _sim_bone(arm_ob, pb, dt, is_s2, arm_world_inv)
            except Exception:
                import traceback
                traceback.print_exc()
        _jiggle_count_acc += len(jiggle_pbs)

    if getattr(scene.vs, 'sim_proc_bones', True):
        try:
            _proc_count_acc += _sim_proc_entries(arm_ob, scene, is_s2, arm_world_inv)
        except Exception:
            import traceback
            traceback.print_exc()


def reset_state(arm_ob=None) -> None:
    if arm_ob is None:
        _states.clear()
        _proc_trigger_cache.clear()
        _jiggle_bone_cache.clear()
        _sim_arm_cache.clear()
        for arm_name, bone_name in list(_overridden_helpers):
            ob = bpy.data.objects.get(arm_name)
            if ob and ob.pose:
                _set_helper_mute(ob, bone_name, False)
        _overridden_helpers.clear()
        _helper_saved_mutes.clear()
    else:
        for k in [k for k in _states if k[0] == arm_ob.name]:
            del _states[k]
        _jiggle_bone_cache.pop(arm_ob.name, None)
        invalidate_proc_cache(arm_ob.name)  # also restores overrides for this arm


# -- Armature candidate cache --------------------------------------------------

def _rebuild_sim_arm_cache(scene) -> list[str]:
    """Scan scene once and cache names of armatures that have jiggle/proc bones."""
    names = []
    for ob in scene.objects:
        if ob.type != 'ARMATURE' or not ob.pose:
            continue
        if (any(pb.bone.vs.bone_is_jigglebone for pb in ob.pose.bones)
                or ob.data.vs.proc_bones):
            names.append(ob.name)
    _sim_arm_cache[scene.name] = names
    return names


# -- Timer (real-time viewport simulation) -------------------------------------

def _get_rate() -> float:
    try:
        return 1.0 / bpy.data.scenes[0].vs.jiggle_sim_rate
    except Exception:
        return 1.0 / 60.0


def _timer_callback():
    global _last_real_time, _jiggle_count_acc, _proc_count_acc
    global _live_jiggle_count, _live_proc_count
    try:
        ctx = bpy.context
        if ctx is None:
            return _get_rate()

        if ctx.mode not in ('OBJECT', 'POSE'):
            return _get_rate()

        # Frame-change handler drives simulation during timeline playback
        if ctx.screen and ctx.screen.is_animation_playing:
            return _get_rate()

        now = time.perf_counter()
        dt  = min(now - _last_real_time, 0.1)
        _last_real_time = now

        _jiggle_count_acc = 0
        _proc_count_acc   = 0
        for scene in bpy.data.scenes:
            if not getattr(scene.vs, 'jiggle_sim_enabled', False):
                continue
            arm_names = _sim_arm_cache.get(scene.name)
            if arm_names is None:
                arm_names = _rebuild_sim_arm_cache(scene)
            for arm_name in arm_names:
                ob = bpy.data.objects.get(arm_name)
                if ob and ob.pose and not ob.hide_render and ob.visible_get():
                    simulate_armature(ob, scene, dt, skip_selected=True)

        _live_jiggle_count = _jiggle_count_acc
        _live_proc_count   = _proc_count_acc

    except Exception:
        import traceback
        traceback.print_exc()

    return _get_rate()


def _start_timer() -> None:
    global _last_real_time
    if not bpy.app.timers.is_registered(_timer_callback):
        _last_real_time = time.perf_counter()
        bpy.app.timers.register(_timer_callback, first_interval=_get_rate())


def _stop_timer() -> None:
    if bpy.app.timers.is_registered(_timer_callback):
        try:
            bpy.app.timers.unregister(_timer_callback)
        except Exception:
            pass


# -- Frame-change handler (timeline playback) ----------------------------------

@bpy.app.handlers.persistent
def _frame_change_post(scene, depsgraph):
    global _jiggle_count_acc, _proc_count_acc, _live_jiggle_count, _live_proc_count
    if not getattr(scene.vs, 'jiggle_sim_enabled', False):
        return
    fps = scene.render.fps / scene.render.fps_base
    dt  = 1.0 / fps
    arm_names = _sim_arm_cache.get(scene.name)
    if arm_names is None:
        arm_names = _rebuild_sim_arm_cache(scene)
    _jiggle_count_acc = 0
    _proc_count_acc   = 0
    for arm_name in arm_names:
        ob = bpy.data.objects.get(arm_name)
        if ob and ob.pose and not ob.hide_render and ob.visible_get():
            simulate_armature(ob, scene, dt)
    _live_jiggle_count = _jiggle_count_acc
    _live_proc_count   = _proc_count_acc


# -- Bone restore helper -------------------------------------------------------

def _restore_jiggle_bones() -> None:
    """Reset matrix_basis to identity on all jiggle bones across all scenes.

    Setting matrix_basis = identity returns each bone to its animated rest pose.
    Blender will overwrite it on the next depsgraph evaluation for keyframed bones;
    for non-keyframed jiggle bones identity IS the animated pose.
    """
    for scene in bpy.data.scenes:
        for ob in scene.objects:
            if ob.type != 'ARMATURE' or not ob.pose:
                continue
            for pb in ob.pose.bones:
                if pb.bone.vs.bone_is_jigglebone:
                    pb.matrix_basis = Matrix.Identity(4)
            proc_bone_names = {e.helper_bone for e in ob.data.vs.proc_bones
                               if e.helper_bone}
            for pb in ob.pose.bones:
                if pb.name in proc_bone_names:
                    # Restore constraints/drivers before clearing matrix_basis
                    # so Blender re-evaluates them naturally on the next tick.
                    override_key = (ob.name, pb.name)
                    if override_key in _overridden_helpers:
                        _set_helper_mute(ob, pb.name, False)
                        _overridden_helpers.discard(override_key)
                    pb.matrix_basis = Matrix.Identity(4)


# -- Depsgraph handler (cache invalidation) ------------------------------------

@bpy.app.handlers.persistent
def _depsgraph_update(scene, depsgraph):
    """Invalidate bone/armature caches when rig data changes.

    Armature data-block updates (bone additions/removals, property changes)
    are rare in normal use and only happen when the user edits the rig, so
    clearing both caches here is safe and doesn't fire during animation playback.
    """
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Armature):
            _jiggle_bone_cache.clear()
            _sim_arm_cache.pop(scene.name, None)
            break


# -- Property update callback --------------------------------------------------

def on_sim_enabled_changed(props, context):
    if props.jiggle_sim_enabled:
        _proc_trigger_cache.clear()
        _sim_arm_cache.clear()
        _start_timer()
    else:
        _stop_timer()
        _states.clear()
        _restore_jiggle_bones()


# -- Save handlers -------------------------------------------------------------

_pre_save_jiggle_states: dict[str, bool] = {}


@bpy.app.handlers.persistent
def _save_pre(scene):
    _pre_save_jiggle_states.clear()
    for sc in bpy.data.scenes:
        was = getattr(sc.vs, 'jiggle_sim_enabled', False)
        _pre_save_jiggle_states[sc.name] = was
        if was:
            sc.vs.jiggle_sim_enabled = False


@bpy.app.handlers.persistent
def _save_post(scene):
    for sc in bpy.data.scenes:
        if _pre_save_jiggle_states.get(sc.name, False):
            sc.vs.jiggle_sim_enabled = True
    _pre_save_jiggle_states.clear()


# -- Registration --------------------------------------------------------------


def register() -> None:
    for fn in bpy.app.handlers.frame_change_post[:]:
        if getattr(fn, '__module__', '').endswith('procbones_sim'):
            bpy.app.handlers.frame_change_post.remove(fn)
    bpy.app.handlers.frame_change_post.append(_frame_change_post)
    for fn in bpy.app.handlers.save_pre[:]:
        if getattr(fn, '__module__', '').endswith('procbones_sim'):
            bpy.app.handlers.save_pre.remove(fn)
    bpy.app.handlers.save_pre.append(_save_pre)
    for fn in bpy.app.handlers.save_post[:]:
        if getattr(fn, '__module__', '').endswith('procbones_sim'):
            bpy.app.handlers.save_post.remove(fn)
    bpy.app.handlers.save_post.append(_save_post)
    for fn in bpy.app.handlers.depsgraph_update_post[:]:
        if getattr(fn, '__module__', '').endswith('procbones_sim'):
            bpy.app.handlers.depsgraph_update_post.remove(fn)
    bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update)


def unregister() -> None:
    _stop_timer()
    _states.clear()
    if _frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_change_post)
    if _save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_pre)
    if _save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_save_post)
    if _depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_update)
