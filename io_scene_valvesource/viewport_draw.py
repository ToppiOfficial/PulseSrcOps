import math

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Euler, Matrix, Vector
from .utils import get_bone_matrix, get_id, is_armature, is_mesh_compatible

_handle          = None
_handle_2d       = None
_hud_handle      = None
_edgeline_handle = None
_hitbox_sync_handle = None
_attachment_mesh_handle = None
_attachment_mesh_depsgraph_handle = None
_attachment_mesh_cache: dict = {}   # mesh.data.session_uid -> (data_uid, [(x,y,z), ...]) flat tris
_last_active_bone_key: str = ''
_edgeline_cache: dict    = {}   # ob.session_uid -> (cache_key, [(color, verts)])
_edgeline_mesh_map: dict = {}   # mesh.session_uid -> ob.session_uid (for weight-paint invalidation)
_edgeline_last_mode: str = ''   # previous context.mode, used to detect pose-mode exit
_edgeline_depsgraph_handle = None
_EDGELINE_THICK_CLAMP = 3.5   # mirrors solid.thickness_clamp in EdgelineBuilder
_label_queue: list       = []
_proc_label_queue: list  = []


def _is_source2(context):
    try:
        vs = context.scene.vs
        return vs.export_format == 'DMX' and vs.dmx_format in ('22', '22_modeldoc')
    except Exception:
        return False


def _get_bone_color(pb):
    try:
        bc = pb.color
        if bc.palette == 'CUSTOM':
            c = bc.custom.normal
            return (c[0], c[1], c[2])
        elif bc.palette != 'DEFAULT':
            idx = int(bc.palette[5:]) - 1  # 'THEME01'->0 … 'THEME20'->19
            c = bpy.context.preferences.themes[0].bone_color_sets[idx].normal
            return (c[0], c[1], c[2])
    except Exception:
        pass
    return (1, 1, 1)


def _bone_octahedron_verts(mat, length):
    s    = length * 0.12
    loc  = mat.to_translation()
    x_ax = Vector((mat[0][0], mat[1][0], mat[2][0])).normalized()
    y_ax = Vector((mat[0][1], mat[1][1], mat[2][1])).normalized()
    z_ax = Vector((mat[0][2], mat[1][2], mat[2][2])).normalized()
    _r   = 0.7071067811865476  # 1/sqrt(2) - rotates ring 45° around Y
    rx   = (x_ax + z_ax) * _r
    rz   = (z_ax - x_ax) * _r
    head = loc
    tail = loc + y_ax * length
    vr   = loc + rx * s + y_ax * s
    vl   = loc - rx * s + y_ax * s
    vf   = loc + rz * s + y_ax * s
    vb   = loc - rz * s + y_ax * s
    return head, tail, vr, vl, vf, vb


def _bone_octahedron_shaded_tris(mat, length):
    """Per-face flat-shaded octahedron triangles, like Blender's solid bones.
    Returns [(shade, [a, b, c]), ...] with shade in 0..1 from a fixed overhead light,
    so every face (head pyramid and tail pyramid alike) gets its own brightness."""
    head, tail, vr, vl, vf, vb = _bone_octahedron_verts(mat, length)
    y_ax  = Vector((mat[0][1], mat[1][1], mat[2][1])).normalized()
    faces = [
        (head, vr, vf), (head, vf, vl), (head, vl, vb), (head, vb, vr),
        (tail, vf, vr), (tail, vl, vf), (tail, vb, vl), (tail, vr, vb),
    ]
    out = []
    for a, b, c in faces:
        n = (b - a).cross(c - a)
        if n.length < 1e-12:
            continue
        n = n.normalized()
        centroid = (a + b + c) / 3.0
        axis_pt  = head + y_ax * (centroid - head).dot(y_ax)   # nearest point on bone axis
        if n.dot(centroid - axis_pt) < 0:                      # force normal to face outward
            n = -n
        shade = 0.35 + 0.65 * (n.z * 0.5 + 0.5)                # down-faces dark, up-faces bright
        out.append((shade, [a, b, c]))
    return out


def _bone_octahedron_lines(mat, length):
    head, tail, vr, vl, vf, vb = _bone_octahedron_verts(mat, length)
    return [
        head, vr,  head, vf,  head, vl,  head, vb,   # head pyramid spokes
        vr, vf,  vf, vl,  vl, vb,  vb, vr,            # mid ring
        tail, vr,  tail, vf,  tail, vl,  tail, vb,    # tail pyramid spokes
    ]


def _circle_lines(center, ax1, ax2, radius, n=24):
    """Line-segment loop of a circle lying in the ax1/ax2 plane."""
    pts = [center + (ax1 * math.cos(2 * math.pi * i / n) +
                     ax2 * math.sin(2 * math.pi * i / n)) * radius
           for i in range(n)]
    verts = []
    for i in range(n):
        verts += [pts[i], pts[(i + 1) % n]]
    return verts


def _sphere_lines(center, ax1, ax2, ax3, radius, n=24):
    """Wireframe sphere: three orthogonal great circles, like Blender's bone head/tail."""
    return (_circle_lines(center, ax1, ax2, radius, n) +
            _circle_lines(center, ax2, ax3, radius, n) +
            _circle_lines(center, ax3, ax1, radius, n))


def _sphere_tris(center, ax1, ax2, ax3, radius, n=16, lat=8):
    """Solid UV-sphere triangles; ax3 is the pole axis."""
    rings = []
    for i in range(lat + 1):
        theta  = math.pi * i / lat            # 0..pi, measured from the +ax3 pole
        ct, st = math.cos(theta), math.sin(theta)
        rings.append([center + ax3 * (radius * ct) +
                      (ax1 * math.cos(2*math.pi*k/n) + ax2 * math.sin(2*math.pi*k/n)) * (radius * st)
                      for k in range(n)])
    verts = []
    for s in range(lat):
        r0, r1 = rings[s], rings[s + 1]
        for i in range(n):
            j = (i + 1) % n
            verts += [r0[i], r0[j], r1[i], r0[j], r1[j], r1[i]]
    return verts


# -- Jiggle geometry helpers ----------------------------------------------------

def _plane_tris(tip, fwd, perp, angle, length, width_scale=0.7):
    """Flat rectangular plane at `angle` rad from fwd toward perp - one page of the book.
    Length is measured along fwd so the plane always reaches full length regardless of angle."""
    dir_vec = (fwd * math.cos(angle) + perp * math.sin(angle)).normalized()
    side    = fwd.cross(perp).normalized()
    hw      = length * width_scale
    far     = tip + dir_vec * length
    a = tip - side * hw
    b = tip + side * hw
    c = far + side * hw
    d = far - side * hw
    return [a, b, c,  a, c, d]


def _plane_lines(tip, fwd, perp, angle, length, width_scale=0.7):
    """Outline of the rectangular plane."""
    dir_vec = (fwd * math.cos(angle) + perp * math.sin(angle)).normalized()
    side    = fwd.cross(perp).normalized()
    hw      = length * width_scale
    far     = tip + dir_vec * length
    a = tip - side * hw
    b = tip + side * hw
    c = far + side * hw
    d = far - side * hw
    return [a, b,  b, c,  c, d,  d, a]


def _cone_tris(tip, fwd, p1, p2, half_angle, h, n=24):
    r      = h * math.tan(half_angle)
    base_c = tip + fwd * h
    circle = [base_c + (p1 * math.cos(2 * math.pi * i / n) +
                        p2 * math.sin(2 * math.pi * i / n)) * r
              for i in range(n)]
    verts = []
    for i in range(n):
        verts += [tip, circle[i], circle[(i + 1) % n]]
    return verts


def _cone_lines(tip, fwd, p1, p2, half_angle, h, n=24):
    r      = h * math.tan(half_angle)
    base_c = tip + fwd * h
    circle = [base_c + (p1 * math.cos(2 * math.pi * i / n) +
                        p2 * math.sin(2 * math.pi * i / n)) * r
              for i in range(n)]
    verts = []
    for s in (0, n // 4, n // 2, 3 * n // 4):
        verts += [tip, circle[s]]
    for i in range(n):
        verts += [circle[i], circle[(i + 1) % n]]
    return verts


def _stick_tris(origin, y_ax, x_ax, z_ax, length, width):
    hw = width * 0.5
    s0 = origin + (-x_ax - z_ax) * hw
    s1 = origin + ( x_ax - z_ax) * hw
    s2 = origin + ( x_ax + z_ax) * hw
    s3 = origin + (-x_ax + z_ax) * hw
    e0 = s0 + y_ax * length
    e1 = s1 + y_ax * length
    e2 = s2 + y_ax * length
    e3 = s3 + y_ax * length
    verts = []
    for a, b, c, d in [(s0, s1, e1, e0), (s1, s2, e2, e1), (s2, s3, e3, e2), (s3, s0, e0, e3)]:
        verts += [a, b, c,  a, c, d]
    verts += [e0, e1, e2,  e0, e2, e3]
    return verts


def _stick_lines(origin, y_ax, x_ax, z_ax, length, width):
    hw = width * 0.5
    s0 = origin + (-x_ax - z_ax) * hw
    s1 = origin + ( x_ax - z_ax) * hw
    s2 = origin + ( x_ax + z_ax) * hw
    s3 = origin + (-x_ax + z_ax) * hw
    e0 = s0 + y_ax * length
    e1 = s1 + y_ax * length
    e2 = s2 + y_ax * length
    e3 = s3 + y_ax * length
    return [
        s0, s1,  s1, s2,  s2, s3,  s3, s0,
        e0, e1,  e1, e2,  e2, e3,  e3, e0,
        s0, e0,  s1, e1,  s2, e2,  s3, e3,
    ]


def _capsule_lines(tip, fwd, perp1, perp2, length, radius, n=16):
    end = tip + fwd * length
    ring_s = [tip + (perp1 * math.cos(2*math.pi*i/n) + perp2 * math.sin(2*math.pi*i/n)) * radius
              for i in range(n)]
    ring_e = [end + (perp1 * math.cos(2*math.pi*i/n) + perp2 * math.sin(2*math.pi*i/n)) * radius
              for i in range(n)]
    verts = []
    for i in range(n):
        verts += [ring_s[i], ring_s[(i+1)%n]]
        verts += [ring_e[i], ring_e[(i+1)%n]]
    for perp, s in ((perp1, 1), (perp1, -1), (perp2, 1), (perp2, -1)):
        p = perp * s * radius
        verts += [tip + p, end + p]
    half = n // 2
    for perp in (perp1, perp2):
        for center, sign in ((tip, -1), (end, 1)):
            arc = [center + (perp * math.cos(math.pi * i / half) +
                             fwd * sign * math.sin(math.pi * i / half)) * radius
                   for i in range(half + 1)]
            for i in range(len(arc) - 1):
                verts += [arc[i], arc[i+1]]
    return verts


def _capsule_tris(tip, fwd, perp1, perp2, length, radius, n=16):
    end  = tip + fwd * length
    lat  = max(4, n // 4)  # latitude steps per hemisphere cap

    def _circ(center, r, off):
        return [center + fwd * off + (perp1 * math.cos(2*math.pi*k/n) +
                                      perp2 * math.sin(2*math.pi*k/n)) * r
                for k in range(n)]

    ring_s = _circ(tip, radius, 0)
    ring_e = _circ(end, radius, 0)

    verts = []
    # Cylinder body
    for i in range(n):
        j = (i + 1) % n
        verts += [ring_s[i], ring_s[j], ring_e[i], ring_s[j], ring_e[j], ring_e[i]]

    # Start hemisphere (tip, extends in -fwd)
    caps = [ring_s] + [_circ(tip, radius * math.cos(math.pi * 0.5 * s / lat),
                              -radius * math.sin(math.pi * 0.5 * s / lat))
                       for s in range(1, lat + 1)]
    for s in range(lat):
        r0, r1 = caps[s], caps[s + 1]
        for i in range(n):
            j = (i + 1) % n
            verts += [r0[i], r0[j], r1[i], r0[j], r1[j], r1[i]]

    # End hemisphere (end, extends in +fwd)
    caps = [ring_e] + [_circ(end, radius * math.cos(math.pi * 0.5 * s / lat),
                              radius * math.sin(math.pi * 0.5 * s / lat))
                       for s in range(1, lat + 1)]
    for s in range(lat):
        r0, r1 = caps[s], caps[s + 1]
        for i in range(n):
            j = (i + 1) % n
            verts += [r0[i], r0[j], r1[i], r0[j], r1[j], r1[i]]

    return verts


def _tapered_capsule_ring_list(p0, p1, perp1, perp2, fwd, r0, r1, n, lat):
    """Ordered latitude rings forming a seamless tapered capsule (convex hull of two
    spheres of radii r0/r1 at p0/p1). Rings run from the head back-pole, through both
    spheres' tangent rings (the connecting band), to the tip front-pole. Each ring is
    a list of n points. theta is measured from +f (toward p1): 0 = +f pole, pi = -f pole."""
    axis = p1 - p0
    L    = axis.length
    if L >= 1e-6:
        f = axis / L
    else:
        f = fwd.normalized() if fwd.length > 1e-9 else Vector((1.0, 0.0, 0.0))

    def ring(center, R, theta):
        ct, st = math.cos(theta), math.sin(theta)
        return [center + f * (R * ct) +
                (perp1 * math.cos(2*math.pi*k/n) + perp2 * math.sin(2*math.pi*k/n)) * (R * st)
                for k in range(n)]

    if L < 1e-6:
        # Coincident endpoints: a single sphere of the larger radius.
        R = max(r0, r1)
        return [ring(p0, R, math.pi - math.pi * i / (2 * lat)) for i in range(2 * lat + 1)]

    sin_a   = max(-1.0, min(1.0, (r0 - r1) / L))
    theta_t = math.acos(sin_a)   # polar angle of both tangent rings
    rings = []
    for i in range(lat + 1):                    # head sphere: theta pi -> theta_t
        rings.append(ring(p0, r0, math.pi - (math.pi - theta_t) * i / lat))
    for i in range(1, lat + 1):                 # tip sphere:  theta theta_t -> 0
        rings.append(ring(p1, r1, theta_t - theta_t * i / lat))
    return rings


def _tapered_capsule_tris(p0, p1, perp1, perp2, fwd, r0, r1, n=32):
    lat   = max(6, n // 3)
    rings = _tapered_capsule_ring_list(p0, p1, perp1, perp2, fwd, r0, r1, n, lat)
    verts = []
    for s in range(len(rings) - 1):
        r_a, r_b = rings[s], rings[s + 1]
        for i in range(n):
            j = (i + 1) % n
            verts += [r_a[i], r_a[j], r_b[i], r_a[j], r_b[j], r_b[i]]
    return verts


def _tapered_capsule_lines(p0, p1, perp1, perp2, fwd, r0, r1, n=32):
    lat   = max(6, n // 3)
    rings = _tapered_capsule_ring_list(p0, p1, perp1, perp2, fwd, r0, r1, n, lat)
    verts = []
    # A few latitude rings: head tangent ring (lat), tip tangent ring (lat+1), plus near-poles.
    ring_idxs = {1, lat, min(lat + 1, len(rings) - 1), len(rings) - 2}
    for s in ring_idxs:
        rg = rings[s]
        for i in range(n):
            verts += [rg[i], rg[(i + 1) % n]]
    # Longitudinal seams along cardinal directions.
    for k in range(0, n, max(1, n // 4)):
        for s in range(len(rings) - 1):
            verts += [rings[s][k], rings[s + 1][k]]
    return verts


def _box_tris(center, l_ax, u_ax, f_ax, l_min, l_max, u_min, u_max, f_min, f_max):
    def c(ls, us, fs):
        return (center
                + l_ax * (l_max if ls else -l_min)
                + u_ax * (u_max if us else -u_min)
                + f_ax * (f_max if fs else -f_min))
    def quad(a, b, cc, d):
        return [a, b, cc,  a, cc, d]
    return (
        quad(c(0,0,0), c(0,0,1), c(0,1,1), c(0,1,0)) +
        quad(c(1,0,0), c(1,1,0), c(1,1,1), c(1,0,1)) +
        quad(c(0,0,0), c(1,0,0), c(1,0,1), c(0,0,1)) +
        quad(c(0,1,0), c(0,1,1), c(1,1,1), c(1,1,0)) +
        quad(c(0,0,0), c(0,1,0), c(1,1,0), c(1,0,0)) +
        quad(c(0,0,1), c(1,0,1), c(1,1,1), c(0,1,1))
    )


def _box_lines(center, l_ax, u_ax, f_ax, l_min, l_max, u_min, u_max, f_min, f_max):
    def c(ls, us, fs):
        return (center
                + l_ax * (l_max if ls else -l_min)
                + u_ax * (u_max if us else -u_min)
                + f_ax * (f_max if fs else -f_min))
    return [
        c(0,0,0), c(1,0,0),  c(0,1,0), c(1,1,0),  c(0,0,1), c(1,0,1),  c(0,1,1), c(1,1,1),
        c(0,0,0), c(0,1,0),  c(1,0,0), c(1,1,0),  c(0,0,1), c(0,1,1),  c(1,0,1), c(1,1,1),
        c(0,0,0), c(0,0,1),  c(1,0,0), c(1,0,1),  c(0,1,0), c(0,1,1),  c(1,1,0), c(1,1,1),
    ]


# Jiggle constraint colors: pitch=red, yaw=blue, angle=green, base spring=cyan
_COLOR_PITCH        = (1.0, 0.2, 0.2)
_COLOR_YAW          = (0.2, 0.4, 1.0)
_COLOR_ANGLE        = (0.2, 1.0, 0.3)
_COLOR_BASE_SPRING  = (0.2, 0.9, 0.9)
_COLOR_COLLIDER     = (1.0, 0.6, 0.1)   # jigglebone collision capsule - orange

# Hitbox group colors matching HLMV
_HBOX_COLORS = {
    0: (1.0,  1.0,  1.0),   # Generic - White
    1: (1.0,  0.15, 0.15),  # Head - Red
    2: (0.15, 1.0,  0.15),  # Chest - Green
    3: (1.0,  1.0,  0.1),   # Stomach - Yellow
    4: (0.15, 0.2,  1.0),   # Left Arm - Deep Blue
    5: (0.85, 0.15, 1.0),   # Right Arm - Bright Violet
    6: (0.1,  1.0,  1.0),   # Left Leg - Bright Cyan
    7: (1.0,  0.55, 0.0),   # Right Leg - Orange
    8: (1.0,  0.4,  0.15),  # Neck - Reddish Orange
}


def _draw_hitbox_for_bone(shader, ob, pb, hb):
    """Draw a single hitbox entry (box or capsule) in bone-local space."""
    bone_mat  = ob.matrix_world @ get_bone_matrix(pb)
    arm_scale = Vector((bone_mat[0][0], bone_mat[1][0], bone_mat[2][0])).length

    r, g, b = _HBOX_COLORS.get(int(hb.group) if hb.group.isdigit() else 0, (1.0, 1.0, 1.0))

    rot_mat = Euler((hb.rotation[0], hb.rotation[1], hb.rotation[2]), 'XYZ').to_matrix()
    bm3     = bone_mat.to_3x3()

    # Axes in world space (not normalized - magnitude encodes object scale,
    # so multiplying by local half-extents gives correct world-space offsets).
    x_w = bm3 @ rot_mat.col[0]
    y_w = bm3 @ rot_mat.col[1]
    z_w = bm3 @ rot_mat.col[2]

    mn = Vector(hb.vec_min)
    mx = Vector(hb.vec_max)
    ctr_local = (mn + mx) * 0.5

    if hb.scale <= 0:
        # Oriented Box
        center_w = (bone_mat @ Vector((*ctr_local, 1.0))).to_3d()
        hx = abs(mx[0] - mn[0]) * 0.5
        hy = abs(mx[1] - mn[1]) * 0.5
        hz = abs(mx[2] - mn[2]) * 0.5
        tris  = _box_tris( center_w, x_w, y_w, z_w, hx, hx, hy, hy, hz, hz)
        lines = _box_lines(center_w, x_w, y_w, z_w, hx, hx, hy, hy, hz, hz)
    else:
        # Capsule - rotate endpoints around the midpoint
        p1_local = ctr_local + rot_mat @ (mn - ctr_local)
        p2_local = ctr_local + rot_mat @ (mx - ctr_local)
        p1_w = (bone_mat @ Vector((*p1_local, 1.0))).to_3d()
        p2_w = (bone_mat @ Vector((*p2_local, 1.0))).to_3d()
        cap_vec = p2_w - p1_w
        length  = cap_vec.length
        if length < 1e-6:
            return
        fwd = cap_vec.normalized()
        up  = Vector((0, 0, 1)) if abs(fwd.z) < 0.9 else Vector((1, 0, 0))
        perp1 = fwd.cross(up).normalized()
        perp2 = fwd.cross(perp1).normalized()
        radius_w = hb.scale * arm_scale
        tris  = _capsule_tris( p1_w, fwd, perp1, perp2, length, radius_w)
        lines = _capsule_lines(p1_w, fwd, perp1, perp2, length, radius_w)

    gpu.state.depth_mask_set(False)
    shader.uniform_float('color', (r, g, b, 0.12))
    batch_for_shader(shader, 'TRIS', {'pos': tris}).draw(shader)
    gpu.state.depth_mask_set(True)
    gpu.state.line_width_set(1.5)
    shader.uniform_float('color', (r, g, b, 0.70))
    batch_for_shader(shader, 'LINES', {'pos': lines}).draw(shader)


def _draw_jigglebone_collider(shader, pb, ghost_mat, scale_fac=1.0):
    """Draw the Source 2 jigglebone collision capsule (tapered, independent end radii).
    Endpoints are bone-local; ghost_mat already bakes in the bone's export offsets."""
    jvs = pb.bone.vs
    if not getattr(jvs, 'jiggle_has_collision', False):
        return

    p0 = (ghost_mat @ Vector((*jvs.jiggle_collision_point0, 1.0))).to_3d()
    p1 = (ghost_mat @ Vector((*jvs.jiggle_collision_point1, 1.0))).to_3d()
    r0 = jvs.jiggle_collision_radius0 * scale_fac
    r1 = jvs.jiggle_collision_radius1 * scale_fac
    if r0 <= 0.0 and r1 <= 0.0:
        return

    axis = p1 - p0
    if axis.length < 1e-6:
        fwd = Vector((ghost_mat[0][0], ghost_mat[1][0], ghost_mat[2][0])).normalized()
    else:
        fwd = axis.normalized()
    up    = Vector((0, 0, 1)) if abs(fwd.z) < 0.9 else Vector((1, 0, 0))
    perp1 = fwd.cross(up).normalized()
    perp2 = fwd.cross(perp1).normalized()

    r, g, b = _COLOR_COLLIDER
    lines = _tapered_capsule_lines(p0, p1, perp1, perp2, fwd, r0, r1)
    gpu.state.line_width_set(1.5)
    shader.uniform_float('color', (r, g, b, 0.85))
    batch_for_shader(shader, 'LINES', {'pos': lines}).draw(shader)


def _draw_jigglebone(shader, pb, ghost_mat, cr, cg, cb, s2, scale_fac=1.0):
    scevs = bpy.context.scene.vs
    jvs  = pb.bone.vs
    x_ax = Vector((ghost_mat[0][0], ghost_mat[1][0], ghost_mat[2][0])).normalized()
    y_ax = Vector((ghost_mat[0][1], ghost_mat[1][1], ghost_mat[2][1])).normalized()
    z_ax = Vector((ghost_mat[0][2], ghost_mat[1][2], ghost_mat[2][2])).normalized()
    tip  = ghost_mat.to_translation()

    if s2:
        fwd        = x_ax
        yaw_perp   = y_ax  # yaw fan sweeps fwd->y
        pitch_perp = z_ax  # pitch fan sweeps fwd->z
        perp1      = y_ax
        perp2      = z_ax
    else:
        # Source 1: bone points along +Z
        fwd        = z_ax
        yaw_perp   = x_ax  # yaw fan sweeps fwd->x (left/right)
        pitch_perp = y_ax  # pitch fan sweeps fwd->y (up/down)
        perp1      = x_ax
        perp2      = y_ax

    has_angle       = jvs.jiggle_has_angle_constraint and jvs.jiggle_angle_constraint > 0
    has_yaw         = jvs.jiggle_has_yaw_constraint   and (jvs.jiggle_yaw_constraint_min   > 0 or jvs.jiggle_yaw_constraint_max   > 0)
    has_pitch       = jvs.jiggle_has_pitch_constraint and (jvs.jiggle_pitch_constraint_min > 0 or jvs.jiggle_pitch_constraint_max > 0)
    has_length      = not jvs.use_bone_length_for_jigglebone_length and jvs.jiggle_length > 0
    has_base_spring = jvs.jiggle_base_type == 'BASESPRING'

    if not has_angle and not has_yaw and not has_pitch and not has_length and not has_base_spring:
        return
    
    if not scevs.preview_jigglebone_constraints:
        return

    display_len = (pb.bone.length if jvs.use_bone_length_for_jigglebone_length else (
        jvs.jiggle_length if jvs.jiggle_length > 0 else pb.bone.length
    )) * scale_fac
    plane_len = pb.bone.length * 0.5 * scale_fac

    if has_angle:
        r, g, b = _COLOR_ANGLE
        tris  = _cone_tris(tip, fwd, perp1, perp2, jvs.jiggle_angle_constraint, display_len * 0.8)
        gpu.state.depth_mask_set(False)
        shader.uniform_float('color', (r, g, b, 0.18))
        batch_for_shader(shader, 'TRIS', {'pos': tris}).draw(shader)
        gpu.state.depth_mask_set(True)

    if has_pitch:
        r, g, b = _COLOR_PITCH
        min_a = jvs.jiggle_pitch_constraint_min
        max_a = jvs.jiggle_pitch_constraint_max
        tris  = (_plane_tris(tip, fwd, pitch_perp, -min_a, plane_len) +
                 _plane_tris(tip, fwd, pitch_perp, +max_a, plane_len))
        gpu.state.depth_mask_set(False)
        shader.uniform_float('color', (r, g, b, 0.22))
        batch_for_shader(shader, 'TRIS', {'pos': tris}).draw(shader)
        gpu.state.depth_mask_set(True)

    if has_yaw:
        r, g, b = _COLOR_YAW
        min_a = jvs.jiggle_yaw_constraint_min
        max_a = jvs.jiggle_yaw_constraint_max
        tris  = (_plane_tris(tip, fwd, yaw_perp, -min_a, plane_len) +
                 _plane_tris(tip, fwd, yaw_perp, +max_a, plane_len))
        gpu.state.depth_mask_set(False)
        shader.uniform_float('color', (r, g, b, 0.22))
        batch_for_shader(shader, 'TRIS', {'pos': tris}).draw(shader)
        gpu.state.depth_mask_set(True)

    if has_base_spring:
        if s2:
            box_l, box_u, box_f = z_ax, y_ax, x_ax
        else:
            box_l, box_u, box_f = x_ax, y_ax, z_ax
        l_min = (jvs.jiggle_left_constraint_min    if jvs.jiggle_has_left_constraint    else 0) * scale_fac
        l_max = (jvs.jiggle_left_constraint_max    if jvs.jiggle_has_left_constraint    else 0) * scale_fac
        u_min = (jvs.jiggle_up_constraint_min      if jvs.jiggle_has_up_constraint      else 0) * scale_fac
        u_max = (jvs.jiggle_up_constraint_max      if jvs.jiggle_has_up_constraint      else 0) * scale_fac
        f_min = (jvs.jiggle_forward_constraint_min if jvs.jiggle_has_forward_constraint else 0) * scale_fac
        f_max = (jvs.jiggle_forward_constraint_max if jvs.jiggle_has_forward_constraint else 0) * scale_fac
        if l_min or l_max or u_min or u_max or f_min or f_max:
            r, g, b = _COLOR_BASE_SPRING
            tris  = _box_tris(tip, box_l, box_u, box_f, l_min, l_max, u_min, u_max, f_min, f_max)
            gpu.state.depth_mask_set(False)
            shader.uniform_float('color', (r, g, b, 0.18))
            batch_for_shader(shader, 'TRIS', {'pos': tris}).draw(shader)
            gpu.state.depth_mask_set(True)

    if has_length:
        cap_r = pb.bone.length * scale_fac * 0.06
        lines = _capsule_lines(tip, fwd, perp1, perp2, display_len, cap_r)
        gpu.state.line_width_set(1.5)
        shader.uniform_float('color', (cr, cg, cb, 0.85))
        batch_for_shader(shader, 'LINES', {'pos': lines}).draw(shader)


_AXIS_COLORS = (
    ((1.0,  0.4,  0.4),  'X'),
    ((0.4,  1.0,  0.4),  'Y'),
    ((0.4,  0.55, 1.0),  'Z'),
)


def _draw_ghost_axes(shader, context, ghost_mat, bone_length):
    tip    = ghost_mat.to_translation()
    axes   = [Vector((ghost_mat[r][c] for r in range(3))).normalized() for c in range(3)]
    scale  = bone_length * 0.45
    region = context.region
    rv3d   = context.region_data

    gpu.state.line_width_set(2.0)
    for ax, ((r, g, b), label) in zip(axes, _AXIS_COLORS):
        end = tip + ax * scale
        shader.uniform_float('color', (r, g, b, 1.0))
        batch_for_shader(shader, 'LINES', {'pos': [tip, end]}).draw(shader)
        pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, end)
        if pos_2d:
            _label_queue.append((pos_2d.x + 3, pos_2d.y + 3, r, g, b, label))


def _draw_labels_2d():
    if not _label_queue and not _proc_label_queue:
        return
    try:
        dm        = 8
        shader_2d = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        for x, y, r, g, b, text in _label_queue:
            # filled diamond at axis tip
            shader_2d.bind()
            shader_2d.uniform_float('color', (r, g, b, 1.0))
            batch_for_shader(shader_2d, 'TRIS', {'pos': [
                (x, y + dm), (x + dm, y), (x, y - dm),
                (x, y + dm), (x, y - dm), (x - dm, y),
            ]}).draw(shader_2d)
            # centered label just above the diamond
            blf.size(0, 14)
            blf.color(0, r, g, b, 1.0)
            w, h = blf.dimensions(0, text)
            blf.position(0, x - w * 0.5, y + dm + 4, 0)
            blf.draw(0, text)
        for x, y, r, g, b, text in _proc_label_queue:
            blf.size(0, 13)
            blf.color(0, r, g, b, 1.0)
            w, h = blf.dimensions(0, text)
            blf.position(0, x - w * 0.5, y + 4, 0)
            blf.draw(0, text)
        gpu.state.blend_set('NONE')
    except Exception:
        pass
    _label_queue.clear()
    _proc_label_queue.clear()


# -- Procedural bone preview ----------------------------------------------------

_COLOR_LOOKAT_HELPER = (0.2, 0.75, 1.0)   # cyan  - the bone doing the aiming
_COLOR_LOOKAT_TARGET = (1.0, 0.55, 0.1)   # orange - the aim target point


def _draw_active_proc_bone_preview(shader, context, ob):
    avs = getattr(ob.data, 'vs', None)
    if avs is None:
        return
    idx = avs.proc_bones_index
    if idx < 0 or idx >= len(avs.proc_bones):
        return

    entry = avs.proc_bones[idx]
    if getattr(entry, 'proc_type', 'TRIGGER') != 'LOOKAT':
        return
    if not entry.helper_bone or not entry.driver_bone:
        return

    helper_pb = ob.pose.bones.get(entry.helper_bone)
    driver_pb = ob.pose.bones.get(entry.driver_bone)
    if not driver_pb:
        return

    region = context.region
    rv3d   = context.region_data
    hr, hg, hb = _COLOR_LOOKAT_HELPER
    tr, tg, tb = _COLOR_LOOKAT_TARGET

    bvs = driver_pb.bone.vs
    if not bvs.ignore_rotation_offset:
        rot_off = (Matrix.Rotation(bvs.export_rotation_offset_z, 4, 'Z') @
                   Matrix.Rotation(bvs.export_rotation_offset_y, 4, 'Y') @
                   Matrix.Rotation(bvs.export_rotation_offset_x, 4, 'X'))
        driver_mat = ob.matrix_world @ driver_pb.matrix @ rot_off
    else:
        driver_mat = ob.matrix_world @ driver_pb.matrix
    arm_scale  = Vector((driver_mat[0][0], driver_mat[1][0], driver_mat[2][0])).length
    off        = getattr(entry, 'lookat_offset', None)
    has_offset = off is not None and not (abs(off[0]) < 1e-9 and abs(off[1]) < 1e-9 and abs(off[2]) < 1e-9)

    if has_offset:
        aim_target  = (driver_mat @ Vector((off[0], off[1], off[2], 1.0))).to_3d()
        driver_head = driver_mat.to_translation()
        gpu.state.line_width_set(1.5)
        shader.uniform_float('color', (tr, tg, tb, 0.6))
        batch_for_shader(shader, 'LINES', {'pos': [driver_head, aim_target]}).draw(shader)
    else:
        aim_target = driver_mat.to_translation()

    # Aim direction line: helper -> aim target (neutral white)
    if helper_pb:
        helper_mat  = ob.matrix_world @ helper_pb.matrix
        helper_head = helper_mat.to_translation()
        gpu.state.line_width_set(1.0)
        shader.uniform_float('color', (0.9, 0.9, 0.9, 0.35))
        batch_for_shader(shader, 'LINES', {'pos': [helper_head, aim_target]}).draw(shader)

        # Cyan crosshair at helper bone head
        h_scale = Vector((helper_mat[0][0], helper_mat[1][0], helper_mat[2][0])).length
        hs = helper_pb.bone.length * h_scale * 0.07
        gpu.state.line_width_set(2.0)
        shader.uniform_float('color', (hr, hg, hb, 0.9))
        batch_for_shader(shader, 'LINES', {'pos': [
            helper_head + Vector(( hs,   0,   0)), helper_head + Vector((-hs,   0,   0)),
            helper_head + Vector((  0,  hs,   0)), helper_head + Vector((  0, -hs,   0)),
            helper_head + Vector((  0,   0,  hs)), helper_head + Vector((  0,   0, -hs)),
        ]}).draw(shader)

        pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, helper_head)
        if pos_2d:
            _proc_label_queue.append((pos_2d.x + 6, pos_2d.y + 6, hr, hg, hb, entry.helper_bone))

    # Orange crosshair at aim target
    s = driver_pb.bone.length * arm_scale * 0.09
    gpu.state.line_width_set(2.0)
    shader.uniform_float('color', (tr, tg, tb, 1.0))
    batch_for_shader(shader, 'LINES', {'pos': [
        aim_target + Vector(( s,  0,  0)), aim_target + Vector((-s,  0,  0)),
        aim_target + Vector(( 0,  s,  0)), aim_target + Vector(( 0, -s,  0)),
        aim_target + Vector(( 0,  0,  s)), aim_target + Vector(( 0,  0, -s)),
    ]}).draw(shader)

    pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, aim_target)
    if pos_2d:
        label = entry.driver_bone + (" + offset" if has_offset else "")
        _proc_label_queue.append((pos_2d.x + 6, pos_2d.y + 6, tr, tg, tb, label))


# -- Pose UIList active-bone sync --------------------------------------------

@persistent
def _on_hitbox_sync_depsgraph(scene, depsgraph):
    global _last_active_bone_key
    try:
        context = bpy.context
        if context.mode != 'POSE':
            if _last_active_bone_key:
                _last_active_bone_key = ''
            return

        ob = context.active_object
        if not ob or ob.type != 'ARMATURE':
            return

        scvs = getattr(getattr(context, 'scene', None), 'vs', None)
        if not getattr(scvs, 'hitbox_sync_pose', True):
            return

        active_pb = context.active_pose_bone
        bone_name = active_pb.name if active_pb else ''
        bone_key  = f"{ob.name}::{bone_name}"

        if bone_key == _last_active_bone_key:
            return
        _last_active_bone_key = bone_key

        if not bone_name:
            return

        avs = getattr(ob.data, 'vs', None)
        if not avs or not avs.hitboxes:
            return

        for i, hb in enumerate(avs.hitboxes):
            if hb.bone_name == bone_name:
                if avs.hitboxes_index != i:
                    avs.hitboxes_index = i  # triggers refresh_hitbox_snapshot via update callback
                break
    except Exception:
        pass


# -- Main draw callback ---------------------------------------------------------

def _draw_export_pose_preview():
    try:
        context = bpy.context

        if context.mode == 'EDIT_ARMATURE':
            ob = context.active_object
            if not ob or not is_armature(ob):
                return
            if not context.scene.vs.preview_export_pose:
                return
            if not context.selected_bones or ob.data.show_axes:
                return
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('ALWAYS')
            shader.bind()
            for eb in context.selected_bones:
                pb = ob.pose.bones.get(eb.name)
                if pb is None:
                    continue
                ghost_mat = ob.matrix_world @ get_bone_matrix(eb.matrix, pb)
                y_col     = Vector((ghost_mat[0][1], ghost_mat[1][1], ghost_mat[2][1]))
                world_bl  = eb.length * y_col.length
                _draw_ghost_axes(shader, context, ghost_mat, world_bl)
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(1.0)
            return

        ob = context.active_object
        if not ob or not is_armature(ob):
            return

        scvs = context.scene.vs

        # -- Hitbox preview --------------------------------------------------------
        avs            = getattr(ob.data, 'vs', None)
        hitbox_entries = list(getattr(avs, 'hitboxes', [])) if avs else []
        preview_mode   = getattr(scvs, 'preview_hitboxes', 'NONE')

        if preview_mode != 'NONE' and hitbox_entries:
            if preview_mode == 'ALL':
                to_draw = hitbox_entries
            elif preview_mode == 'POSE':
                sel_names = {pb.name for pb in (context.selected_pose_bones or [])}
                to_draw   = [hb for hb in hitbox_entries if hb.bone_name in sel_names]
            else:  # SELECTED
                idx     = avs.hitboxes_index if avs else -1
                to_draw = [hitbox_entries[idx]] if 0 <= idx < len(hitbox_entries) else []
            if to_draw:
                shader_hb = gpu.shader.from_builtin('UNIFORM_COLOR')
                gpu.state.blend_set('ALPHA')
                gpu.state.depth_test_set('ALWAYS')
                gpu.state.face_culling_set('NONE')
                shader_hb.bind()
                for hb in to_draw:
                    pb_hb = ob.pose.bones.get(hb.bone_name)
                    if pb_hb:
                        _draw_hitbox_for_bone(shader_hb, ob, pb_hb, hb)
                gpu.state.face_culling_set('NONE')
                gpu.state.blend_set('NONE')
                gpu.state.depth_test_set('NONE')
                gpu.state.line_width_set(1.0)

        if context.mode != 'POSE':
            return

        # -------------------------------------------------------------------------

        if scvs.preview_proc_bones:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('ALWAYS')
            gpu.state.face_culling_set('NONE')
            shader.bind()
            _draw_active_proc_bone_preview(shader, context, ob)
            gpu.state.face_culling_set('NONE')
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(1.0)

        # -------------------------------------------------------------------------

        preview_pose = scvs.preview_export_pose
        if not context.selected_pose_bones:
            return

        s2     = _is_source2(context)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('ALWAYS')
        gpu.state.face_culling_set('NONE')
        shader.bind()

        for pb in context.selected_pose_bones:
            b = pb.bone.vs
            has_rot = not b.ignore_rotation_offset and any((
                b.export_rotation_offset_x, b.export_rotation_offset_y, b.export_rotation_offset_z
            ))
            has_loc = not b.ignore_location_offset and any((
                b.export_location_offset_x, b.export_location_offset_y, b.export_location_offset_z
            ))
            is_jiggle = b.bone_is_jigglebone

            if not is_jiggle and not preview_pose:
                continue
            if not has_rot and not has_loc and not is_jiggle:
                continue

            bl        = pb.bone.length
            ghost_mat = ob.matrix_world @ get_bone_matrix(pb)
            curr_mat  = ob.matrix_world @ pb.matrix
            cr, cg, cb = _get_bone_color(pb)

            # Scale factor: length of ghost_mat Y column encodes object's world scale
            y_col     = Vector((ghost_mat[0][1], ghost_mat[1][1], ghost_mat[2][1]))
            scale_fac = y_col.length
            world_bl  = bl * scale_fac

            if preview_pose and (has_rot or has_loc):
                gpu.state.face_culling_set('BACK')
                gpu.state.depth_mask_set(False)
                for shade, tri in _bone_octahedron_shaded_tris(ghost_mat, world_bl):
                    shader.uniform_float('color', (cr * shade, cg * shade, cb * shade, 0.28))
                    batch_for_shader(shader, 'TRIS', {'pos': tri}).draw(shader)
                gpu.state.depth_mask_set(True)
                gpu.state.face_culling_set('NONE')

                ghost_head = ghost_mat.to_translation()
                ghost_y    = y_col.normalized()
                curr_y     = Vector((curr_mat[0][1], curr_mat[1][1], curr_mat[2][1])).normalized()
                ghost_tail = ghost_head + ghost_y * world_bl
                curr_tail  = curr_mat.to_translation() + curr_y * world_bl
                gpu.state.line_width_set(1.5)
                shader.uniform_float('color', (0.6, 0.85, 1.0, 0.55))
                batch_for_shader(shader, 'LINES', {'pos': [curr_tail, ghost_tail]}).draw(shader)

                # Blender-style joint caps: solid translucent sphere + wireframe, at the tip
                # and (only when there's a location offset) at the head, so the head cap
                # doesn't pile onto the real bone head.
                gx        = Vector((ghost_mat[0][0], ghost_mat[1][0], ghost_mat[2][0])).normalized()
                gz        = Vector((ghost_mat[0][2], ghost_mat[1][2], ghost_mat[2][2])).normalized()
                sphere_r  = world_bl * 0.05
                cap_pts   = [ghost_tail] + ([ghost_head] if has_loc else [])
                for c in cap_pts:
                    gpu.state.face_culling_set('BACK')
                    gpu.state.depth_mask_set(False)
                    shader.uniform_float('color', (cr, cg, cb, 0.25))
                    batch_for_shader(shader, 'TRIS', {'pos': _sphere_tris(c, gx, gz, ghost_y, sphere_r)}).draw(shader)
                    gpu.state.depth_mask_set(True)
                    gpu.state.face_culling_set('NONE')

                if not ob.data.show_axes:
                    _draw_ghost_axes(shader, context, ghost_mat, world_bl)

            if is_jiggle:
                _draw_jigglebone(shader, pb, ghost_mat, cr, cg, cb, s2, scale_fac)
                _draw_jigglebone_collider(shader, pb, ghost_mat, scale_fac)

        gpu.state.face_culling_set('NONE')
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(1.0)
    except Exception:
        import traceback; traceback.print_exc()


# -- Edgeline preview ----------------------------------------------------------

def _mat_color(mat_name: str) -> tuple:
    """Deterministic RGB from material name - identical across calls, no external deps."""
    h = 0
    for c in mat_name.encode('utf-8'):
        h = (h * 31 + c) & 0xFFFFFFFF
    hue = (h % 1000) / 1000.0
    s, v = 0.75, 0.90
    i   = int(hue * 6)
    f   = hue * 6 - i
    p, q, t_ = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    sector = i % 6
    if sector == 0: return (v,  t_, p)
    if sector == 1: return (q,  v,  p)
    if sector == 2: return (p,  v,  t_)
    if sector == 3: return (p,  q,  v)
    if sector == 4: return (t_, p,  v)
    return (v, p, q)


def _edgeline_cache_key(ob: bpy.types.Object) -> tuple:
    vs  = ob.vs
    mat = ob.matrix_world
    return (
        id(ob.data),
        round(vs.base_toon_edgeline_thickness, 4),
        vs.edgeline_per_material,
        getattr(vs, 'toon_edgeline_vertexgroup', ''),
        getattr(vs, 'non_exportable_vgroup', ''),
        round(getattr(vs, 'non_exportable_vgroup_tolerance', 0.90), 3),
        tuple(round(mat[i][j], 4) for i in range(4) for j in range(4)),
    )


def _build_edgeline_verts(ob: bpy.types.Object, depsgraph) -> list:
    """
    Builds vertex position lists for the edgeline shell - called only on cache miss.
    Returns [(color_rgb, [world_pos, ...]), ...] - GPUBatch created inline at draw time.
    """
    vs             = ob.vs
    thickness      = vs.base_toon_edgeline_thickness
    per_mat        = vs.edgeline_per_material
    edge_vg_name   = getattr(vs, 'toon_edgeline_vertexgroup', '')
    nonexp_vg_name = getattr(vs, 'non_exportable_vgroup', '')
    nonexp_tol     = getattr(vs, 'non_exportable_vgroup_tolerance', 0.90)
    EDGE_HIDE_TOL  = 0.90

    eval_ob = ob.evaluated_get(depsgraph)
    mesh    = eval_ob.to_mesh()
    mesh.calc_loop_triangles()

    # Per-vertex minimum adjacent edge length for thickness clamping.
    # Mirrors MOD_solidify_extrude.cc: offset = abs(thickness) * offset_clamp (3.5),
    # then per-vertex: if min_edge_len < offset -> scalar = min_edge_len / offset -> t *= scalar.
    clamp_offset    = thickness * _EDGELINE_THICK_CLAMP   # reference = thickness * 3.5
    clamp_offset_sq = clamp_offset * clamp_offset
    min_edge_len_sq: dict[int, float] = {}
    verts = mesh.vertices
    for e in mesh.edges:
        i0, i1 = e.vertices
        ln_sq = (Vector(verts[i0].co) - Vector(verts[i1].co)).length_squared
        if ln_sq < min_edge_len_sq.get(i0, float('inf')):
            min_edge_len_sq[i0] = ln_sq
        if ln_sq < min_edge_len_sq.get(i1, float('inf')):
            min_edge_len_sq[i1] = ln_sq

    edge_weights:   dict[int, float] = {}
    nonexp_weights: dict[int, float] = {}

    if edge_vg_name:
        vg = ob.vertex_groups.get(edge_vg_name)
        if vg:
            vgi = vg.index
            for v in mesh.vertices:
                for g in v.groups:
                    if g.group == vgi:
                        edge_weights[v.index] = g.weight
                        break

    if nonexp_vg_name:
        vg = ob.vertex_groups.get(nonexp_vg_name)
        if vg:
            vgi = vg.index
            for v in mesh.vertices:
                for g in v.groups:
                    if g.group == vgi:
                        nonexp_weights[v.index] = g.weight
                        break

    buckets: dict[int, list] = {}
    world_mat = ob.matrix_world

    for tri in mesh.loop_triangles:
        vi = tri.vertices

        if nonexp_weights and all(nonexp_weights.get(i, 0.0) >= nonexp_tol for i in vi):
            continue
        if edge_weights and all(edge_weights.get(i, 0.0) >= EDGE_HIDE_TOL for i in vi):
            continue

        # Face normal (not vertex normal) for displacement: guarantees the direction is
        # outward for front-facing triangles, inward-facing for back faces. This means
        # back-facing shell triangles always displace away from the camera -> fail the depth
        # test -> no bleed-through. Vertex normals at concave areas can average toward the
        # camera even on back-facing triangles, causing the smudge artifact.
        face_normal = Vector(tri.normal)
        slot   = tri.material_index if per_mat else 0
        bucket = buckets.setdefault(slot, [])

        for idx in vi:
            v     = mesh.vertices[idx]
            w     = edge_weights.get(idx, 0.0)
            t     = thickness * (1.0 - w)
            ln_sq = min_edge_len_sq.get(idx, clamp_offset_sq)
            if ln_sq < clamp_offset_sq:
                t *= math.sqrt(ln_sq) / clamp_offset
            bucket.append(world_mat @ (Vector(v.co) + face_normal * t))

    src_mats = ob.data.materials
    eval_ob.to_mesh_clear()

    result = []
    for slot, verts in sorted(buckets.items()):
        if not verts:
            continue
        color = (
            _mat_color(src_mats[slot].name)
            if per_mat and slot < len(src_mats) and src_mats[slot]
            else (0.0, 0.0, 0.0)
        )
        result.append((color, verts))
    return result


@persistent
def _on_edgeline_depsgraph_update(scene, depsgraph):
    """Invalidate cache entries when an Object or its Mesh data-block is updated."""
    if not _edgeline_cache:
        return
    for update in depsgraph.updates:
        uid = getattr(update.id, 'session_uid', None)
        if uid is None:
            continue
        # Object updated directly (transform, VS properties)
        if uid in _edgeline_cache:
            del _edgeline_cache[uid]
            continue
        # Mesh data-block updated (weight paint, sculpt, edit mode)
        ob_uid = _edgeline_mesh_map.get(uid)
        if ob_uid is not None:
            _edgeline_cache.pop(ob_uid, None)


def _draw_edgeline_preview():
    global _edgeline_last_mode
    try:
        context = bpy.context

        if not getattr(getattr(context.scene, 'vs', None), 'preview_edgeline', False):
            return

        cur_mode = context.mode

        # Edgeline is a static preview only - skip during animation playback.
        if context.screen.is_animation_playing:
            return

        # Edgeline is incompatible with live simulation (pose or jiggle).
        # Clear cache on exit so it rebuilds fresh from the current pose/sim state.
        live = (cur_mode == 'POSE'
                or getattr(getattr(context.scene, 'vs', None), 'jiggle_sim_enabled', False))
        if live:
            if not _edgeline_last_mode.startswith('_live'):
                _edgeline_last_mode = '_live'
            return
        if _edgeline_last_mode == '_live':
            _edgeline_cache.clear()
        _edgeline_last_mode = cur_mode

        if cur_mode.startswith('EDIT'):
            return
        try:
            if context.space_data.shading.type == 'WIREFRAME':
                return
        except Exception:
            return

        depsgraph    = context.evaluated_depsgraph_get()
        shader       = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.face_culling_set('FRONT')
        shader.bind()

        if context.mode == 'PAINT_WEIGHT':
            # Only draw the painted object - always fresh, no cache.
            ob = context.active_object
            if (ob and ob.visible_get()
                    and getattr(getattr(ob, 'vs', None), 'use_toon_edgeline', False)
                    and is_mesh_compatible(ob)):
                try:
                    for color, verts in _build_edgeline_verts(ob, depsgraph):
                        shader.uniform_float('color', (*color, 1.0))
                        batch_for_shader(shader, 'TRIS', {'pos': verts}).draw(shader)
                except Exception:
                    import traceback; traceback.print_exc()
        else:
            for ob in context.view_layer.objects:
                if not ob.visible_get():
                    continue
                if not getattr(getattr(ob, 'vs', None), 'use_toon_edgeline', False):
                    continue
                if not is_mesh_compatible(ob):
                    continue

                try:
                    uid = ob.session_uid
                    key = _edgeline_cache_key(ob)
                    if uid not in _edgeline_cache or _edgeline_cache[uid][0] != key:
                        _edgeline_cache[uid] = (key, _build_edgeline_verts(ob, depsgraph))
                        _edgeline_mesh_map[ob.data.session_uid] = uid
                    for color, verts in _edgeline_cache[uid][1]:
                        shader.uniform_float('color', (*color, 1.0))
                        batch_for_shader(shader, 'TRIS', {'pos': verts}).draw(shader)
                except Exception:
                    import traceback; traceback.print_exc()

        gpu.state.face_culling_set('NONE')
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
    except Exception:
        import traceback; traceback.print_exc()


def _draw_sim_hud():
    try:
        context = bpy.context
        if not getattr(getattr(context.scene, 'vs', None), 'jiggle_sim_enabled', False):
            return

        region = context.region
        cx     = region.width // 2

        lines = [
            get_id('label_sim_gizmo_disabled',    format_string=True),
            get_id('label_sim_gizmo_disabled_2',  format_string=True),
            get_id('label_sim_keyframe_warning',  format_string=True),
            get_id('label_sim_keyframe_warning_2', format_string=True),
        ]

        font_id    = 0
        font_size  = 12
        pad        = 7
        gap        = 3
        group_gap  = 9
        y_base     = 16

        blf.size(font_id, font_size)
        # Split into two groups: gizmo (first 2) and keyframe (last 2)
        group1 = lines[:2]
        group2 = lines[2:]
        dims1  = [blf.dimensions(font_id, l) for l in group1]
        dims2  = [blf.dimensions(font_id, l) for l in group2]
        all_dims = dims1 + dims2
        max_w   = max(w for w, h in all_dims)
        total_h = (sum(h for _, h in dims2) + gap * (len(dims2) - 1) +
                   group_gap +
                   sum(h for _, h in dims1) + gap * (len(dims1) - 1))

        box_x0 = cx - max_w * 0.5 - pad
        box_x1 = cx + max_w * 0.5 + pad
        box_y0 = y_base - pad
        box_y1 = y_base + total_h + pad

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        shader.bind()
        shader.uniform_float('color', (0.05, 0.05, 0.05, 0.68))
        batch_for_shader(shader, 'TRIS', {'pos': [
            (box_x0, box_y0), (box_x1, box_y0), (box_x1, box_y1),
            (box_x0, box_y0), (box_x1, box_y1), (box_x0, box_y1),
        ]}).draw(shader)

        blf.color(font_id, 1.0, 0.88, 0.55, 0.90)
        y = y_base
        for text, (w, h) in zip(reversed(group2), reversed(dims2)):
            blf.position(font_id, cx - w * 0.5, y, 0)
            blf.draw(font_id, text)
            y += h + gap
        y += group_gap - gap  # extra space between groups
        for text, (w, h) in zip(reversed(group1), reversed(dims1)):
            blf.position(font_id, cx - w * 0.5, y, 0)
            blf.draw(font_id, text)
            y += h + gap

        # Bottom-left textbox: live count of simulated jiggle / procedural bones.
        from . import procbones_sim
        jiggle_n, proc_n = procbones_sim.get_sim_counts()
        count_lines = [
            get_id('label_sim_hud_jiggle_count', format_string=True).format(jiggle_n),
            get_id('label_sim_hud_proc_count',   format_string=True).format(proc_n),
        ]
        c_dims  = [blf.dimensions(font_id, l) for l in count_lines]
        c_max_w = max(w for w, h in c_dims)
        c_tot_h = sum(h for _, h in c_dims) + gap * (len(c_dims) - 1)
        cb_x0 = pad
        cb_y0 = y_base - pad
        cb_x1 = pad + c_max_w + pad * 2
        cb_y1 = y_base + c_tot_h + pad

        # blf.draw above may leave the GPU blend state altered; re-assert ALPHA
        # and re-bind our shader so this box matches the center box exactly.
        gpu.state.blend_set('ALPHA')
        shader.bind()
        shader.uniform_float('color', (0.05, 0.05, 0.05, 0.68))
        batch_for_shader(shader, 'TRIS', {'pos': [
            (cb_x0, cb_y0), (cb_x1, cb_y0), (cb_x1, cb_y1),
            (cb_x0, cb_y0), (cb_x1, cb_y1), (cb_x0, cb_y1),
        ]}).draw(shader)

        blf.color(font_id, 1.0, 0.88, 0.55, 0.90)
        cy = y_base
        for text, (w, h) in zip(reversed(count_lines), reversed(c_dims)):
            blf.position(font_id, cb_x0 + pad, cy, 0)
            blf.draw(font_id, text)
            cy += h + gap

        gpu.state.blend_set('NONE')
    except Exception:
        pass


def _build_attachment_mesh_tris(mesh_ob):
    mesh = mesh_ob.data
    mesh.calc_loop_triangles()
    verts = mesh.vertices
    tris = []
    for lt in mesh.loop_triangles:
        for vi in lt.vertices:
            co = verts[vi].co
            tris.append((co.x, co.y, co.z))
    return tris


@persistent
def _on_attachment_mesh_depsgraph_update(scene, depsgraph):
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Mesh):
            uid = update.id.session_uid
            _attachment_mesh_cache.pop(uid, None)


def _draw_attachment_mesh_preview():
    context = bpy.context
    if not hasattr(context, 'scene') or not context.scene:
        return
    try:
        vs = context.scene.vs
    except AttributeError:
        return
    preview_mode = vs.preview_attachment_mesh
    if preview_mode == 'NONE':
        return
    if context.mode.startswith('EDIT'):
        return
    if context.screen.is_animation_playing:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()

    selected = {ob.session_uid for ob in context.selected_objects}

    for ob in context.scene.objects:
        if preview_mode == 'SELECTED' and ob.session_uid not in selected:
            continue
        if ob.type != 'EMPTY':
            continue
        vs_ob = ob.vs
        if not vs_ob.dmx_attachment:
            continue
        render_idx = vs_ob.attachment_display_mesh_render_index
        meshes = vs_ob.attachment_display_meshes
        if render_idx < 0 or render_idx >= len(meshes):
            continue
        item = meshes[render_idx]
        mesh_ob = item.mesh
        if mesh_ob is None or mesh_ob.type != 'MESH':
            continue

        data_uid = mesh_ob.data.session_uid
        cached = _attachment_mesh_cache.get(data_uid)
        if cached is None or cached[0] != data_uid:
            try:
                tris = _build_attachment_mesh_tris(mesh_ob)
            except Exception:
                continue
            _attachment_mesh_cache[data_uid] = (data_uid, tris)
            local_verts = tris
        else:
            local_verts = cached[1]

        if not local_verts:
            continue

        mat = ob.matrix_world
        world_verts = [mat @ Vector(v) for v in local_verts]

        color = item.color
        try:
            is_wireframe = False
            try:
                is_wireframe = context.space_data.shading.type == 'WIREFRAME'
            except Exception:
                pass
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('ALWAYS' if is_wireframe else 'LESS_EQUAL')
            gpu.state.depth_mask_set(False)
            gpu.state.face_culling_set('NONE')
            shader.uniform_float('color', (color[0], color[1], color[2], color[3]))
            batch_for_shader(shader, 'TRIS', {'pos': world_verts}).draw(shader)
        finally:
            gpu.state.blend_set('NONE')
            gpu.state.depth_mask_set(True)


def register_draw_handler():
    global _handle, _handle_2d, _hud_handle, _edgeline_handle, _edgeline_depsgraph_handle, _hitbox_sync_handle, _attachment_mesh_handle, _attachment_mesh_depsgraph_handle
    if _handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception: pass
    if _handle_2d is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_handle_2d, 'WINDOW')
        except Exception: pass
    if _hud_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_hud_handle, 'WINDOW')
        except Exception: pass
    if _edgeline_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_edgeline_handle, 'WINDOW')
        except Exception: pass
    if _edgeline_depsgraph_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_edgeline_depsgraph_handle)
        except Exception: pass
    if _hitbox_sync_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_hitbox_sync_handle)
        except Exception: pass
    if _attachment_mesh_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_attachment_mesh_handle, 'WINDOW')
        except Exception: pass
    if _attachment_mesh_depsgraph_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_attachment_mesh_depsgraph_handle)
        except Exception: pass
    _edgeline_cache.clear()
    _edgeline_mesh_map.clear()
    _attachment_mesh_cache.clear()
    _handle    = bpy.types.SpaceView3D.draw_handler_add(
        _draw_export_pose_preview, (), 'WINDOW', 'POST_VIEW'
    )
    _handle_2d = bpy.types.SpaceView3D.draw_handler_add(
        _draw_labels_2d, (), 'WINDOW', 'POST_PIXEL'
    )
    _hud_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_sim_hud, (), 'WINDOW', 'POST_PIXEL'
    )
    _edgeline_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_edgeline_preview, (), 'WINDOW', 'POST_VIEW'
    )
    _attachment_mesh_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_attachment_mesh_preview, (), 'WINDOW', 'POST_VIEW'
    )
    bpy.app.handlers.depsgraph_update_post.append(_on_edgeline_depsgraph_update)
    _edgeline_depsgraph_handle = _on_edgeline_depsgraph_update
    bpy.app.handlers.depsgraph_update_post.append(_on_hitbox_sync_depsgraph)
    _hitbox_sync_handle = _on_hitbox_sync_depsgraph
    bpy.app.handlers.depsgraph_update_post.append(_on_attachment_mesh_depsgraph_update)
    _attachment_mesh_depsgraph_handle = _on_attachment_mesh_depsgraph_update


def unregister_draw_handler():
    global _handle, _handle_2d, _hud_handle, _edgeline_handle, _edgeline_depsgraph_handle, _hitbox_sync_handle, _attachment_mesh_handle, _attachment_mesh_depsgraph_handle
    if _handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception: pass
        _handle = None
    if _handle_2d is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_handle_2d, 'WINDOW')
        except Exception: pass
        _handle_2d = None
    if _hud_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_hud_handle, 'WINDOW')
        except Exception: pass
        _hud_handle = None
    if _edgeline_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_edgeline_handle, 'WINDOW')
        except Exception: pass
        _edgeline_handle = None
    if _edgeline_depsgraph_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_edgeline_depsgraph_handle)
        except Exception: pass
        _edgeline_depsgraph_handle = None
    if _hitbox_sync_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_hitbox_sync_handle)
        except Exception: pass
        _hitbox_sync_handle = None
    if _attachment_mesh_handle is not None:
        try: bpy.types.SpaceView3D.draw_handler_remove(_attachment_mesh_handle, 'WINDOW')
        except Exception: pass
        _attachment_mesh_handle = None
    if _attachment_mesh_depsgraph_handle is not None:
        try: bpy.app.handlers.depsgraph_update_post.remove(_attachment_mesh_depsgraph_handle)
        except Exception: pass
        _attachment_mesh_depsgraph_handle = None
    _edgeline_cache.clear()
    _edgeline_mesh_map.clear()
    _attachment_mesh_cache.clear()