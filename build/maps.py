"""Parse the .jm2 map files (one per 64x64 map square)."""
import os
import glob
import common
from common import read_text


class MapSquare:
    __slots__ = ('mx', 'mz', 'h', 'o', 'os', 'orot', 'f', 'u', 'locs', 'npcs', 'objs', 'has_level')

    def __init__(self, mx, mz):
        self.mx = mx
        self.mz = mz
        # per level, flat 64*64 arrays indexed x*64+z
        self.h = [[0] * 4096 for _ in range(4)]
        self.o = [[0] * 4096 for _ in range(4)]
        self.os = [[0] * 4096 for _ in range(4)]
        self.orot = [[0] * 4096 for _ in range(4)]
        self.f = [[0] * 4096 for _ in range(4)]
        self.u = [[0] * 4096 for _ in range(4)]
        self.locs = []   # (level, x, z, id, shape, angle)
        self.npcs = []   # (level, x, z, id)
        self.objs = []   # (level, x, z, id, count)
        self.has_level = [False] * 4


def parse_map(path):
    base = os.path.basename(path)[1:-4]
    mx, mz = (int(v) for v in base.split('_'))
    sq = MapSquare(mx, mz)
    section = None
    h, o, os_, orot, f, u = sq.h, sq.o, sq.os, sq.orot, sq.f, sq.u
    for line in read_text(path).splitlines():
        if not line:
            continue
        if line[0] == '=':
            section = line.strip('= ').strip()
            continue
        head, _, data = line.partition(': ')
        parts = head.split(' ')
        if len(parts) != 3:
            continue
        level = int(parts[0])
        x = int(parts[1])
        z = int(parts[2])
        idx = x * 64 + z
        if section == 'MAP':
            for tok in data.split(' '):
                if not tok:
                    continue
                t = tok[0]
                info = tok[1:]
                if t == 'h':
                    h[level][idx] = int(info)
                elif t == 'u':
                    u[level][idx] = int(info)
                    sq.has_level[level] = True
                elif t == 'o':
                    bits = info.split(';')
                    o[level][idx] = int(bits[0])
                    if len(bits) > 1:
                        os_[level][idx] = int(bits[1])
                    if len(bits) > 2:
                        orot[level][idx] = int(bits[2])
                    sq.has_level[level] = True
                elif t == 'f':
                    f[level][idx] = int(info)
        elif section == 'LOC':
            bits = data.split(' ')
            lid = int(bits[0])
            shape = int(bits[1]) if len(bits) > 1 else 10
            angle = int(bits[2]) if len(bits) > 2 else 0
            sq.locs.append((level, x, z, lid, shape, angle))
        elif section == 'NPC':
            sq.npcs.append((level, x, z, int(data)))
        elif section == 'OBJ':
            bits = data.split(' ')
            sq.objs.append((level, x, z, int(bits[0]), int(bits[1]) if len(bits) > 1 else 1))
    return sq


def load_all_maps():
    """Returns dict (mx, mz) -> MapSquare"""
    out = {}
    for path in sorted(glob.glob(os.path.join(common.CONTENT, 'maps', 'm*.jm2'))):
        sq = parse_map(path)
        out[(sq.mx, sq.mz)] = sq
    return out
