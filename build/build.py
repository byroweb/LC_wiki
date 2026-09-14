#!/usr/bin/env python3
"""Build the Lost City knowledge graph, wiki and map explorer from source/content.

Usage:  python build/build.py [--skip-tiles] [--force-tiles]
"""
import os
import re
import sys
import json
import html
import glob
import math
import time
import shutil
from collections import Counter, defaultdict
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from common import (SITE, STATIC, parse_config_files, parse_pack, parse_labels,
                    pretty, read_text, relpath)
from maps import load_all_maps
from drops import load_script_blocks, DropEvaluator, prob_text
from portals import PortalResolver
from render import Renderer, flo_colours, load_sprites, GROUND_DECOR


def esc(s):
    return html.escape(str(s), quote=True)


def slugify(s):
    s = re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')
    return s or 'x'


TABLE_NAMES = {
    'randomherb': 'Herb drop table',
    'randomjewel': 'Gem drop table',
    'ultrarare_getitem': 'Ultra-rare drop table',
    'megararetable': 'Mega-rare drop table',
    'randomjunk': 'Junk drop table',
    'gem_rock_table': 'Gem rock table',
}

CLUE_LABELS = {'CLUE_EASY': 'Clue scroll (easy)', 'CLUE_MEDIUM': 'Clue scroll (medium)', 'CLUE_HARD': 'Clue scroll (hard)'}
STAT_KEYS = [('stabattack', 'Stab att'), ('slashattack', 'Slash att'), ('crushattack', 'Crush att'), ('magicattack', 'Magic att'), ('rangeattack', 'Range att'),
             ('stabdefence', 'Stab def'), ('slashdefence', 'Slash def'), ('crushdefence', 'Crush def'), ('magicdefence', 'Magic def'), ('rangedefence', 'Range def'),
             ('strengthbonus', 'Strength'), ('rangebonus', 'Ranged str'), ('prayerbonus', 'Prayer')]
NPC_BONUS_KEYS = [('attackbonus', 'Attack bonus'), ('strengthbonus', 'Strength bonus'), ('rangebonus', 'Ranged bonus'), ('stabdefence', 'Stab def'), ('slashdefence', 'Slash def'),
                  ('crushdefence', 'Crush def'), ('magicdefence', 'Magic def'), ('rangedefence', 'Range def')]


class Build:
    def __init__(self):
        self.t0 = time.time()
        self.gen = None   # filled by tables.build_tables: recipes and resource stands

    def fishing_for_npc(self, n):
        """[[fish, level, tool, bait, xp], ...] for a fishing-spot NPC, from the recipe table."""
        if not self.gen:
            return None
        out = []
        for r in self.gen['recipes']:
            if r['skill'] == 'fishing' and n['name'] in r.get('station_npcs', []):
                out.append([self.item_label(r['product']), r['level'], self.item_label(r['tool']) if r.get('tool') else None,
                            self.item_label(r['inputs'][0]['item']) if r['inputs'] else None, r['xp']])
        out.sort(key=lambda f: f[1])
        return out or None

    def map_stands(self):
        """Resource stands for the map viewer: [level,x,z,kind,resource label,itemId,count,radius,levelReq,xp,tool,bank,bankDist,bbox]."""
        if not self.gen:
            return []
        rec = {}
        for r in self.gen['recipes']:
            if r.get('product') and r['skill'] in ('woodcutting', 'mining', 'fishing'):
                rec.setdefault(r['product'], r)
        out = []
        for s in self.gen['stands']:
            if s['kind'] == 'tree' and s['resource'] == 'logs' and s['count'] < 6:
                continue   # single ordinary trees are everywhere; keep woods only
            r = rec.get(s['resource'])
            tool = None
            if r:
                if r.get('tools'):
                    tool = ', '.join(self.item_label(t['item']) for t in r['tools'][:2]) + ('...' if len(r['tools']) > 2 else '')
                elif r.get('tool'):
                    tool = self.item_label(r['tool']) + ((' + ' + self.item_label(r['inputs'][0]['item'])) if r['inputs'] else '')
            it = self.items.get(s['resource'])
            out.append([s['floor'], s['centre']['x'], s['centre']['z'], s['kind'], self.item_label(s['resource']), it['id'] if it else None,
                        s['count'], s['radius'], s['level'], r['xp'] if r else None, tool,
                        s['nearest_bank']['name'] if s['nearest_bank'] else None, s['nearest_bank']['distance'] if s['nearest_bank'] else None, s['bbox']])
        return out

    def log(self, msg):
        print('[%6.1fs] %s' % (time.time() - self.t0, msg), flush=True)

    # ------------------------------------------------------------------ load
    def load(self):
        self.npc_id2name, self.npc_name2id = parse_pack('npc')
        self.obj_id2name, self.obj_name2id = parse_pack('obj')
        self.loc_id2name, self.loc_name2id = parse_pack('loc')
        self.flo_id2name, _ = parse_pack('flo')
        self.npc_cfg = parse_config_files('npc')
        self.obj_cfg = parse_config_files('obj')
        self.loc_cfg = parse_config_files('loc')
        self.inv_cfg = parse_config_files('inv')
        self.flo_cfg = parse_config_files('flo')
        self.dbrow_cfg = parse_config_files('dbrow')
        self.enum_cfg = parse_config_files('enum')
        self.labels = parse_labels()
        # which game revision the content is (branch name + date from the Content README)
        self.revision = 'unknown'
        self.revision_date = ''
        try:
            import subprocess
            self.revision = subprocess.check_output(['git', '-C', common.CONTENT, 'rev-parse', '--abbrev-ref', 'HEAD'], text=True).strip()
        except Exception:
            pass
        m = re.search(r'<h1>\s*Lost City\s*-\s*([^<]+?)\s*</h1>', read_text(os.path.join(common.CONTENT, 'README.md')))
        if m:
            self.revision_date = m.group(1).strip()
        self.rev_text = 'revision %s%s' % (self.revision, (' (%s)' % self.revision_date) if self.revision_date else '')
        # the rune essence mine has no map label of its own; derive one from the teleport enum
        ess = self.enum_cfg.get('essence_mine_teleports')
        if ess:
            first = [v.split(',', 1)[1] for v in ess['multi'].get('val', [])][:1]
            if first:
                l, mx, mz, lx, lz = (int(v) for v in first[0].split('_'))
                self.labels.append({'name': 'Rune Essence Mine', 'x': mx * 64 + lx, 'z': mz * 64 + lz, 'size': 0})
        self.log('configs: %d npcs, %d objs, %d locs, %d invs' % (len(self.npc_cfg), len(self.obj_cfg), len(self.loc_cfg), len(self.inv_cfg)))
        self.squares = load_all_maps()
        self.log('maps: %d squares' % len(self.squares))
        self.blocks = load_script_blocks()
        self.log('scripts: %d blocks' % len(self.blocks))

    # ------------------------------------------------------------------ areas
    def build_areas(self):
        self.areas = []
        seen = Counter()
        for l in self.labels:
            slug = slugify(l['name'])
            seen[slug] += 1
            if seen[slug] > 1:
                slug = '%s_%d_%d' % (slug, l['x'], l['z'])
            self.areas.append({'slug': slug, 'name': l['name'], 'x': l['x'], 'z': l['z'], 'size': l['size'],
                               'npcs': Counter(), 'objs': Counter(), 'shops': set(), 'url': 'area/%s.html' % slug})
        self.area_by_slug = {a['slug']: a for a in self.areas}
        for a in self.areas:
            a['regions'] = {a['name']} if a['size'] == 2 else self.region_of(a['x'], a['z'])
            a['region'] = ', '.join(sorted(a['regions']))

    # Surface bounds for the regions that the nearest-label fallback gets wrong.
    # First match wins; the Wilderness (exact coord_pair zone from the content) is
    # checked before any of these. Coordinates outside every rect fall back to the
    # nearest *mainland kingdom* label -- islands and the Wilderness never take part in
    # that fallback, so e.g. Trollheim can no longer land in the Wilderness by proximity
    # to its label, nor Death Plateau in Entrana.
    REGION_BOUNDS = [
        # islands (x1, z1, x2, z2 inclusive)
        ('Entrana', [(2800, 3320, 2880, 3400)]),
        ('Crandor', [(2810, 3250, 2880, 3319)]),
        ('Karamja', [(2680, 2870, 2975, 3210), (2950, 3020, 3010, 3080)]),   # main island + ship yard
        # mainland east -> west; River Salve is at x~3410
        ('Morytania', [(3425, 3200, 3599, 3519), (3392, 3520, 3599, 6399)]),
        ('Kingdom Of Misthalin', [(3058, 3300, 3424, 3519), (3080, 2800, 3424, 3299),
                                  (3050, 3050, 3150, 3140)]),                  # + Tutorial Island
        ('Kingdom of Asgarnia', [(2830, 3211, 3079, 3299), (2830, 3300, 3057, 3519),
                                 (2830, 3520, 2943, 3899)]),                   # + troll country north of Burthorpe
        ('Feldip Hills', [(2300, 2800, 2679, 3064)]),
        ('Kingdom of Kandarin', [(2300, 3065, 2829, 3599)]),
    ]
    MAINLAND_KINGDOMS = ('Kingdom Of Misthalin', 'Kingdom of Asgarnia', 'Kingdom of Kandarin', 'Feldip Hills', 'Morytania')

    def wilderness_zones(self):
        """[(x1, z1, x2, z2), ...] exact Wilderness rects from the coord_pair_table db row."""
        if getattr(self, '_wild', None) is None:
            from portals import parse_coord
            rects = []
            cfg = self.dbrow_cfg.get('wilderness_zones')
            if cfg and cfg['d'].get('table') == 'coord_pair_table':
                for v in cfg['multi'].get('data', []):
                    parts = v.split(',')
                    if parts[0] != 'coord_pair' or len(parts) < 3:
                        continue
                    p1, p2 = parse_coord(parts[1]), parse_coord(parts[2])
                    if p1 and p2:
                        rects.append((min(p1[1], p2[1]), min(p1[2], p2[2]), max(p1[1], p2[1]), max(p1[2], p2[2])))
            if not rects:   # content without the row: the box the 2004 content uses
                rects = [(2944, 3520, 3391, 6399), (2944, 9920, 3391, 12799)]
            self._wild = rects
        return self._wild

    def region_of(self, x, z):
        """Coarse region tags for a coordinate.

        Underground places (z >= 6400) get 'Underground' plus the region of the surface
        directly above them, so the Wilderness includes its dungeons. The Wilderness is
        the exact coord_pair zone the content itself uses (wilderness_zones db row);
        everything else is matched against REGION_BOUNDS, then falls back to the nearest
        mainland kingdom label.
        """
        regions = set()
        sx, sz = x, z
        if z >= 6400:
            regions.add('Underground')
            sz = z - 6400
        for x1, z1, x2, z2 in self.wilderness_zones():
            if x1 <= x <= x2 and z1 <= z <= z2:
                regions.add('Wilderness')
                return regions
        for name, rects in self.REGION_BOUNDS:
            if any(x1 <= sx <= x2 and z1 <= sz <= z2 for x1, z1, x2, z2 in rects):
                regions.add(name)
                return regions
        best, bd = None, 1e9
        for a in self.areas:
            if a['size'] != 2 or a['name'] not in self.MAINLAND_KINGDOMS:
                continue
            d = math.hypot(a['x'] - sx, a['z'] - sz)
            if d < bd:
                best, bd = a, d
        regions.add(best['name'] if best else 'Unknown')
        return regions

    def underground_twin(self, surface):
        """Derived area for the dungeon level below a surface label, e.g. 'Edgeville (underground)'."""
        slug = surface['slug'] + '_underground'
        twin = self.area_by_slug.get(slug)
        if twin is None:
            twin = {'slug': slug, 'name': surface['name'] + ' (underground)', 'x': surface['x'], 'z': surface['z'] + 6400,
                    'size': surface['size'], 'npcs': Counter(), 'objs': Counter(), 'shops': set(), 'url': 'area/%s.html' % slug,
                    'surface': surface['slug']}
            twin['regions'] = self.region_of(twin['x'], twin['z'])
            twin['region'] = ', '.join(sorted(twin['regions']))
            self.areas.append(twin)
            self.area_by_slug[slug] = twin
        return twin

    def nearest_area(self, x, z):
        if z >= 6400:
            surface = self.nearest_area(x, z - 6400)
            return self.underground_twin(surface) if surface else None
        best, best_score = None, 1e9
        for a in self.areas:
            if a.get('surface'):
                continue
            d = math.hypot(a['x'] - x, a['z'] - z)
            if d > 120:
                continue
            w = {0: 1.0, 1: 1.7, 2: 4.0}.get(a['size'], 1.0)
            score = d * w
            if score < best_score:
                best, best_score = a, score
        return best

    # ------------------------------------------------------------------ entities
    def build_entities(self):
        self.npcs = {}
        for name, cfg in self.npc_cfg.items():
            d = cfg['d']
            vis = d.get('vislevel')
            lvl = int(vis) if vis and vis.isdigit() else None
            ops = [d[k] for k in ('op1', 'op2', 'op3', 'op4', 'op5') if d.get(k) and d[k] != 'hidden']
            m = re.match(r'scripts/areas/([^/]+)/', cfg['file'])
            self.npcs[name] = {
                'name': name, 'id': self.npc_name2id.get(name), 'display': d.get('name') or pretty(name),
                'desc': d.get('desc', ''), 'level': lvl, 'hidden': vis == 'hide', 'ops': ops,
                'hp': d.get('hitpoints'), 'att': d.get('attack'), 'str': d.get('strength'), 'def': d.get('defence'),
                'respawn': d.get('respawnrate'), 'category': d.get('category'), 'params': cfg['params'], 'file': cfg['file'],
                'area_folder': m.group(1) if m else None, 'spawns': [], 'areas': Counter(), 'drops': [], 'tables': set(),
                'shop': cfg['params'].get('owned_shop'), 'dialogue': [], 'quests': set(), 'scripts': [], 'handler': None,
                'minimap': d.get('minimap') != 'no', 'wander': d.get('wanderrange'), 'maxrange': d.get('maxrange'),
                'moverestrict': d.get('moverestrict'), 'hunt': d.get('huntmode'),
                'url': 'npc/%s.html' % name,
            }
        self.npc_by_id = {n['id']: n for n in self.npcs.values() if n['id'] is not None}

        self.items = {}
        for name, cfg in self.obj_cfg.items():
            d = cfg['d']
            self.items[name] = {
                'name': name, 'id': self.obj_name2id.get(name), 'display': d.get('name') or pretty(name),
                'desc': d.get('desc', ''), 'cost': d.get('cost'), 'weight': d.get('weight'), 'members': d.get('members') == 'yes',
                'tradeable': d.get('tradeable') != 'no', 'stackable': d.get('stackable') == 'yes', 'category': d.get('category'),
                'wearpos': d.get('wearpos'), 'params': cfg['params'], 'file': cfg['file'], 'dummy': d.get('dummyitem') == 'yes',
                'iops': [d[k] for k in ('iop1', 'iop2', 'iop3', 'iop4', 'iop5') if d.get(k)],
                'dropped_by': [], 'sold_at': [], 'spawns': [], 'areas': Counter(), 'quests': set(), 'scripts': [], 'in_tables': [],
                'cert': name.startswith('cert_'), 'url': 'item/%s.html' % name,
            }
        self.item_by_id = {i['id']: i for i in self.items.values() if i['id'] is not None}

        # shops
        self.shops = {}
        for n in self.npcs.values():
            inv = n['shop']
            if not inv:
                continue
            if inv not in self.shops:
                cfg = self.inv_cfg.get(inv)
                stock = []
                if cfg:
                    for k, v in cfg['kv']:
                        if k.startswith('stock'):
                            bits = v.split(',')
                            stock.append((bits[0].strip(), bits[1].strip() if len(bits) > 1 else '?', bits[2].strip() if len(bits) > 2 else ''))
                self.shops[inv] = {'inv': inv, 'title': n['params'].get('shop_title') or pretty(inv), 'owners': [], 'stock': stock,
                                   'sell': n['params'].get('shop_sell_multiplier'), 'buy': n['params'].get('shop_buy_multiplier'),
                                   'allstock': bool(cfg and cfg['d'].get('allstock') == 'yes'), 'file': cfg['file'] if cfg else None,
                                   'url': 'shop/%s.html' % inv}
            self.shops[inv]['owners'].append(n['name'])
        for s in self.shops.values():
            for (it, cnt, rate) in s['stock']:
                if it in self.items:
                    self.items[it]['sold_at'].append((s['inv'], cnt))

    # ------------------------------------------------------------------ spawns
    def build_spawns(self):
        self.npc_spawns = []
        self.obj_spawns = []
        self.functions = []
        loc_fn = {}
        for lid, lname in self.loc_id2name.items():
            cfg = self.loc_cfg.get(lname)
            if cfg and 'mapfunction' in cfg['d']:
                label = cfg['d'].get('name') or pretty(lname.replace('_icon', '').replace('_store', ' shop'))
                loc_fn[lid] = (int(cfg['d']['mapfunction']), label)
        for sq in self.squares.values():
            bx, bz = sq.mx << 6, sq.mz << 6
            for (level, x, z, nid) in sq.npcs:
                n = self.npc_by_id.get(nid)
                if n is None:
                    continue
                gx, gz = bx + x, bz + z
                area = self.nearest_area(gx, gz)
                n['spawns'].append((level, gx, gz, area['slug'] if area else None))
                if area:
                    n['areas'][area['slug']] += 1
                    area['npcs'][n['name']] += 1
                self.npc_spawns.append([level, gx, gz, nid])
            for (level, x, z, oid, count) in sq.objs:
                it = self.item_by_id.get(oid)
                if it is None:
                    continue
                gx, gz = bx + x, bz + z
                area = self.nearest_area(gx, gz)
                it['spawns'].append((level, gx, gz, count, area['slug'] if area else None))
                if area:
                    it['areas'][area['slug']] += 1
                    area['objs'][it['name']] += 1
                self.obj_spawns.append([level, gx, gz, oid, count])
            for (level, x, z, lid, shape, angle) in sq.locs:
                if lid in loc_fn:
                    fn, label = loc_fn[lid]
                    self.functions.append([level, bx + x, bz + z, fn, label])
        for n in self.npcs.values():
            if n['shop']:
                for slug in n['areas']:
                    self.area_by_slug[slug]['shops'].add(n['shop'])
        self.log('spawns: %d npcs, %d objs, %d map icons' % (len(self.npc_spawns), len(self.obj_spawns), len(self.functions)))

    # ------------------------------------------------------------------ runecrafting: altars, mysterious ruins, essence mine
    def build_runecraft(self):
        def coord(s):
            l, mx, mz, lx, lz = (int(v) for v in s.strip().split(',')[0].split('_')[:5])
            return [l, mx * 64 + lx, mz * 64 + lz]
        self.rc = []          # markers: dict(level,x,z,kind,rune,rune_item,talisman,level_req,members,dests)
        self.rc_by_item = {}  # rune/talisman item -> info (for wiki pages)
        for name, cfg in sorted(self.dbrow_cfg.items()):
            if cfg['d'].get('table') != 'runecraft_table':
                continue
            d = {}
            enters, exits = [], []
            for k, v in cfg['kv']:
                if k != 'data' or ',' not in v:
                    continue
                dk, dv = v.split(',', 1)
                dk = dk.strip()
                if dk == 'enter_coord':
                    enters.append(coord(dv))
                elif dk == 'exit_coord':
                    exits.append(coord(dv))
                else:
                    d[dk] = dv.strip()
            if 'altar_coord' not in d:
                continue
            altar = coord(d['altar_coord'])
            info = {'rune': d.get('name', pretty(name)), 'rune_item': d.get('rune'), 'talisman': d.get('talisman'),
                    'level_req': int(d.get('level', 1) or 1), 'xp': d.get('experience'), 'members': d.get('members') == '1',
                    'altar': altar, 'ruins': exits[0] if exits else None}
            self.rc.append(dict(info, level=altar[0], x=altar[1], z=altar[2], kind='altar', dests=exits[:1]))
            if exits:
                self.rc.append(dict(info, level=exits[0][0], x=exits[0][1], z=exits[0][2], kind='ruins', dests=enters[:1] or [altar]))
            for it in (info['rune_item'], info['talisman']):
                if it:
                    self.rc_by_item.setdefault(it, []).append(info)
        # essence mine: wizards whose dialogue teleports you there, and the portal back out
        mine = None
        ess = self.enum_cfg.get('essence_mine_teleports')
        if ess:
            vals = [v.split(',', 1)[1] for v in ess['multi'].get('val', [])]
            if vals:
                mine = coord(vals[0])
        self.essence_mine = mine
        if mine:
            for n in self.npcs.values():
                if any('teleport_to_essence_mine' in self.blocks[k]['body'] for k in self.blocks
                       if k[0].startswith('opnpc') and k[1] == n['name']) or \
                   any(('teleport_to_essence_mine' in read_text(os.path.join(common.CONTENT, f))) for f in n['scripts'] if f.endswith('.rs2') and '/' + n['name'] + '.rs2' in f):
                    n['tp'] = {'label': 'Rune Essence Mine', 'dest': mine}
            consts = {}
            for path in glob.glob(os.path.join(common.SCRIPTS, '**', '*.constant'), recursive=True):
                for line in read_text(path).splitlines():
                    mm = re.match(r'^\^(essence_mine_to_[a-z_]+)\s*=\s*(\S+)', line.strip())
                    if mm:
                        consts[mm.group(1)] = coord(mm.group(2))
            portal_id = self.loc_name2id.get('essencemine_portal')
            if portal_id is not None and consts:
                for sq in self.squares.values():
                    for (level, x, z, lid, shape, angle) in sq.locs:
                        if lid == portal_id:
                            self.portals.append([level, (sq.mx << 6) + x, (sq.mz << 6) + z, 'Essence mine portal', 'Enter', list(consts.values())])
        self.log('runecraft: %d altar/ruins markers, %d essence-mine wizards' % (len(self.rc), sum(1 for n in self.npcs.values() if n.get('tp'))))

    # ------------------------------------------------------------------ mining sites: which ores are near each pickaxe icon
    def build_mining_sites(self):
        rock_info = {}   # rock loc name -> (ore name, output item, level)
        for name, cfg in self.dbrow_cfg.items():
            if cfg['d'].get('table') != 'mining_table':
                continue
            rocks = [v.split(',', 1)[1].strip() for k, v in cfg['kv'] if k == 'data' and v.startswith('rock,')]
            d = {}
            for k, v in cfg['kv']:
                if k == 'data' and ',' in v:
                    dk, dv = v.split(',', 1)
                    d[dk.strip()] = dv.strip()
            for r in rocks:
                rock_info[r] = (d.get('ore_name', pretty(name)), d.get('rock_output'), int(d.get('rock_level', 1) or 1))
        rock_ids = {lid: rock_info[lname] for lid, lname in self.loc_id2name.items() if lname in rock_info}
        rocks = []   # (level, x, z, info)
        for sq in self.squares.values():
            bx, bz = sq.mx << 6, sq.mz << 6
            for (level, x, z, lid, shape, angle) in sq.locs:
                if lid in rock_ids:
                    rocks.append((level, bx + x, bz + z, rock_ids[lid]))
        self.mining_sites = []
        for fn in self.functions:
            if fn[3] != 8:
                continue
            counts = {}
            for (lv, x, z, info) in rocks:
                if lv == fn[0] and abs(x - fn[1]) <= 16 and abs(z - fn[2]) <= 16:
                    counts[info] = counts.get(info, 0) + 1
            ores = sorted(counts.items(), key=lambda kv: (kv[0][2], kv[0][0]))
            fn.append({'ores': [[ore, item, level, cnt] for (ore, item, level), cnt in ores]})
            self.mining_sites.append(fn)
        self.log('mining sites: %d icons, %d rocks, %d ore types' % (len(self.mining_sites), len(rocks), len(rock_info)))

    # ------------------------------------------------------------------ entrances (ladders, caves, trapdoors...)
    def build_portals(self):
        resolver = PortalResolver(self.blocks)
        self.portal_resolver = resolver
        candidates = {}
        for lid, lname in self.loc_id2name.items():
            cfg = self.loc_cfg.get(lname)
            cat = cfg['d'].get('category') if cfg else None
            if resolver.loc_has_teleport(lname, cat):
                ops = [cfg['d'][k] for k in ('op1', 'op2', 'op3', 'op4', 'op5') if cfg and cfg.get('d', {}).get(k) and cfg['d'][k] != 'hidden']
                label = (cfg['d'].get('name') if cfg else None) or pretty(lname)
                candidates[lid] = (lname, cat, label, ops)
        self.portals = []
        for sq in self.squares.values():
            bx, bz = sq.mx << 6, sq.mz << 6
            for (level, x, z, lid, shape, angle) in sq.locs:
                c = candidates.get(lid)
                if c is None:
                    continue
                lname, cat, label, ops = c
                gx, gz = bx + x, bz + z
                dests = []
                for (dl, dx, dz) in resolver.resolve(lname, (level, gx, gz), angle, cat):
                    if not (0 <= dl <= 3) or (dx >> 6, dz >> 6) not in self.squares:
                        continue
                    if dl == level and abs(dz - gz) < 1000 and abs(dx - gx) + abs(dz - gz) < 8:
                        continue   # tiny same-floor hops (squeezing through bushes etc.) are not entrances
                    dests.append([dl, dx, dz])
                if dests:
                    self.portals.append([level, gx, gz, label, ops[0] if ops else '', dests, lname])
        # dungeon "!" icons: link to the closest entrance, else assume the standard z+6400 underground offset
        for fn in self.functions:
            if fn[3] != 12:
                continue
            best, bd = None, 12
            for p in self.portals:
                if p[0] != fn[0]:
                    continue
                d = abs(p[1] - fn[1]) + abs(p[2] - fn[2])
                if d < bd:
                    best, bd = p, d
            if best:
                fn.append({'dests': best[5]})
            elif ((fn[1] >> 6), ((fn[2] + 6400) >> 6)) in self.squares:
                fn.append({'dests': [[0, fn[1], fn[2] + 6400]]})
        self.log('entrances: %d placed, %d loc types' % (len(self.portals), len(candidates)))

    # ------------------------------------------------------------------ drops
    def build_drops(self):
        ev = DropEvaluator(self.blocks)
        self.tables = {}
        pending = set()

        def row_from_entry(e, npc=None):
            if e.item == 'DEATH_DROP':
                dd = npc['params'].get('death_drop') if npc else None
                if not dd or dd == 'null':
                    return None
                return {'item': dd, 'label': self.item_label(dd), 'table': None, 'count': e.count, 'prob': e.prob, 'notes': list(e.notes), 'kind': e.kind}
            if e.item and e.item in CLUE_LABELS:
                return {'item': None, 'label': CLUE_LABELS[e.item], 'table': None, 'count': e.count, 'prob': e.prob, 'notes': list(e.notes), 'kind': 'tertiary'}
            if e.table:
                pending.add(e.table)
                return {'item': None, 'label': None, 'table': e.table, 'count': e.count, 'prob': e.prob, 'notes': list(e.notes), 'kind': e.kind}
            if e.item and e.item not in self.items:
                return None
            return {'item': e.item, 'label': self.item_label(e.item), 'table': None, 'count': e.count, 'prob': e.prob, 'notes': list(e.notes), 'kind': e.kind}

        for n in self.npcs.values():
            key = None
            if ('ai_queue3', n['name']) in self.blocks:
                key = ('ai_queue3', n['name'])
            elif n['category'] and ('ai_queue3', '_' + n['category']) in self.blocks:
                key = ('ai_queue3', '_' + n['category'])
            n['handler'] = key
            rows = []
            if key:
                entries, tables, visited = ev.evaluate_handler(key)
                n['handler_file'] = self.blocks[key]['file']
                for e in entries:
                    r = row_from_entry(e, n)
                    if r:
                        rows.append(r)
            else:
                dd = n['params'].get('death_drop')
                if dd and dd != 'null' and (n['hp'] or 'Attack' in n['ops']):
                    rows.append({'item': dd, 'label': self.item_label(dd), 'table': None, 'count': '1', 'prob': Fraction(1), 'notes': [], 'kind': 'always'})
            n['drops'] = rows

        # shared tables (closure)
        done = set()
        while pending - done:
            t = (pending - done).pop()
            done.add(t)
            res = ev.evaluate_table(t)
            rows = []
            for e in res['entries']:
                r = row_from_entry(e)
                if r:
                    rows.append(r)
            self.tables[t] = {'name': t, 'display': TABLE_NAMES.get(t, pretty(t)), 'entries': rows, 'file': res['file'],
                              'used_by': [], 'parents': [], 'url': 'table/%s.html' % t, 'kind': 'script'}
        # db-row drop tables
        for name, cfg in self.dbrow_cfg.items():
            if cfg['d'].get('table') != 'drop_table':
                continue
            total = None
            rows = []
            for k, v in cfg['kv']:
                if k != 'data':
                    continue
                bits = [b.strip() for b in v.split(',')]
                if bits[0] == 'total':
                    total = int(bits[1])
                elif bits[0] == 'drop' and len(bits) >= 4 and total:
                    rows.append({'item': bits[1], 'label': self.item_label(bits[1]), 'table': None, 'count': bits[2],
                                 'prob': Fraction(int(bits[3]), total), 'notes': [], 'kind': 'main'})
            self.tables[name] = {'name': name, 'display': TABLE_NAMES.get(name, pretty(name)), 'entries': rows, 'file': cfg['file'],
                                 'used_by': [], 'parents': [], 'url': 'table/%s.html' % name, 'kind': 'db'}

        # reverse links
        for t in self.tables.values():
            for r in t['entries']:
                if r['table'] and r['table'] in self.tables:
                    self.tables[r['table']]['parents'].append(t['name'])
                elif r['item'] and r['item'] in self.items:
                    self.items[r['item']]['in_tables'].append((t['name'], r))
        for n in self.npcs.values():
            for r in n['drops']:
                if r['table'] and r['table'] in self.tables:
                    self.tables[r['table']]['used_by'].append((n['name'], r))
            for fr in self.flatten(n['drops']):
                if fr['item'] and fr['item'] in self.items:
                    self.items[fr['item']]['dropped_by'].append((n['name'], fr))
        self.log('drops: %d npcs with handlers, %d tables' % (sum(1 for n in self.npcs.values() if n['handler']), len(self.tables)))

    def flatten(self, rows, via=(), depth=0):
        out = []
        for r in rows:
            if r['table']:
                t = self.tables.get(r['table'])
                if not t or depth > 4 or r['table'] in via:
                    continue
                for sub in t['entries']:
                    p = None if (r['prob'] is None or sub['prob'] is None) else r['prob'] * sub['prob']
                    s2 = dict(sub)
                    s2['prob'] = p
                    s2['notes'] = list(r['notes']) + list(sub['notes'])
                    s2['kind'] = 'main' if sub['kind'] != 'tertiary' else 'tertiary'
                    out.extend(self.flatten([s2], via + (r['table'],), depth + 1))
            else:
                r2 = dict(r)
                r2['via'] = via
                out.append(r2)
        return out

    def item_label(self, name):
        it = self.items.get(name)
        return it['display'] if it else pretty(name)

    # ------------------------------------------------------------------ scripts / quests / dialogue
    def build_script_index(self):
        ident_re = re.compile(r'[a-z_][a-z0-9_]*')
        names = set(self.npcs) | set(self.items)
        refs = defaultdict(list)
        quest_text = {}
        for path in sorted(glob.glob(os.path.join(common.SCRIPTS, '**', '*.rs2'), recursive=True)):
            rel = relpath(path)
            text = read_text(path)
            for ident in set(ident_re.findall(text)) & names:
                refs[ident].append(rel)
            m = re.match(r'scripts/quests/(quest_[a-z0-9_]+)/', rel)
            if m:
                quest_text.setdefault(m.group(1), []).append(text)
        self.quests = {}
        for folder, texts in sorted(quest_text.items()):
            name = None
            for t in texts:
                m = re.search(r'Quest complete: ([^"]+)', t)
                if m:
                    name = m.group(1).strip()
                    break
            self.quests[folder] = {'folder': folder, 'name': name or pretty(folder[6:]) + ' (quest)', 'npcs': set(), 'items': set(),
                                   'url': 'quest/%s.html' % folder}
        for ident, files in refs.items():
            rec = self.npcs.get(ident) or self.items.get(ident)
            rec['scripts'] = sorted(set(files))
            for f in files:
                m = re.match(r'scripts/quests/(quest_[a-z0-9_]+)/', f)
                if m and m.group(1) in self.quests:
                    q = self.quests[m.group(1)]
                    rec['quests'].add(m.group(1))
                    (q['npcs'] if ident in self.npcs else q['items']).add(ident)
        self.log('script index: %d referenced names, %d quests' % (len(refs), len(self.quests)))

    def build_dialogue(self):
        chat_re = re.compile(r'~(chatnpc|chatplayer|objbox|mesbox|chatnpc_specific)\s*\(\s*"((?:[^"\\]|\\.)*)"|@([a-z0-9_]+)|~([a-z0-9_]+)|(?<![@~$a-z0-9_])([a-z][a-z0-9_]+)(?=\s*[,)])')
        for n in self.npcs.values():
            keys = [(t, n['name']) for t in ('opnpc1', 'opnpc3', 'opnpc2', 'opnpc4', 'opnpc5', 'opnpcu', 'opnpct')]
            if n['category']:
                keys += [(t, '_' + n['category']) for t in ('opnpc1', 'opnpc3')]
            lines = []
            visited = set()

            def walk(key, depth):
                if key in visited or key not in self.blocks or len(lines) >= 80 or depth > 5:
                    return
                visited.add(key)
                blk = self.blocks[key]
                for m in chat_re.finditer(blk['body']):
                    if m.group(1):
                        text = re.sub(r'<[^>]*>', '', m.group(2)).replace('|', ' ').replace('\\"', '"').strip()
                        who = {'chatnpc': 'npc', 'chatnpc_specific': 'npc', 'chatplayer': 'player'}.get(m.group(1), 'text')
                        if text:
                            lines.append((who, text))
                    elif m.group(3):
                        walk(('label', m.group(3)), depth + 1)
                    elif m.group(4):
                        pk = ('proc', m.group(4))
                        if pk in self.blocks and self.blocks[pk]['file'] == blk['file'] and '~chat' in self.blocks[pk]['body']:
                            walk(pk, depth + 1)
                    elif m.group(5):
                        # bare identifier used as an argument (e.g. @multi3("...", label_name, ...))
                        lk = ('label', m.group(5))
                        if lk in self.blocks and self.blocks[lk]['file'] == blk['file']:
                            walk(lk, depth + 1)
                    if len(lines) >= 80:
                        break

            for k in keys:
                walk(k, 0)
            # dedupe consecutive duplicates
            out = []
            for l in lines:
                if not out or out[-1] != l:
                    out.append(l)
            n['dialogue'] = out

    # ------------------------------------------------------------------ tiles
    def render_tiles(self, skip=False, force=False):
        tiles = {0: [], 1: [], 2: [], 3: []}
        icons_dir = os.path.join(SITE, 'icons')
        os.makedirs(icons_dir, exist_ok=True)
        for i, fr in enumerate(load_sprites('mapfunction', 15, 15)):
            fr.save(os.path.join(icons_dir, 'mapfunction_%d.png' % i))
        if skip:
            for lv in range(4):
                d = os.path.join(SITE, 'tiles', str(lv))
                if os.path.isdir(d):
                    tiles[lv] = [f[:-4] for f in os.listdir(d) if f.endswith('.png')]
            self.tiles = tiles
            return
        flo_rgb = flo_colours(self.flo_cfg, self.flo_id2name)
        loc_info = {}
        for lid, lname in self.loc_id2name.items():
            cfg = self.loc_cfg.get(lname)
            d = cfg['d'] if cfg else {}
            loc_info[lid] = {
                'mapscene': int(d['mapscene']) if d.get('mapscene', '').lstrip('-').isdigit() else -1,
                'active': any(k.startswith('op') and d[k] != 'hidden' for k in d),
                'width': int(d.get('width', 1) or 1), 'length': int(d.get('length', 1) or 1),
            }
        renderer = Renderer(self.squares, flo_rgb, loc_info, load_sprites('mapscene', 8, 8))
        for lv in range(4):
            os.makedirs(os.path.join(SITE, 'tiles', str(lv)), exist_ok=True)
        n = 0
        for (mx, mz), sq in sorted(self.squares.items()):
            for lv in range(4):
                key = '%d_%d' % (mx, mz)
                path = os.path.join(SITE, 'tiles', str(lv), key + '.png')
                if os.path.exists(path) and not force:
                    tiles[lv].append(key)
                    continue
                img = renderer.render(sq, lv)
                if img is None:
                    continue
                img.save(path, optimize=True)
                tiles[lv].append(key)
            n += 1
            if n % 50 == 0:
                self.log('rendered %d/%d squares' % (n, len(self.squares)))
        self.tiles = tiles
        self.log('tiles: ' + ', '.join('level %d: %d' % (lv, len(t)) for lv, t in tiles.items()))

    # ------------------------------------------------------------------ data files
    def write_data(self):
        os.makedirs(os.path.join(SITE, 'data'), exist_ok=True)
        # map data
        npcs = {}
        for n in self.npc_by_id.values():
            main = [r for r in n['drops'] if r['kind'] != 'always']
            main.sort(key=lambda r: (r['prob'] is None, -(r['prob'] or 0)))
            dr = []
            for r in main[:6]:
                label = r['label'] or (self.tables[r['table']]['display'] if r['table'] in self.tables else pretty(r['table']))
                pt = prob_text(r['prob'])
                dr.append([label, r['count'], pt[0], pt[1]])
            always = [(r['label'] or (self.tables[r['table']]['display'] if r['table'] in self.tables else pretty(r['table'] or '?')))
                      for r in n['drops'] if r['kind'] == 'always']
            st = [n['hp'], n['att'], n['str'], n['def']] if n['hp'] else None
            npcs[n['id']] = {'n': n['display'], 'l': n['level'], 'd': n['desc'], 'u': n['url'], 'st': st, 'dr': dr, 'dn': len(main),
                             'dd': ', '.join(always) if always else None, 'sh': self.shops[n['shop']]['title'] if n['shop'] in self.shops else None,
                             'c': n['category'], 'ic': 1 if self.has_npc_icon(n) else 0,
                             'fs': self.fishing_for_npc(n) if self.gen else None,
                             'wr': int(n['wander']) if n['wander'] and n['wander'].isdigit() else 5,
                             'fight': bool(n['hp']) or 'Attack' in n['ops'],
                             'ch': (bool(n['hp']) or 'Attack' in n['ops']) and n['moverestrict'] != 'nomove',
                             'mr': int(n['maxrange']) if n['maxrange'] and n['maxrange'].isdigit() else 7,
                             'mv': n['moverestrict'], 'tp': n.get('tp')}
        ore_items = {row[1] for fn in self.mining_sites for row in fn[5]['ores'] if row[1]}
        objs = {i['id']: {'n': i['display'], 'd': i['desc'], 'u': i['url'], 'ic': 1 if self.has_icon(i) else 0} for i in self.item_by_id.values() if i['spawns'] or i['name'] in ore_items}
        item_ids = {i['name']: i['id'] for i in self.items.values() if i['id'] is not None}
        for fn in self.mining_sites:
            for row in fn[5]['ores']:
                row.append(item_ids.get(row[1]))
        mxs = [k[0] for k in self.squares]
        mzs = [k[1] for k in self.squares]
        data = {
            'bounds': {'mx0': min(mxs), 'mx1': max(mxs), 'mz0': min(mzs), 'mz1': max(mzs)},
            'tiles': {str(lv): sorted(t) for lv, t in self.tiles.items()},
            'labels': [[a['name'], a['x'], a['z'], a['size'], a['url']] for a in self.areas],
            'npcs': npcs, 'npc_spawns': self.npc_spawns, 'objs': objs, 'obj_spawns': self.obj_spawns, 'functions': self.functions,
            'portals': self.portals,
            'stands': self.map_stands(),
            'rc': [[m['level'], m['x'], m['z'], m['kind'], m['rune'], item_ids.get(m['rune_item']), item_ids.get(m['talisman']),
                    m['level_req'], m['xp'], 1 if m['members'] else 0, m['dests']] for m in self.rc],
            'essence_mine': self.essence_mine,
            'revision': self.rev_text,
        }
        for m in self.rc:
            for it in (m['rune_item'], m['talisman']):
                i = self.items.get(it)
                if i and i['id'] is not None and i['id'] not in objs:
                    objs[i['id']] = {'n': i['display'], 'd': i['desc'], 'u': i['url'], 'ic': 1 if self.has_icon(i) else 0}
        with open(os.path.join(SITE, 'data', 'map_data.js'), 'w', encoding='utf-8') as f:
            f.write('window.MAPDATA = ' + json.dumps(data, separators=(',', ':')) + ';\n')

        # search index
        search = []
        for n in self.npcs.values():
            search.append({'t': n['display'], 'u': n['url'], 'k': 'npc', 's': ('Level %s. ' % n['level'] if n['level'] else '') + n['desc']})
        for i in self.items.values():
            if not i['dummy']:
                search.append({'t': i['display'] + (' (noted)' if i['cert'] else ''), 'u': i['url'], 'k': 'item', 's': i['desc']})
        for a in self.areas:
            search.append({'t': a['name'], 'u': a['url'], 'k': 'area', 's': 'Area at %d, %d' % (a['x'], a['z'])})
        for q in self.quests.values():
            search.append({'t': q['name'], 'u': q['url'], 'k': 'quest', 's': 'Quest'})
        for s in self.shops.values():
            search.append({'t': s['title'], 'u': s['url'], 'k': 'shop', 's': 'Shop run by ' + ', '.join(self.npcs[o]['display'] for o in s['owners'])})
        for t in self.tables.values():
            search.append({'t': t['display'], 'u': t['url'], 'k': 'table', 's': 'Drop table'})
        with open(os.path.join(SITE, 'data', 'search.js'), 'w', encoding='utf-8') as f:
            f.write('window.SEARCH = ' + json.dumps(search, separators=(',', ':')) + ';\n')

        # knowledge graph
        nodes, edges = [], []

        # region membership (used by the graph's region filter)
        def regions_of_areas(slugs):
            out = set()
            for s in slugs:
                if s in self.area_by_slug:
                    out |= self.area_by_slug[s]['regions']
            return out
        npc_regions = {n['name']: regions_of_areas(n['areas']) for n in self.npcs.values()}
        item_regions = {}
        for i in self.items.values():
            r = regions_of_areas(i['areas'])
            for npc_name, _row in i['dropped_by']:
                r |= npc_regions.get(npc_name, set())
            for inv, _cnt in i['sold_at']:
                for o in self.shops[inv]['owners']:
                    r |= npc_regions.get(o, set())
            item_regions[i['name']] = r
        shop_regions = {s['inv']: set().union(*(npc_regions.get(o, set()) for o in s['owners'])) for s in self.shops.values()}
        quest_regions = {q['folder']: set().union(*(npc_regions.get(n, set()) for n in q['npcs'])) if q['npcs'] else set() for q in self.quests.values()}
        table_regions = {t['name']: set().union(*(npc_regions.get(n, set()) for n, _r in t['used_by'])) if t['used_by'] else set() for t in self.tables.values()}
        all_regions = sorted(set().union(*npc_regions.values()) | set().union(*(a['regions'] for a in self.areas)))

        def node(id_, type_, label, url=None, sub=None, regions=None, m=None):
            nodes.append({'id': id_, 'type': type_, 'label': label, 'url': url, 'sub': sub, 'r': sorted(regions) if regions else [], 'm': m})

        def map_hash(level, x, z, extra=''):
            return 'x=%d&z=%d&level=%d&zoom=6%s' % (x, z, level, extra)

        def edge(s, t, type_, l=None, r=None):
            edges.append({'s': s, 't': t, 'type': type_, 'l': l, 'r': r})

        cats = set()
        for n in self.npcs.values():
            node('npc:' + n['name'], 'npc', n['display'] + (' (lvl %d)' % n['level'] if n['level'] else ''), n['url'], n['desc'], npc_regions[n['name']],
                 ('npc=%d' % n['id']) if n['id'] is not None and n['spawns'] else None)
            if n['category']:
                cats.add(n['category'])
        for i in self.items.values():
            node('item:' + i['name'], 'item', i['display'] + (' (noted)' if i['cert'] else ''), i['url'], i['desc'], item_regions[i['name']],
                 ('obj=%d' % i['id']) if i['id'] is not None and i['spawns'] else None)
        for a in self.areas:
            node('area:' + a['slug'], 'area', a['name'], a['url'], 'Area at %d, %d (%s)' % (a['x'], a['z'], a['region']), a['regions'], map_hash(0, a['x'], a['z']))
        for q in self.quests.values():
            node('quest:' + q['folder'], 'quest', q['name'], q['url'], 'Quest', quest_regions[q['folder']])
        for s in self.shops.values():
            owner_spawns = [sp for o in s['owners'] for sp in self.npcs[o]['spawns']]
            node('shop:' + s['inv'], 'shop', s['title'], s['url'], 'Shop', shop_regions[s['inv']],
                 map_hash(owner_spawns[0][0], owner_spawns[0][1], owner_spawns[0][2], '&npc=%d' % self.npcs[s['owners'][0]]['id']) if owner_spawns and self.npcs[s['owners'][0]]['id'] is not None else None)
        for t in self.tables.values():
            node('table:' + t['name'], 'table', t['display'], t['url'], 'Drop table', table_regions[t['name']])
        for c in sorted(cats):
            node('category:' + c, 'category', pretty(c), None, 'NPC category')
        for n in self.npcs.values():
            nid = 'npc:' + n['name']
            for r in n['drops']:
                pt = prob_text(r['prob'])
                lab = pt[0] if r['prob'] is not None and r['prob'] < 1 else ('always' if r['prob'] == 1 else 'drops')
                if r['table'] and r['table'] in self.tables:
                    edge(nid, 'table:' + r['table'], 'rolls', lab, 'rolled by')
                elif r['item'] and r['item'] in self.items:
                    edge(nid, 'item:' + r['item'], 'drops', lab, 'dropped by')
            for slug, cnt in n['areas'].items():
                edge(nid, 'area:' + slug, 'spawns_in', 'spawns in (x%d)' % cnt, 'home of')
            if n['shop'] in self.shops:
                edge(nid, 'shop:' + n['shop'], 'runs', 'runs shop', 'run by')
            if n['category']:
                edge(nid, 'category:' + n['category'], 'in_category', 'category', 'members')
            for q in n['quests']:
                edge('quest:' + q, nid, 'involves', 'involves NPC', 'in quest')
            if self.gen:
                for r in self.gen['recipes']:
                    if r['skill'] == 'fishing' and n['name'] in r.get('station_npcs', []) and r['product'] in self.items:
                        edge(nid, 'item:' + r['product'], 'catches', 'catches (level %d)' % r['level'], 'caught at')
        for i in self.items.values():
            iid = 'item:' + i['name']
            for slug, cnt in i['areas'].items():
                edge(iid, 'area:' + slug, 'found_in', 'ground spawn in (x%d)' % cnt, 'ground items')
            for q in i['quests']:
                edge('quest:' + q, iid, 'involves', 'involves item', 'in quest')
        for s in self.shops.values():
            for (it, cnt, rate) in s['stock']:
                if it in self.items:
                    edge('shop:' + s['inv'], 'item:' + it, 'sells', 'sells (stock %s)' % cnt, 'sold at')
        for t in self.tables.values():
            for r in t['entries']:
                pt = prob_text(r['prob'])
                if r['table'] and r['table'] in self.tables:
                    edge('table:' + t['name'], 'table:' + r['table'], 'rolls', pt[0], 'rolled by')
                elif r['item'] and r['item'] in self.items:
                    edge('table:' + t['name'], 'item:' + r['item'], 'contains', pt[0], 'in table')
        graph = {'meta': {'source': 'LostCityRS Content, ' + self.rev_text, 'generated': time.strftime('%Y-%m-%d'),
                          'node_types': ['npc', 'item', 'area', 'quest', 'shop', 'table', 'category'], 'regions': all_regions,
                          'edge_types': ['drops', 'rolls', 'contains', 'spawns_in', 'found_in', 'runs', 'sells', 'involves', 'in_category']},
                 'nodes': nodes, 'edges': edges}
        with open(os.path.join(SITE, 'data', 'graph.json'), 'w', encoding='utf-8') as f:
            json.dump(graph, f, indent=1)
        with open(os.path.join(SITE, 'data', 'graph.js'), 'w', encoding='utf-8') as f:
            f.write('window.GRAPH = ' + json.dumps(graph, separators=(',', ':')) + ';\n')
        self.log('graph: %d nodes, %d edges' % (len(nodes), len(edges)))

    # ------------------------------------------------------------------ html helpers
    def page(self, title, body, depth, extra_head=''):
        base = '../' * depth
        nav = ''.join('<a href="%s%s">%s</a>' % (base, u, t) for u, t in [
            ('map.html', 'World map'), ('graph.html', 'Knowledge graph'), ('chunks.html', 'Chunk picker'), ('npcs.html', 'NPCs'), ('items.html', 'Items'),
            ('areas.html', 'Areas'), ('quests.html', 'Quests'), ('shops.html', 'Shops'), ('tables.html', 'Drop tables')])
        return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>%s - Lost City Wiki</title>'
                '<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="%sassets/style.css?v=%s">%s</head>'
                '<body data-base="%s"><header class="top"><a class="brand" href="%sindex.html">Lost City Wiki</a><nav>%s</nav>'
                '<div class="search"><input id="q" placeholder="Search the wiki..." autocomplete="off"><div class="results" id="q-results"></div></div></header>'
                '<main>%s</main><footer>Generated from the LostCityRS <code>Content</code> repository, %s. Game assets are the property of Jagex Ltd.</footer>'
                '<script src="%sdata/search.js?v=%s"></script><script src="%sassets/wiki.js?v=%s"></script></body></html>'
                % (esc(title), base, self.stamp, extra_head, base, base, nav, body, esc(self.rev_text), base, self.stamp, base, self.stamp))

    def write(self, rel, content):
        path = os.path.join(SITE, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def npc_link(self, name, base):
        n = self.npcs.get(name)
        if not n:
            return esc(pretty(name))
        return '%s<a href="%s%s">%s</a>' % (self.npc_icon_img(n, base), base, n['url'], esc(n['display']))

    def has_icon(self, item):
        return item['id'] is not None and os.path.exists(os.path.join(SITE, 'icons', 'items', '%d.png' % item['id']))

    def has_npc_icon(self, npc):
        return npc['id'] is not None and os.path.exists(os.path.join(SITE, 'icons', 'npcs', '%d.png' % npc['id']))

    def npc_icon_img(self, npc, base, cls='icon'):
        if not self.has_npc_icon(npc):
            return ''
        return '<img class="%s" src="%sicons/npcs/%d.png" alt="">' % (cls, base, npc['id'])

    def icon_img(self, item, base, cls='icon'):
        if not self.has_icon(item):
            return ''
        return '<img class="%s" src="%sicons/items/%d.png" alt="">' % (cls, base, item['id'])

    def item_link(self, name, base):
        i = self.items.get(name)
        if not i:
            return esc(pretty(name))
        return '%s<a href="%s%s">%s</a>%s' % (self.icon_img(i, base), base, i['url'], esc(i['display']), ' <span class="tag">noted</span>' if i['cert'] else '')

    def table_link(self, name, base):
        t = self.tables.get(name)
        if not t:
            return esc(pretty(name))
        return '<a href="%s%s">%s</a> <span class="tag">table</span>' % (base, t['url'], esc(t['display']))

    def area_link(self, slug, base):
        a = self.area_by_slug.get(slug)
        if not a:
            return '<span class="small">unlabelled</span>'
        return '<a href="%s%s">%s</a>' % (base, a['url'], esc(a['name']))

    def map_link(self, base, x, z, level, text='map', extra=''):
        return '<a href="%smap.html#x=%d&z=%d&level=%d&zoom=6%s" title="Open in the map explorer">%s</a>' % (base, x, z, level, extra, text)

    def notes_html(self, notes):
        out = []
        for nt in notes:
            if nt == 'members':
                out.append('<span class="tag members">members</span>')
            elif nt == 'placed in inventory':
                out.append('<span class="tag">given directly</span>')
            else:
                out.append('<span class="tag">%s</span>' % esc(nt))
        return ' '.join(out)

    def drops_html(self, rows, base):
        if not rows:
            return '<p class="small">No drops recorded.</p>'
        groups = [('always', 'Always dropped'), ('main', 'Drop table'), ('tertiary', 'Tertiary drops')]
        out = []
        for kind, title in groups:
            rs = [r for r in rows if r['kind'] == kind]
            if not rs:
                continue
            if kind == 'main':
                rs.sort(key=lambda r: (r['prob'] is None, -(r['prob'] or 0)))
            out.append('<h3>%s</h3><table class="data"><thead><tr><th>Item</th><th>Quantity</th><th>Rarity</th><th>Notes</th></tr></thead><tbody>' % title)
            for r in rs:
                if r['table']:
                    cell = self.table_link(r['table'], base)
                elif r['item']:
                    cell = self.item_link(r['item'], base)
                else:
                    cell = esc(r['label'])
                pt = prob_text(r['prob'])
                rarity = pt[0] + (' <span class="small">(%s)</span>' % pt[1] if pt[1] and pt[0] != 'Always' else '')
                out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (cell, esc(r['count']) if r['count'] else '<span class="small">see table</span>', rarity, self.notes_html(r['notes'])))
            out.append('</tbody></table>')
        return ''.join(out)

    # ------------------------------------------------------------------ pages
    def write_pages(self):
        for n in self.npcs.values():
            self.write(n['url'], self.npc_page(n))
        for i in self.items.values():
            self.write(i['url'], self.item_page(i))
        for a in self.areas:
            self.write(a['url'], self.area_page(a))
        for q in self.quests.values():
            self.write(q['url'], self.quest_page(q))
        for s in self.shops.values():
            self.write(s['url'], self.shop_page(s))
        for t in self.tables.values():
            self.write(t['url'], self.table_page(t))
        self.write_indexes()
        self.log('pages written')

    def npc_page(self, n):
        base = '../'
        info = ['<h3>%s</h3>%s<table>' % (esc(n['display']), ('<div class="iconbox">%s</div>' % self.npc_icon_img(n, base, 'icon big')) if self.has_npc_icon(n) else '')]
        rows = [('Config name', '<code>%s</code>' % esc(n['name'])), ('NPC id', n['id'] if n['id'] is not None else '?'),
                ('Combat level', n['level'] if n['level'] else ('hidden' if n['hidden'] else 'n/a')),
                ('Hitpoints', n['hp']), ('Attack / Strength / Defence', '%s / %s / %s' % (n['att'], n['str'], n['def']) if n['hp'] else None),
                ('Respawn', ('%s ticks' % n['respawn']) if n['respawn'] else None), ('Options', ', '.join(n['ops']) or None),
                ('Category', esc(n['category']) if n['category'] else None), ('Aggression', n['hunt']),
                ('Wander range', ('%s tiles from spawn' % (n['wander'] or 5)) if n['moverestrict'] != 'nomove' else 'does not move'),
                ('Max range', ('%s tiles from spawn' % (n['maxrange'] or 7)) if (n['hp'] or 'Attack' in n['ops']) and n['moverestrict'] != 'nomove' else None), ('Movement', n['moverestrict']),
                ('Area folder', esc(n['area_folder']) if n['area_folder'] else None),
                ('Defined in', '<code>%s</code>' % esc(n['file']))]
        for k, v in rows:
            if v is not None and v != '' and v != 'None / None / None':
                info.append('<tr><td>%s</td><td>%s</td></tr>' % (k, v))
        bon = [(lab, n['params'][k]) for k, lab in NPC_BONUS_KEYS if k in n['params']]
        if bon:
            info.append('<tr><td>Bonuses</td><td>%s</td></tr>' % ', '.join('%s %s' % (l, esc(v)) for l, v in bon))
        info.append('</table></div>')
        body = ['<div class="infobox">' + ''.join(info)]
        body.append('<h1><span class="dot npc"></span>%s</h1><div class="sub">%s</div>' % (esc(n['display']), esc(n['desc'])))
        if n['id'] is not None and n['spawns']:
            body.append('<p><a href="%smap.html#npc=%d">Show all %d spawn%s on the world map &rarr;</a> &nbsp; <a href="%sgraph.html#npc:%s">Open in knowledge graph &rarr;</a></p>'
                        % (base, n['id'], len(n['spawns']), 's' if len(n['spawns']) != 1 else '', base, esc(n['name'])))
        else:
            body.append('<p><a href="%sgraph.html#npc:%s">Open in knowledge graph &rarr;</a></p>' % (base, esc(n['name'])))
        # drops
        if n['drops'] or n['hp']:
            body.append('<h2>Drops</h2>')
            if n['handler']:
                body.append('<p class="small">Drop script: <code>%s</code> (%s)</p>' % (esc(n['handler_file']), 'category handler <code>%s</code>' % esc(n['handler'][1]) if n['handler'][1].startswith('_') else 'specific handler'))
            body.append(self.drops_html(n['drops'], base))
        # locations
        body.append('<h2>Locations</h2>')
        if n['spawns']:
            grouped = Counter((lv, x, z, slug) for (lv, x, z, slug) in n['spawns'])
            body.append('<table class="data"><thead><tr><th>Area</th><th>Coordinates</th><th>Level</th><th>Count</th><th>Map</th></tr></thead><tbody>')
            for (lv, x, z, slug), cnt in sorted(grouped.items(), key=lambda kv: (self.area_by_slug[kv[0][3]]['name'] if kv[0][3] else 'zzz', kv[0][1], kv[0][2]))[:200]:
                body.append('<tr><td>%s</td><td>%d, %d</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (self.area_link(slug, base), x, z, lv, cnt, self.map_link(base, x, z, lv, 'view', '&npc=%d' % n['id'])))
            body.append('</tbody></table>')
            if len(grouped) > 200:
                body.append('<p class="small">Showing 200 of %d spawn points.</p>' % len(grouped))
        else:
            body.append('<p class="small">This NPC is not placed on the map by the map files (it may be spawned by scripts, e.g. during quests, or be a variant).</p>')
        # fishing spot: what it gives (from the recipe table, i.e. the fishing scripts)
        fish = self.fishing_for_npc(n)
        if fish:
            body.append('<h2>Fishing</h2><table class="data"><thead><tr><th>Fish</th><th>Level</th><th>Tool</th><th>Bait</th><th>XP</th></tr></thead><tbody>')
            for r in self.gen['recipes']:
                if r['skill'] == 'fishing' and n['name'] in r.get('station_npcs', []):
                    body.append('<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                        self.item_link(r['product'], base), r['level'], self.item_link(r['tool'], base) if r.get('tool') else '',
                        self.item_link(r['inputs'][0]['item'], base) if r['inputs'] else '', r['xp']))
            body.append('</tbody></table>')
        # shop
        if n['shop'] in self.shops:
            s = self.shops[n['shop']]
            body.append('<h2>Shop</h2><p>Runs <a href="%s%s">%s</a>%s.</p>' % (base, s['url'], esc(s['title']), ' (buys any item)' if s['allstock'] else ''))
            body.append(self.stock_html(s, base))
        # dialogue
        if n['dialogue']:
            body.append('<h2>Dialogue</h2><div class="dialogue">')
            for who, text in n['dialogue']:
                label = n['display'] if who == 'npc' else ('Player' if who == 'player' else '')
                body.append('<div class="line"><span class="who %s">%s</span>%s</div>' % (who, esc(label), esc(text)))
            body.append('</div>')
        # quests
        if n['quests']:
            body.append('<h2>Quests</h2><p>' + ' '.join('<a class="chip" href="%s%s">%s</a>' % (base, self.quests[q]['url'], esc(self.quests[q]['name'])) for q in sorted(n['quests'])) + '</p>')
        # related
        if n['category']:
            same = [m for m in self.npcs.values() if m['category'] == n['category'] and m['name'] != n['name']]
            if same:
                body.append('<h2>Related NPCs (category %s)</h2><div class="cols">' % esc(n['category']) + ''.join(self.npc_link(m['name'], base) for m in same[:60]) + '</div>')
        for slug in n['areas']:
            pass
        if n['scripts']:
            body.append('<h2>Referenced in scripts</h2><div class="cols">' + ''.join('<code>%s</code>' % esc(f) for f in n['scripts'][:40]) + '</div>')
            if len(n['scripts']) > 40:
                body.append('<p class="small">+%d more files</p>' % (len(n['scripts']) - 40))
        body.append('<div class="clear"></div>')
        return self.page(n['display'], ''.join(body), 1)

    def stock_html(self, s, base):
        out = ['<table class="data"><thead><tr><th>Item</th><th>Stock</th><th>Value</th><th>Sells for</th></tr></thead><tbody>']
        sell = int(s['sell']) if s['sell'] and s['sell'].isdigit() else 1000
        for (it, cnt, rate) in s['stock']:
            item = self.items.get(it)
            cost = int(item['cost']) if item and item['cost'] and item['cost'].isdigit() else None
            price = ('%d gp' % (cost * sell // 1000)) if cost is not None else ''
            out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (self.item_link(it, base), esc(cnt), ('%d gp' % cost) if cost is not None else '', price))
        out.append('</tbody></table>')
        if s['sell'] or s['buy']:
            out.append('<p class="small">Shop sells at %s%% of item value and buys at %s%%.</p>' % (int(s['sell'] or 1000) / 10, int(s['buy'] or 400) / 10))
        return ''.join(out)

    def recipe_index(self):
        """(what makes each item, what each item goes into), built once from the recipe table."""
        if getattr(self, '_recipe_index', None) is None:
            makes, uses = defaultdict(list), defaultdict(list)
            for r in (self.gen or {}).get('recipes', []):
                if r.get('product'):
                    makes[r['product']].append(r)
                for inp in r['inputs']:
                    uses[inp['item']].append(r)
            self._recipe_index = (makes, uses)
        return self._recipe_index

    DRIVER_TEXT = {'op_loc': 'click it', 'op_npc': 'click the spot', 'op_item': 'click in pack',
                   'use_item_on': 'use on item', 'use_item_on_loc': 'use on object',
                   'chat_picker': 'chat menu', 'interface': 'interface'}

    def driver_html(self, r):
        """How the recipe is started -- and for a menu, the options it offers, since those
        labels are the only thing a caller can match on."""
        txt = esc(self.DRIVER_TEXT.get(r.get('driver'), r.get('driver') or ''))
        pk = r.get('picker')
        if pk and pk.get('labels'):
            txt += '<div class="small">%s</div>' % esc(' / '.join(pk['labels']))
        return txt

    def recipe_rows(self, rows, base, show_product):
        """One table of recipes, from the maker's side or the user's side."""
        head = ['Skill', 'Level', 'XP', 'Ingredients', 'Tool', 'How', 'Where']
        out = ['<table class="data"><thead><tr>%s<th>Makes</th></tr></thead><tbody>'
               % ''.join('<th>%s</th>' % h for h in head)]
        for r in sorted(rows, key=lambda r: (r['skill'], r['level'], r.get('product') or '')):
            where = r.get('station') or ''
            if r.get('station') == 'fishing_spot' and r.get('station_npcs'):
                where = 'fishing spot'
            made = r.get('product') or r.get('product_loc') or ''
            n = r.get('produces_n', 1)
            prod = (self.item_link(made, base) if made in self.items else esc(made)) if show_product else ''
            extra = []
            if r.get('byproducts'):
                extra.append('gives back ' + ', '.join(self.item_link(x, base) for x in r['byproducts']))
            if r.get('burnt'):
                extra.append('can spoil into ' + self.item_link(r['burnt'], base))
            if r.get('members'):
                extra.append('<span class="tag members">members</span>')
            if r.get('notes'):
                extra.append(esc(r['notes']))
            out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td></tr>' % (
                esc(r['skill'].title()), r['level'] or '', r.get('xp') or '',
                ', '.join('%s%s%s' % (self.item_link(x['item'], base), (' &times;%d' % x['n']) if x['n'] > 1 else '',
                                      ('<span class="small"> per %d</span>' % x['per']) if x.get('per') else '')
                          for x in r['inputs']) or '&mdash;',
                self.item_link(r['tool'], base) if r.get('tool') in self.items else esc(r.get('tool') or ''),
                self.driver_html(r),
                esc(where.replace('_', ' ')),
                ('%s%s' % (prod, (' &times;%d' % n) if n > 1 else '')) if show_product else ((' &times;%d' % n) if n > 1 else '1'),
                ('<div class="small">%s</div>' % '; '.join(extra)) if extra else ''))
        out.append('</tbody></table>')
        return ''.join(out)

    def item_page(self, i):
        base = '../'
        info = ['<h3>%s</h3>%s<table>' % (esc(i['display']), ('<div class="iconbox">%s</div>' % self.icon_img(i, base, 'icon big')) if self.has_icon(i) else '')]
        rows = [('Config name', '<code>%s</code>' % esc(i['name'])), ('Item id', i['id'] if i['id'] is not None else '?'),
                ('Value', ('%s gp' % i['cost']) if i['cost'] else None), ('Weight', i['weight']),
                ('Members', 'Yes' if i['members'] else 'No'), ('Tradeable', 'Yes' if i['tradeable'] else 'No'),
                ('Stackable', 'Yes' if i['stackable'] else 'No'), ('Equip slot', i['wearpos']),
                ('Options', ', '.join(i['iops']) or None), ('Category', esc(i['category']) if i['category'] and not i['category'].startswith('category_') else None),
                ('Level required', esc(i['params']['levelrequire']) if 'levelrequire' in i['params'] else None),
                ('Defined in', '<code>%s</code>' % esc(i['file']))]
        for k, v in rows:
            if v is not None and v != '':
                info.append('<tr><td>%s</td><td>%s</td></tr>' % (k, v))
        st = [(lab, i['params'][k]) for k, lab in STAT_KEYS if k in i['params']]
        if st:
            info.append('<tr><td>Equipment stats</td><td>%s</td></tr>' % ', '.join('%s %s' % (l, esc(v)) for l, v in st))
        info.append('</table></div>')
        body = ['<div class="infobox">' + ''.join(info)]
        body.append('<h1><span class="dot item"></span>%s%s</h1><div class="sub">%s</div>' % (esc(i['display']), ' <span class="tag">noted</span>' if i['cert'] else '', esc(i['desc'])))
        links = ['<a href="%sgraph.html#item:%s">Open in knowledge graph &rarr;</a>' % (base, esc(i['name']))]
        if i['id'] is not None and i['spawns']:
            links.insert(0, '<a href="%smap.html#obj=%d">Show %d ground spawn%s on the world map &rarr;</a>' % (base, i['id'], len(i['spawns']), 's' if len(i['spawns']) != 1 else ''))
        body.append('<p>' + ' &nbsp; '.join(links) + '</p>')
        makes, uses = self.recipe_index()
        # fishing keeps its own section below, which names the spots
        made_by = [r for r in makes.get(i['name'], []) if r['skill'] != 'fishing']
        if made_by:
            body.append('<h2>How to make</h2>' + self.recipe_rows(made_by, base, False))
        for s in (self.gen or {}).get('services', {}).get(i['name'], []):
            body.append('<p>Made by %s from %s%s for %d gp.</p>' % (
                self.npc_link(s['npc'], base), ('%d&times; ' % s['n']) if s['n'] > 1 else '',
                self.item_link(s['from'], base), s['cost']))
        if uses.get(i['name']):
            body.append('<h2>Used to make</h2>' + self.recipe_rows(uses[i['name']], base, True))
        if i['dropped_by']:
            body.append('<h2>Dropped by</h2><table class="data"><thead><tr><th>NPC</th><th>Level</th><th>Quantity</th><th>Rarity</th><th>Via</th><th>Notes</th></tr></thead><tbody>')
            rows = sorted(i['dropped_by'], key=lambda kv: (kv[1]['prob'] is None, -(kv[1]['prob'] or 0)))
            for npc_name, r in rows[:150]:
                n = self.npcs[npc_name]
                pt = prob_text(r['prob'])
                via = ' &rarr; '.join(self.table_link(t, base) for t in r.get('via', ()))
                body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s %s</td><td>%s</td><td>%s</td></tr>' % (
                    self.npc_link(npc_name, base), n['level'] or '', esc(r['count']), pt[0], ('<span class="small">(%s)</span>' % pt[1]) if pt[1] and pt[0] != 'Always' else '', via, self.notes_html(r['notes'])))
            body.append('</tbody></table>')
        if i['in_tables']:
            body.append('<h2>In drop tables</h2><p>' + ' '.join('<a class="chip" href="%s%s">%s (%s)</a>' % (base, self.tables[t]['url'], esc(self.tables[t]['display']), prob_text(r['prob'])[0]) for t, r in i['in_tables']) + '</p>')
        if i['sold_at']:
            body.append('<h2>Sold at</h2><table class="data"><thead><tr><th>Shop</th><th>Shopkeeper</th><th>Stock</th></tr></thead><tbody>')
            for inv, cnt in i['sold_at']:
                s = self.shops[inv]
                body.append('<tr><td><a href="%s%s">%s</a></td><td>%s</td><td>%s</td></tr>' % (base, s['url'], esc(s['title']), ', '.join(self.npc_link(o, base) for o in s['owners']), esc(cnt)))
            body.append('</tbody></table>')
        if i['spawns']:
            body.append('<h2>Ground spawns</h2><table class="data"><thead><tr><th>Area</th><th>Coordinates</th><th>Level</th><th>Count</th><th>Map</th></tr></thead><tbody>')
            for (lv, x, z, cnt, slug) in sorted(i['spawns'], key=lambda s: (self.area_by_slug[s[4]]['name'] if s[4] else 'zzz', s[1], s[2]))[:200]:
                body.append('<tr><td>%s</td><td>%d, %d</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (self.area_link(slug, base), x, z, lv, cnt, self.map_link(base, x, z, lv, 'view', '&obj=%d' % i['id'])))
            body.append('</tbody></table>')
        if i['quests']:
            body.append('<h2>Quests</h2><p>' + ' '.join('<a class="chip" href="%s%s">%s</a>' % (base, self.quests[q]['url'], esc(self.quests[q]['name'])) for q in sorted(i['quests'])) + '</p>')
        if self.gen:
            frows = [r for r in self.gen['recipes'] if r['skill'] == 'fishing' and r.get('product') == i['name']]
            if frows:
                body.append('<h2>Fishing</h2>')
                for r in frows:
                    spots = sorted({sn for sn in r.get('station_npcs', []) if self.npcs[sn]['spawns']}, key=lambda sn: self.npcs[sn]['display'])
                    body.append('<p>Level %d%s%s, %s xp. Caught at: %s</p>' % (
                        r['level'], (' with ' + self.item_link(r['tool'], base)) if r.get('tool') else '',
                        (' and ' + self.item_link(r['inputs'][0]['item'], base)) if r['inputs'] else '', r['xp'],
                        ', '.join('%s (%d spawn%s)' % (self.npc_link(sn, base), len(self.npcs[sn]['spawns']), 's' if len(self.npcs[sn]['spawns']) != 1 else '') for sn in spots) or 'no placed spots'))
        for info in self.rc_by_item.get(i['name'], []):
            a, r = info['altar'], info['ruins']
            body.append('<h2>Runecrafting</h2><p>%s altar: level %d, %s xp per essence%s. Crafts %s using %s.</p><p>Altar at %d, %d (%s)%s</p>' % (
                esc(info['rune']), info['level_req'], esc(info['xp'] or '?'), ' <span class="tag members">members</span>' if info['members'] else '',
                self.item_link(info['rune_item'], base) if info['rune_item'] else '?', self.item_link(info['talisman'], base) if info['talisman'] else 'a talisman',
                a[1], a[2], self.map_link(base, a[1], a[2], a[0], 'view altar'),
                ('; mysterious ruins at %d, %d (%s)' % (r[1], r[2], self.map_link(base, r[1], r[2], r[0], 'view ruins'))) if r else ''))
        if i['scripts']:
            body.append('<h2>Referenced in scripts</h2><div class="cols">' + ''.join('<code>%s</code>' % esc(f) for f in i['scripts'][:40]) + '</div>')
            if len(i['scripts']) > 40:
                body.append('<p class="small">+%d more files</p>' % (len(i['scripts']) - 40))
        body.append('<div class="clear"></div>')
        return self.page(i['display'], ''.join(body), 1)

    def area_page(self, a):
        base = '../'
        kind = 'Underground area' if a.get('surface') else {0: 'Location', 1: 'Town', 2: 'Region'}.get(a['size'], 'Area')
        body = ['<h1><span class="dot area"></span>%s</h1><div class="sub">%s at %d, %d &middot; %s</div>' % (esc(a['name']), kind, a['x'], a['z'], esc(a['region']))]
        body.append('<p><a href="%smap.html#x=%d&z=%d&level=0&zoom=5">Open on the world map &rarr;</a> &nbsp; <a href="%sgraph.html#area:%s">Open in knowledge graph &rarr;</a>' % (base, a['x'], a['z'], base, esc(a['slug'])))
        if a.get('surface'):
            body.append(' &nbsp; Surface: ' + self.area_link(a['surface'], base))
        twin = self.area_by_slug.get(a['slug'] + '_underground')
        if twin:
            body.append(' &nbsp; Below: ' + self.area_link(twin['slug'], base))
        body.append('</p>')
        body.append('<h2>NPCs</h2>')
        if a['npcs']:
            body.append('<table class="data"><thead><tr><th>NPC</th><th>Level</th><th>Spawns</th><th>Notable drops</th></tr></thead><tbody>')
            for name, cnt in sorted(a['npcs'].items(), key=lambda kv: (-(self.npcs[kv[0]]['level'] or 0), self.npcs[kv[0]]['display'])):
                n = self.npcs[name]
                main = [r for r in n['drops'] if r['kind'] == 'main' and r['prob'] is not None]
                main.sort(key=lambda r: -r['prob'])
                seen_labels = []
                for r in main:
                    lab = r['label'] or (self.tables[r['table']]['display'] if r['table'] in self.tables else '')
                    if lab and lab not in seen_labels:
                        seen_labels.append(lab)
                    if len(seen_labels) >= 4:
                        break
                notable = ', '.join(seen_labels)
                body.append('<tr><td>%s</td><td>%s</td><td>%d</td><td class="small">%s</td></tr>' % (self.npc_link(name, base), n['level'] or ('shop' if n['shop'] else ''), cnt, esc(notable)))
            body.append('</tbody></table>')
        else:
            body.append('<p class="small">No NPC spawns are assigned to this label.</p>')
        if a['objs']:
            body.append('<h2>Ground items</h2><div class="cols">' + ''.join('%s <span class="small">x%d</span><br>' % (self.item_link(name, base), cnt) for name, cnt in sorted(a['objs'].items())) + '</div>')
        if a['shops']:
            body.append('<h2>Shops</h2><div class="cols">' + ''.join('<a href="%s%s">%s</a>' % (base, self.shops[s]['url'], esc(self.shops[s]['title'])) for s in sorted(a['shops']) if s in self.shops) + '</div>')
        return self.page(a['name'], ''.join(body), 1)

    def quest_page(self, q):
        base = '../'
        body = ['<h1><span class="dot quest"></span>%s</h1><div class="sub">Scripts in <code>scripts/quests/%s/</code></div>' % (esc(q['name']), esc(q['folder']))]
        body.append('<p><a href="%sgraph.html#quest:%s">Open in knowledge graph &rarr;</a></p>' % (base, esc(q['folder'])))
        body.append('<h2>NPCs involved</h2><div class="cols">' + ''.join(self.npc_link(n, base) for n in sorted(q['npcs'], key=lambda x: self.npcs[x]['display'])) + '</div>')
        body.append('<h2>Items involved</h2><div class="cols">' + ''.join(self.item_link(i, base) for i in sorted(q['items'], key=lambda x: self.items[x]['display'])) + '</div>')
        body.append('<p class="small">Involvement is derived from identifiers referenced by the quest scripts.</p>')
        return self.page(q['name'], ''.join(body), 1)

    def shop_page(self, s):
        base = '../'
        body = ['<h1><span class="dot shop"></span>%s</h1><div class="sub">Inventory config <code>%s</code></div>' % (esc(s['title']), esc(s['inv']))]
        body.append('<p>Run by ' + ', '.join(self.npc_link(o, base) for o in s['owners']) + '. <a href="%sgraph.html#shop:%s">Open in knowledge graph &rarr;</a></p>' % (base, esc(s['inv'])))
        locs = set()
        for o in s['owners']:
            for slug in self.npcs[o]['areas']:
                locs.add(slug)
        if locs:
            body.append('<p>Located in: ' + ', '.join(self.area_link(l, base) for l in sorted(locs)) + '</p>')
        body.append('<h2>Stock</h2>' + self.stock_html(s, base))
        if s['allstock']:
            body.append('<p class="small">This is a general store: it accepts any tradeable item.</p>')
        return self.page(s['title'], ''.join(body), 1)

    def table_page(self, t):
        base = '../'
        body = ['<h1><span class="dot table"></span>%s</h1><div class="sub">%s <code>%s</code>%s</div>' % (
            esc(t['display']), 'Shared drop proc' if t['kind'] == 'script' else 'Database drop table', esc(t['name']), (' in <code>%s</code>' % esc(t['file'])) if t['file'] else '')]
        body.append('<p><a href="%sgraph.html#table:%s">Open in knowledge graph &rarr;</a></p>' % (base, esc(t['name'])))
        body.append('<h2>Entries</h2>' + self.drops_html(t['entries'], base))
        if t['used_by']:
            body.append('<h2>Rolled by</h2><table class="data"><thead><tr><th>NPC</th><th>Level</th><th>Chance to roll this table</th></tr></thead><tbody>')
            for npc_name, r in sorted(t['used_by'], key=lambda kv: self.npcs[kv[0]]['display']):
                pt = prob_text(r['prob'])
                body.append('<tr><td>%s</td><td>%s</td><td>%s %s</td></tr>' % (self.npc_link(npc_name, base), self.npcs[npc_name]['level'] or '', pt[0], ('(%s)' % pt[1]) if pt[1] else ''))
            body.append('</tbody></table>')
        if t['parents']:
            body.append('<h2>Referenced by tables</h2><p>' + ' '.join(self.table_link(p, base) for p in sorted(set(t['parents']))) + '</p>')
        return self.page(t['display'], ''.join(body), 1)

    def write_indexes(self):
        base = ''
        counts = [('map.html', len(self.npc_spawns) + len(self.obj_spawns), 'map markers', 'World map explorer'),
                  ('graph.html', 0, '', 'Knowledge graph'), ('chunks.html', 0, '', 'Chunk picker'), ('npcs.html', len(self.npcs), 'NPCs', 'NPC index'),
                  ('items.html', len([i for i in self.items.values() if not i['dummy']]), 'items', 'Item index'),
                  ('areas.html', len(self.areas), 'areas', 'Area index'), ('quests.html', len(self.quests), 'quests', 'Quest index'),
                  ('shops.html', len(self.shops), 'shops', 'Shop index'), ('tables.html', len(self.tables), 'tables', 'Shared drop tables')]
        body = ['<h1>Lost City Wiki</h1><div class="sub">A generated wiki, knowledge graph and map explorer for the Lost City server content, %s.</div>' % esc(self.rev_text)]
        body.append('<div class="hero">' + ''.join('<a class="card" href="%s"><div class="big">%s</div><div>%s</div><div class="small">%s</div></a>' % (u, c if c else '&rarr;', t, s) for u, c, s, t in counts) + '</div>')
        body.append('<h2>Start exploring</h2><div class="cards">')
        bandit = self.area_by_slug.get('bandit_camp')
        if bandit:
            body.append('<div class="card"><h3>Bandit Camp (Wilderness)</h3><p class="small">Fat Tony, Noterazzo, Black Heather, Donny the lad, Speedy Keith, bandits and rats.</p>'
                        '<a href="map.html#x=%d&z=%d&level=0&zoom=6">Open on the map</a> &middot; <a href="%s">Area page</a></div>' % (bandit['x'], bandit['z'] + 6, bandit['url']))
        for name in ('lumbridge', 'varrock', 'falador', 'draynor_village', 'edgeville', 'al_kharid', 'east_ardougne'):
            a = self.area_by_slug.get(name)
            if a:
                body.append('<div class="card"><h3>%s</h3><p class="small">%d NPC types, %d ground item types.</p><a href="map.html#x=%d&z=%d&level=0&zoom=4">Open on the map</a> &middot; <a href="%s">Area page</a></div>' % (esc(a['name']), len(a['npcs']), len(a['objs']), a['x'], a['z'], a['url']))
        body.append('</div><h2>How it was built</h2><p class="small">NPC, item, floor and scenery definitions come from the <code>.npc</code>, <code>.obj</code>, <code>.flo</code> and <code>.loc</code> configs; spawns and terrain from the <code>.jm2</code> map files; drop tables by interpreting the RuneScript death handlers (<code>[ai_queue3,...]</code>); shops from <code>.inv</code> configs; quest involvement from identifier references inside the quest scripts. The raw graph is in <code>data/graph.json</code>.</p>')
        self.write('index.html', self.page('Home', ''.join(body), 0))

        # NPCs
        rows = ['<h1>NPCs</h1><input class="filter" data-target="#t" placeholder="Filter NPCs..."><table class="data" id="t"><thead><tr><th>NPC</th><th>Level</th><th>HP</th><th>Category</th><th>Main area</th><th>Spawns</th><th>Drops</th></tr></thead><tbody>']
        for n in sorted(self.npcs.values(), key=lambda n: n['display'].lower()):
            area = n['areas'].most_common(1)[0][0] if n['areas'] else None
            rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td></tr>' % (
                self.npc_link(n['name'], base), n['level'] or '', n['hp'] or '', esc(n['category'] or ''), self.area_link(area, base) if area else '', len(n['spawns']), len([r for r in n['drops'] if r['kind'] != 'always'])))
        rows.append('</tbody></table>')
        self.write('npcs.html', self.page('NPCs', ''.join(rows), 0))
        # Items
        rows = ['<h1>Items</h1><input class="filter" data-target="#t" placeholder="Filter items..."><table class="data" id="t"><thead><tr><th>Item</th><th>Value</th><th>Members</th><th>Dropped by</th><th>Sold at</th><th>Ground spawns</th></tr></thead><tbody>']
        for i in sorted(self.items.values(), key=lambda i: i['display'].lower()):
            if i['dummy'] or i['cert']:
                continue
            rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td></tr>' % (
                self.item_link(i['name'], base), i['cost'] or '', 'yes' if i['members'] else '', len(set(d[0] for d in i['dropped_by'])), len(i['sold_at']), len(i['spawns'])))
        rows.append('</tbody></table>')
        self.write('items.html', self.page('Items', ''.join(rows), 0))
        # Areas
        rows = ['<h1>Areas</h1><input class="filter" data-target="#t" placeholder="Filter areas..."><table class="data" id="t"><thead><tr><th>Area</th><th>Type</th><th>Coordinates</th><th>NPC types</th><th>Ground item types</th><th>Map</th></tr></thead><tbody>']
        for a in sorted(self.areas, key=lambda a: a['name']):
            rows.append('<tr><td><a href="%s">%s</a></td><td>%s</td><td>%d, %d</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
                a['url'], esc(a['name']), 'underground' if a.get('surface') else {0: 'location', 1: 'town', 2: 'region'}.get(a['size'], ''), a['x'], a['z'], len(a['npcs']), len(a['objs']), self.map_link(base, a['x'], a['z'], 0, 'view')))
        rows.append('</tbody></table>')
        self.write('areas.html', self.page('Areas', ''.join(rows), 0))
        # Quests
        rows = ['<h1>Quests</h1><table class="data"><thead><tr><th>Quest</th><th>NPCs</th><th>Items</th></tr></thead><tbody>']
        for q in sorted(self.quests.values(), key=lambda q: q['name']):
            rows.append('<tr><td><a href="%s">%s</a></td><td>%d</td><td>%d</td></tr>' % (q['url'], esc(q['name']), len(q['npcs']), len(q['items'])))
        rows.append('</tbody></table>')
        self.write('quests.html', self.page('Quests', ''.join(rows), 0))
        # Shops
        rows = ['<h1>Shops</h1><input class="filter" data-target="#t" placeholder="Filter shops..."><table class="data" id="t"><thead><tr><th>Shop</th><th>Shopkeeper</th><th>Location</th><th>Stock lines</th></tr></thead><tbody>']
        for s in sorted(self.shops.values(), key=lambda s: s['title']):
            locs = set()
            for o in s['owners']:
                locs |= set(self.npcs[o]['areas'])
            rows.append('<tr><td><a href="%s">%s</a></td><td>%s</td><td>%s</td><td>%d</td></tr>' % (s['url'], esc(s['title']), ', '.join(self.npc_link(o, base) for o in s['owners']), ', '.join(self.area_link(l, base) for l in sorted(locs)), len(s['stock'])))
        rows.append('</tbody></table>')
        self.write('shops.html', self.page('Shops', ''.join(rows), 0))
        # Tables
        rows = ['<h1>Shared drop tables</h1><p class="small">Procs and database tables that several NPCs (or skills) roll on.</p><table class="data"><thead><tr><th>Table</th><th>Entries</th><th>Rolled by</th></tr></thead><tbody>']
        for t in sorted(self.tables.values(), key=lambda t: t['display']):
            rows.append('<tr><td><a href="%s">%s</a></td><td>%d</td><td>%d</td></tr>' % (t['url'], esc(t['display']), len(t['entries']), len(t['used_by'])))
        rows.append('</tbody></table>')
        self.write('tables.html', self.page('Drop tables', ''.join(rows), 0))

    # ------------------------------------------------------------------ static
    def copy_static(self):
        os.makedirs(os.path.join(SITE, 'assets'), exist_ok=True)
        for f in ('style.css', 'wiki.js', 'map.js', 'graph.js'):
            shutil.copy(os.path.join(STATIC, f), os.path.join(SITE, 'assets', f))
        shutil.copy(os.path.join(STATIC, 'chunks_ui.js'), os.path.join(SITE, 'assets', 'chunks.js'))
        # stamp asset URLs so browsers pick up rebuilt scripts/styles instead of cached copies
        stamp = str(int(time.time()))
        self.stamp = stamp
        for f in ('map.html', 'graph.html', 'chunks.html'):
            text = read_text(os.path.join(STATIC, f))
            text = re.sub(r'(assets/[a-z]+\.(?:js|css)|data/[a-z_]+\.js)"', r'\1?v=' + stamp + '"', text)
            with open(os.path.join(SITE, f), 'w', encoding='utf-8') as out:
                out.write(text)

    def clean(self):
        if not os.path.isdir(SITE):
            return
        for name in os.listdir(SITE):
            if name in ('tiles', 'icons'):
                continue
            p = os.path.join(SITE, name)
            if name == 'data':
                # keep data/tables until the new ones are written at the end of the build
                for sub in os.listdir(p):
                    if sub != 'tables':
                        sp = os.path.join(p, sub)
                        shutil.rmtree(sp) if os.path.isdir(sp) else os.remove(sp)
                continue
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def main():
    skip = '--skip-tiles' in sys.argv
    force = '--force-tiles' in sys.argv
    if '--content' in sys.argv:
        common.set_content(sys.argv[sys.argv.index('--content') + 1])
    b = Build()
    b.load()
    if '--tiles-only' in sys.argv:
        b.render_tiles(skip=False, force=force)
        b.log('tiles done')
        return
    b.build_areas()
    b.build_entities()
    b.build_spawns()
    b.build_mining_sites()
    b.build_portals()
    b.build_drops()
    b.build_script_index()
    b.build_runecraft()
    b.build_dialogue()
    if '--tables-only' in sys.argv:
        from tables import build_tables
        build_tables(b)
        from chunks import build_chunk_data
        build_chunk_data(b)
        import subprocess
        raise SystemExit(subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'validate_tables.py'), '--content', common.CONTENT]))
    b.clean()
    b.copy_static()
    b.render_tiles(skip=skip, force=force)
    if '--no-tables' not in sys.argv:
        from tables import build_tables
        build_tables(b)          # also feeds the map's resources layer (b.gen)
        from chunks import build_chunk_data
        build_chunk_data(b)
    b.write_data()
    b.write_pages()
    if '--no-tables' not in sys.argv:
        import subprocess
        rc = subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'validate_tables.py'), '--content', common.CONTENT])
        if rc != 0:
            raise SystemExit('table validation failed (exit %d)' % rc)
    b.log('done -> %s' % SITE)


if __name__ == '__main__':
    main()
