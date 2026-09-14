#!/usr/bin/env python3
"""Referential-integrity check for site/data/tables/*.json against the Content checkout.

Usage: python build/validate_tables.py [--content <path>]
Exit code 1 (and a list of problems) when any reference does not resolve.
"""
import os
import re
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from common import SITE, parse_config_files, parse_pack

if '--content' in sys.argv:
    common.set_content(sys.argv[sys.argv.index('--content') + 1])

TABLES = os.path.join(SITE, 'data', 'tables')
DRIVERS = ('op_loc', 'op_npc', 'op_item', 'use_item_on', 'use_item_on_loc', 'chat_picker', 'interface')
RECIPE_SKILLS = ('woodcutting', 'mining', 'smithing', 'cooking', 'fletching', 'firemaking', 'crafting', 'fishing', 'herblore', 'magic', 'none')
SOURCE_KINDS = ('shop', 'drop', 'skill', 'service', 'chest', 'handout', 'minigame', 'spawn', 'quest')
errors = []
counts = {}


def err(msg):
    errors.append(msg)


def load(name):
    with open(os.path.join(TABLES, name), encoding='utf-8') as f:
        return json.load(f)


items = set(parse_config_files('obj'))
npcs = set(parse_config_files('npc'))
locs = set(parse_config_files('loc'))
quests = {os.path.basename(p) for p in glob.glob(os.path.join(common.SCRIPTS, 'quests', 'quest_*'))}
squares = set()
for p in glob.glob(os.path.join(common.CONTENT, 'maps', 'm*.jm2')):
    mx, mz = os.path.basename(p)[1:-4].split('_')
    squares.add((int(mx), int(mz)))


def check_tile(t, where):
    if not isinstance(t, dict) or not all(k in t for k in ('level', 'x', 'z')):
        err('%s: malformed tile %r' % (where, t))
        return
    if not (0 <= t['level'] <= 3):
        err('%s: level out of range %r' % (where, t))
    if (t['x'] >> 6, t['z'] >> 6) not in squares:
        err('%s: tile %r is not on any map square' % (where, t))


def check_header(doc, name):
    h = doc.get('_generated')
    if not h or not h.get('revision') or not h.get('content_commit'):
        err('%s: missing _generated header' % name)


# ---- areas
areas = load('areas.json')
check_header(areas, 'areas.json')
slugs = {a['slug'] for a in areas['areas']}
if len(slugs) != len(areas['areas']):
    err('areas.json: duplicate slugs')
for a in areas['areas']:
    if a['parent'] and a['parent'] not in slugs:
        err('areas.json: %s parent %s unknown' % (a['slug'], a['parent']))
    for r in a['rects']:
        if not (r[0] <= r[2] and r[1] <= r[3]):
            err('areas.json: %s bad rect %r' % (a['slug'], r))
counts['areas'] = len(areas['areas'])

# ---- recipes
rec = load('recipes.json')
check_header(rec, 'recipes.json')
for it in rec.get('unobtainable_inputs', {}).get('items', []):
    if it not in items:
        err('recipes.json: unobtainable input %s is not an item' % it)
for r in rec['recipes']:
    w = 'recipes.json: %s/%s' % (r['skill'], r.get('product') or r.get('product_loc'))
    if r.get('product') and r['product'] not in items:
        err(w + ': product not an item')
    if r.get('product_loc') and r['product_loc'] not in locs:
        err(w + ': product_loc not a loc')
    if not r.get('product') and not r.get('product_loc'):
        err(w + ': no product')
    for i in r['inputs']:
        if i['item'] not in items:
            err(w + ': input %s not an item' % i['item'])
    if r.get('tool') and r['tool'] not in items and r['tool'] not in ('axe', 'pickaxe'):
        err(w + ': tool %s not an item' % r['tool'])
    for t in r.get('tools', []):
        if t['item'] not in items:
            err(w + ': tool %s not an item' % t['item'])
    for ln in r.get('station_locs', []):
        if ln not in locs:
            err(w + ': station loc %s unknown' % ln)
    for nn in r.get('station_npcs', []):
        if nn not in npcs:
            err(w + ': station npc %s unknown' % nn)
    if r.get('burnt') and r['burnt'] not in items:
        err(w + ': burnt %s not an item' % r['burnt'])
    for bp in r.get('byproducts', []):
        if bp not in items:
            err(w + ': byproduct %s not an item' % bp)
    if r.get('produces_n', 1) < 1:
        err(w + ': produces_n below 1')
    if not isinstance(r['level'], int) or r['level'] < 0:
        err(w + ': bad level')
    if r.get('skill') not in RECIPE_SKILLS:
        err(w + ': skill %r not one of %s' % (r.get('skill'), '/'.join(RECIPE_SKILLS)))
    if r.get('driver') not in DRIVERS:
        err(w + ': driver %r not one of %s' % (r.get('driver'), '/'.join(DRIVERS)))
    if r.get('picker'):
        pk = r['picker']
        if pk.get('choices') != len(pk.get('labels') or []):
            err(w + ': picker says %s choices but lists %d labels' % (pk.get('choices'), len(pk.get('labels') or [])))
        for lb in pk.get('labels') or []:
            if '<' in lb:
                err(w + ': picker label %r still has an unresolved substitution' % lb)
    elif r.get('driver') == 'chat_picker':
        err(w + ': chat_picker with no picker labels')
    for i in r['inputs']:
        if i.get('per') is not None and (not isinstance(i['per'], int) or i['per'] < 1):
            err(w + ': input %s has a bad per' % i['item'])
counts['recipes'] = len(rec['recipes'])

# ---- item sources
src = load('item_sources.json')
check_header(src, 'item_sources.json')
n_src = 0
for iname, entry in src['items'].items():
    if iname not in items:
        err('item_sources.json: %s not an item' % iname)
    for s in entry['sources']:
        n_src += 1
        k, ref = s['kind'], s['ref']
        if k not in SOURCE_KINDS:
            err('item_sources.json: %s has a source of unknown kind %r' % (iname, k))
        if k == 'chest' and (ref not in locs or s['detail']['key'] not in items):
            err('item_sources.json: %s chest %s / key %s do not resolve' % (iname, ref, s['detail'].get('key')))
        elif k == 'handout' and ref not in npcs:
            err('item_sources.json: %s handout ref %s unknown npc' % (iname, ref))
        elif k in ('shop', 'drop', 'service') and ref not in npcs:
            err('item_sources.json: %s %s ref %s unknown npc' % (iname, k, ref))
        elif k == 'service' and s['detail']['from'] not in items:
            err('item_sources.json: %s service takes %s, not an item' % (iname, s['detail']['from']))
        elif k == 'quest' and ref not in quests:
            err('item_sources.json: %s quest %s unknown' % (iname, ref))
        elif k == 'spawn':
            for t in s['detail']['tiles']:
                check_tile(t, 'item_sources.json: %s spawn' % iname)
counts['item_sources (items / sources)'] = '%d / %d' % (len(src['items']), n_src)

# ---- npc safety
saf = load('npc_safety.json')
check_header(saf, 'npc_safety.json')
for n in saf['npcs']:
    if n['npc'] not in npcs:
        err('npc_safety.json: %s unknown npc' % n['npc'])
    for a in n['areas']:
        if a not in slugs:
            err('npc_safety.json: %s area %s unknown' % (n['npc'], a))
    if n['aggressive'] not in (True, False, None):
        err('npc_safety.json: %s aggressive not bool/null' % n['npc'])
    if n.get('placed') is not bool(n['spawns']):
        err('npc_safety.json: %s placed disagrees with its spawn count' % n['npc'])
    if not (0 <= n.get('wilderness_spawns', 0) <= n['spawns']):
        err('npc_safety.json: %s has more Wilderness spawns than spawns' % n['npc'])
    if n.get('hunts') and not (n['aggressive'] and n['huntrange'] > 0):
        err('npc_safety.json: %s hunts but is not aggressive with a huntrange' % n['npc'])
    h = n.get('hunt')
    if h and h.get('level_check') not in ('off', 'outside_wilderness'):
        err('npc_safety.json: %s hunt level_check %r unknown' % (n['npc'], h.get('level_check')))
for e in saf['random_events']:
    if e['npc'] not in npcs:
        err('npc_safety.json: random event %s unknown npc' % e['npc'])
    for r in e['rewards']:
        if r not in items:
            err('npc_safety.json: random event %s reward %s unknown' % (e['npc'], r))
counts['npc_safety (npcs / random events)'] = '%d / %d' % (len(saf['npcs']), len(saf['random_events']))

# ---- landmarks
lm = load('landmarks.json')
check_header(lm, 'landmarks.json')
kinds = {}
for e in lm['landmarks']:
    w = 'landmarks.json: %s %s' % (e['kind'], e.get('ref'))
    kinds[e['kind']] = kinds.get(e['kind'], 0) + 1
    check_tile(e['tile'], w)
    if e['kind'] == 'transport_npc':
        if e['ref'] not in npcs:
            err(w + ': unknown npc')
        for t in e['to']:
            check_tile(t, w + ' to')
    else:
        if e.get('ref') and e['ref'] not in locs:
            err(w + ': unknown loc')
        if isinstance(e.get('to'), dict):
            check_tile(e['to'], w + ' to')
    if e['kind'] == 'bank_booth':
        check_tile(e['stand'], w + ' stand')
        if e['area'] not in slugs:
            err(w + ': bank area %s unknown' % e['area'])
counts['landmarks'] = '%d (%s)' % (len(lm['landmarks']), ', '.join('%s %d' % kv for kv in sorted(kinds.items())))

# ---- resource stands
st = load('resource_stands.json')
check_header(st, 'resource_stands.json')
for s in st['stands']:
    w = 'resource_stands.json: %s %s @%d,%d' % (s['kind'], s['resource'], s['centre']['x'], s['centre']['z'])
    if s['resource'] not in items:
        err(w + ': resource not an item')
    table = npcs if s.get('ref_kind') == 'npc' else locs
    for r in s['refs']:
        if r not in table:
            err(w + ': ref %s is not a %s' % (r, s.get('ref_kind')))
    for t in s['tiles']:
        check_tile(t, w)
    if s['nearest_bank'] and s['nearest_bank']['slug'] not in slugs:
        err(w + ': nearest bank %s unknown' % s['nearest_bank']['slug'])
    if s.get('area') not in slugs:
        err(w + ': area %s unknown' % s.get('area'))
for s in st['npc_stands']:
    if s['npc'] not in npcs:
        err('resource_stands.json: npc stand %s unknown npc' % s['npc'])
    if s.get('area') not in slugs:
        err('resource_stands.json: npc stand %s area unknown' % s['npc'])
counts['resource_stands (stands / npc_stands)'] = '%d / %d' % (len(st['stands']), len(st['npc_stands']))

# ---- chat verdicts
ch = load('chat_verdicts.json')
check_header(ch, 'chat_verdicts.json')
total = sum(len(v) for v in ch['verdicts'].values())
if total < 100:
    err('chat_verdicts.json: suspiciously few messages (%d)' % total)
counts['chat_verdicts (strings / categories)'] = '%d / %d' % (total, len(ch['verdicts']))

print('table validation (%s, revision %s, commit %s)' % (os.path.relpath(common.CONTENT), areas['_generated']['revision'], areas['_generated']['content_commit']))
for k, v in counts.items():
    print('  %-42s %s' % (k, v))
if errors:
    print('  %d problems:' % len(errors))
    for e in errors[:60]:
        print('   - ' + e)
    if len(errors) > 60:
        print('   ... %d more' % (len(errors) - 60))
    sys.exit(1)
print('  OK: every reference resolves against the content')
