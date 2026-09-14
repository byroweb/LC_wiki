"""Render minimap-style PNG tiles (4 px per game tile, 256x256 per map square)."""
import os
from PIL import Image
import common
from common import hexcolour

TILE_PX = 4
SQ_PX = 64 * TILE_PX

# From the client: 13 overlay shape masks (index = jm2 shape + 1) and rotation maps.
MASK = [
    [0] * 16,
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
    [0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1],
    [0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 0],
    [1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 0, 0, 1, 1],
    [1, 1, 1, 1, 1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1],
]
ROT = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [12, 8, 4, 0, 13, 9, 5, 1, 14, 10, 6, 2, 15, 11, 7, 3],
    [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    [3, 7, 11, 15, 2, 6, 10, 14, 1, 5, 9, 13, 0, 4, 8, 12],
]

WALL_STRAIGHT, WALL_DIAGONALCORNER, WALL_L, WALL_SQUARECORNER = 0, 1, 2, 3
WALL_DIAGONAL = 9
GROUND_DECOR = 22
WALL_RGB = (0xEE, 0xEE, 0xEE)
DOOR_RGB = (0xEE, 0x00, 0x00)


def texture_average(name):
    path = os.path.join(common.CONTENT, 'textures', name + '.png')
    if not os.path.exists(path):
        return (0x80, 0x80, 0x80)
    im = Image.open(path).convert('RGB')
    px = [p for p in im.getdata() if p != (255, 0, 255)]
    if not px:
        return (0x80, 0x80, 0x80)
    n = len(px)
    return (sum(p[0] for p in px) // n, sum(p[1] for p in px) // n, sum(p[2] for p in px) // n)


def flo_colours(flo_cfg, flo_id2name):
    """flo id -> (r,g,b) or None (invisible/black)."""
    out = {}
    for fid, name in flo_id2name.items():
        cfg = flo_cfg.get(name)
        if not cfg:
            out[fid] = (0x80, 0x80, 0x80)
            continue
        d = cfg['d']
        if 'texture' in d:
            out[fid] = texture_average(d['texture'])
        elif 'colour' in d:
            v = hexcolour(d['colour'])
            if v == 0xFF00FF:
                out[fid] = None
            else:
                out[fid] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
        else:
            out[fid] = (0x80, 0x80, 0x80)
    return out


def darken(c, k):
    if c is None:
        return None
    return (max(0, min(255, int(c[0] * k))), max(0, min(255, int(c[1] * k))), max(0, min(255, int(c[2] * k))))


def load_sprites(name, fw, fh):
    """Split sprites/<name>.png into frames (pink = transparent). Returns list of RGBA images."""
    im = Image.open(os.path.join(common.CONTENT, 'sprites', name + '.png')).convert('RGBA')
    cols = im.width // fw
    rows = im.height // fh
    frames = []
    for i in range(cols * rows):
        fx, fy = (i % cols) * fw, (i // cols) * fh
        fr = im.crop((fx, fy, fx + fw, fy + fh))
        data = [(0, 0, 0, 0) if (p[0], p[1], p[2]) == (255, 0, 255) else p for p in fr.getdata()]
        fr.putdata(data)
        frames.append(fr)
    return frames


class Renderer:
    def __init__(self, squares, flo_rgb, loc_info, mapscene_frames):
        """
        squares: dict (mx,mz)->MapSquare
        flo_rgb: flo id -> rgb or None
        loc_info: loc id -> dict(mapscene:int|-1, active:bool, width:int, length:int)
        """
        self.squares = squares
        self.flo_rgb = flo_rgb
        self.loc_info = loc_info
        self.mapscene = mapscene_frames
        self._ucache = {}

    def _raw_underlay(self, level, gx, gz):
        sq = self.squares.get((gx >> 6, gz >> 6))
        if sq is None:
            return None
        idx = (gx & 63) * 64 + (gz & 63)
        u = sq.u[level][idx]
        if u == 0:
            return None
        return self.flo_rgb.get(u - 1)

    def _underlay_grid(self, sq, level):
        """Blurred underlay colours for a square/level, with border lookups into neighbours."""
        key = (sq.mx, sq.mz, level)
        if key in self._ucache:
            return self._ucache[key]
        bx, bz = sq.mx << 6, sq.mz << 6
        raw = {}
        for x in range(-2, 66):
            for z in range(-2, 66):
                raw[(x, z)] = self._raw_underlay(level, bx + x, bz + z)
        out = [None] * 4096
        for x in range(64):
            for z in range(64):
                c = raw[(x, z)]
                if c is None:
                    continue
                r = g = b = n = 0
                for dx in (-2, -1, 0, 1, 2):
                    for dz in (-2, -1, 0, 1, 2):
                        cc = raw[(x + dx, z + dz)]
                        if cc is None:
                            continue
                        r += cc[0]
                        g += cc[1]
                        b += cc[2]
                        n += 1
                out[x * 64 + z] = (r // n, g // n, b // n)
        self._ucache[key] = out
        return out

    def render(self, sq, level):
        """Returns PIL RGBA image or None if the level is empty for this square."""
        f1 = sq.f[1]
        src_levels = [level] * 4096
        if level < 3:
            for i in range(4096):
                if f1[i] & 2:
                    src_levels[i] = level + 1
        used_levels = set(src_levels)
        if not any(sq.has_level[lv] for lv in used_levels):
            return None
        buf = bytearray(SQ_PX * SQ_PX * 4)
        ugrids = {lv: self._underlay_grid(sq, lv) for lv in used_levels}
        hmap = sq.h
        flo_rgb = self.flo_rgb
        for x in range(64):
            px0 = x * TILE_PX
            for z in range(64):
                idx = x * 64 + z
                lv = src_levels[idx]
                py0 = (63 - z) * TILE_PX
                ug = ugrids[lv][idx]
                shade = 1.0
                if ug is not None:
                    h = hmap[lv]
                    hw = h[(x - 1) * 64 + z] if x > 0 else h[idx]
                    he = h[(x + 1) * 64 + z] if x < 63 else h[idx]
                    hn = h[idx + 1] if z < 63 else h[idx]
                    hs = h[idx - 1] if z > 0 else h[idx]
                    d = (hw - he) + (hn - hs)
                    if d:
                        shade = max(0.55, min(1.35, 1.0 + d / 48.0))
                bg = darken(ug, 0.92 * shade) if ug is not None else None
                o = sq.o[lv][idx]
                if o:
                    fg = flo_rgb.get(o - 1)
                    fg = (0, 0, 0) if fg is None else darken(fg, 0.92 * shade)
                    shape = sq.os[lv][idx] + 1
                    if shape >= len(MASK):
                        shape = 1
                    mask = MASK[shape]
                    rot = ROT[sq.orot[lv][idx] & 3]
                    k = 0
                    for row in range(4):
                        base = ((py0 + row) * SQ_PX + px0) * 4
                        for col in range(4):
                            c = fg if mask[rot[k]] else bg
                            k += 1
                            if c is None:
                                continue
                            p = base + col * 4
                            buf[p] = c[0]
                            buf[p + 1] = c[1]
                            buf[p + 2] = c[2]
                            buf[p + 3] = 255
                elif bg is not None:
                    for row in range(4):
                        base = ((py0 + row) * SQ_PX + px0) * 4
                        for col in range(4):
                            p = base + col * 4
                            buf[p] = bg[0]
                            buf[p + 1] = bg[1]
                            buf[p + 2] = bg[2]
                            buf[p + 3] = 255
        img = Image.frombytes('RGBA', (SQ_PX, SQ_PX), bytes(buf))
        self._draw_locs(img, sq, level)
        return img

    @staticmethod
    def _put(px, x, y, c):
        if 0 <= x < SQ_PX and 0 <= y < SQ_PX:
            px[x, y] = (c[0], c[1], c[2], 255)

    def _draw_locs(self, img, sq, level):
        px = img.load()
        f1 = sq.f[1]
        for (lv, x, z, lid, shape, angle) in sq.locs:
            idx = x * 64 + z
            disp = lv
            if f1[idx] & 2 and lv >= 1:
                disp = lv - 1
            if disp != level:
                continue
            info = self.loc_info.get(lid)
            if info is None:
                continue
            x0 = x * TILE_PX
            ytop = (63 - z) * TILE_PX
            ms = info['mapscene']
            if ms != -1:
                if ms == 22 or shape == GROUND_DECOR or 12 <= shape <= 21:
                    continue
                fr = self.mapscene[ms] if ms < len(self.mapscene) else None
                if fr is None:
                    continue
                w, l = info['width'], info['length']
                if angle & 1:
                    w, l = l, w
                dx = x0 + (w * TILE_PX - fr.width) // 2
                dy = (63 - (z + l - 1)) * TILE_PX + (l * TILE_PX - fr.height) // 2
                if 0 <= dx < SQ_PX and 0 <= dy < SQ_PX:
                    img.alpha_composite(fr, (dx, dy))
                continue
            col = DOOR_RGB if info['active'] else WALL_RGB
            if shape in (WALL_STRAIGHT, WALL_L):
                edges = [angle]
                if shape == WALL_L:
                    edges.append((angle + 1) & 3)
                for e in edges:
                    if e == 0:
                        for i in range(4):
                            self._put(px, x0, ytop + i, col)
                    elif e == 1:
                        for i in range(4):
                            self._put(px, x0 + i, ytop, col)
                    elif e == 2:
                        for i in range(4):
                            self._put(px, x0 + 3, ytop + i, col)
                    else:
                        for i in range(4):
                            self._put(px, x0 + i, ytop + 3, col)
            elif shape == WALL_DIAGONAL:
                if angle in (0, 2):
                    for i in range(4):
                        self._put(px, x0 + i, ytop + 3 - i, col)
                else:
                    for i in range(4):
                        self._put(px, x0 + i, ytop + i, col)
            elif shape in (WALL_SQUARECORNER, WALL_DIAGONALCORNER):
                if angle == 0:
                    self._put(px, x0, ytop, col)
                elif angle == 1:
                    self._put(px, x0 + 3, ytop, col)
                elif angle == 2:
                    self._put(px, x0 + 3, ytop + 3, col)
                else:
                    self._put(px, x0, ytop + 3, col)
