"""Chunk-picker data: site/data/chunks.js.

Splits the world into the game's own 64x64 map squares ("chunks") and records
what each one holds: NPC spawns, ground items, resource stands, stations, banks,
shops, entrances and quests. The picker page then works out, for a chosen set of
chunks, what a player who starts with nothing can actually do -- a fixpoint, since
a tool unlocked in one chunk makes resources in another chunk usable.

Content behind a barrier (an underground region whose every way in asks the player
to carry something, such as Zanaris behind the dramen staff) goes into a separate
"part" of its chunk, tagged with what opens it. The picker only counts a gated
part once the closure can supply the key.

Everything comes from the same data the wiki and the tables are built from.
"""
import os
import re
import glob
import json
import time
import subprocess
from collections import defaultdict, Counter
from fractions import Fraction

import common
from common import SITE, read_text

# station kinds the picker cares about (landmark kind -> key used by recipes)
STATION_KINDS = ('bank', 'furnace', 'anvil', 'range', 'fire', 'spinning_wheel', 'potters_wheel', 'pottery_oven', 'altar')
RES_KIND = {'tree': 0, 'rock': 1, 'fishing_spot': 2}
# the rare drop tables: a chunk run cannot plan around these any more than around a random event
RARE_TABLES = ('ultrarare_getitem', 'megararetable')
EVENT_DIR = 'scripts/macro events/'          # random-event npcs and their items are out of scope
MIN_DROP_CHANCE = Fraction(1, 512)
# equipment bonuses, in the order the picker's best-in-slot table expects them
EQUIP_STATS = ('stabattack', 'slashattack', 'crushattack', 'magicattack', 'rangeattack',
               'stabdefence', 'slashdefence', 'crushdefence', 'magicdefence', 'rangedefence',
               'strengthbonus', 'rangebonus', 'prayerbonus')
WAY_KINDS = ('ladder', 'stairs', 'trapdoor', 'cave', 'passage', 'transport_npc')
SKILL_NAMES = ('crafting', 'smithing', 'cooking', 'fletching', 'firemaking', 'woodcutting', 'mining',
               'fishing', 'herblore', 'magic', 'thieving', 'agility', 'runecraft', 'prayer')


def chunk_of(x, z):
    """Map square holding a tile. A dungeon (z >= 6400) belongs to the chunk above it."""
    mx, mz = x >> 6, z >> 6
    if mz >= 100:
        mz -= 100
    return '%d_%d' % (mx, mz)


def square_of(t):
    return (t['x'] >> 6, t['z'] >> 6)


def gated_squares(landmarks):
    """Real map squares you can only reach by carrying something.

    Underground squares joined by entrances form a region; if every way into that
    region from outside asks for an item, the region is gated by those items, and
    any one of the listed alternatives opens it.
    """
    edges = []
    for e in landmarks:
        if e['kind'] not in WAY_KINDS:
            continue
        dests = e['to'] if isinstance(e.get('to'), list) else ([e['to']] if e.get('to') else [])
        src = square_of(e['tile'])
        for d in dests:
            edges.append((src, square_of(d), e.get('requires') or {}))

    under = {s for edge in edges for s in edge[:2] if s[1] >= 100}
    parent = {s: s for s in under}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b_, req in edges:
        if a in under and b_ in under:
            parent[find(a)] = find(b_)
    regions = defaultdict(set)
    for s in under:
        regions[find(s)].add(s)

    out = {}
    for squares in regions.values():
        alts = []
        open_route = False
        for a, b_, req in edges:
            if b_ not in squares or a in squares:
                continue
            items = req.get('items') or []
            if not items:
                open_route = True
                break
            alt = {'i': sorted(items)}
            if alt not in alts:
                alts.append(alt)
        if open_route or not alts:
            continue
        for s in squares:
            out[s] = alts
    return out


WALL_SHAPES = {0: 'straight', 2: 'l', 9: 'diagonal'}
DIRS = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}    # the four wall angles, as the client uses them
ROOM_MIN, ROOM_MAX = 4, 2500


class Rooms:
    """Which tiles a locked door shuts off, traced from the walls in the map files.

    Walls sit on tile edges. Doors that anyone can open are left passable, so flooding
    outwards from a locked door escapes to the outdoors unless the door really is the
    way in; the bounded side is the room behind it.
    """

    def __init__(self, b, landmarks):
        openable = set()
        for lid, nm in b.loc_id2name.items():
            cfg = b.loc_cfg.get(nm)
            d = cfg['d'] if cfg else {}
            if any(k.startswith('op') and str(d[k]).lower() in ('open', 'close') for k in d):
                openable.add(lid)
        self.solid = defaultdict(set)
        self.doors = defaultdict(set)
        for sq in b.squares.values():
            bx, bz = sq.mx << 6, sq.mz << 6
            for (level, x, z, lid, shape, angle) in sq.locs:
                kind = WALL_SHAPES.get(shape)
                if not kind:
                    continue
                if kind == 'diagonal':
                    edges = {0, 1, 2, 3}
                elif kind == 'l':
                    edges = {angle & 3, (angle + 1) & 3}
                else:
                    edges = {angle & 3}
                t = (level, bx + x, bz + z)
                (self.doors if lid in openable else self.solid)[t] |= edges
        # a door with any requirement of its own is a wall too, not a way through
        self.shut = {(e['tile']['level'], e['tile']['x'], e['tile']['z'])
                     for e in landmarks if e['kind'] in ('door', 'gate') and (e.get('requires') or {})}

    def _blocked(self, t, extra):
        edges = self.solid.get(t)
        if t in self.shut or t in extra:
            edges = (edges or set()) | self.doors.get(t, set())
        return edges or ()

    def _flood(self, level, start, extra):
        seen = {start}
        queue = [start]
        while queue:
            x, z = queue.pop()
            here = self._blocked((level, x, z), extra)
            for e, (dx, dz) in DIRS.items():
                if e in here:
                    continue
                nx, nz = x + dx, z + dz
                if ((e + 2) & 3) in self._blocked((level, nx, nz), extra):
                    continue
                if (nx, nz) in seen:
                    continue
                if len(seen) >= ROOM_MAX:
                    return None
                seen.add((nx, nz))
                queue.append((nx, nz))
        return seen

    def behind(self, level, x, z):
        """The smallest enclosed area next to this door, or None if no side is enclosed."""
        extra = {(level, x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        best = None
        for (dx, dz) in DIRS.values():
            r = self._flood(level, (x + dx, z + dz), extra)
            if r and len(r) >= ROOM_MIN and (best is None or len(r) < len(best)):
                best = r
        if best is None:
            return None
        # Take in the wall line as well. A wall sits on the edge of a tile, so the tile that
        # carries it lies outside the room, and an upper floor is built out over it: Scavvo's
        # rune shop stands above the Champions' Guild south wall.
        ring = set()
        for (rx, rz) in best:
            for e, (dx, dz) in DIRS.items():
                t = (rx + dx, rz + dz)
                if t in best or t in ring:
                    continue
                if e in self.solid.get((level, rx, rz), ()) or ((e + 2) & 3) in self.solid.get((level,) + t, ()):
                    ring.add(t)
        return best | ring


def build_chunk_data(b):
    if not b.gen:
        b.log('chunks: skipped (needs the tables)')
        return
    recipes = b.gen['recipes']
    stands = b.gen['stands']
    landmarks = b.gen['landmarks']
    square_gate = gated_squares(landmarks)

    # a quest's varp -> its folder, so a door that checks %heroquest means "needs Hero's Quest"
    quest_var = {}
    for path in glob.glob(os.path.join(common.SCRIPTS, 'quests', 'quest_*', 'configs', '*.varp')):
        folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
        for line in read_text(path).splitlines():
            m = re.match(r'^\[([a-z_0-9]+)\]', line.strip())
            if m:
                quest_var[m.group(1)] = folder

    # what each quest is worth in quest points ("^cook_questpoints = 1")
    quest_points = {}
    for path in glob.glob(os.path.join(common.SCRIPTS, '**', '*.constant'), recursive=True):
        for line in read_text(path).splitlines():
            m = re.match(r'^\^([a-z_0-9]+)_questpoints\s*=\s*(\d+)', line.strip())
            if m and ('quest_' + m.group(1)) in b.quests:
                quest_points['quest_' + m.group(1)] = int(m.group(2))

    # a guild or other small named place whose door checks a quest: everything inside it
    # waits on that quest. Only "place" labels (the smallest kind) qualify, so a quest door
    # inside a town cannot lock the whole town.
    label_gates = []
    room_gates = {}
    rooms = Rooms(b, landmarks)
    places = [a for a in b.areas if a['size'] == 0 and not a.get('surface')]
    for e in landmarks:
        if e['kind'] not in ('door', 'gate'):
            continue
        req = e.get('requires') or {}
        qs = sorted({quest_var[v] for v in req.get('vars', []) if v in quest_var})
        levels = req.get('levels') or {}
        points = req.get('questpoints')
        if not qs and not levels and not points:
            continue
        alt = {}
        if qs:
            alt['q'] = qs
        if levels:
            alt['s'] = sorted([[sk, lv] for sk, lv in levels.items()])
        if points:
            alt['qp'] = points
        carried = sorted(req.get('items') or [])
        if carried:
            alt['i'] = carried
        t = e['tile']
        # Best case: the walls tell us exactly what the door shuts off, so a whole guild is
        # covered however it is shaped, and nothing outside it is.
        room = rooms.behind(t['level'], t['x'], t['z'])
        if room:
            for (x, z) in room:
                cur = room_gates.setdefault((x, z), [])
                if alt not in cur:
                    cur.append(alt)
            continue
        # the door has to sit in (or right on the edge of) the place it locks, and only the
        # nearest such place counts, so one quest door cannot claim its whole neighbourhood
        best, bd = None, 1e9
        for a in places:
            box = (a['x'] - 12, a['z'] - 12, a['x'] + 12, a['z'] + 12)
            if not (box[0] - 6 <= t['x'] <= box[2] + 6 and box[1] - 6 <= t['z'] <= box[3] + 6):
                continue
            d = abs(a['x'] - t['x']) + abs(a['z'] - t['z'])
            if d < bd:
                best, bd = (box, a['name']), d
        if not best:
            # No named place here. A level door is a guild entrance (the Mining Guild has no
            # map label), so lock a small box around it. Quest doors get no such fallback:
            # they are often back rooms inside a town, and a box would catch the shop next door.
            if (not levels and not points) or qs:
                continue
            best = ((t['x'] - 8, t['z'] - 8, t['x'] + 8, t['z'] + 8), e.get('name') or 'behind a door')
        entry = (best[0], [alt], best[1])
        if entry not in label_gates:
            label_gates.append(entry)

    def label_gate_at(x, z):
        hit = room_gates.get((x, z))
        if hit:
            return hit
        for (box, alts, name) in label_gates:
            if box[0] <= x <= box[2] and box[1] <= z <= box[3]:
                return alts
        return None

    def new_part():
        return {'n': Counter(), 'o': Counter(), 'r': Counter(), 'st': Counter(), 'e': [], 'sh': set(), 'q': set(), 'g': None}

    chunks = defaultdict(lambda: {'parts': {}})
    items_used = set()
    npcs_used = set()
    skipped_events = set()

    def P(x, z):
        """The part of the chunk at this tile: ungated, or behind whatever bars the way in."""
        ck = chunk_of(x, z)
        g = square_gate.get((x >> 6, z >> 6)) or label_gate_at(x, z)
        key = json.dumps(g) if g else ''
        parts = chunks[ck]['parts']
        if key not in parts:
            parts[key] = new_part()
            parts[key]['g'] = g
        return parts[key]

    def is_event_item(name):
        it = b.items.get(name)
        return bool(it) and it['file'].startswith(EVENT_DIR)

    # ---- NPC spawns
    for name, n in b.npcs.items():
        if n['id'] is None or n['file'].startswith(EVENT_DIR):
            continue
        for (lv, x, z, slug) in n['spawns']:
            P(x, z)['n'][name] += 1
            npcs_used.add(name)

    # ---- ground item spawns
    for name, it in b.items.items():
        if it['id'] is None or it['file'].startswith(EVENT_DIR):
            continue
        for (lv, x, z, count, slug) in it['spawns']:
            P(x, z)['o'][name] += count
            items_used.add(name)

    # ---- resource stands, tile by tile (a wood can straddle two squares)
    for s in stands:
        kind = RES_KIND.get(s['kind'])
        if kind is None:
            continue
        for t in s['tiles']:
            P(t['x'], t['z'])['r'][(kind, s['resource'], s['level'])] += 1
        items_used.add(s['resource'])

    # ---- placements of the locs that conversions happen at (filled after the conversions are known)
    loc_places = defaultdict(list)
    for sq in b.squares.values():
        bx, bz = sq.mx << 6, sq.mz << 6
        for (level, x, z, lid, shape, angle) in sq.locs:
            nm = b.loc_id2name.get(lid)
            if nm:
                loc_places[nm].append((bx + x, bz + z))

    # ---- landmarks: stations, banks, entrances
    for e in landmarks:
        t = e['tile']
        kind = e['kind']
        part = P(t['x'], t['z'])
        if kind == 'bank_booth':
            part['st']['bank'] += 1
        elif kind in STATION_KINDS:
            part['st'][kind] += 1
        elif kind in WAY_KINDS:
            dests = e['to'] if isinstance(e.get('to'), list) else ([e['to']] if e.get('to') else [])
            for d in dests:
                to = chunk_of(d['x'], d['z'])
                if to != chunk_of(t['x'], t['z']):
                    part['e'].append([kind, e['name'], to])
        elif kind in ('door', 'gate') and (e.get('requires') or {}).get('coins'):
            part['st']['toll_gate'] += 1

    # ---- shops (by their vendor's spawn)
    shops = {}
    for inv, s in b.shops.items():
        stock = [it for (it, cnt, rate) in s['stock'] if it in b.items and b.items[it]['id'] is not None]
        if not stock:
            continue
        owner_spawn = None
        for o in s['owners']:
            if b.npcs[o]['spawns'] and not b.npcs[o]['file'].startswith(EVENT_DIR):
                owner_spawn = (o, b.npcs[o]['spawns'][0])
                break
        if not owner_spawn:
            continue
        o, (lv, x, z, slug) = owner_spawn
        P(x, z)['sh'].add(inv)
        shops[inv] = {'t': s['title'], 'o': o, 'i': stock}
        items_used.update(stock)
        npcs_used.add(o)

    # ---- quests: the chunks holding the NPCs they need
    quests = {}
    for folder, q in b.quests.items():
        need = set()
        placed = 0
        for nn in q['npcs']:
            sp = b.npcs[nn]['spawns']
            if sp:
                placed += 1
                need.add(chunk_of(sp[0][1], sp[0][2]))
        if not need or placed < 1:
            continue
        quests[folder] = {'n': q['name'], 'c': sorted(need)}
        for nn in q['npcs']:
            sp = b.npcs[nn]['spawns']
            if sp:
                P(sp[0][1], sp[0][2])['q'].add(folder)

    # ---- recipes, compacted; tools become groups (any one of them will do)
    rec = []
    for r in recipes:
        tools = []
        if r.get('tools'):
            tools.append([t['item'] for t in sorted(r['tools'], key=lambda t: (t['level'], t['item']))])
        elif r.get('tool'):
            tools.append([r['tool']])
        inputs = [[i['item'], i['n']] for i in r['inputs']]
        station = r.get('station')
        if station == 'pottery':
            station = 'potters_wheel'
        rec.append({'p': r.get('product'), 's': r['skill'], 'lv': r['level'], 'in': inputs, 't': tools,
                    'st': station, 'xp': r['xp'], 'n': r.get('produces_n', 1),
                    'fire': 1 if (r['skill'] == 'firemaking' and not r.get('product')) else 0})
        for i in inputs:
            items_used.add(i[0])
        for g in tools:
            items_used.update(g)
        if r.get('product'):
            items_used.add(r['product'])

    # ---- "use A on B" style steps the scripts define: quest carving, harvesting a special tree...
    def reach(key, depth=2):
        seen, out, todo = set(), [], [(key, 0)]
        while todo:
            k, d = todo.pop()
            if k in seen or k not in b.blocks or d > depth:
                continue
            seen.add(k)
            body = b.blocks[k]['body']
            out.append(body)
            for m in re.finditer(r'([@~])([a-z_0-9]+)', body):
                nk = ('label' if m.group(1) == '@' else 'proc', m.group(2))
                if nk in b.blocks and b.blocks[nk]['file'] == b.blocks[k]['file']:
                    todo.append((nk, d + 1))
        return '\n'.join(out)

    axe_tools = next((r['t'][0] for r in rec if r['s'] == 'woodcutting' and r['t']), [])
    pick_tools = next((r['t'][0] for r in rec if r['s'] == 'mining' and r['t']), [])
    conv_locs, seen_conv = set(), set()
    for key, blk in sorted(b.blocks.items()):
        trig, who = key
        if not (trig.startswith('opheld') or trig.startswith('oploc') or trig.startswith('opnpc')):
            continue
        text = reach(key)
        adds = [m for m in re.findall(r'inv_add\(inv,\s*([a-z_0-9]+)', text) if m in b.items]
        if not adds or len(set(adds)) > 3:
            continue
        dels = sorted({m for m in re.findall(r'inv_del\(inv,\s*([a-z_0-9]+)', text) if m in b.items})
        harvest = not dels and trig.startswith('oploc') and ('axe_checker' in text or 'pickaxe_checker' in text)
        if not dels and not harvest:
            continue
        prod = adds[0]
        if prod in dels:
            continue
        station, tools = None, []
        if trig.startswith('oploc'):
            if who not in b.loc_cfg:
                continue
            station = 'loc:' + who
            conv_locs.add(who)
        elif trig.startswith('opnpc'):
            if who not in b.npcs:
                continue
            station = 'npc:' + who
        elif who in b.items and who not in dels:
            tools = [[who]]
        if harvest:
            tools = [list(axe_tools if 'axe_checker' in text and 'pickaxe' not in text else pick_tools)]
        used = re.search(r'last_useitem\s*=\s*([a-z_0-9]+)', text)
        if used and used.group(1) in b.items and used.group(1) not in dels:
            tools.append([used.group(1)])
        lvl, skill = 1, 'other'
        lm = re.search(r'stat\((' + '|'.join(SKILL_NAMES) + r')\)\s*<\s*(\d+)', text)
        if lm:
            skill, lvl = lm.group(1), int(lm.group(2))
        sig = (prod, tuple(dels), station)
        if sig in seen_conv:
            continue
        seen_conv.add(sig)
        qs = sorted({quest_var[v] for v in re.findall(r'%([a-z_0-9]+)', text) if v in quest_var})
        entry = {'p': prod, 's': skill, 'lv': lvl, 'in': [[d, 1] for d in dels], 't': [t for t in tools if t],
                 'st': station, 'xp': 0, 'n': 1, 'fire': 0, 'src': b.blocks[key]['file']}
        if qs:
            entry['q'] = qs[0]
        rec.append(entry)
        items_used.add(prod)
        items_used.update(dels)
        for t in tools:
            items_used.update(t)

    # gate keys need names in the item table even if nothing else references them
    for alts in square_gate.values():
        for alt in alts:
            items_used.update(i for i in alt if i in b.items)

    # ---- drops (rare-table, random-event and one-in-a-blue-moon rolls left out on purpose)
    drops = {}
    skipped_rare = set()
    for name in list(npcs_used):
        n = b.npcs[name]
        got = []
        for row in b.flatten(n['drops']):
            if not row['item'] or row['item'] not in b.items or b.items[row['item']]['id'] is None:
                continue
            if any(t in RARE_TABLES for t in row.get('via', ())) or (row['prob'] is not None and row['prob'] < MIN_DROP_CHANCE):
                skipped_rare.add(row['item'])
                continue
            if is_event_item(row['item']):
                skipped_events.add(row['item'])
                continue
            if row['item'] not in got:
                got.append(row['item'])
        if got:
            drops[name] = got
            items_used.update(got)

    for nm in sorted(conv_locs):
        for (x, z) in loc_places.get(nm, []):
            P(x, z).setdefault('l', set()).add(nm)

    # ---- the tables the page needs
    npc_tbl = {}
    for name in sorted(npcs_used):
        n = b.npcs[name]
        npc_tbl[name] = {'n': n['display'], 'l': n['level'], 'a': 1 if 'Attack' in n['ops'] else 0,
                         'd': drops.get(name, []), 'u': n['url'],
                         'hp': int(n['hp']) if n['hp'] and n['hp'].isdigit() else None}

    def stat(params, key):
        try:
            return int(params.get(key))
        except (TypeError, ValueError):
            return 0

    item_tbl = {}
    for name in sorted(items_used):
        it = b.items.get(name)
        if not it or it['id'] is None:
            continue
        irec = {'n': it['display'], 'u': it['url'], 'id': it['id']}
        if it['members']:
            irec['m'] = 1
        if it['wearpos']:
            cfg = b.obj_cfg.get(name)
            irec['w'] = it['wearpos']
            w2 = cfg['d'].get('wearpos2') if cfg else None
            if w2:
                irec['w2'] = w2
            irec['e'] = [stat(it['params'], k) for k in EQUIP_STATS]
            lr = stat(it['params'], 'levelrequire')
            if lr:
                irec['lr'] = lr
        item_tbl[name] = irec

    # ---- area name per chunk, and every square that exists
    area_of = {}
    for a in b.areas:
        if a.get('surface') or a['size'] == 2:
            continue
        area_of.setdefault(chunk_of(a['x'], a['z']), []).append((a['size'], a['name']))
    all_chunks = {}
    for (mx, mz) in b.squares:
        all_chunks.setdefault('%d_%d' % (mx, mz if mz < 100 else mz - 100), None)
    for ck in chunks:
        all_chunks[ck] = True

    def dump_part(p):
        out = {}
        if p.get('g'):
            out['g'] = p['g']
        if p['n']:
            out['n'] = sorted([[k, v] for k, v in p['n'].items()])
        if p['o']:
            out['o'] = sorted([[k, v] for k, v in p['o'].items()])
        if p['r']:
            out['r'] = sorted([[k[0], k[1], v, k[2]] for k, v in p['r'].items()])
        if p['st']:
            out['st'] = dict(sorted(p['st'].items()))
        if p['e']:
            seen, ee = set(), []
            for k, nm, to in p['e']:
                if (k, to) in seen:
                    continue
                seen.add((k, to))
                ee.append([k, nm, to])
            out['e'] = sorted(ee)
        if p.get('l'):
            out['l'] = sorted(p['l'])
        if p['sh']:
            out['sh'] = sorted(p['sh'])
        if p['q']:
            out['q'] = sorted(p['q'])
        return out

    final = {}
    gated_parts = 0
    for ck in sorted(all_chunks, key=lambda k: (int(k.split('_')[0]), int(k.split('_')[1]))):
        rec_c = {}
        names = sorted(area_of.get(ck, []), reverse=True)
        if names:
            rec_c['a'] = names[0][1]
            if len(names) > 1:
                rec_c['a2'] = [n for _s, n in names[1:6]]
        parts = chunks[ck]['parts'] if ck in chunks else {}
        dumped = []
        for key in sorted(parts, key=lambda k: (k != '', k)):
            d = dump_part(parts[key])
            if len(d) > (1 if 'g' in d else 0):
                dumped.append(d)
                if d.get('g'):
                    gated_parts += 1
        if dumped:
            rec_c['p'] = dumped
        final[ck] = rec_c

    try:
        commit = subprocess.check_output(['git', '-C', common.CONTENT, 'rev-parse', '--short', 'HEAD'], text=True).strip()
    except Exception:
        commit = 'unknown'
    data = {
        '_generated': {'tool': 'LC_wiki build/chunks.py', 'revision': b.revision, 'revision_date': b.revision_date,
                       'content_commit': commit, 'date': time.strftime('%Y-%m-%d'),
                       'rules': 'chunk = one 64x64 map square; every floor of the square is included, and a dungeon belongs to the chunk above it',
                       'gates': 'content you cannot walk into becomes a separate part of its chunk: an underground region whose every way in asks for an item, or a small named place (a guild) whose door checks a quest varp. Each alternative is {i:[items]} or {q:[quests]}',
                       'conversions': 'recipes with a "src" come from a script: use one item on another, harvest a special loc, or a quest step; "q" names a quest whose npcs must all be in unlocked chunks',
                       'drops': 'rare drop table (%s), drops rarer than %s and random-event items (%s) are left out: a run cannot be planned around them'
                                % (', '.join(RARE_TABLES), MIN_DROP_CHANCE, EVENT_DIR)},
        'chunks': final, 'npcs': npc_tbl, 'items': item_tbl, 'shops': shops, 'quests': quests, 'recipes': rec,
        'questpoints': quest_points,
        'coins': 'coins',
        'skills': sorted({r['s'] for r in rec}),
    }
    os.makedirs(os.path.join(SITE, 'data'), exist_ok=True)
    with open(os.path.join(SITE, 'data', 'chunks.js'), 'w', encoding='utf-8') as f:
        f.write('window.CHUNKDATA = ' + json.dumps(data, separators=(',', ':'), sort_keys=True) + ';\n')
    b.log('chunks: %d squares, %d npcs, %d items, %d recipes (%d rare-table drops left out)'
          % (len(final), len(npc_tbl), len(item_tbl), len(rec), len(skipped_rare - set(item_tbl))))
    b.log('chunks: %d gated squares, %d locked rooms, %d locked places -> %d gated parts'
          % (len(square_gate), len({id(v) for v in room_gates.values()}), len(label_gates), gated_parts))
