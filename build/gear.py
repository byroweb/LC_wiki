"""Equipment builder data: what you can wear, how fast it swings, and what it hits for.

Everything on this page is read out of the content rather than typed in:

  * equipment bonuses, attack rate and weapon category from the `.obj` params
    (`scripts/skill_combat/configs/combat.param` defines them);
  * the attack styles of each weapon category from the `combat_style_table`
    dbrows, and their names ("Chop", "Lunge", "Rapid"...) from the combat
    interfaces, matched to the style buttons by their y position;
  * the level needed to wear something from the `[opheld2,<obj>]`
    `@levelrequire_*` gates;
  * monster stats and bonuses from the `.npc` configs;
  * each monster's attack profile by walking its `[ai_opplayer2]` /
    `[ai_applayer2]` handler and weighting the `random(n)` branches it goes
    through;
  * the prayer bonuses from `~check_attack_prayer` and friends, with the names
    and levels from the `prayers` dbtable.

The arithmetic the page then does is the arithmetic in
`scripts/skill_combat/scripts/combat.rs2` and `player_combat_stat.rs2`:

    effective_stat = stat * max(100, prayer) / 100          (~combat_effective_stat)
    roll           = (effective + 8 + style_bonus) * (bonus + 64)   (~combat_stat)
    max_hit        = (strength_roll + 320) / 640            (~combat_maxhit)

and a hit lands when `randominc(attack_roll) > randominc(defence_roll)`, which
over the two uniform rolls comes to

    a <= d:  a / (2 * (d + 1))
    a >  d:  (2a - d) / (2 * (a + 1))

Damage is `randominc(max_hit)`, so a hit averages `max_hit / 2`.

Dragonfire is the one thing the walker cannot read off the scripts as numbers,
because each dragon's breath proc is shaped differently.  The four breath models
below carry the numbers *and* the patterns they were read from; `_check_breath`
re-matches those patterns against the live script and fails the build if the
content has moved on, so the table cannot quietly go stale.
"""
import os
import re
import json
from fractions import Fraction

import common
from common import read_text, pretty
from drops import parse_statements, split_args

# scripts/player/configs/equip.constant, in the order the engine's
# ObjType.getWearPosId() maps the config `wearpos=` names to.
WEARPOS = ['hat', 'back', 'front', 'righthand', 'torso', 'lefthand', 'arms',
           'legs', 'head', 'hands', 'feet', 'jaw', 'ring', 'quiver']
# the slots the equipment screen actually shows (arms/head/jaw are model slots
# that items only ever cover through wearpos2/wearpos3)
SLOTS = [(0, 'Head'), (1, 'Cape'), (2, 'Amulet'), (3, 'Weapon'), (4, 'Body'), (5, 'Shield'),
         (7, 'Legs'), (9, 'Gloves'), (10, 'Boots'), (12, 'Ring'), (13, 'Ammo')]

# the 13 numbers ~equip_get_bonuses returns, in its own order
BONUS_KEYS = ['stabattack', 'slashattack', 'crushattack', 'magicattack', 'rangeattack',
              'stabdefence', 'slashdefence', 'crushdefence', 'magicdefence', 'rangedefence',
              'strengthbonus', 'prayerbonus', 'rangebonus']
BONUS_LABELS = ['Stab', 'Slash', 'Crush', 'Magic', 'Ranged',
                'Stab', 'Slash', 'Crush', 'Magic', 'Ranged',
                'Strength', 'Prayer', 'Ranged strength']

# npc_param names the monster side of the fight reads
NPC_BONUSES = ['attackbonus', 'strengthbonus', 'rangeattack', 'rangebonus', 'magicattack',
               'stabdefence', 'slashdefence', 'crushdefence', 'magicdefence', 'rangedefence']

DAMAGETYPES = ['stab', 'slash', 'crush', 'ranged', 'magic', 'melee']

# ~combat_get_damagestyle_bonuses: damagestyle -> (attack, strength, defence, ranged)
STYLE_BONUS_PROC = 'combat_get_damagestyle_bonuses'

# The dragonfire procs.  Every dragon's breath is shaped a little differently, so
# the numbers live here rather than being read out of the script -- but `checks`
# are re-matched against the named proc on every build, and a miss aborts the
# build instead of shipping a stale table.
#
#   base             max hit with nothing resisting it
#   onhit            max hit when the dragon's attack roll beats your defence
#                    roll (null when the roll does not raise the max hit)
#   gate             true when a failed attack roll means no damage at all
#   shield           max hit while wearing the anti-dragon shield
#   prayer           max hit under Protect from Magic
#   potionOff        what an antifire potion takes off
#   potionOffShield  what it takes off instead while the shield is up
#   prayerOffShield  what Protect from Magic takes off on top of the shield
#   rollDef          which of your defence rolls the breath is rolled against:
#                    'damagetype' (the dragon's own melee damage type, which is
#                    what [proc,dragon_fire] passes), 'magic', or null for no roll
BREATHS = {
    'dragon': {
        'label': 'Dragonfire', 'proc': 'dragon_fire', 'delay': 4, 'rollDef': 'damagetype',
        'base': 30, 'onhit': 50, 'gate': False, 'shield': 5, 'prayer': 10,
        'potionOff': 15, 'potionOffShield': 15, 'prayerOffShield': 0,
        'checks': [r'def_int \$maxhit = 30;',
                   r'antidragonbreathshield\) > 0\) \{\s*\$maxhit = 5;',
                   r'%prayer12 = \^true\) \{\s*\$maxhit = 10;',
                   r'\$maxhit = add\(\$maxhit, 20\);',
                   r'\$maxhit = sub\(\$maxhit, 15\);'],
    },
    'elvarg': {
        'label': 'Dragonfire', 'proc': 'elvarg_max_hit', 'delay': 4, 'rollDef': None,
        'base': 70, 'onhit': None, 'gate': False, 'shield': 10, 'prayer': 55,
        'potionOff': 15, 'potionOffShield': 3, 'prayerOffShield': 3,
        'checks': [r'def_int \$maxhit = 70;',
                   r'antidragonbreathshield\) > 0\) \{[\s\S]*?\$maxhit = sub\(\$maxhit, 60\);',
                   r'\$maxhit = sub\(\$maxhit, 3\);[\s\S]*\$maxhit = sub\(\$maxhit, 3\);',
                   r'\$maxhit = sub\(\$maxhit, 15\);[\s\S]*\$maxhit = sub\(\$maxhit, 15\);'],
    },
    'kbd_fiery': {
        'label': 'Fiery breath', 'proc': 'kbd_fiery_breath_maxhit', 'delay': 5, 'rollDef': 'magic',
        'base': 65, 'onhit': None, 'gate': True, 'shield': 15, 'prayer': 20,
        'potionOff': 15, 'potionOffShield': 15, 'prayerOffShield': 0,
        'checks': [r'def_int \$maxhit = 65;',
                   r'%prayer12 = \^true\) \{[\s\S]*?\$maxhit = 20;',
                   r'antidragonbreathshield\) > 0\) \{[\s\S]*?\$maxhit = 15;',
                   r'\$maxhit = sub\(\$maxhit, 15\);'],
    },
    'kbd_special': {
        'label': 'Toxic / icy / shocking breath', 'proc': 'kbd_special_breath_maxhit', 'delay': 5,
        'rollDef': 'magic',
        'base': 50, 'onhit': None, 'gate': True, 'shield': 10, 'prayer': 15,
        'potionOff': 0, 'potionOffShield': 0, 'prayerOffShield': 0,
        'checks': [r'def_int \$maxhit = 50;',
                   r'%prayer12 = \^true\) \{\s*\$maxhit = 15;',
                   r'antidragonbreathshield\) > 0\) \{\s*\$maxhit = 10;'],
    },
}

# the procs an attack profile can bottom out in
ATTACK_CALLS = {
    'npc_meleeattack': ('melee', None),
    'npc_default_attack': ('melee', None),
    'dragon_melee': ('melee', None),
    'npc_rangeattack': ('ranged', None),
    'dragon_fire': ('breath', 'dragon'),
    'elvarg_dragon_fire_op': ('breath', 'elvarg'),
    'elvarg_dragon_fire_ap': ('breath', 'elvarg'),
    'kbd_dragonfire_close': ('breath', 'kbd_fiery'),
    'kbd_dragonfire_far': ('breath', 'kbd_fiery'),
    'kbd_toxic_breath': ('breath', 'kbd_special'),
    'kbd_icy_breath': ('breath', 'kbd_special'),
    'kbd_shocking_breath': ('breath', 'kbd_special'),
}
SPELL_CALLS = ('npc_cast_spell', 'npc_cast_spell_with_forced_max_hit')


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# obj `weight=` is written in four units; the pages want one number
_WEIGHT_UNITS = {'g': 1.0, 'kg': 1000.0, 'lb': 453.59237, 'oz': 28.349523125}
_WEIGHT_RE = re.compile(r'^(-?[0-9.]+)(g|kg|lb|oz)$')


def _weight_grams(raw):
    """An obj's `weight=` in grams.  Unknown units are a build error, not a zero."""
    if not raw:
        return 0
    m = _WEIGHT_RE.match(str(raw).strip().lower())
    if not m:
        raise SystemExit('gear: cannot read weight %r - update _weight_grams in build/gear.py' % raw)
    return int(round(float(m.group(1)) * _WEIGHT_UNITS[m.group(2)]))


# [proc,player_ranged_check_ammo] singles this bow out: it fires ogre arrows and
# nothing else, while every other bow fires arrows and refuses ogre ones
OGRE_BOW = 'ogre_bow'


# ---------------------------------------------------------------- attack styles

def parse_interface(path):
    """An .if file -> [{'name', 'layer', 'y', 'text', 'mode'}] in file order."""
    comps = []
    cur = None
    for raw in read_text(path).splitlines():
        line = common.strip_comment(raw).strip()
        if not line:
            continue
        m = re.match(r'^\[([^\]]+)\]$', line)
        if m:
            cur = {'name': m.group(1), 'layer': None, 'y': 0, 'text': None, 'mode': None}
            comps.append(cur)
            continue
        if cur is None or '=' not in line:
            continue
        k, v = (s.strip() for s in line.split('=', 1))
        if k == 'layer':
            cur['layer'] = v
        elif k == 'y':
            cur['y'] = _int(v)
        elif k == 'text':
            cur['text'] = v
        elif k == 'script1' and v.startswith('eq,'):
            cur['mode'] = _int(v[3:], None)
    return comps


def style_names(path):
    """{attack style index -> button label} for a combat interface.

    The style buttons are the components that test `com_mode`; the label next to
    one is the text component on the same layer at the nearest y.
    """
    comps = parse_interface(path)
    buttons = [c for c in comps if c['mode'] is not None]
    out = {}
    for b in buttons:
        texts = [c for c in comps
                 if c['text'] and c['layer'] == b['layer'] and not c['text'].startswith('(')
                 and '%' not in c['text']]
        if not texts:
            continue
        best = min(texts, key=lambda c: abs(c['y'] - b['y']))
        out[b['mode']] = best['text']
    return out


def build_styles(b, constants):
    """{style table -> [[name, damagestyle, damagetype], ...]} and {category -> style table}."""
    combat = b.blocks.get(('proc', 'combat_get_weapon_style_data'))
    if not combat:
        raise SystemExit('gear: [proc,combat_get_weapon_style_data] not found')
    cat2table = {}
    for cats, table in re.findall(r'case\s+([a-z0-9_,\s]+?)\s*:\s*return\(([a-z0-9_]+)\);', combat['body']):
        for cat in (c.strip() for c in cats.split(',')):
            cat2table[cat] = table

    # category -> combat interface, from the tab the game opens for that weapon
    tab_body = ''
    for key, blk in b.blocks.items():
        if key[0] == 'proc' and 'weapon_category_tab_attack(' in blk['body'] and 'switch_category' in blk['body']:
            tab_body = blk['body']
            break
    cat2iface = {}
    for cats, iface in re.findall(r'case\s+([a-z0-9_,\s]+?)\s*:\s*~\.?weapon_category_tab_attack\(([a-z0-9_]+):', tab_body):
        for cat in (c.strip() for c in cats.split(',')):
            cat2iface[cat] = iface
    cat2iface['default'] = 'combat_unarmed'

    ifaces = {}
    for root, _dirs, files in os.walk(common.SCRIPTS):
        for f in files:
            if f.endswith('.if'):
                ifaces[f[:-3]] = os.path.join(root, f)

    # the interface that names each style table's buttons
    table2iface = {}
    for cat, table in cat2table.items():
        if cat in cat2iface:
            table2iface.setdefault(table, cat2iface[cat])
    table2iface.setdefault('weapon_unarmed_table', 'combat_unarmed')

    tables = {}
    for table, iface in sorted(table2iface.items()):
        cfg = b.dbrow_cfg.get(table)
        if not cfg:
            raise SystemExit('gear: dbrow [%s] not found' % table)
        vals = {}
        for k in ('damagestyle', 'damagetype'):
            vals[k] = [constants[v.split(',', 1)[1].strip().lstrip('^')]
                       for v in cfg['multi'].get('data', []) if v.split(',', 1)[0].strip() == k]
        names = style_names(ifaces[iface]) if iface in ifaces else {}
        rows = []
        for i, (ds, dt) in enumerate(zip(vals['damagestyle'], vals['damagetype'])):
            rows.append([names.get(i, 'Style %d' % (i + 1)), ds, dt])
        tables[table] = rows

    bonuses = {}
    proc = b.blocks.get(('proc', STYLE_BONUS_PROC))
    for style, vals in re.findall(r'case\s+\^([a-z_]+)\s*:\s*return\(([-0-9,\s]+)\);', proc['body']):
        bonuses[constants[style]] = [int(v) for v in vals.split(',')]
    m = re.search(r'case\s+default\s*:\s*return\(([-0-9,\s]+)\);', proc['body'])
    default_bonus = [int(v) for v in m.group(1).split(',')] if m else [0, 0, 0, 0]
    return cat2table, tables, bonuses, default_bonus


# ---------------------------------------------------------------- requirements

def _gate(b, label, args, seen=()):
    """What a `@levelrequire_*` label demands, read out of the label itself.

    The label spells its own gate out: `stat_base(defence) < $level` against the
    argument the [opheld2] line passed, or against a number when the level is
    written into the label (the staff of Iban).  A quest gate calls a
    `~levelrequire_*_quest` proc whose message names the quest, and the label
    then hands off to the plain level gate, which is followed through.
    """
    blk = b.blocks.get(('label', label))
    if not blk or label in seen:
        return {}
    params = blk['params']

    def value(tok):
        tok = tok.strip()
        if not tok.startswith('$'):
            return _int(tok, None)
        name = tok[1:]
        i = params.index(name) if name in params else -1
        return args[i] if 0 <= i < len(args) else None

    req = {}
    for stat, bound in re.findall(r'stat_base\(([a-z]+)\)\s*<\s*(\$?[a-z_0-9]+)', blk['body']):
        level = value(bound)
        if level is not None:
            req[stat] = max(req.get(stat, 0), level)
    for proc in re.findall(r'~(levelrequire_[a-z_0-9]*quest)\b', blk['body']):
        quest = b.blocks.get(('proc', proc))
        m = re.search(r'You need to complete (?:the )?([^".]+)', quest['body']) if quest else None
        if m:
            req['quest'] = re.sub(r'\s+first$', '', m.group(1).strip())
    for nxt, nargs in re.findall(r'@(levelrequire_[a-z_0-9]+)\(([^)]*)\)', blk['body']):
        for stat, level in _gate(b, nxt, [value(a) for a in split_args(nargs)], seen + (label,)).items():
            if stat == 'quest':
                req.setdefault(stat, level)
            else:
                req[stat] = max(req.get(stat, 0), level)
    return req


def build_requirements(b):
    """{item -> {'attack': 40, ..., 'quest': '...'}} from the [opheld2] level gates."""
    out = {}
    for key, blk in b.blocks.items():
        if key[0] != 'opheld2':
            continue
        m = re.match(r'\s*@(levelrequire_[a-z_0-9]+)\(([^)]*)\)', blk['body'])
        if not m:
            continue
        req = _gate(b, m.group(1), [_int(a, None) for a in split_args(m.group(2))])
        if req:
            out[key[1]] = req
    return out


# ---------------------------------------------------------------- prayers

def build_prayers(b, constants):
    """[[varp, name, level, kind, multiplier]] for the prayers the fight maths reads.

    Which varp a prayer lives in is its place in the prayer book rather than its
    ^constant, so it is read from the prayer's own activate label, which names
    both: `~get_prayer_data(^prayer_rockskin)` next to `%prayer3`.
    """
    varp_of = {}
    for key, blk in b.blocks.items():
        if key[0] != 'label' or not key[1].startswith('activate_prayer_'):
            continue
        const = re.search(r'~get_prayer_data\(\^([a-z_0-9]+)\)', blk['body'])
        varp = re.search(r'%prayer(\d+)', blk['body'])
        if const and varp:
            varp_of[const.group(1)] = int(varp.group(1))
    names = {}
    for cfg in b.dbrow_cfg.values():
        if cfg['d'].get('table') != 'prayers':
            continue
        data = {}
        for v in cfg['multi'].get('data', []):
            k, _, val = v.partition(',')
            data.setdefault(k.strip(), []).append(val.strip())
        varp = varp_of.get(data.get('prayer', [''])[0].lstrip('^'))
        if varp is None:
            continue
        names[varp] = (data.get('name', [pretty(cfg['name'])])[0], _int(data.get('level', ['1'])[0], 1))
    out = []
    for proc, kind in (('check_attack_prayer', 'attack'), ('check_strength_prayer', 'strength'),
                       ('check_defence_prayer', 'defence')):
        blk = b.blocks.get(('proc', proc))
        for varp, mult in re.findall(r'%prayer(\d+) = \^true\) return \((\d+)\)', blk['body']):
            varp = int(varp)
            name, level = names.get(varp, ('Prayer %d' % varp, 1))
            out.append([varp, name, level, kind, int(mult)])
    blk = b.blocks.get(('proc', 'check_protect_prayer'))
    for style, varp in re.findall(r'\$style = \^([a-z_]+)[^)]*\) & %prayer(\d+) = \^true', blk['body']):
        varp = int(varp)
        name, level = names.get(varp, ('Prayer %d' % varp, 1))
        out.append([varp, name, level, 'protect_' + style.replace('_style', ''), 100])
    out.sort(key=lambda r: r[2])
    return out


# ---------------------------------------------------------------- monsters

class AttackWalker:
    """Weighted attack leaves of a monster's combat AI handler.

    Walks the handler like the engine would, following `@label` jumps and `~proc`
    calls, and splits the weight at every branch: `random(n)` conditions get
    their real odds, anything else is split evenly and the profile is marked
    approximate so the page can say so.
    """

    MAX_DEPTH = 8

    def __init__(self, blocks, spells):
        self.blocks = blocks
        self.spells = spells

    def _walk(self, key):
        self.leaves, self.approx, self.modes = {}, False, set()
        self.run(self.stmts(key), Fraction(1), 0, set(), False, {})
        return self.summary() if self.leaves else None

    def profile(self, npc_name, category=None):
        """([{'kind','model','spell','weight'}], approximate) with you in melee
        range, or None for the default melee handler.

        An npc's own handler wins over the one its category shares.
        """
        for name in ([npc_name] + (['_' + category] if category else [])):
            for trigger in ('ai_opplayer2', 'ai_applayer2'):
                key = (trigger, name)
                if key not in self.blocks:
                    continue
                got = self._walk(key)
                if got:
                    return got
                # an npc whose op handler only hands over to the ap handler
                # (witches, mages) has its attacks there instead
                if 'applayer2' not in self.modes:
                    break
        return None

    def range_profile(self, npc_name, category=None):
        """What it can still do once you are out of its reach, which for most
        monsters is nothing: reaching a player who is not adjacent needs an
        [ai_applayer2] handler, and only the ranged, the casters and two of the
        dragons have one.  The rest are the safespot.
        """
        for name in ([npc_name] + (['_' + category] if category else [])):
            key = ('ai_applayer2', name)
            if key in self.blocks:
                return self._walk(key)
        return None

    def stmts(self, key):
        blk = self.blocks.get(key)
        if not blk:
            return []
        if 'stmts' not in blk:
            blk['stmts'] = parse_statements(blk['body'])[0]
        return blk['stmts']

    def summary(self):
        total = sum(self.leaves.values())
        rows = []
        for (kind, model, spell), w in sorted(self.leaves.items(), key=lambda kv: str(kv[0])):
            rows.append({'kind': kind, 'model': model, 'spell': spell, 'weight': w / total})
        return rows, self.approx

    def emit(self, kind, model, spell, weight, guessed):
        key = (kind, model, spell)
        self.leaves[key] = self.leaves.get(key, Fraction(0)) + weight
        # only an attack actually reached through a branch we had to guess at
        # makes the profile approximate; a `if (...) return` guard does not
        if guessed:
            self.approx = True

    # ---- the walk

    def run(self, stmts, weight, depth, seen, guessed, variables):
        for st in stmts:
            if st[0] == 'stmt':
                self.run_stmt(st[1], weight, depth, seen, guessed, variables)
            elif st[0] == 'if':
                self.run_if(st[1], st[2], weight, depth, seen, guessed, variables)
            elif st[0] == 'switch':
                for _labels, body in st[2]:
                    self.run(body, weight / max(len(st[2]), 1), depth, seen, True, dict(variables))
            elif st[0] == 'while':
                self.run(st[2], weight, depth, seen, guessed, variables)

    def run_stmt(self, text, weight, depth, seen, guessed, variables):
        # a roll stashed in a local, so `if ($random_attack = 0)` can be read
        m = re.match(r'def_int \$([a-z0-9_]+) = random\((\d+)\)$', text.strip())
        if m:
            variables['$' + m.group(1)] = int(m.group(2))
            return
        m = re.search(r'npc_setmode\((applayer2|opplayer2)\)', text)
        if m:
            self.modes.add(m.group(1))
        for m in re.finditer(r'[~@]([a-z0-9_]+)|gosub\(([a-z0-9_]+)', text):
            name = m.group(1) or m.group(2)
            if name in SPELL_CALLS:
                args = self.call_args(text, name)
                spell = args[0].lstrip('^') if args else None
                speed = _int(args[1], None) if len(args) > 1 else None
                self.emit('magic', None, (spell, speed), weight, guessed)
                continue
            if name in ATTACK_CALLS:
                kind, model = ATTACK_CALLS[name]
                self.emit(kind, model, None, weight, guessed)
                continue
            if depth >= self.MAX_DEPTH or name in seen:
                continue
            for trigger in ('proc', 'label'):
                key = (trigger, name)
                if key in self.blocks:
                    self.run(self.stmts(key), weight, depth + 1, seen | {name}, guessed, {})
                    break

    def call_args(self, text, name):
        i = text.find('~' + name)
        if i < 0:
            return []
        j = text.find('(', i)
        if j < 0:
            return []
        depth, k = 0, j
        while k < len(text):
            if text[k] == '(':
                depth += 1
            elif text[k] == ')':
                depth -= 1
                if depth == 0:
                    return split_args(text[j + 1:k])
            k += 1
        return []

    def run_if(self, chain, else_block, weight, depth, seen, guessed, variables):
        left = weight
        branch_guessed = guessed
        floor = {}          # variable -> the `<` bound an earlier branch already took
        for cond, body in chain:
            roll = self.odds(cond, variables)
            if roll is None:
                take = left * Fraction(1, 2)
                branch_guessed = True
            elif roll[0]:
                # every branch tests the same roll, so the odds are of the whole
                # weight, and a `<` chain takes only the slice above the last one
                var, op, n, k = roll
                if op == '=':
                    take = weight * Fraction(1, n)
                else:
                    take = weight * Fraction(max(min(k, n) - floor.get(var, 0), 0), n)
                    floor[var] = max(min(k, n), floor.get(var, 0))
            else:
                # a fresh roll each time, so these odds are of what is left
                _var, op, n, k = roll
                take = left * (Fraction(1, n) if op == '=' else Fraction(min(k, n), n))
            take = max(min(take, left), Fraction(0))
            self.run(body, take, depth, seen, branch_guessed, dict(variables))
            left -= take
        if else_block:
            self.run(else_block, left, depth, seen, branch_guessed, dict(variables))

    @staticmethod
    def odds(cond, variables):
        """(variable, operator, sides, bound) for an `if` that tests a die roll,
        either written out or through a local it was just assigned to."""
        cond = cond.strip()
        m = re.fullmatch(r'(random\((\d+)\)|\$[a-z0-9_]+)\s*([=<])\s*(\d+)', cond)
        if not m:
            return None
        var = None if m.group(2) else m.group(1)
        n = int(m.group(2)) if m.group(2) else variables.get(var)
        if not n or n <= 0:
            return None
        return var, m.group(3), n, int(m.group(4))


def build_spells(b, constants):
    """{spell constant name -> {'name', 'maxhit'}} from the combat spell dbrows."""
    out = {}
    for cfg in b.dbrow_cfg.values():
        if cfg['d'].get('table') != 'magic_spell_table':
            continue
        data = {}
        for v in cfg['multi'].get('data', []):
            k, _, val = v.partition(',')
            data.setdefault(k.strip(), []).append(val.strip())
        spell = data.get('spell', [None])[0]
        if not spell:
            continue
        out[spell.lstrip('^')] = {'name': data.get('name', [pretty(cfg['name'])])[0],
                                  'maxhit': _int(data.get('maxhit', ['0'])[0], 0)}
    return out


def build_regen(b):
    """[ticks, hitpoints] the health_regen timer restores, and the same under Rapid Heal.

    `[timer,health_regen]` heals a flat amount; how often is the interval the
    login script arms it with, and the Rapid Heal prayer re-arms it faster.
    """
    timer = b.blocks.get(('timer', 'health_regen'))
    m = re.search(r'stat_heal\(hitpoints,\s*(\d+)', timer['body']) if timer else None
    amount = int(m.group(1)) if m else 1
    # the slowest arming is the ordinary one; Rapid Heal re-arms it faster
    ticks = 0
    for blk in b.blocks.values():
        for v in re.findall(r'settimer\(health_regen,\s*(\d+)\)', blk['body']):
            ticks = max(ticks, int(v))
    if not ticks:
        raise SystemExit('gear: no settimer(health_regen, ...) found - the regen rate needs updating')
    return [ticks, amount]


def _attack_entry(r, spells):
    """One weighted attack of a monster's profile, as the pages read it."""
    e = {'k': r['kind'], 'w': round(float(r['weight']), 6)}
    if r['model']:
        e['m'] = r['model']
    if r['spell']:
        name, speed = r['spell']
        sp = spells.get(name)
        e['sp'] = sp['name'] if sp else pretty(name or 'spell')
        e['mh'] = sp['maxhit'] if sp else 0
        if speed:
            e['d'] = speed
    return e


def _mark_variants(items, cfgnames):
    """Fold away the copies of an item these pages cannot tell apart.

    The content names a variant after the item it copies: a gold-trimmed rune
    platebody is `rune_platebody_gold`, a Saradomin one is
    `rune_platebody_saradomin`, a poisoned iron dagger is `iron_dagger_p`.  So
    the first and best test is that naming -- an item whose config name is
    another item's config name plus a suffix, and which reads identically to it
    on every number these pages use, is that item in another colour and folds
    into it.  God armour is exactly this: the four platebodies of Saradomin,
    Guthix, Zamorak and rune are one item with four recolours, down to the
    weight and the 65,000gp.

    Taking the base from the config name rather than guessing at the display
    names also gets the survivor right.  Picking the shortest name instead left
    the trimmed rune kiteshields folded into "Saradomin kite", which is two
    characters shorter than "Rune kiteshield" and not the item anybody means.

    A second pass catches what the naming does not: within a stats group, items
    whose display names differ only by a parenthetical fold together -- the
    seven coloured capes, the eighteen chompy bird hats, the eight rings of
    dueling.  It has to go by that stem and not by the stats alone, because a
    stats group is only as specific as its numbers: every item with no bonuses
    at all shares one, so a ring of dueling would otherwise fold into a sapphire
    ring and chompy hats into party hats.

    Every pass needs the two items to read identically on the same terms: the
    slot, covered slots, category, attack rate, level gate, requirements and all
    13 bonuses.  The requirements have to be in there -- a Hazeel Cult death
    dagger has exactly the stats of a black dagger and needs no attack level for
    it, so leaving them out folds the real black dagger into a quest item -- and
    what the pages would treat differently is kept whatever its name, so a
    silver sickle(b) (+5 prayer) and a bronze spear(p) (a different stab bonus)
    both stay.  A group of nothing but parenthetical names (rings of dueling,
    which differ only in charges left) keeps its first member rather than
    vanishing entirely.
    """
    def reads(iid):
        rec = items[iid]
        return (rec['s'], tuple(rec.get('c', ())), rec['r'], rec.get('lr', 0), tuple(rec['b']),
                json.dumps(rec.get('req'), sort_keys=True), rec.get('w', 0),
                rec.get('cat') if rec['s'] == WEARPOS.index('righthand') else None)

    folded = 0

    # 1. a config name that is another item's plus a suffix
    by_cfg = {}
    for iid, name in cfgnames.items():
        by_cfg.setdefault(name, iid)
    for iid, name in cfgnames.items():
        parts = name.split('_')
        for cut in range(len(parts) - 1, 0, -1):
            base = by_cfg.get('_'.join(parts[:cut]))
            if base is None or base == iid:
                continue
            if reads(base) == reads(iid):
                items[iid]['dup'] = base
                folded += 1
            break
    # a variant of a variant points at whatever survived
    for iid in items:
        seen = set()
        while items[iid].get('dup') in items and items[items[iid]['dup']].get('dup') is not None:
            if items[iid]['dup'] in seen:
                break
            seen.add(items[iid]['dup'])
            items[iid]['dup'] = items[items[iid]['dup']]['dup']

    # 2. what the naming missed: same stats, and a display name that differs
    #    only by a parenthetical
    def stem(name):
        return re.sub(r'\s*\([^)]*\)\s*$', '', name).strip().lower()

    groups = {}
    for iid in items:
        if 'dup' not in items[iid]:
            groups.setdefault((reads(iid), stem(items[iid]['n'])), []).append(iid)
    for ids in groups.values():
        if len(ids) < 2:
            continue
        plain = [i for i in ids if '(' not in items[i]['n']]
        keep = min(plain or ids)                      # the base item is the older id
        for i in ids:
            if i != keep:
                items[i]['dup'] = keep
                folded += 1
    return folded


def _disambiguate(items, cfgnames):
    """Qualify the items still sharing a display name once the copies are folded.

    What is left differs in ways these pages do read, so it cannot be folded and
    must not look alike either.  The content calls all four dragonhide bodies
    "Dragonhide body" and tells them apart by config name and recolour, so the
    picker would otherwise offer four identical-looking rows needing ranged 40,
    50, 60 and 70.  The qualifier is whatever the config name says that the
    display name does not: black_dragonhide_body -> "Dragonhide body (black)".
    """
    groups = {}
    for iid, rec in items.items():
        if not rec.get('dup'):
            groups.setdefault(rec['n'], []).append(iid)
    renamed = 0
    for name, ids in groups.items():
        if len(ids) < 2:
            continue
        said = set(re.findall(r'[a-z]+', name.lower()))
        # tokens every member carries say nothing about which one this is
        shared = set.intersection(*[set(cfgnames[i].split('_')) for i in ids])
        said |= shared
        # a monk's robe top and bottom share their name and differ only in where
        # they go, and the slot says that far better than the config name does
        slots = [items[i]['s'] for i in ids]
        by_slot = len(set(slots)) == len(slots)
        for iid in ids:
            if by_slot:
                extra = [dict(SLOTS).get(items[iid]['s'], '').lower()]
            else:
                extra = [w for w in cfgnames[iid].split('_') if w not in said]
            if extra and extra[0]:
                items[iid]['n'] = '%s (%s)' % (name, ' '.join(extra))
                renamed += 1
    return renamed


def _check_breath(b):
    """Re-read the dragonfire numbers out of their procs, so the table cannot go stale."""
    for key, spec in sorted(BREATHS.items()):
        blk = b.blocks.get(('proc', spec['proc']))
        if not blk:
            raise SystemExit('gear: [proc,%s] not found - the %s breath model needs updating'
                             % (spec['proc'], key))
        for pattern in spec['checks']:
            if not re.search(pattern, blk['body']):
                raise SystemExit('gear: %s no longer matches [proc,%s] (%s) - update BREATHS in build/gear.py'
                                 % (pattern, spec['proc'], blk['file']))


def _check_ammo_rule(b):
    """Re-read the bow/ammo gate the pages model, so it cannot go stale.

    [proc,player_ranged_check_ammo] returns null in four cases and the caller
    does `p_stopaction`, so a shot that fails any of them simply never happens.
    CB.canFire in static/combat.js is these four checks.
    """
    blk = b.blocks.get(('proc', 'player_ranged_check_ammo'))
    if not blk:
        raise SystemExit('gear: [proc,player_ranged_check_ammo] not found - the ammo model needs updating')
    checks = [r'\$ammo\s*=\s*null',
              r'\$rhand\s*=\s*ogre_bow\s*&\s*\$ammo_cat\s*!\s*ogre_arrows',
              r'\$weapon_cat\s*=\s*weapon_bow\s*&\s*\$ammo_cat\s*!\s*arrows',
              r'\$weapon_cat\s*=\s*weapon_crossbow\s*&\s*\$ammo_cat\s*!\s*bolts',
              r'oc_param\(\$ammo,\s*levelrequire\)\s*>\s*oc_param\(\$rhand,\s*levelrequire\)']
    for pattern in checks:
        if not re.search(pattern, blk['body']):
            raise SystemExit('gear: %s no longer matches [proc,player_ranged_check_ammo] (%s) - update '
                             'canFire in build/static/combat.js' % (pattern, blk['file']))


# ---------------------------------------------------------------- the build

def build_gear_data(b):
    """Write site/data/gear.js.  Returns a one-line summary for the build log."""
    from tables import load_constants
    constants = load_constants()
    _check_breath(b)
    _check_ammo_rule(b)

    cat2table, style_tables, style_bonuses, default_style_bonus = build_styles(b, constants)
    reqs = build_requirements(b)
    prayers = build_prayers(b, constants)
    regen = build_regen(b)
    spells = build_spells(b, constants)

    # ---- items
    items = {}
    cfgnames = {}
    placeholders = 0
    for it in b.items.values():
        if it['id'] is None or it['dummy'] or not it['wearpos']:
            continue
        # a handful of unfinished items carry 255 in every bonus as a placeholder.
        # They are not real gear, and left in they win every comparison outright.
        if all(_int(it['params'].get(k, 0)) == 255 for k in BONUS_KEYS):
            placeholders += 1
            continue
        slot = WEARPOS.index(it['wearpos']) if it['wearpos'] in WEARPOS else None
        if slot is None:
            continue
        cfg = b.obj_cfg[it['name']]
        covers = [WEARPOS.index(cfg['d'][k]) for k in ('wearpos2', 'wearpos3')
                  if cfg['d'].get(k) in WEARPOS]
        bonus = [_int(it['params'].get(k, 0)) for k in BONUS_KEYS]
        rec = {'n': it['display'], 's': slot, 'u': it['url'],
               'b': bonus, 'r': _int(it['params'].get('attackrate', 4), 4)}
        if covers:
            rec['c'] = covers
        if it['category']:
            rec['cat'] = it['category']
        if it['members']:
            rec['m'] = 1
        if b.has_icon(it):
            rec['ic'] = 1
        if it['name'] in reqs:
            rec['req'] = reqs[it['name']]
        lr = _int(it['params'].get('levelrequire', 0))
        if lr:
            rec['lr'] = lr
        reach = _int(it['params'].get('attackrange', 0))
        if reach:
            rec['ar'] = reach        # ~player_attackrange, +2 on longrange
        weight = _weight_grams(it['weight'])
        if weight:
            rec['w'] = weight        # grams; what a set weighs is a running cost
        cost = _int(it['cost'])
        if cost:
            rec['v'] = cost
        if it['name'] == OGRE_BOW:
            rec['oa'] = 1            # fires ogre arrows and refuses ordinary ones
        items[it['id']] = rec
        cfgnames[it['id']] = it['name']
    _mark_variants(items, cfgnames)
    renamed = _disambiguate(items, cfgnames)

    # ---- monsters
    walker = AttackWalker(b.blocks, spells)
    monsters = {}
    approx_count = 0
    for n in b.npc_by_id.values():
        if not n['hp'] or 'Attack' not in n['ops'] or n['hidden']:
            continue
        p = n['params']
        stats = {'hitpoints': _int(n['hp']), 'attack': _int(n['att']), 'strength': _int(n['str']),
                 'defence': _int(n['def'])}
        cfg = b.npc_cfg[n['name']]
        for k in ('magic', 'ranged'):
            stats[k] = _int(cfg['d'].get(k, 0))
        damagetype = constants.get(str(p.get('damagetype', '')).lstrip('^'), _int(p.get('damagetype', 2), 2))
        prof = walker.profile(n['name'], n['category'])
        at_range = walker.range_profile(n['name'], n['category'])
        if prof is None:
            prof = ([{'kind': 'melee', 'model': None, 'spell': None, 'weight': Fraction(1)}], False)
        rows, approx = prof
        approx_count += 1 if approx else 0
        atk = [_attack_entry(r, spells) for r in rows]
        monsters[n['id']] = {
            'n': n['display'], 'lv': n['level'], 'u': n['url'],
            'st': [stats['attack'], stats['strength'], stats['defence'], stats['magic'],
                   stats['ranged'], stats['hitpoints']],
            'b': [_int(p.get(k, 0)) for k in NPC_BONUSES],
            'r': _int(p.get('attackrate', 4), 4),
            'dt': damagetype,
            'md': _int(p.get('max_dealt', 1000), 1000),
            'atk': atk,
            'ic': 1 if b.has_npc_icon(n) else 0,
        }
        if at_range:
            monsters[n['id']]['ap'] = [_attack_entry(r, spells) for r in at_range[0]]
        if approx:
            monsters[n['id']]['aprx'] = 1
        if n['category']:
            monsters[n['id']]['c'] = n['category']

    shield = b.items.get('antidragonbreathshield')
    data = {
        'rev': b.rev_text,
        # the shield every dragonfire proc checks for, and the 4 ticks an
        # empty-handed swing takes ([label,player_melee_attack])
        'antifireShield': shield['id'] if shield else None,
        'unarmedRate': 4,
        # [timer,health_regen]: this many hitpoints back every this many ticks
        'regen': regen,
        'slots': [[i, WEARPOS[i], label] for i, label in SLOTS],
        'wearpos': WEARPOS,
        'bonusKeys': BONUS_KEYS,
        'bonusLabels': BONUS_LABELS,
        'damagetypes': DAMAGETYPES,
        'catStyle': cat2table,
        'styles': style_tables,
        'styleBonus': {str(k): v for k, v in sorted(style_bonuses.items())},
        'styleBonusDefault': default_style_bonus,
        'unarmed': 'weapon_unarmed_table',
        'prayers': prayers,
        'breaths': {k: {kk: vv for kk, vv in v.items() if kk != 'checks'} for k, v in BREATHS.items()},
        'items': items,
        'monsters': monsters,
    }
    path = os.path.join(common.SITE, 'data', 'gear.js')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('window.GEAR = ' + json.dumps(data, separators=(',', ':')) + ';\n')
    dups = sum(1 for r in items.values() if 'dup' in r)
    reach = sum(1 for m in monsters.values() if 'ap' in m)
    weighed = sum(1 for r in items.values() if 'w' in r)
    return ('gear: %d equippable items (%d indistinguishable copies folded away, %d placeholders dropped, '
            '%d same-named ones qualified, %d weighed), '
            '%d monsters (%d with an approximated attack profile, %d that can reach a safespot), '
            '%d weapon style tables, %d hp per %d ticks'
            % (len(items), dups, placeholders, renamed, weighed, len(monsters), approx_count, reach,
               len(style_tables), regen[1], regen[0]))
