# World pose of a Unity Transform, read from its native TransformHierarchy (Unity 2022.3, 64-bit)
import math

import numpy as np

CACHED_PTR = 0x10        # UnityEngine.Object.m_CachedPtr
HIERARCHY, INDEX = 0x30, 0x38
LOCAL_TRS, PARENTS = 0x18, 0x20
TRS_SIZE = 0x30          # float4 position, float4 rotation (xyzw), float4 scale
MAX_DEPTH = 64


def quat_rotate(q, v):
    x, y, z, w = q
    u = np.array([x, y, z])
    return 2 * np.dot(u, v) * u + (w * w - np.dot(u, u)) * v + 2 * w * np.cross(u, v)


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def world_pose_native(proc, native):
    """(position xyz, forward xyz) of a native Transform, or None."""
    hierarchy, index = proc.u64(native + HIERARCHY), proc.i32(native + INDEX)
    if not hierarchy or index is None or not 0 <= index < 1 << 20:
        return None
    trs, parents = proc.u64(hierarchy + LOCAL_TRS), proc.u64(hierarchy + PARENTS)
    if not trs or not parents:
        return None
    chain = []
    for _ in range(MAX_DEPTH):
        f = proc.floats(trs + index * TRS_SIZE, 12)
        if f is None:
            return None
        chain.append(f)
        index = proc.i32(parents + index * 4)
        if index is None or index < 0:
            break
    # Fold from the root down: world = parent.pos + parent.rot * (parent.scale * local.pos)
    pos, rot, scale = np.zeros(3), (0.0, 0.0, 0.0, 1.0), np.ones(3)
    for f in reversed(chain):
        pos = pos + quat_rotate(rot, scale * np.array(f[0:3]))
        rot = quat_mul(rot, f[4:8])
        scale = scale * np.array(f[8:11])
    if not all(math.isfinite(v) for v in pos):
        return None
    forward = quat_rotate(rot, np.array([0.0, 0.0, 1.0]))
    return tuple(float(v) for v in pos), tuple(float(v) for v in forward)


def world_pose(proc, managed_transform):
    native = proc.u64(managed_transform + CACHED_PTR)
    return world_pose_native(proc, native) if native else None


def world_position(proc, managed_transform):
    pose = world_pose(proc, managed_transform)
    return pose[0] if pose else None
