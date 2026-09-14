"""Machine-readable tables of the Lost City world: site/data/tables/*.json.

Every value here is derived from the Content checkout the build was pointed at
(see build.py --content).  Nothing is typed from memory of the game; when a
referenced item/npc/loc is missing from the content the generator raises.
"""
import os
import re
import json
import math
import glob
import time
import itertools
import subprocess
from collections import defaultdict, Counter

import common
from common import SITE, parse_config_files, pretty, read_text, relpath
from drops import prob_text
from portals import parse_coord

OUT = os.path.join(SITE, 'data', 'tables')
SKILLS = ('attack', 'defence', 'strength', 'hitpoints', 'ranged', 'prayer', 'magic', 'cooking', 'woodcutting',
          'fletching', 'fishing', 'firemaking', 'crafting', 'smithing', 'mining', 'herblore', 'agility',
          'thieving', 'runecraft')
XP_DIV = 10.0   # the content stores xp in tenths (250 = 25 xp)
# how a recipe is started, which is not the same question as where it is done: most recipes
# have no station at all, and these are the mechanisms hiding behind that empty field.
# `skill` is a trained skill, or 'none' for a step nothing trains: picking a field, joining
# two key halves, pulling the legs off a toad.  A consumer needs the row either way.
RECIPE_SKILLS = ('woodcutting', 'mining', 'smithing', 'cooking', 'fletching', 'firemaking',
                 'crafting', 'fishing', 'herblore', 'magic', 'none')
DRIVERS = ('op_loc',            # click the tree, the rock, the fishing spot loc
           'op_npc',            # click the NPC (a fishing spot is an NPC)
           'op_item',           # click the item in your pack (identifying a herb)
           'use_item_on',       # use one item on another, and it just happens
           'use_item_on_loc',   # use an item on a wheel, a fire, a furnace, a sand pit
           'chat_picker',       # a menu of options appears; see the recipe's `picker`
           'interface')         # a full interface opens


def load_constants():
    """Every numeric ^constant the scripts define, so a condition can be read literally."""
    out = {'true': 1, 'false': 0}
    for path in glob.glob(os.path.join(common.SCRIPTS, '**', '*.constant'), recursive=True):
        for line in read_text(path).splitlines():
            m = re.match(r'^\^([a-z_0-9]+)\s*=\s*(-?\d+)\s*$', line.strip())
            if m:
                out[m.group(1)] = int(m.group(2))
    return out


class Gen:
    def __init__(self, b):
        self.b = b
        self.items = b.items
        self.npcs = b.npcs
        self.locs = b.loc_cfg
        self.blocks = b.blocks
        self.loc_name2id = b.loc_name2id
        self.loc_id2name = b.loc_id2name
        self.placements = self._placements()
        self.hunt_cfg = parse_config_files('hunt')
        self.struct_cfg = parse_config_files('struct')
        self.obj_cfg = b.obj_cfg
        self.respawn = (0, 3221, 3218)
        self.constants = load_constants()

    # ------------------------------------------------------------ helpers
    def fail(self, msg):
        raise SystemExit('tables: ' + msg)

    def item(self, name, where):
        if name not in self.items:
            self.fail('unknown item %r referenced by %s' % (name, where))
        return name

    def npc(self, name, where):
        if name not in self.npcs:
            self.fail('unknown npc %r referenced by %s' % (name, where))
        return name

    def loc(self, name, where):
        if name not in self.locs:
            self.fail('unknown loc %r referenced by %s' % (name, where))
        return name

    def _placements(self):
        """loc debugname -> [(level, x, z, angle)]"""
        out = defaultdict(list)
        for sq in self.b.squares.values():
            bx, bz = sq.mx << 6, sq.mz << 6
            for (level, x, z, lid, shape, angle) in sq.locs:
                name = self.loc_id2name.get(lid)
                if name:
                    out[name].append((level, bx + x, bz + z, angle))
        return out

    def dbrows(self, table):
        rows = []
        for name, cfg in sorted(self.b.dbrow_cfg.items()):
            if cfg['d'].get('table') != table:
                continue
            d = defaultdict(list)
            for k, v in cfg['kv']:
                if k == 'data' and ',' in v:
                    dk, dv = v.split(',', 1)
                    d[dk.strip()].append(dv.strip())
            rows.append((name, d, cfg['file']))
        return rows

    def body(self, kind, name):
        return self.blocks.get((kind, name), {}).get('body', '')

    def held_headers(self, relfile):
        """Non-category [opheldu,X] handlers defined in a script file (X = the item being used)."""
        return [k[1] for k, bl in self.blocks.items() if bl['file'] == relfile and k[0] == 'opheldu' and not k[1].startswith('_')]

    def checked_items(self, text):
        return [x for x in re.findall(r'inv_total\(inv,\s*([a-z_0-9]+)\)', text) if x in self.items]

    def struct_users(self, param):
        """struct debugname -> the items whose <param> points at it.

        A struct-driven recipe family names its rows from the objs that take part in it:
        cooking_apple carries param=uncooked_pie_struct,uncooked_apple_pie.  Which side of
        the recipe the obj is on (ingredient or product) is up to the caller -- the content
        uses both -- but the link itself is always this param.
        """
        out = defaultdict(list)
        for iname, it in sorted(self.items.items()):
            s = it['params'].get(param)
            if not s:
                continue
            if s not in self.struct_cfg:
                self.fail('%s on %s names an unknown struct %r' % (param, iname, s))
            out[s].append(iname)
        if not out:
            self.fail('no obj carries the param %r' % param)
        return out

    @staticmethod
    def call_args(text, fn):
        """[[arg, ...], ...] for every fn(...) in text, keeping nested calls in one piece."""
        out = []
        for m in re.finditer(r'(?<![a-z_0-9])%s\s*\(' % re.escape(fn), text):
            i, depth, args, cur = m.end(), 1, [], ''
            while i < len(text):
                c = text[i]
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        break
                if depth == 1 and c == ',':
                    args.append(cur.strip())
                    cur = ''
                else:
                    cur += c
                i += 1
            args.append(cur.strip())
            out.append(args)
        return out

    def _cap(self, expr, body, seen=()):
        """(most this expression can be, note), or (None, ...) when only the pack limits it.

        Counts are written as arithmetic over inv_total: an ogre arrow batch is
        "divide(min(inv_total(inv, feather), 24), 4)", i.e. six, not twenty-four.
        """
        expr = expr.strip()
        if expr.isdigit():
            return int(expr), None
        if expr.startswith('$'):
            if expr in seen:
                return None, None
            m = re.search(r'def_int\s+%s\s*=\s*([^\n;]+)' % re.escape(expr), body)
            return self._cap(m.group(1), body, seen + (expr,)) if m else (None, None)
        r = re.match(r'~random_range\(\s*(\d+)\s*,\s*(\d+)\s*\)$', expr)
        if r:
            return int(r.group(1)), 'a random %s-%s per action' % (r.group(1), r.group(2))
        for fn in ('min', 'max', 'divide', 'multiply'):
            if not expr.startswith(fn + '('):
                continue
            vals, notes = [], []
            for a in self.call_args(expr, fn)[0]:
                v, nt = self._cap(a, body, seen)
                vals.append(v)
                if nt:
                    notes.append(nt)
            note = notes[0] if notes else None
            known = [v for v in vals if v is not None]
            if fn in ('min', 'max'):
                return ((min if fn == 'min' else max)(known) if known else None), note
            if len(vals) == 2 and vals[0] is not None and vals[1]:
                return (vals[0] // vals[1] if fn == 'divide' else vals[0] * vals[1]), note
            return None, note
        return None, None                       # inv_total and friends: however many you carry

    def batch_cap(self, expr, body):
        """(n, note) for the count an inv_add moves: a literal, or the cap on its local."""
        if expr.strip().isdigit():
            return int(expr.strip()), None          # a fixed number, not a ceiling
        n, note = self._cap(expr, body)
        if n is None:
            return 1, None
        return n, note or ('up to %d per action' % n if n > 1 else None)

    def bit_recipes(self, proc):
        """([(inputs, product, xp)...], fallback) from a proc that settles a packed bitfield.

        The gnome dishes count what went in four bits per ingredient: the add_* label sets
        "case cheese : ... setbit_range(..., 4, 7)", the proc reads
        "$cheese_count = getbit_range(..., 4, 7)" and walks an if-chain of exact counts.
        Matching on the bit ranges pairs ingredient with counter without trusting the names.
        """
        bl = self.blocks.get(('proc', proc))
        if not bl:
            self.fail('no [proc,%s]' % proc)
        text = self.live_code(self.script(bl['file']))
        item_bits = {}
        for m in re.finditer(r'case\s+([a-z_0-9]+)\s*:\s*%([a-z_0-9]+)\s*=\s*setbit_range_toint\(%[a-z_0-9]+,\s*add\(getbit_range\(%[a-z_0-9]+,\s*(\d+),\s*(\d+)\)', text):
            if m.group(1) in self.items:
                item_bits[(m.group(2), int(m.group(3)), int(m.group(4)))] = m.group(1)
        body = self.live_code(bl['body'])
        var_item = {}
        for m in re.finditer(r'def_int\s+\$([a-z_0-9]+)\s*=\s*getbit_range\(%([a-z_0-9]+),\s*(\d+),\s*(\d+)\)', body):
            key = (m.group(2), int(m.group(3)), int(m.group(4)))
            if key in item_bits:
                var_item[m.group(1)] = item_bits[key]
        if not var_item:
            self.fail('[proc,%s]: no counter matches an ingredient' % proc)
        out = []
        for m in re.finditer(r'if\s*\(([^{]*?\$total\s*=\s*\d+[^{]*?)\)\s*\{(.*?)^\}', body, re.S | re.M):
            conds = {v: int(n) for v, n in re.findall(r'\$([a-z_0-9]+)\s*=\s*(\d+)', m.group(1))}
            total = conds.pop('total', None)
            r = re.search(r'return\(([a-z_0-9+]+)\)', m.group(2))
            if not r or r.group(1) not in self.items:
                continue
            for v in conds:
                if v not in var_item:
                    self.fail('[proc,%s]: counter $%s has no ingredient' % (proc, v))
            if sum(conds.values()) != total:
                self.fail('[proc,%s]: counts for %s do not add up to $total' % (proc, r.group(1)))
            xp = re.search(r'stat_advance\(cooking,\s*(\d+)\)', m.group(2))
            out.append(([(var_item[v], n) for v, n in conds.items()], r.group(1), int(xp.group(1)) / XP_DIV if xp else 0))
        if not out:
            self.fail('[proc,%s]: no exact-count branch returns an item' % proc)
        fb = re.findall(r'^return\(([a-z_0-9+]+)\);', body, re.M)
        return out, (fb[-1] if fb and fb[-1] in self.items else None)

    def live_code(self, text):
        """The text with its // comments dropped, so a commented-out older version is not read."""
        return '\n'.join(self.uncomment(ln) for ln in text.splitlines())

    def mentions(self):
        """name -> the blocks that jump to or name it, for walking back to a handler."""
        if getattr(self, '_mentions', None) is None:
            m = defaultdict(set)
            for k, b in self.blocks.items():
                for n in set(re.findall(r'[@,]\s*([a-z_][a-z_0-9]*)', b['body'])):
                    m[n].add(k)
            self._mentions = m
        return self._mentions

    def entry_kinds(self, label):
        """The handler kinds that can reach a label: opheldu, oploc1, opnpc1, opheld1..."""
        ment, seen, todo, kinds = self.mentions(), set(), [label], set()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            for k in ment.get(n, ()):
                if k[0] == 'label' or k[0] == 'proc':
                    todo.append(k[1])
                elif k[0].startswith('op'):
                    kinds.add(k[0])
        return kinds

    def driver_for(self, label, station=None):
        """(driver, picker) for a recipe written as a label: what the player actually does."""
        kind, picker = self.menu(label)
        if kind:
            return kind, picker
        kinds = self.entry_kinds(label)
        if any(k.startswith('oploc') for k in kinds):
            return 'use_item_on_loc', None
        if any(k.startswith('opnpc') for k in kinds):
            return 'op_npc', None
        if 'opheldu' in kinds:
            return 'use_item_on', None
        if any(k.startswith('opheld') for k in kinds):
            return 'op_item', None
        if station:
            return 'use_item_on_loc', None
        self.fail('[label,%s]: nothing reaches it, so there is no way to start it' % label)

    def menu(self, label, bound=None):
        """('chat_picker', {...}) / ('interface', None) / (None, None) for what a label opens.

        Station-less recipes are driven three different ways that look identical in the row --
        one item used straight on another, a chat menu of choices, or a full interface -- so
        this reads the block to tell them apart.  For a menu it also returns the option labels,
        which are the only handle a caller has on it: the icons beside them are live 3D models.
        Labels built at runtime are resolved against <bound>, the item the label was called on.
        """
        body = self.body('label', label)
        if not body:
            self.fail('no [label,%s] to read a driver from' % label)
        body = self.live_code(body)     # an older menu is often left commented out above the real one
        m = re.search(r'~((?:multiobj|p_choice)\d[a-z_]*)\s*\(', body)
        if m:
            args = self.call_args(body, '~' + m.group(1))[0]
            strings = [a for a in args if a.startswith('"') and a.endswith('"')]
            if not strings:
                self.fail('[label,%s]: %s with no option labels' % (label, m.group(1)))
            head = m.group(1)
            # *_header puts the question last, the plain forms put it first
            opts = strings[:-1] if head.endswith('_header') else strings[1:]
            return 'chat_picker', {'choices': len(opts), 'labels': [self.resolve_label(s[1:-1], bound) for s in opts]}
        if 'if_open' in body:
            return 'interface', None
        return None, None

    def resolve_label(self, text, bound):
        """A menu label with its <...> substitutions filled in for one item, where we can.

        "<~string_removeright(oc_name($log), 5)> Short Bow." is "Oak Short Bow." for oak logs:
        the display name with the trailing " logs" cut off.
        """
        def sub(m):
            inner = m.group(1)
            r = re.match(r'~string_removeright\(\s*oc_name\(\$[a-z_0-9]+\)\s*,\s*(\d+)\s*\)$', inner)
            if r and bound:
                return self.items[bound]['display'][:-int(r.group(1))]
            if re.match(r'oc_name\(\$[a-z_0-9]+\)$', inner) and bound:
                return self.items[bound]['display']
            if re.match(r'lowercase\(oc_name\(\$[a-z_0-9]+\)\)$', inner) and bound:
                return self.items[bound]['display'].lower()
            return m.group(0)                       # leave anything else visible as a template
        return re.sub(r'<([^<>]*)>', sub, text)

    def literal_dels(self, rel, label):
        """The fixed items a [label,X] consumes; the ones it reads off a struct row vary."""
        bl = self.blocks.get(('label', label))
        if not bl or bl['file'] != rel:
            self.fail('%s: no [label,%s] block' % (rel, label))
        return [a[1] for a in self.call_args(bl['body'], 'inv_del')
                if a[0] == 'inv' and len(a) > 2 and a[1] in self.items]

    def use_pairs(self, label):
        """(a, b) for every "use a on b" that reaches this label.

        A label is linked across files -- stew.rs2 owns make_incomplete_stew but cooked_meat.rs2
        jumps to it too -- so this looks at every handler: X in [opheldu,X] is one half and the
        objs its cases dispatch on are the other.  The pair is sorted, since using a knife on a
        log and a log on a knife are the same recipe.
        """
        out = set()
        for k, bl in sorted(self.blocks.items()):
            if not k[0].startswith('opheld') or k[1] not in self.items or '@' + label not in bl['body']:
                continue
            for m in re.finditer(r'case\s+([a-z_0-9, ]+?)\s*:\s*@%s\b' % re.escape(label), bl['body']):
                for c in m.group(1).split(','):
                    if c.strip() in self.items:
                        out.add(tuple(sorted((k[1], c.strip()))))
        return sorted(out)

    def callers_of(self, label):
        """Every item that takes part in a "use on" reaching this label."""
        return sorted({x for pair in self.use_pairs(label) for x in pair})

    def combine(self, rel, label, skill, product, dynamic=None, also=(), kind='label', **over):
        """Recipes the content writes out longhand as a [label,X] instead of a config row.

        The inv_del calls in the block are the inputs, the stat(<skill>) guard is the level,
        stat_advance is the xp and the leftover inv_add calls are byproducts (the emptied pot
        a dough hands back).  <product> only picks which inv_add is the recipe's point -- pass
        several when one block offers a choice, and every number still comes out of the block.

        An input the block reads out of a slot (def_obj $x = inv_getobj(...)) is filled from
        the handlers that jump here, or from <dynamic> (a list per such input) when the
        script gets it some other way; each possibility becomes its own recipe.  A block that
        stops adding the product, or an input that cannot be filled, fails the build.
        """
        bl = self.blocks.get((kind, label))
        if not bl or bl['file'] != rel:
            self.fail('%s: no [%s,%s] block' % (rel, kind, label))
        body = bl['body']
        adds = {a[1]: a[2] for a in self.call_args(body, 'inv_add') if a[0] == 'inv' and len(a) > 2}
        # what a failed roll hands you instead: "if (stat_random(...) = false) { ... }"
        spoiled = sorted({a[1] for m in re.finditer(r'stat_random\([^)]*\)\s*=\s*false\s*\)\s*\{(.*?)^\}', body, re.S | re.M)
                          for a in self.call_args(m.group(1), 'inv_add') if a[0] == 'inv' and a[1] in self.items})
        products = [product] if isinstance(product, str) else list(product)
        counts = {}
        for p in products:
            self.item(p, label)
            if p in adds:
                counts[p] = adds[p]
            else:                                   # added through a local: "$choice = pizza_base"
                held = [v for v, expr in adds.items()
                        if v.startswith('$') and re.search(r'%s\s*=\s*%s\b' % (re.escape(v), re.escape(p)), body)]
                if not held:
                    self.fail('[label,%s] does not inv_add %s (it adds %s)' % (label, p, ', '.join(sorted(adds)) or 'nothing'))
                counts[p] = adds[held[0]]
        fixed, holes = [], []
        for a in self.call_args(body, 'inv_del'):
            if a[0] != 'inv' or len(a) < 3:
                continue
            if a[1] in self.items:
                fixed.append((a[1], a[2].strip()))
            elif a[1].startswith('$') or a[1].startswith('inv_getobj('):
                holes.append(a[2].strip())
        if not fixed and not holes:
            self.fail('[label,%s] deletes nothing, so it is not a recipe' % label)
        known = {i for i, _ in fixed} | set(products)
        if dynamic is None:
            if len(holes) > 1:
                # both halves come out of a slot, so only the pairings the handlers actually
                # offer are real: potato goes into a meatless stew, cooked meat into a meaty one
                fills = [tuple(p) for p in self.use_pairs(label) if not (set(p) & known)]
                if len(holes) != 2 or not fills:
                    self.fail('[label,%s]: cannot pair up its %d slot inputs' % (label, len(holes)))
            else:
                fills = [(x,) for x in self.callers_of(label) if x not in known]
        else:
            if len(dynamic) != len(holes):
                self.fail('[label,%s] reads %d item(s) from a slot but %d were supplied' % (label, len(holes), len(dynamic)))
            fills = [c for c in itertools.product(*dynamic) if len(set(c)) == len(c) and not (set(c) & known)]
        if not fills and holes:
            self.fail('[label,%s]: cannot tell what it is combined with' % label)

        def count_for(per, made, batch):
            # n is what ONE action consumes, for produces_n made -- a consumer multiplies
            # from there, so "15 feathers + 15 shafts -> 15" and never "1 + 1 -> 15"
            if per == made:
                return batch                                    # deleted by the same count as added
            if per.isdigit():
                return int(per)
            m = re.match(r'multiply\(\s*%s\s*,\s*(\d+)\s*\)$' % re.escape(made), per)
            return int(m.group(1)) * batch if m else 1

        lv = [int(x) for x in re.findall(r'stat\(%s\)\s*<\s*(\d+)' % skill, body)]
        xp = re.search(r'stat_advance\(%s,\s*multiply\(\$[a-z_0-9]+,\s*(\d+)\)\)' % skill, body) \
            or re.search(r'stat_advance\(%s,\s*(\d+)\)' % skill, body)
        want = over.pop('notes', None)
        drv, pick = (self.driver_for(label, over.get('station')) if kind == 'label'
                     else ('op_item' if kind.startswith('opheld') else 'use_item_on_loc', None))
        out = []
        for p in products:
            made = counts[p].strip()
            n, note = self.batch_cap(made, body)
            base = [{'item': i, 'n': count_for(per, made, n)} for i, per in fixed]
            for combo in (fills or [()]):
                used = {x['item'] for x in base} | set(combo) | set(products)
                # an item the block only counts, never consumes, is a tool: the mould, the knife
                tools = [x for x in self.checked_items(body) if x not in used]
                kw = dict(driver=drv, picker=pick, tool=tools[0] if tools else None,
                          product=p, skill=skill, level=max(lv) if lv else 1,
                          inputs=base + [{'item': c, 'n': count_for(per, made, n)} for c, per in zip(combo, holes)],
                          xp=(int(xp.group(1)) / XP_DIV) if xp else 0, source=rel, produces_n=n,
                          members='map_members = ^false' in body,
                          notes='; '.join(x for x in (note, want) if x) or None)
                if spoiled:
                    kw['burnt'] = spoiled[0]
                extra = sorted(x for x in adds if x not in (p,) + tuple(also) + tuple(spoiled) and x in self.items)
                if extra:
                    kw['byproducts'] = extra      # the emptied pot/bucket a recipe hands back
                kw.update(over)
                out.append(kw)
        return out

    def reachable_text(self, key, depth=3):
        """Body of a handler plus the labels/procs it jumps to (same file), a few levels deep."""
        seen, out, todo = set(), [], [(key, 0)]
        while todo:
            k, d = todo.pop()
            if k in seen or k not in self.blocks or d > depth:
                continue
            seen.add(k)
            body = self.blocks[k]['body']
            out.append(body)
            for m in re.finditer(r'([@~])([a-z_0-9]+)', body):
                nk = ('label' if m.group(1) == '@' else 'proc', m.group(2))
                if nk in self.blocks and self.blocks[nk]['file'] == self.blocks[k]['file']:
                    todo.append((nk, d + 1))
        return '\n'.join(out)

    def first_ident(self, text, rx, where):
        m = re.search(rx, text)
        if not m:
            self.fail('could not find %s in %s' % (rx, where))
        return self.item(m.group(1), where)

    def script(self, rel):
        return read_text(os.path.join(common.CONTENT, rel))

    @staticmethod
    def tile(level, x, z):
        return {'level': level, 'x': x, 'z': z}

    def header(self, table, covers, not_covered):
        b = self.b
        try:
            commit = subprocess.check_output(['git', '-C', common.CONTENT, 'rev-parse', '--short', 'HEAD'], text=True).strip()
        except Exception:
            commit = 'unknown'
        return {
            'table': table,
            'tool': 'LC_wiki build/tables.py',
            'revision': b.revision, 'revision_date': b.revision_date, 'content_commit': commit,
            # relative to the repository root: an absolute path would print the machine it was built on
            'content_path': os.path.relpath(common.CONTENT, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace(os.sep, '/'),
            'date': time.strftime('%Y-%m-%d'),
            'coordinates': 'absolute world tiles {level,x,z}; 0_50_50_21_18 -> level 0, x 3221, z 3218',
            'covers': covers, 'not_covered': not_covered,
        }

    def write(self, name, obj):
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
            f.write('\n')

    @staticmethod
    def cluster(points, gap):
        """Union points (level,x,z,...) whose Chebyshev distance <= gap on the same level."""
        by_cell = defaultdict(list)
        for i, p in enumerate(points):
            by_cell[(p[0], p[1] // gap, p[2] // gap)].append(i)
        parent = list(range(len(points)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for (lv, cx, cz), idxs in by_cell.items():
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in by_cell.get((lv, cx + dx, cz + dz), []):
                        for i in idxs:
                            if max(abs(points[i][1] - points[j][1]), abs(points[i][2] - points[j][2])) <= gap:
                                parent[find(i)] = find(j)
        members = defaultdict(list)
        for i in range(len(points)):
            members[find(i)].append(points[i])
        return list(members.values())

    @staticmethod
    def stats(points):
        xs = [p[1] for p in points]
        zs = [p[2] for p in points]
        cx, cz = round(sum(xs) / len(xs)), round(sum(zs) / len(zs))
        radius = max(max(abs(p[1] - cx), abs(p[2] - cz)) for p in points)
        return {'x': cx, 'z': cz}, radius, [min(xs), min(zs), max(xs), max(zs)]

    def nearest_town(self, x, z):
        best, bd = None, 1e9
        for a in self.b.areas:
            if a.get('surface') or a['size'] != 1:
                continue
            d = math.hypot(a['x'] - x, a['z'] - z)
            if d < bd:
                best, bd = a, d
        return best, bd

    def near_info(self, x, z):
        label, d1 = self.nearest_label(x, z)
        town, d2 = self.nearest_town(x, z)
        return {'label': label['slug'] if label else None, 'direction': self.direction(x - label['x'], z - label['z']) if label else None, 'distance': round(d1) if label else None,
                'town': town['slug'] if town else None, 'town_direction': self.direction(x - town['x'], z - town['z']) if town else None, 'town_distance': round(d2) if town else None}

    def nearest_label(self, x, z):
        best, bd = None, 1e9
        for a in self.b.areas:
            if a.get('surface') or a['size'] == 2:
                continue
            d = math.hypot(a['x'] - x, a['z'] - z)
            if d < bd:
                best, bd = a, d
        return best, bd

    @staticmethod
    def direction(dx, dz):
        if abs(dx) < 8 and abs(dz) < 8:
            return 'at'
        ang = math.degrees(math.atan2(dz, dx))
        names = ['east', 'north-east', 'north', 'north-west', 'west', 'south-west', 'south', 'south-east']
        return names[int(((ang + 22.5) % 360) // 45)] + ' of'

    # ------------------------------------------------------------ recipes
    def recipes(self):
        R = []
        I = self.item

        def add(**kw):
            kw.setdefault('inputs', [])
            kw.setdefault('tool', None)
            kw.setdefault('station', None)
            kw.setdefault('station_locs', [])
            kw.setdefault('notes', None)
            kw.setdefault('picker', None)
            # how the player starts it, which the station cannot say: three quite different
            # mechanisms (item on item, chat menu, interface) all have no station at all
            if kw.get('driver') not in DRIVERS:
                self.fail('recipe for %s has no driver' % (kw.get('product') or kw.get('product_loc')))
            if kw.get('skill') not in RECIPE_SKILLS:
                self.fail('recipe for %s has skill %r' % (kw.get('product'), kw.get('skill')))
            R.append(kw)

        def tool_levels(names):
            out = []
            for n in names:
                I(n, 'tool list')
                lvl = self.items[n]['params'].get('levelrequire')
                out.append({'item': n, 'level': int(lvl) if lvl and lvl.isdigit() else 1})
            return out

        def lvl_of(n):
            return int(self.items[n]['params'].get('levelrequire', '1') or 1)

        # woodcutting: trees.dbrow rows (axes listed in successchance; rows without it get every axe the table knows)
        all_axes = sorted({s.split(',')[0] for _n, d, _f in self.dbrows('woodcutting_trees') for s in d.get('successchance', [])}, key=lambda t: (lvl_of(t), t))
        for name, d, f in self.dbrows('woodcutting_trees'):
            trees = [self.loc(t, f) for t in d['tree']]
            product = I(d['product'][0], f)
            row_axes = sorted({s.split(',')[0] for s in d.get('successchance', [])}, key=lambda t: (lvl_of(t), t)) or all_axes
            add(driver='op_loc', product=product, skill='woodcutting', level=int(d['levelrequired'][0]), tool='axe', tools=tool_levels(row_axes),
                station='tree', station_locs=trees, xp=int(d['productexp'][0]) / XP_DIV, source=f, produces_n=1)
        # mining: mine.dbrow rows; pickaxes are the ones pickaxe_checker.rs2 looks for
        picks = sorted({p for p in re.findall(r'\b([a-z_]+_pickaxe)\b', self.script('scripts/skill_mining/scripts/pickaxe_checker.rs2')) if p in self.items}, key=lambda t: (lvl_of(t), t))
        for name, d, f in self.dbrows('mining_table'):
            rocks = [self.loc(t, f) for t in d['rock']]
            if not d.get('rock_output'):
                continue   # rows without an output are not recipes
            add(driver='op_loc', product=I(d['rock_output'][0], f), skill='mining', level=int(d['rock_level'][0]), tool='pickaxe', tools=tool_levels(picks),
                station='rock', station_locs=rocks, xp=int(d['rock_exp'][0]) / XP_DIV, source=f, produces_n=1)
        # smelting (structs) -> furnace
        furnaces = sorted(n for n, c in self.locs.items() if c['d'].get('name') == 'Furnace' or (c['d'].get('category') or '') == 'smithing_furnace')
        for name, cfg in sorted(self.struct_cfg.items()):
            p = cfg['params']
            if not name.startswith('smelting_') or 'product' not in p:
                continue
            inputs = [{'item': I(p['ingredient'], cfg['file']), 'n': 1}]
            if p.get('ingredient_secondary'):
                inputs.append({'item': I(p['ingredient_secondary'], cfg['file']), 'n': int(p.get('ingredient_secondary_count', 1))})
            add(driver='interface', product=I(p['product'], cfg['file']), skill='smithing', level=int(p.get('levelrequired', 1)), inputs=inputs,
                station='furnace', station_locs=furnaces, xp=int(p.get('productexp', 0)) / XP_DIV, source=cfg['file'],
                produces_n=int(p.get('bar_count', 1)), notes='smelting')
        # smithing -> anvil; the tool is what the anvil handler checks for
        anvils = sorted(n for n, c in self.locs.items() if c['d'].get('name') == 'Anvil')
        anvil_keys = [k for k in self.blocks if k[0] in ('oploc1', 'oplocu') and (k[1] in anvils or k[1] in {'_' + (self.locs[a]['d'].get('category') or '') for a in anvils})]
        hammer = None
        for k in anvil_keys:
            found = self.checked_items(self.reachable_text(k))
            if found:
                hammer = found[0]
                break
        if not hammer:
            self.fail('anvil handler does not check for a tool (looked at %s)' % anvil_keys)
        xp_per_bar = {}
        for name, cfg in self.struct_cfg.items():
            if name.startswith('smithing_') and cfg['params'].get('namedobj'):
                xp_per_bar[cfg['params']['namedobj']] = int(cfg['params'].get('xpperbar', 0))
        for name, d, f in self.dbrows('smithing'):
            bar = I(d['bar'][0], f)
            n_bars = int(d['bar_amount'][0])
            add(driver='interface', product=I(d['product'][0], f), skill='smithing', level=int(d['levelrequired'][0]), inputs=[{'item': bar, 'n': n_bars}],
                tool=hammer, station='anvil', station_locs=anvils, xp=xp_per_bar.get(bar, 0) * n_bars / XP_DIV, source=f,
                produces_n=int(d['product_amount'][0]))
        # cooking -> range / fire
        ranges = sorted(n for n, c in self.locs.items() if c['d'].get('category') == 'cooking_oven')
        fires = sorted(n for n, c in self.locs.items() if c['d'].get('category') == 'cooking_fire')
        for name, d, f in self.dbrows('cooking_generic'):
            if not d.get('cooked') or not d.get('uncooked'):
                continue
            # the content refuses the wrong heat source: cantcookmessage_fire = oven only,
            # cantcookmessage_range = fire only (cooking.rs2 checks both against lc_category)
            no_fire = bool(d.get('cantcookmessage_fire'))
            no_range = bool(d.get('cantcookmessage_range'))
            if no_fire and no_range:
                station, locs, note = 'neither', [], 'cannot be cooked on a fire or a range: ' + d['cantcookmessage_fire'][0]
            elif no_fire:
                station, locs, note = 'range', ranges, 'range only: ' + d['cantcookmessage_fire'][0]
            elif no_range:
                station, locs, note = 'fire', fires, 'fire only: ' + d['cantcookmessage_range'][0]
            else:
                station, locs, note = 'range_or_fire', ranges + fires, None
            add(driver='use_item_on_loc', product=I(d['cooked'][0], f), skill='cooking', level=int(d.get('levelrequired', ['1'])[0]),
                inputs=[{'item': I(d['uncooked'][0], f), 'n': 1}], station=station, station_locs=locs,
                xp=int(d.get('experience', ['0'])[0]) / XP_DIV, source=f, produces_n=1, notes=note,
                burnt=I(d['burnt'][0], f) if d.get('burnt') and d['burnt'][0] != 'null' else None)
        # cooking, before the fire: cooking_generic.dbrow above only knows raw -> cooked, and the
        # mixing that makes the raw thing is written out longhand in the cooking_inv scripts,
        # mostly with no xp of its own.  Fillings and kebab stages come off a struct row each.
        ck = 'scripts/skill_cooking/scripts/cooking_inv/scripts/'
        waters = sorted(n for n, it in self.items.items() if it['params'].get('is_water_source') == '^true')
        if not waters:
            self.fail('no obj carries is_water_source, so dough has no water')
        doughs = [c for c in re.findall(r'\$choice\s*=\s*([a-z_0-9]+)', self.body('label', 'dough_interface')) if c in self.items]
        if not doughs:
            self.fail('dough.rs2: [label,dough_interface] offers no dough')
        for r in self.combine(ck + 'dough/dough.rs2', 'dough_interface', 'cooking', doughs,
                              dynamic=[waters[:1]], notes='water: any of ' + ', '.join(waters)):
            add(**r)
        for sub, lab, prod in (('pies/pies', 'make_pie_shell', 'pie_shell'),
                               ('pizza/pizza', 'make_incomplete_pizza', 'incomplete_pizza'),
                               ('pizza/pizza', 'make_uncooked_pizza', 'uncooked_pizza'),
                               ('cakes/cakes', 'make_uncooked_cake', 'uncooked_cake'),
                               ('cakes/cakes', 'make_chocolate_cake', 'chocolate_cake'),
                               ('cakes/cakes', 'make_chocolate_milk', 'chocolaty_milk'),
                               ('cakes/cakes', 'make_hangover_cure', 'hangover_cure'),
                               ('stew/stew', 'make_curry', 'uncooked_curry'),
                               ('stew/stew', 'make_uncooked_stew', 'uncooked_stew'),
                               ('wine/wine', 'make_wine', 'jug_unfermented_wine'),
                               ('ugthanki_kebab/ugthanki_kebab', 'make_ugthanki_kebab', 'ugthanki_kebab'),   # bad kebab handled below
                               ('oomlie_bird_meat/oomlie_bird_meat', 'make_oomlie_wrap', 'wrapped_oomlie')):
            for r in self.combine(ck + sub + '.rs2', lab, 'cooking', prod):
                add(**r)
        # which half-made stew you get depends on what went in, so the block names the meats
        stew_rel, stew_lab = ck + 'stew/stew.rs2', 'make_incomplete_stew'
        meats = sorted({x for x in re.findall(r'\$ingredient\s*=\s*([a-z_0-9]+)', self.body('label', stew_lab)) if x in self.items})
        pot = self.literal_dels(stew_rel, stew_lab)
        veg = [x for x in self.callers_of(stew_lab) if x not in meats and x not in pot]
        for prod, other, alts in (('stew2', 'stew1', meats), ('stew1', 'stew2', veg)):
            for r in self.combine(stew_rel, stew_lab, 'cooking', prod, dynamic=[alts], also=[other]):
                add(**r)
        # a filling, a topping or a kebab ingredient carries the struct row for what it makes;
        # the fixed half (the pie shell, the plain pizza, the bowl) is the label's literal inv_del
        for param, sub, lab in (('uncooked_pie_struct', 'pies/pies', 'make_uncooked_pie'),
                                ('pizza_topping_struct', 'pizza/pizza', 'make_pizza_with_topping'),
                                ('ugthanki_kebab_struct', 'ugthanki_kebab/ugthanki_kebab', 'make_bowl_mixture')):
            rel = ck + sub + '.rs2'
            partners = self.literal_dels(rel, lab)
            held = [x for x in self.checked_items(self.body('label', lab)) if x not in partners]
            for sname, carriers in sorted(self.struct_users(param).items()):
                p = self.struct_cfg[sname]['params']
                if not p.get('product'):
                    continue
                for it in carriers:
                    if p.get('ingredient') and lab == 'make_bowl_mixture':
                        continue      # a part-filled bowl: it goes through make_bowl_mixture2
                    add(driver='use_item_on', product=I(p['product'], sname), skill='cooking',
                        level=int(p.get('levelrequired', 1) or 1),
                        inputs=[{'item': it, 'n': 1}] + [{'item': x, 'n': 1} for x in partners],
                        tool=held[0] if held else None,
                        xp=int(p.get('productexp', 0) or 0) / XP_DIV, source=self.struct_cfg[sname]['file'],
                        produces_n=1, members=self.items[it].get('members') is True)
        # adding a second ingredient to a part-filled bowl: the row names the one that gives
        # "product"; "product2" is whichever other ingredient fits, which the row does not say
        kebab_rel = ck + 'ugthanki_kebab/ugthanki_kebab.rs2'
        for sname, carriers in sorted(self.struct_users('ugthanki_kebab_struct').items()):
            p = self.struct_cfg[sname]['params']
            if not (p.get('ingredient') and p.get('product')):
                continue
            for it in carriers:
                add(driver='use_item_on', product=I(p['product'], sname), skill='cooking', level=1,
                    inputs=[{'item': it, 'n': 1}, {'item': I(p['ingredient'], sname), 'n': 1}],
                    xp=0, source=self.struct_cfg[sname]['file'], produces_n=1, members=True,
                    notes=('the same bowl with the other ingredient gives ' + p['product2']) if p.get('product2') else None)
        # gnome restaurant: dough in a tin makes the raw dish, and the last topping settles which
        # dish it becomes -- the case list in finish_gnome_food is that whole table.  The
        # ingredient counting in between is a packed bitfield and is not modelled.
        gn = 'scripts/skill_cooking/scripts/gnome_cooking/'
        for sname, carriers in sorted(self.struct_users('gnome_cooking_struct').items()):
            p = self.struct_cfg[sname]['params']
            if not p.get('product'):
                continue
            for it in carriers:
                add(driver='use_item_on', product=I(p['product'], sname), skill='cooking', level=1,
                    inputs=[{'item': it, 'n': 1}] + [{'item': x, 'n': 1} for x in self.literal_dels(gn + 'gianne_dough.rs2', 'make_raw_gnome')],
                    xp=0, source=self.struct_cfg[sname]['file'], produces_n=1, members=True)
        top_xp = re.search(r'stat_advance\(cooking,\s*(\d+)\)', self.body('label', 'gnome_topping_add'))
        # a dish that takes any of several fruits nests a second switch on the ingredient, so
        # the case naming the dish is the enclosing one, not the one on the calling line
        toppings, outer, outer_indent = [], None, -1
        for ln in self.live_code(self.body('label', 'finish_gnome_food')).splitlines():
            cm = re.match(r'(\s*)case\s+([a-z_0-9+]+)\s*:(.*)$', ln)
            if not cm:
                continue
            indent, name, rest = len(cm.group(1)), cm.group(2), cm.group(3)
            call = re.search(r'@gnome_topping_add\(([^)]*)\)', rest)
            if not call:
                outer, outer_indent = name, indent
                continue
            toppings.append((outer if indent > outer_indent >= 0 else name, call.group(1)))
        for food, argtext in toppings:
            args = [a.strip() for a in argtext.split(',')]
            if len(args) != 5 or food not in self.items:
                continue
            req, prod, fail = args[2], args[3], args[4]
            done = self.items[I(prod, 'finish_gnome_food')]['params'].get('gnome_cooking_type') is None
            add(driver='use_item_on', product=prod, skill='cooking', level=1,
                inputs=[{'item': food, 'n': 1}, {'item': I(req, 'finish_gnome_food'), 'n': 1}],
                xp=(int(top_xp.group(1)) / XP_DIV) if (top_xp and done) else 0,
                source=gn + 'gnome_food_finish.rs2', produces_n=1, members=True,
                burnt=I(fail, 'finish_gnome_food'),
                notes='the wrong topping spoils it' + ('' if done else '; still needs another topping'))
        # smithing that is not a smithing_table row: cannonballs at a furnace, and the two
        # dragon square halves at an anvil
        for rel, lab, prod, station, locs in (
                ('scripts/skill_smithing/scripts/smelting/cannonballs.rs2', 'smelt_cannonballs', 'mcannonball', 'furnace', furnaces),
                ('scripts/skill_smithing/scripts/smithing/dragon_sq.rs2', 'make_dragon_sq', 'dragon_sq_shield', 'anvil', anvils)):
            for r in self.combine(rel, lab, 'smithing', prod, station=station, station_locs=locs):
                add(**r)
        # the chisel cuts gems (crafting, below) and bolt tips (fletching, here), so both
        # sections need it; the gem rows also need what a mis-hit leaves behind
        gem_rel = 'scripts/skill_crafting/scripts/gem/uncut_gem.rs2'
        chisel = [x for x in self.held_headers(gem_rel) if x in self.items and not x.startswith('uncut')]
        if not chisel:
            self.fail('%s: no [opheldu,<tool>] handler, so gem cutting has no tool' % gem_rel)
        chisel = chisel[0]
        # the same chisel handler also cuts bolt tips, so the fletching rows below need it too
        chisel_cases = {c.strip() for m in re.finditer(r'case\s+([a-z_0-9, ]+?)\s*:', self.body('opheldu', chisel))
                        for c in m.group(1).split(',') if c.strip() in self.items}
        # a mis-hit smashes the gem: the roll is the row's success_rate, the debris is in the label
        smashed = [a[1] for a in self.call_args(self.body('label', 'crafting_gem'), 'inv_add')
                   if a[0] == 'inv' and a[1] in self.items and a[1] != 'inv_getobj']
        # fletching: the second ingredient is the [opheldu,X] handler in the same script that mentions the row's item
        fl = 'scripts/skill_fletching/scripts/'

        def partner(item, relfile):
            cat = self.items[item]['category']
            for x in self.held_headers(relfile):
                if x == item or x not in self.items:
                    continue
                b_ = self.body('opheldu', x)
                if re.search(r'\b%s\b' % re.escape(item), b_) or (cat and re.search(r'\b%s\b' % re.escape(cat), b_)):
                    return x
            return None

        knife = None
        for k in [k for k in self.blocks if k[0] == 'opheldu' and self.blocks[k]['file'] == fl + 'cut_logs.rs2']:
            # "case knife : @fletch_log(...)" - the used item whose case jumps to the fletching label
            for it, lab in re.findall(r'case\s+([a-z_0-9]+)\s*:\s*@([a-z_0-9]+)', self.body(*k)):
                if 'fletch' in lab and it in self.items:
                    knife = it
            m = re.search(r'last_useitem\s*[=!]\s*([a-z_0-9]+)', self.body(*k))
            if not knife and m and m.group(1) in self.items:
                knife = m.group(1)
        if not knife:
            self.fail('cut_logs.rs2: could not determine the fletching tool')
        shafts = [x for x in self.held_headers(fl + 'arrows.rs2') if x in self.items and 'shaft' in x]
        # "def_int $fletching_experience = multiply($shaft_count, 5)" - the xp per shaft lives in the script
        sm = re.search(r'multiply\(\$shaft_count,\s*(\d+)\)', self.script(fl + 'cut_logs.rs2'))
        shaft_xp = int(sm.group(1)) if sm else 0
        # cutting a log opens a menu, and which menu depends on whether the log gives shafts:
        # three fixed options if it does, otherwise two whose labels are built from the log name
        fb = self.live_code(self.body('label', 'fletch_log'))
        sv = re.search(r'def_int\s+(\$[a-z_0-9]+)\s*=\s*db_getfield\([^)]*fletch_bow_table:shafts', fb)
        if not sv:
            self.fail('fletch_log: cannot tell which branch reads the shaft count')
        split = fb.find('} else {', fb.find('%s < 1' % sv.group(1)))
        menus = [(m.start(), m.group(1)) for m in re.finditer(r'~((?:multiobj|p_choice)\d[a-z_]*)\s*\(', fb)]
        if len(menus) != 2 or split < 0:
            self.fail('fletch_log: expected a menu either side of the shaft check, found %d' % len(menus))

        def log_picker(log, has_shafts):
            want = [n for pos, n in menus if (pos > split) == bool(has_shafts)][0]
            args = self.call_args(fb, '~' + want)[0]
            strings = [a[1:-1] for a in args if a.startswith('"') and a.endswith('"')]
            return {'choices': len(strings) - 1, 'labels': [self.resolve_label(x, log) for x in strings[1:]]}

        for name, d, f in self.dbrows('fletch_bow_table'):
            log = I(d['log'][0], f)
            if d.get('shafts') and shafts:
                n_shafts = int(d['shafts'][0])
                add(driver='chat_picker', picker=log_picker(log, True), product=shafts[0], skill='fletching', level=1, inputs=[{'item': log, 'n': 1}], tool=knife,
                    xp=n_shafts * shaft_xp / XP_DIV, source=f, produces_n=n_shafts,
                    notes='xp is %d per shaft, from cut_logs.rs2' % shaft_xp)
            for key in ('shortbow', 'longbow'):
                if d.get(key):
                    prod, lvl, xp = d[key][0].split(',')
                    add(driver='chat_picker', picker=log_picker(log, bool(d.get('shafts'))), product=I(prod.strip(), f), skill='fletching', level=int(lvl), inputs=[{'item': log, 'n': 1}], tool=knife,
                        xp=int(xp) / XP_DIV, source=f, produces_n=1)
        for name, d, f in self.dbrows('fletching_table'):
            prod, n = d['product'][0].split(',')
            item = I(d['item'][0], f)
            script = fl + ('bows.rs2' if item.startswith('unstrung') else 'arrows.rs2' if 'arrow' in item else 'bolts.rs2' if 'bolt' in item else 'darts.rs2')
            # make_arrows / make_darts / make_bolts take min(tips, partner, $max_count) and delete
            # that many of BOTH, so one action eats the row's count of each; stringing a bow and
            # chiselling bolt tips delete one
            batched = (not item.startswith('unstrung') and item not in chisel_cases
                       and bool(re.search(r'inv_del\(inv,\s*\$[a-z_]+,\s*\$[a-z_]+_count\)', self.script(script))))
            each = int(n) if batched else 1
            inputs = [{'item': item, 'n': each}]
            other = partner(item, script)
            if other:
                inputs.append({'item': other, 'n': each})
            add(driver='use_item_on', product=I(prod.strip(), f), skill='fletching', level=int(d['level'][0]), inputs=inputs,
                tool=chisel if item in chisel_cases else None,
                xp=int(d['experience'][0]) / XP_DIV, source=f, produces_n=int(n),
                notes=None if (other or item in chisel_cases) else 'second ingredient not found in ' + script)
        # the steps with no config row of their own: headless arrows (the arrowheads above are
        # useless without them) and the whole ogre arrow chain, which Big Chompy Bird Hunting gates
        for r in self.combine(fl + 'arrows.rs2', 'make_headless_arrows', 'fletching', 'headless_arrow'):
            add(**r)
        ogre = 'requires Big Chompy Bird Hunting (%chompybird)'
        for lab, prod in (('make_ogre_shafts', 'ogre_arrow_shaft'),
                          ('make_wolf_bone_tips', 'wolfbone_arrowheads'),
                          ('make_ogre_headless_arrows', 'ogre_headless_arrow'),
                          ('make_ogre_arrows', 'ogre_arrow')):
            for r in self.combine(fl + 'ogre_arrows.rs2', lab, 'fletching', prod, notes=ogre):
                add(**r)
        # firemaking: logs carry levelrequire + productexp; tool = the [opheldu,X] handler in firemaking.rs2
        fm_file = 'scripts/skill_firemaking/configs/firemaking.obj'
        fm_rel = 'scripts/skill_firemaking/scripts/firemaking.rs2'
        fm_script = self.script(fm_rel)
        fire_loc = re.search(r'(?<![a-z_])loc_add\(\s*\$?[a-z_0-9]+\s*,\s*([a-z_0-9]+)', fm_script)
        fire_loc = self.loc(fire_loc.group(1), 'firemaking.rs2') if fire_loc else None
        tinder = [x for x in self.held_headers(fm_rel) if x in self.items]
        tinder = tinder[0] if tinder else self.first_ident(fm_script, r'inv_total\(inv,\s*([a-z_0-9]+)\)', fm_rel)
        for name, cfg in sorted(self.obj_cfg.items()):
            p = cfg['params']
            if cfg['file'] == fm_file and 'levelrequire' in p and 'productexp' in p:
                add(driver='use_item_on', product=None, product_loc=fire_loc, skill='firemaking', level=int(p['levelrequire']),
                    inputs=[{'item': name, 'n': 1}], tool=tinder, xp=int(p['productexp']) / XP_DIV, source=cfg['file'], produces_n=1)
        # crafting: gems, leather, jewellery, spinning, pottery, glass
        cr = 'scripts/skill_crafting/scripts/'
        for name, d, f in self.dbrows('gem_cutting_table'):
            add(driver='use_item_on', product=I(d['cut_gem'][0], f), skill='crafting', level=int(d['level'][0]), inputs=[{'item': I(d['uncut_gem'][0], f), 'n': 1}],
                tool=chisel, xp=int(d['experience'][0]) / XP_DIV, source=f, produces_n=1,
                burnt=smashed[0] if (smashed and d.get('success_rate')) else None)
        # leather: the needle is the handler, the thread is what craft_leather refuses to work
        # without -- and it spends one reel per N items, which only the counter in the label says
        lr = cr + 'leather/leather.rs2'
        needles = [x for x in self.held_headers(lr) if x in self.items and 'leather' not in x]
        craft = self.body('label', 'craft_leather')
        threads = [x for x in self.checked_items(craft) if x in needles]
        if not threads:
            self.fail('%s: craft_leather checks no consumable, so thread was lost' % lr)
        thread = threads[0]
        needle = [x for x in needles if x != thread]
        if not needle:
            self.fail('%s: no needle handler' % lr)
        spent = re.search(r'%[a-z_]*thread[a-z_]*\s*>\s*(\d+)', craft)
        per = int(spent.group(1)) + 1 if spent else 1
        # three ways into the same table: the buttons of an interface, a menu for dragon hide,
        # and one product the needle handler makes outright
        lt = self.script(lr)
        by_button = {m.group(1) for m in re.finditer(r'^\[if_button,[^\]]*\]\s*@craft_leather\(([a-z_0-9]+)\)', lt, re.M)}
        direct = {c for k, b in self.blocks.items() if b['file'] == lr and k[0] == 'opheldu'
                  for c in re.findall(r'@craft_leather\(([a-z_0-9]+)\)', b['body'])}
        dhide_driver, dhide_picker = self.menu('craft_dhide_interface')

        def leather_driver(prod):
            if prod in by_button:
                return 'interface', None
            if prod in direct:
                return 'use_item_on', None
            return dhide_driver, dhide_picker

        for name, d, f in self.dbrows('craft_leather_table'):
            leather, n = d['leather'][0].split(',')
            ldrv, lpick = leather_driver(d['product'][0])
            add(driver=ldrv, picker=lpick, product=I(d['product'][0], f), skill='crafting', level=int(d['levelrequired'][0]),
                inputs=[{'item': I(leather.strip(), f), 'n': int(n)}, {'item': thread, 'n': 1, 'per': per}],
                tool=needle[0], xp=int(d['productexp'][0]) / XP_DIV, source=f, produces_n=1, members=d.get('members', ['false'])[0] == 'true')
        gold_bar = self.first_ident(self.script(cr + 'jewellery/jewellery.rs2'), r'\$bar\s*=\s*([a-z_0-9]+)', 'jewellery.rs2')
        wheels = sorted(n for n, c in self.locs.items() if c['d'].get('name') == 'Spinning wheel')
        pottery_stations = sorted(n for n, c in self.locs.items() if (c['d'].get('category') or '') in ('potters_wheel', 'pottery_oven'))
        pot_script = self.script(cr + 'pottery/pottery.rs2')
        softclay = self.first_ident(pot_script, r'inv_add\(inv,\s*([a-z_0-9]+)', 'pottery.rs2')
        clay = self.first_ident(pot_script, r'inv_del\(inv,\s*([a-z_0-9]+)', 'pottery.rs2')
        cm = re.search(r'case ([a-z_0-9, ]+):', pot_script)
        waters = [w.strip() for w in cm.group(1).split(',') if w.strip() in self.items] if cm else []
        add(driver='use_item_on', product=softclay, skill='crafting', level=1, inputs=[{'item': clay, 'n': 1}] + ([{'item': waters[0], 'n': 1}] if waters else []),
            xp=0, source=cr + 'pottery/pottery.rs2', produces_n=1, notes=('water: any of ' + ', '.join(waters)) if waters else None)
        glass_script = self.script(cr + 'glass/glass.rs2')
        glass_heads = [x for x in self.held_headers(cr + 'glass/glass.rs2') if x in self.items]
        pipe = [x for x in glass_heads if 'pipe' in x]
        molten_c = [x for x in glass_heads if x not in pipe]
        if not molten_c:
            self.fail('glass.rs2: no [opheldu,<glass>] handler found')
        molten = molten_c[0]
        glass_in = sorted({x for x in re.findall(r'inv_del\(inv,\s*([a-z_0-9]+)', glass_script) if x in self.items and x != molten and 'empty' not in x})
        cut = glass_script.find('[label,craft_glass_interface]')
        gxp = re.search(r'stat_advance\(crafting,\s*(\d+)\)', glass_script[:cut] if cut > 0 else glass_script)
        add(driver='use_item_on_loc', product=molten, skill='crafting', level=1, inputs=[{'item': x, 'n': 1} for x in glass_in], station='furnace', station_locs=furnaces,
            xp=(int(gxp.group(1)) / XP_DIV) if gxp else 0, source=cr + 'glass/glass.rs2', produces_n=1)
        # a mould is picked from a short chat menu, everything else in the jewellery table is a
        # button on the interface; the potter's wheel and the glassblowing pipe each open a menu
        jw = cr + 'jewellery/jewellery.rs2'
        jmenus = sorted(k[1] for k, b in self.blocks.items() if k[0] == 'label' and b['file'] == jw
                        and re.search(r'~p_choice\d[a-z_]*\s*\(', b['body']))
        if not jmenus:
            self.fail('%s: no chat menu, so the mould recipes have no driver' % jw)
        jewel_menu = self.menu(jmenus[0])

        def jewel_driver(p):
            return jewel_menu[0] if p.get('mould') and jewel_menu[0] else 'interface'

        def jewel_picker(p):
            if not (p.get('mould') and jewel_menu[0]):
                return None
            return dict(jewel_menu[1], note='only the moulds you are carrying are offered')

        pot_driver, pot_picker = self.menu('craft_pottery_interface')
        glass_driver, glass_picker = self.menu('craft_glass_interface')
        # filling a bucket at a sand pit: no config row, but molten glass starts here
        sand_pits = sorted(n for n, c in self.locs.items() if (c['d'].get('category') or '') == 'sand_pit')
        if not sand_pits:
            self.fail('no sand_pit loc, so a bucket of sand comes from nowhere')
        for r in self.combine(cr + 'glass/glass.rs2', 'sand_fill', 'crafting', 'bucket_sand',
                              station='sand_pit', station_locs=sand_pits):
            add(**r)
        # capes: the one cape no dye row produces is the plain one you start from
        capes = self.struct_users('crafting_capes_struct')
        plain = [items[0] for s, items in sorted(capes.items())
                 if self.struct_cfg[s]['params'].get('color') and not self.struct_cfg[s]['params'].get('cape')
                 and not self.struct_cfg[s]['params'].get('product')]
        if len(plain) != 1:
            self.fail('dye_cape.struct: expected exactly one undyed cape, got %s' % (plain or 'none'))
        plain_cape = plain[0]
        cxp = re.search(r'stat_advance\(crafting,\s*(\d+)\)', self.body('label', 'craft_capes'))
        cape_xp = int(cxp.group(1)) / XP_DIV if cxp else 0
        for name, cfg in sorted(self.struct_cfg.items()):
            p = cfg['params']
            f = cfg['file']
            if '/jewellery/' in f and p.get('product'):
                inputs = [{'item': gold_bar, 'n': 1}]
                if p.get('gem'):
                    inputs.append({'item': I(p['gem'], f), 'n': 1})
                add(driver=jewel_driver(p), picker=jewel_picker(p), product=I(p['product'], f), skill='crafting', level=int(p.get('levelrequired', 1)), inputs=inputs,
                    tool=I(p['mould'], f) if p.get('mould') else None, station='furnace', station_locs=furnaces,
                    xp=int(p.get('productexp', 0)) / XP_DIV, source=f, produces_n=1)
            elif '/spinning/' in f and p.get('product'):
                add(driver='use_item_on_loc', product=I(p['product'], f), skill='crafting', level=int(p.get('levelrequire', 1)), inputs=[{'item': I(p['ingredient'], f), 'n': 1}],
                    station='spinning_wheel', station_locs=wheels, xp=int(p.get('productexp', 0)) / XP_DIV, source=f, produces_n=1)
            elif '/pottery/' in f and p.get('product'):
                add(driver=pot_driver, picker=pot_picker, product=I(p['product'], f), skill='crafting', level=int(p.get('levelrequire', 1)), inputs=[{'item': name if name in self.items else softclay, 'n': 1}],
                    station='pottery', station_locs=pottery_stations, xp=int(p.get('productexp', 0)) / XP_DIV, source=f, produces_n=1)
            elif '/glass/' in f and p.get('product'):
                add(driver=glass_driver, picker=glass_picker, product=I(p['product'], f), skill='crafting', level=int(p.get('levelrequire', 1)), inputs=[{'item': molten, 'n': 1}],
                    tool=pipe[0] if pipe else None, xp=int(p.get('productexp', 0)) / XP_DIV, source=f, produces_n=1)
            elif ('/studded/' in f or '/battlestaves/' in f) and p.get('product'):
                # the row names the piece being upgraded; the fixed half (the studs, the
                # battlestaff) is the literal inv_del in the label that reads the row
                lab, script = (('craft_studded', cr + 'studded/studded.rs2') if '/studded/' in f
                               else ('craft_staff', cr + 'battlestaves/battlestaves.rs2'))
                base = I(p['ingredient'], f)
                add(driver='use_item_on', product=I(p['product'], f), skill='crafting',
                    level=int(p.get('levelrequired') or p.get('levelrequire', 1)),
                    inputs=[{'item': base, 'n': 1}] + [{'item': x, 'n': 1} for x in self.literal_dels(script, lab) if x != base],
                    xp=int(p.get('productexp', 0)) / XP_DIV, source=f, produces_n=1, members=True)
            elif '/dye_cape/' in f:
                # a dye row carries both halves of the cape wardrobe: "cape" is what dyeing a
                # cape with it gives, and the two ingredients are what mixing it needs
                if p.get('ingredient') and p.get('secondary_ingredient'):
                    add(driver='use_item_on', product=I(p['product'], f), skill='crafting', level=1,
                        inputs=[{'item': I(p[k], f), 'n': 1} for k in ('ingredient', 'secondary_ingredient')],
                        xp=0, source=f, produces_n=1, notes='mixed from two dyes, no experience')
                if p.get('cape'):
                    add(driver='use_item_on', product=I(p['cape'], f), skill='crafting', level=1,
                        inputs=[{'item': I(p['product'], f), 'n': 1}, {'item': plain_cape, 'n': 1}],
                        xp=cape_xp, source=f, produces_n=1,
                        notes='any coloured cape can be re-dyed, not just the plain one')
        # herblore: identify a herb, then brew. The level and xp for identifying sit on the
        # identified herb; every brewing step (herb into water, secondary into the unfinished
        # vial) is a brew_potion struct. This revision has no Druidic Ritual check -- see the
        # comment in identify.rs2 -- so the only gates are members and the herblore level.
        for key, blk in sorted(self.blocks.items()):
            if key[0] != 'opheld1':
                continue
            m = re.search(r'~attempt_identify_herb\(\s*([a-z_0-9]+)', blk['body'])
            if not m or key[1] not in self.items or m.group(1) not in self.items:
                continue
            herb = m.group(1)
            pr = self.items[herb]['params']
            add(driver='op_item', product=herb, skill='herblore', level=int(pr.get('identified_herb_level', 1) or 1),
                inputs=[{'item': key[1], 'n': 1}], xp=int(pr.get('identified_herb_exp', 0) or 0) / XP_DIV,
                source=blk['file'], produces_n=1, members=True, notes='identify')
        for name, cfg in sorted(self.struct_cfg.items()):
            pp = cfg['params']
            if 'brew_potion_mixture' not in pp:
                continue
            ins = [{'item': I(pp[k], cfg['file']), 'n': 1}
                   for k in ('brew_potion_solvent', 'brew_potion_ingredient') if pp.get(k)]
            add(driver='use_item_on', product=I(pp['brew_potion_mixture'], cfg['file']), skill='herblore',
                level=int(pp.get('brew_potion_level', 1) or 1), inputs=ins,
                xp=int(pp.get('brew_potion_exp', 0) or 0) / XP_DIV, source=cfg['file'], produces_n=1, members=True)

        # herblore, third step: grinding a secondary. The obj carries what it grinds into, and
        # the proc names the tool it refuses to work without.
        grind_rel = 'scripts/skill_herblore/scripts/grinding/grind_ingredient.rs2'
        pestle = re.search(r'\$used_with\s*!\s*([a-z_0-9]+)', self.script(grind_rel))
        if not pestle or pestle.group(1) not in self.items:
            self.fail('grind_ingredient.rs2: could not find the grinding tool')
        for iname, it in sorted(self.items.items()):
            refined = it['params'].get('grindable_ingredient_refined')
            if refined:
                add(driver='use_item_on', product=I(refined, grind_rel), skill='herblore', level=1,
                    inputs=[{'item': iname, 'n': 1}], tool=pestle.group(1), xp=0,
                    source=grind_rel, produces_n=1, members=True, notes='grind')
        # fishing: parse the spot scripts for fish_roll(fish1, fish2, equipment, bait) with the level gates around them
        spot_dir = os.path.join(common.SCRIPTS, 'skill_fishing', 'scripts', 'fishing_spots')
        spots_by_cat = defaultdict(list)
        for nname, n in self.npcs.items():
            if n['category'] and 'fish' in n['category']:
                spots_by_cat[n['category']].append(nname)
        seen = set()
        for path in sorted(glob.glob(os.path.join(spot_dir, '*.rs2'))):
            text = read_text(path)
            rel = relpath(path)
            cats = set(re.findall(r'^\[opnpc[0-9u],_([a-z_]+)\]', text, re.M))
            # a spot is usually an NPC, but the waterfall one is a loc you click; without its
            # loc those rows carried no placement at all and read as duplicates of the NPC ones
            spot_locs = sorted({k[1] for k, b in self.blocks.items()
                                if b['file'] == rel and k[0].startswith('oploc') and k[1] in self.locs})
            for m in re.finditer(r'~fish_roll(?:_loc)?\(([^)]*)\)', text):
                args = [a.strip() for a in m.group(1).split(',')]
                fish1, fish2 = args[0], args[1]
                equip = args[2] if len(args) > 3 else None
                bait = args[-1] if args[-1] != 'null' else None
                before = text[:m.start()]
                if not equip or equip == 'null':
                    # loc-based spots (waterfall) pass no equipment: it is the nearest ~check_fish_equipment above
                    ce = re.findall(r'~check_fish_equipment\(\s*([a-z_0-9]+)\s*\)', before)
                    if not ce:
                        # or the script dispatches on the item used: "case oily_fishing_rod : @..."
                        ce = [c for c in re.findall(r'case\s+([a-z_0-9]+)\s*:', before) if c in self.items]
                    equip = ce[-1] if ce else None
                lv = re.findall(r'stat\(fishing\)\s*<\s*(\d+)', before)
                level1 = int(lv[-1]) if lv else 1
                ge = re.findall(r'stat\(fishing\)\s*>=\s*(\d+)', before[-400:])
                level2 = int(ge[-1]) if ge else level1
                for fish, lvl in ((fish1, level1), (fish2, level2)):
                    if fish == 'null':
                        continue
                    I(fish, rel)
                    if (fish, rel) in seen:
                        continue
                    seen.add((fish, rel))
                    st = self.items[fish]['params'].get('fishing_struct')
                    xp = int(self.struct_cfg[st]['params'].get('productexp', 0)) / XP_DIV if st in self.struct_cfg else 0
                    npcs_here = sorted(set(sum((spots_by_cat[c] for c in cats), [])))
                    add(driver='op_npc' if npcs_here else 'op_loc',
                        product=fish, skill='fishing', level=lvl, inputs=[{'item': I(bait, rel), 'n': 1}] if bait else [],
                        tool=I(equip, rel) if equip and equip != 'null' else None,
                        station='fishing_spot', station_npcs=npcs_here,
                        station_locs=[] if npcs_here else spot_locs, xp=xp, source=rel, produces_n=1)
            # a spot whose catch varies with level rolls its own table in a proc instead of
            # calling ~fish_roll: each catch carries its own stat_advance, and the deeper fish
            # sit behind a bail-out partway down the roll ("if ($level < 46) return")
            npcs_here = sorted(set(sum((spots_by_cat[c] for c in cats), [])))
            for key, blk in sorted(self.blocks.items()):
                if key[0] != 'proc' or blk['file'] != rel or 'stat_advance(fishing' not in blk['body']:
                    continue
                callers = [b['body'] for k, b in self.blocks.items() if b['file'] == rel and '~' + key[1] in b['body']]
                gates = [int(x) for c in callers for x in re.findall(r'stat\(fishing\)\s*<\s*(\d+)', c)]
                equips = [e for c in callers for e in re.findall(r'~check_fish_equipment\(\s*([a-z_0-9]+)\s*\)', c)]
                pos, cur, segs = 0, min(gates) if gates else 1, []
                for g in re.finditer(r'\$level\s*<\s*(\d+)', blk['body']):
                    segs.append((cur, blk['body'][pos:g.start()]))
                    cur, pos = max(cur, int(g.group(1))), g.end()
                segs.append((cur, blk['body'][pos:]))
                for lvl, seg in segs:
                    for am in re.finditer(r'inv_add\(inv,\s*([a-z_0-9]+),\s*1\)(.*?)(?=inv_add\(|\Z)', seg, re.S):
                        fish = am.group(1)
                        xm = re.search(r'stat_advance\(fishing,\s*(\d+)\)', am.group(2))
                        if fish not in self.items or not xm or (fish, rel) in seen:
                            continue
                        seen.add((fish, rel))
                        add(driver='op_npc' if npcs_here else 'op_loc',
                            product=fish, skill='fishing', level=lvl, inputs=[],
                            tool=I(equips[0], rel) if equips else None, station='fishing_spot',
                            station_npcs=npcs_here, station_locs=[] if npcs_here else spot_locs,
                            xp=int(xm.group(1)) / XP_DIV,
                            source=rel, produces_n=1, notes='one roll of the %s table' % key[1])
        # ---- the leaves no row reached: steps nothing trains, plus two skills
        # the reader had not touched.  Each is read from the handler that performs it.
        # picking a field: every [oploc2,X] in pickables.rs2 hands over one item
        pk_rel = 'scripts/general_use/scripts/pickables.rs2'
        picked = 0
        for k, bl in sorted(self.blocks.items()):
            if bl['file'] != pk_rel or not k[0].startswith('oploc') or k[1] not in self.locs:
                continue
            got = [a[1] for a in self.call_args(bl['body'], '~pickup_loc_floor') if len(a) > 1 and a[1] in self.items] \
                + [a[1] for a in self.call_args(bl['body'], 'inv_add') if a[0] == 'inv' and a[1] in self.items]
            if got:
                picked += 1
                add(driver='op_loc', product=got[0], skill='none', level=1, inputs=[], station='pickable',
                    station_locs=[k[1]], xp=0, source=pk_rel, produces_n=1)
        if not picked:
            self.fail('%s: no pickable handed anything over' % pk_rel)
        # pulling the legs off a toad, opening an oyster, joining the crystal key halves
        for r in self.combine(gn + 'swamp_toad.rs2', 'swamp_toad', 'none', 'toads_legs', kind='opheld1'):
            add(**r)
        oy_rel = 'scripts/skill_fishing/scripts/fishing_spots/memberfish.rs2'
        oy = self.body('opheld1', 'oystershell')
        roll = re.search(r'random\((\d+)\)', oy)
        hit = re.search(r'\$random\s*<\s*(\d+)', oy)
        empties = [a[1] for a in self.call_args(oy, 'inv_add') if a[0] == 'inv' and a[1] in self.items and a[1] != 'smalloysterpearls']
        for r in self.combine(oy_rel, 'oystershell', 'none', 'smalloysterpearls', kind='opheld1', also=empties,
                              notes=('%s in %s shells hold a pearl; the rest give %s' % (hit.group(1), roll.group(1), ', '.join(empties)))
                              if (roll and hit and empties) else None):
            add(**r)
        for r in self.combine('scripts/areas/area_taverly/scripts/crystal_key.rs2', 'join_keys', 'none', 'crystal_key'):
            add(**r)
        # a knife on a fruit: the label sets the two products per fruit, then asks which
        fr_rel = gn + 'cutting_fruit.rs2'
        fb2 = self.live_code(self.body('label', 'slice_or_dice_interface'))
        cuts = re.findall(r'case\s+([a-z_0-9]+)\s*:\s*\$sliced\s*=\s*([a-z_0-9]+);\s*\$diced\s*=\s*([a-z_0-9]+);', fb2)
        if not cuts:
            self.fail('%s: slice_or_dice_interface names no fruit' % fr_rel)
        fruits = {c[0] for c in cuts}
        blades = [x for x in self.held_headers(fr_rel) if x in self.items and x not in fruits]
        if len(blades) != 1:
            self.fail('%s: expected one tool handler, got %s' % (fr_rel, blades))
        extra_n = {m.group(1): int(m.group(2)) for m in re.finditer(r'\$product\s*=\s*([a-z_0-9]+)\)\s*\{\s*inv_add\(inv,\s*\$product,\s*(\d+)\)', fb2)}
        for fruit, sliced, diced in cuts:
            for prod in (sliced, diced):
                kind_, pick = self.menu('slice_or_dice_interface', bound=fruit)
                add(driver=kind_ or 'use_item_on', picker=pick, product=I(prod, fr_rel), skill='cooking', level=1,
                    inputs=[{'item': I(fruit, fr_rel), 'n': 1}], tool=blades[0], xp=0, source=fr_rel,
                    produces_n=extra_n.get(prod, 1))
        # gem rocks: the one mining row with no output rolls a drop table in the script instead
        gem_rows = [(n, d, f) for n, d, f in self.dbrows('mining_table') if not d.get('rock_output')]
        tbl = re.search(r'roll_on_drop_table\(\s*([a-z_0-9]+)\s*\)', self.body('label', 'get_ore_gem_rock'))
        if len(gem_rows) != 1 or not tbl:
            self.fail('mining: expected one table-rolled rock and its table, got %d rows' % len(gem_rows))
        gname, gd, gf = gem_rows[0]
        drops = [(name, d, f) for name, d, f in self.dbrows('drop_table') if name == tbl.group(1)]
        if not drops:
            self.fail('mining: drop table %s not found' % tbl.group(1))
        _n, td, tf = drops[0]
        total = int(td['total'][0])
        for row in td['drop']:
            gem, cnt, weight = [x.strip() for x in row.split(',')]
            add(driver='op_loc', product=I(gem, tf), skill='mining', level=int(gd['rock_level'][0]), tool='pickaxe', tools=tool_levels(picks),
                station='rock', station_locs=[self.loc(t, gf) for t in gd['rock']], xp=int(gd['rock_exp'][0]) / XP_DIV, source=gf,
                produces_n=int(cnt), notes='one roll of %s: %s in %d' % (tbl.group(1), weight, total))
        # magic: a spell that turns one item into another, on an obelisk or in the pack
        for name, d, f in self.dbrows('magic_spell_table'):
            if not d.get('convertobj'):
                continue
            parts = [x.strip() for x in d['convertobj'][0].split(',')]
            if len(parts) < 2 or parts[0] not in self.items or parts[1] not in self.items:
                continue
            runes = [x.strip() for x in d.get('runesrequired', [''])[0].split(',')]
            ins = [{'item': parts[0], 'n': 1}] + [{'item': I(runes[i], f), 'n': int(runes[i + 1])}
                                                    for i in range(0, len(runes) - 1, 2) if runes[i] and runes[i] != 'null']
            loc = d['loc_type'][0] if d.get('loc_type') and d['loc_type'][0] != 'null' else None
            add(driver='use_item_on_loc' if loc else 'use_item_on', product=parts[1], skill='magic',
                level=int(d.get('levelrequired', ['1'])[0]), inputs=ins, station=loc,
                station_locs=[self.loc(loc, f)] if loc else [], xp=int(d.get('experience', ['0'])[0]) / XP_DIV,
                source=f, produces_n=1, members=d.get('members', ['false'])[0] == 'true',
                notes='cast %s on %s' % (name.replace('magic_spell_', '').replace('_', ' '), 'the ' + loc.replace('_', ' ') if loc else 'the item'))
        # gnome dishes and cocktails: what went in is counted in a packed bitfield and settled
        # by an if-chain of exact counts when the dish is cooked or the shaker poured
        for half, proc in re.findall(r'case\s+([a-z_0-9]+)\s*:\s*\$cooked_item\s*=\s*~([a-z_0-9]+)', self.body('label', 'cook_item')):
            rows, fallback = self.bit_recipes(proc)
            for ins, prod, xp in rows:
                add(driver='use_item_on_loc', product=prod, skill='cooking', level=1,
                    inputs=[{'item': I(half, proc), 'n': 1}] + [{'item': it, 'n': n} for it, n in ins],
                    station='range', station_locs=ranges, xp=xp, source=self.blocks[('proc', proc)]['file'],
                    produces_n=1, members=True, burnt=fallback,
                    notes='add the ingredients to the half-baked dish, then cook it' + ('; gnome spice is not used up' if any(it == 'gnome_spice' for it, _ in ins) else ''))
        shaker = [x for x in self.held_headers(gn + 'gnome_cocktail_shaker.rs2') if x in self.items and 'shaker' in x]
        glass = self.literal_dels(gn + 'gnome_cocktail_shaker.rs2', 'pour_cocktail_shaker')
        rows, fallback = self.bit_recipes('gnome_cocktail')
        for ins, prod, xp in rows:
            add(driver='use_item_on', product=prod, skill='cooking', level=1,
                inputs=[{'item': g, 'n': 1} for g in glass] + [{'item': it, 'n': n} for it, n in ins],
                tool=shaker[0] if shaker else None, xp=xp, source=gn + 'gnome_cocktail_shaker.rs2', produces_n=1,
                members=True, burnt=fallback, notes='add the ingredients to the shaker, then pour it into the glass')
        # the last touches on a cocktail, same table shape as the dishes; a null ingredient is a
        # stage that only cooking advances, so it is not a recipe here
        for m in re.finditer(r'case\s+([a-z_0-9]+)\s*:\s*@gnome_drink_add\(([^)]*)\)', self.body('label', 'finish_cocktail')):
            args = [a.strip() for a in m.group(2).split(',')]
            if len(args) != 5 or m.group(1) not in self.items or args[2] == 'null':
                continue
            food, req, prod, fail = m.group(1), args[2], args[3], args[4]
            done = self.items[I(prod, 'finish_cocktail')].get('category') == 'gnome_cocktail'
            add(driver='use_item_on', product=prod, skill='cooking', level=1,
                inputs=[{'item': food, 'n': 1}, {'item': I(req, 'finish_cocktail'), 'n': 1}],
                xp=(int(top_xp.group(1)) / XP_DIV) if (top_xp and done) else 0, source=gn + 'gnome_cocktail_finish.rs2',
                produces_n=1, members=True, burnt=I(fail, 'finish_cocktail'),
                notes='the wrong topping spoils it' + ('' if done else '; still needs another topping'))
        # seasoning a worm or a toad's legs: one handler's cases name the product outright
        seen_gn = set()
        for k, bl in sorted(self.blocks.items()):
            if k[0] != 'opheldu' or k[1] not in self.items or bl['file'] != gn + 'gnome_cooking.rs2':
                continue
            for other, prod in re.findall(r'case\s+([a-z_0-9]+)\s*:\s*@opheldu_make_gnome_food\(([a-z_0-9]+),', bl['body']):
                pair = tuple(sorted((k[1], other)))
                if other not in self.items or (pair, prod) in seen_gn:
                    continue
                seen_gn.add((pair, prod))
                add(driver='use_item_on', product=I(prod, 'opheldu_make_gnome_food'), skill='cooking', level=1,
                    inputs=[{'item': x, 'n': 1} for x in pair], xp=0, source=gn + 'gnome_cooking.rs2', produces_n=1,
                    members=True, notes='gnome spice is not used up' if 'gnome_spice' in pair else None)
        R.sort(key=lambda r: (r['skill'], r['level'], r.get('product') or ''))
        for r in R:
            ex = []
            for ln in r['station_locs']:
                for (lv, x, z, a) in self.placements.get(ln, []):
                    ex.append((math.hypot(x - self.respawn[1], z - self.respawn[2]) + lv * 1000, lv, x, z, ln))
            for nn in r.get('station_npcs', []):
                for (lv, x, z, s_) in self.npcs[nn]['spawns']:
                    ex.append((math.hypot(x - self.respawn[1], z - self.respawn[2]) + lv * 1000, lv, x, z, nn))
            ex.sort()
            r['station_examples'] = [{'loc': e[4], 'tile': self.tile(e[1], e[2], e[3])} for e in ex[:3]]
        self.recipe_products = {r['product'] for r in R if r.get('product')}
        return R

    # ------------------------------------------------------------ banks / landmarks
    def banks(self):
        booth_locs = [n for n, c in self.locs.items() if c['d'].get('name') in ('Bank booth', 'Bank chest') and any(c['d'].get(k) for k in ('op1', 'op2'))]
        bankers = [(lv, x, z) for n in self.npcs.values() if n['display'] == 'Banker' or ('Bank' in n['ops']) for (lv, x, z, s) in n['spawns']]
        booths = []
        for ln in booth_locs:
            for (lv, x, z, angle) in self.placements.get(ln, []):
                near = [(abs(bx - x) + abs(bz - z), bx, bz) for (bl, bx, bz) in bankers if bl == lv and abs(bx - x) <= 6 and abs(bz - z) <= 6]
                if near:
                    near.sort()
                    _, bx, bz = near[0]
                    dx, dz = x - bx, z - bz
                    stand = (x + (1 if dx > 0 else -1), z) if abs(dx) >= abs(dz) else (x, z + (1 if dz > 0 else -1))
                    conf = 'opposite the nearest banker'
                else:
                    fx, fz = [(0, -1), (-1, 0), (0, 1), (1, 0)][angle & 3]
                    stand = (x + fx, z + fz)
                    conf = 'loc rotation (no banker nearby)'
                booths.append({'loc': ln, 'level': lv, 'x': x, 'z': z, 'stand': self.tile(lv, stand[0], stand[1]), 'stand_from': conf})
        clusters = self.cluster([(b['level'], b['x'], b['z'], b) for b in booths], 4)
        banks = []
        for c in clusters:
            centre, radius, bbox = self.stats(c)
            label, dist = self.nearest_label(centre['x'], centre['z'])
            banks.append({'label': label, 'centre': centre, 'bbox': bbox, 'level': c[0][0], 'booths': [p[3] for p in c]})
        by_label = defaultdict(list)
        for bk in banks:
            by_label[bk['label']['slug'] if bk['label'] else 'unlabelled'].append(bk)
        out = []
        for slug, group in sorted(by_label.items()):
            for bk in group:
                lab = bk['label']
                base = lab['name'] if lab else 'Unlabelled'
                if len(group) > 1 and lab:
                    d = self.direction(bk['centre']['x'] - lab['x'], bk['centre']['z'] - lab['z']).replace(' of', '')
                    name = '%s %s bank' % (base, d)
                else:
                    name = '%s bank' % base
                if bk['level']:
                    name += ' (level %d)' % bk['level']
                bslug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
                out.append({'slug': bslug, 'name': name, 'level': bk['level'], 'centre': bk['centre'],
                            'rect': [bk['bbox'][0] - 3, bk['bbox'][1] - 3, bk['bbox'][2] + 3, bk['bbox'][3] + 3],
                            'parent': lab['slug'] if lab else None,
                            'booths': [{'loc': p['loc'], 'tile': self.tile(p['level'], p['x'], p['z']), 'stand': p['stand'], 'stand_from': p['stand_from']}
                                       for p in sorted(bk['booths'], key=lambda p: (p['x'], p['z']))]})
        self.bank_list = out
        return out

    def nearest_bank(self, level, x, z):
        best, bd = None, 1e9
        for bk in self.bank_list:
            d = math.hypot(bk['centre']['x'] - x, bk['centre']['z'] - z) + (0 if bk['level'] == level else 500)
            if d < bd:
                best, bd = bk, d
        if not best:
            return None
        return {'slug': best['slug'], 'name': best['name'], 'distance': round(bd), 'stands': [b['stand'] for b in best['booths'][:2]]}

    IGNORE_VARS = {'lastcombat', 'npc_lastcombat', 'action_delay', 'macro_event', 'npc_int',
                   'npc_action_delay', 'npc_aggressive_player', 'npc_attacking_uid'}

    # ---- what a script refuses to do, and to whom
    OPENS = re.compile(r'open_and_close_door|door_open|loc_change|teleport|telejump|climb_ladder')
    IF_LINE = re.compile(r'^\s*if\s*\((.*)\)\s*\{\s*$')
    ELSE_IF = re.compile(r'^\s*\}\s*else\s+if\s*\((.*)\)\s*\{\s*$')
    ELSE = re.compile(r'^\s*\}\s*else\s*\{\s*$')
    RETURNS = re.compile(r'^\s*return\b', re.M)
    CMP = re.compile(r'^(.*?)\s*(<=|>=|!|=|<|>)\s*(.*)$')

    def constant(self, tok):
        """The number a ^constant or literal stands for, or None if it is not a number."""
        tok = tok.strip()
        if re.fullmatch(r'-?\d+', tok):
            return int(tok)
        if tok.startswith('^'):
            return self.constants.get(tok[1:])
        return None

    def value_now(self, tok):
        """What this expression is worth for a player who has done nothing, or None if unknown."""
        tok = tok.strip()
        if tok.startswith('%'):
            return 0                                   # every varp starts at zero
        if re.fullmatch(r'stat\([a-z]+\)', tok):
            return 1                                   # every skill starts at 1
        if re.fullmatch(r'(testbit|inv_total|inv_getobj|inv_freespace)\(.*\)', tok):
            return 0
        if tok == 'map_members':
            return 1
        return self.constant(tok)

    def holds_now(self, cond):
        """Is this condition true for a player who has done nothing?

        Parts we cannot read count as true, so an unreadable branch is assumed taken: that
        keeps a real lock rather than silently opening it.
        """
        for group in cond.split('|'):
            ok = True
            for clause in group.split('&'):
                m = self.CMP.match(clause.strip())
                if not m:
                    continue
                a, op, c = self.value_now(m.group(1)), m.group(2), self.value_now(m.group(3))
                if a is None or c is None:
                    continue
                if not {'=': a == c, '!': a != c, '<': a < c, '>': a > c,
                        '<=': a <= c, '>=': a >= c}[op]:
                    ok = False
                    break
            if ok:
                return True
        return False

    def refuses(self, text, file):
        """Does this block end the script instead of letting you through?"""
        if self.OPENS.search(text):
            return False
        if self.RETURNS.search(text):
            return True
        for m in re.finditer(r'@([a-z_0-9]+)', text):
            blk = self.blocks.get(('label', m.group(1)))
            if blk is None or blk['file'] != file or not self.OPENS.search(blk['body']):
                return True
        return False

    @staticmethod
    def uncomment(line):
        """The line without its trailing // comment (quotes carry chat text, so respect them)."""
        i = line.find('//')
        while i >= 0:
            if line.count('"', 0, i) % 2 == 0:
                return line[:i]
            i = line.find('//', i + 2)
        return line

    def blocks_in(self, body):
        """Every branch of every if/else chain here, as (guards, block text).

        A guard is (condition, negated); the else of `if (you have a map)` is that same
        condition negated, which is where a good many requirements are written.
        """
        lines = [self.uncomment(l) for l in body.splitlines()]
        out, i, n = [], 0, len(lines)
        while i < n:
            m = self.IF_LINE.match(lines[i])
            if not m:
                i += 1
                continue
            cond, earlier, block = m.group(1), [], []
            depth, j = 1, i + 1
            while j < n:
                line = lines[j]
                nxt = self.ELSE_IF.match(line) or self.ELSE.match(line) if depth == 1 else None
                if nxt is not None:
                    out.append((tuple(earlier + [(cond, False)]) if cond else tuple(earlier),
                                '\n'.join(block)))
                    earlier = earlier + [(cond, True)]
                    cond = nxt.group(1) if nxt.re is self.ELSE_IF else None
                    block, j = [], j + 1
                    continue
                depth += line.count('{') - line.count('}')
                if depth <= 0:
                    break
                block.append(line)
                j += 1
            out.append((tuple(earlier + [(cond, False)]) if cond else tuple(earlier),
                        '\n'.join(block)))
            i = j + 1
        return out

    def guards_hold(self, guards):
        """Would a player who has done nothing land in this branch?"""
        return all(self.holds_now(c) is not neg for (c, neg) in guards)

    @staticmethod
    def guards_text(guards):
        return '\n'.join(c for (c, _) in guards)

    def refusing_guards(self, body, file):
        """The innermost guards of every branch that refuses to let you through.

        An outer branch that eventually opens the door still counts while it holds an inner
        branch that turns you away first, which is how most guild doors are written.
        """
        out = []
        for guards, btext in self.blocks_in(body):
            inner = self.refusing_guards(btext, file)
            if inner:
                out += [guards + g for g in inner]
            elif self.refuses(btext, file):
                out.append(guards)
        return out

    def refusing_conditions(self, body, file):
        """Those refusals a fresh player would actually walk into."""
        return [self.guards_text(g) for g in self.refusing_guards(body, file) if self.guards_hold(g)]

    THROUGH = re.compile(r'teleport|telejump|climb_ladder')
    UNBARS = re.compile(r'open_and_close_door|door_open|loc_change')

    @staticmethod
    def outer_lines(lines):
        """The lines of a body that are not inside a nested block."""
        out, depth = [], 0
        for line in lines:
            if depth == 0:
                out.append(line)
            depth += line.count('{') - line.count('}')
        return out

    def travel_paths(self, body, rx, prefix=()):
        """The chain of conditions guarding each point where this script lets you through."""
        lines = [self.uncomment(l) for l in body.splitlines()]
        out = []
        if rx.search('\n'.join(self.outer_lines(lines))):
            out.append(prefix)
        for guards, btext in self.blocks_in(body):
            out += self.travel_paths(btext, rx, prefix + guards)
        return out

    def enabling_conditions(self, body):
        """Conditions on the way through that a fresh player does not meet.

        A door can turn you away by simply not acting: the Zanaris door teleports you only
        while you wear a dramen staff, and the Heroes' Guild door swings open only once the
        quest is done. Either counts only when it guards every route of its kind, so the
        Varrock east gate, which checks a quest on one branch but moves you anyway on
        another, asks for nothing.
        """
        out = []
        for rx in (self.THROUGH, self.UNBARS):
            paths = self.travel_paths(body, rx)
            if not paths or any(self.guards_hold(p) for p in paths):
                continue
            out += [c for p in paths for (c, neg) in p if self.holds_now(c) is neg]
        return out

    def refusal_text(self, keys):
        """Every condition that actually turns a fresh player away, across the reachable scripts."""
        out = []
        for key in keys:
            seen, todo = set(), [(key, 0)]
            while todo:
                k, d = todo.pop()
                if k in seen or k not in self.blocks or d > 2:
                    continue
                seen.add(k)
                blk = self.blocks[k]
                out += self.refusing_conditions(blk['body'], blk['file'])
                out += self.enabling_conditions(blk['body'])
                for m in re.finditer(r'([@~])([a-z_0-9]+)', blk['body']):
                    nk = ('label' if m.group(1) == '@' else 'proc', m.group(2))
                    if nk in self.blocks and self.blocks[nk]['file'] == blk['file']:
                        todo.append((nk, d + 1))
        return '\n'.join(out)

    def requirements(self, keys):
        """What a door/ladder/passage asks for: coins, quest vars, and items it checks you carry."""
        text = self.refusal_text(keys)
        full = '\n'.join(self.reachable_text(k, 2) for k in keys)
        req = {}
        coins = re.findall(r'inv_del\(inv,\s*coins,\s*(\d+)\)', full)
        if coins:
            req['coins'] = int(coins[0])
        vars_ = sorted(set(re.findall(r'%([a-z][a-z0-9_]*)', text)) - self.IGNORE_VARS)
        if vars_:
            req['vars'] = vars_
        items = sorted({m for m in re.findall(r'inv_total\((?:inv|worn),\s*([a-z_0-9]+)\)\s*(?:>\s*0|>=\s*1|<\s*1)', text)
                        if m in self.items})
        if items:
            req['items'] = items
        qp = [int(n) for n in re.findall(r'%qp\s*(?:<|>=)\s*(\d+)', text)]
        if qp:
            req['questpoints'] = max(qp)
        levels = {}
        for sk, op, n in re.findall(r'stat\(([a-z]+)\)\s*(<=|>=|<|>)\s*(\d+)', text):
            need = int(n) + 1 if op in ('<=', '>') else int(n)
            if sk in SKILLS and need > levels.get(sk, 0):
                levels[sk] = need
        if levels:
            req['levels'] = levels
        if re.search(r'locked', full, re.I):
            req['locked_message'] = True
        return req

    def landmarks(self):
        L = []
        for bk in self.bank_list:
            for b in bk['booths']:
                L.append({'kind': 'bank_booth', 'name': bk['name'], 'ref': b['loc'], 'tile': b['tile'], 'stand': b['stand'], 'stand_from': b['stand_from'], 'area': bk['slug']})
        for p in self.b.portals:
            level, x, z, label, op, dests = p[:6]
            lname = p[6] if len(p) > 6 else None
            if lname is None or lname not in self.locs:
                continue
            low = label.lower()
            kind = 'ladder' if 'ladder' in low else 'stairs' if 'stair' in low else 'trapdoor' if 'trapdoor' in low else 'cave' if 'cave' in low else 'passage'
            cat = self.locs[lname]['d'].get('category') or ''
            keys = [k for k in self.blocks if k[0].startswith('oploc') and (k[1] == lname or (cat and k[1] == '_' + cat))]
            req = self.requirements(keys) if keys else {}
            for d in dests:
                L.append({'kind': kind, 'name': label, 'ref': lname, 'op': op, 'tile': self.tile(level, x, z),
                          'to': self.tile(*d), 'requires': req})
        ignore_vars = {'lastcombat', 'npc_lastcombat', 'action_delay', 'macro_event', 'npc_int', 'npc_action_delay', 'npc_aggressive_player', 'npc_attacking_uid'}
        for ln, cfg in sorted(self.locs.items()):
            d = cfg['d']
            nm = (d.get('name') or '').lower()
            cat = d.get('category') or ''
            if not ('door' in nm or 'gate' in nm or 'door' in cat or 'gate' in cat):
                continue
            if not any(d.get(k) for k in ('op1', 'op2', 'op3')):
                continue   # decorative
            is_gate = 'gate' in nm or 'gate' in cat
            keys = [k for k in (('oploc1', ln), ('oploc2', ln), ('oploc1', '_' + cat) if cat else None) if k and k in self.blocks]
            if not keys and not is_gate:
                continue
            req = self.requirements(keys) if keys else {}
            if not req and not is_gate:
                continue
            for (lv, x, z, a) in self.placements.get(ln, []):
                L.append({'kind': 'gate' if is_gate else 'door', 'name': d.get('name') or pretty(ln), 'ref': ln, 'tile': self.tile(lv, x, z), 'requires': req,
                          'ops': [d[k] for k in ('op1', 'op2', 'op3') if d.get(k) and d[k] != 'hidden'], 'source': self.blocks[keys[0]]['file'] if keys else cfg['file']})
        for name, dd, f in self.dbrows('locked_door'):
            ln = self.loc(dd['loc'][0], f)
            for (lv, x, z, a) in self.placements.get(ln, []):
                L.append({'kind': 'door', 'name': self.locs[ln]['d'].get('name') or pretty(ln), 'ref': ln, 'tile': self.tile(lv, x, z),
                          'requires': {'skill': 'thieving', 'level': int(dd['level'][0]), 'tool': dd['tool'][0].split(',')[0] if dd.get('tool') else None}, 'source': f})
        resolver = self.b.portal_resolver
        for nname, n in sorted(self.npcs.items()):
            keys = [k for k in resolver.teleporting if k[0].startswith('opnpc') and k[1] == nname]
            if not keys or not n['spawns']:
                continue
            spawn = n['spawns'][0]
            dests = []
            for k in keys:
                env = {'coord': (spawn[0], spawn[1], spawn[2]), 'angle': 0, 'vars': {}}
                out = []
                resolver.run(resolver.stmts(k), env, out, 0)
                for dd in out:
                    if dd and dd not in dests and (dd[0] != spawn[0] or abs(dd[1] - spawn[1]) + abs(dd[2] - spawn[2]) >= 20)                             and 0 <= dd[0] <= 3 and (dd[1] >> 6, dd[2] >> 6) in self.b.squares and tuple(dd) != self.respawn:
                        dests.append(dd)
            if not dests:
                continue
            files = sorted({self.blocks[k]['file'] for k in keys})
            text = '\n'.join(self.script(f) for f in files)
            coins = re.findall(r'inv_del\(inv,\s*coins,\s*(\d+)\)', text)
            req = {}
            if coins:
                req['coins'] = int(coins[0])
            vars_ = sorted(set(re.findall(r'%([a-z][a-z0-9_]*)\s*(?:<|>|=|!)', text)) - ignore_vars)
            if vars_:
                req['vars'] = vars_
            L.append({'kind': 'transport_npc', 'name': n['display'], 'ref': nname, 'tile': self.tile(*spawn[:3]), 'to': [self.tile(*dd) for dd in dests], 'requires': req, 'source': files})
        station_names = {'Furnace': 'furnace', 'Anvil': 'anvil', 'Spinning wheel': 'spinning_wheel', "Potter's Wheel": 'potters_wheel', "Potter's wheel": 'potters_wheel',
                         'Pottery Oven': 'pottery_oven', 'Pottery oven': 'pottery_oven', 'Altar': 'altar'}
        for ln, cfg in sorted(self.locs.items()):
            d = cfg['d']
            kind = station_names.get(d.get('name'))
            if d.get('category') in ('cooking_oven', 'cooking_fire'):
                kind = 'range' if d['category'] == 'cooking_oven' else 'fire'
            if not kind:
                continue
            ops = [d[k] for k in ('op1', 'op2', 'op3') if d.get(k) and d[k] != 'hidden']
            for (lv, x, z, a) in self.placements.get(ln, []):
                L.append({'kind': kind, 'name': d.get('name'), 'ref': ln, 'tile': self.tile(lv, x, z), 'ops': ops, 'use_item_on': not ops})
        L.sort(key=lambda e: (e['kind'], e['tile']['level'], e['tile']['x'], e['tile']['z'], e.get('ref') or ''))
        return L

    # ------------------------------------------------------------ resource stands
    def stands(self):
        S = []
        groups = []
        for name, d, f in self.dbrows('woodcutting_trees'):
            pts = [(lv, x, z, ln) for ln in d['tree'] for (lv, x, z, a) in self.placements.get(ln, [])]
            groups.append((d['product'][0], 'tree', int(d['levelrequired'][0]), pts))
        for name, d, f in self.dbrows('mining_table'):
            if not d.get('rock_output'):
                continue
            pts = [(lv, x, z, ln) for ln in d['rock'] for (lv, x, z, a) in self.placements.get(ln, [])]
            groups.append((d['rock_output'][0], 'rock', int(d['rock_level'][0]), pts))
        fish_by_cat = defaultdict(set)
        for r in self.recipe_list:
            if r['skill'] == 'fishing':
                for sn in r.get('station_npcs', []):
                    fish_by_cat[self.npcs[sn]['category']].add((r['product'], r['level']))
        for cat, fishes in sorted(fish_by_cat.items()):
            pts = [(lv, x, z, nn) for nn, n in self.npcs.items() if n['category'] == cat for (lv, x, z, s) in n['spawns']]
            for fish, lvl in sorted(fishes):
                groups.append((fish, 'fishing_spot', lvl, pts))
        for resource, kind, level, pts in groups:
            if not pts:
                continue
            for c in self.cluster(pts, 6 if kind == 'fishing_spot' else 4):
                centre, radius, bbox = self.stats(c)
                S.append({'resource': resource, 'kind': kind, 'level': level, 'floor': c[0][0], 'centre': centre, 'count': len(c), 'radius': radius,
                          'bbox': bbox,
                          # trees and rocks are locs but a fishing spot is an NPC, so say which
                          # table the refs resolve against rather than leaving it to be guessed
                          'refs': sorted(set(p[3] for p in c)), 'ref_kind': 'npc' if kind == 'fishing_spot' else 'loc',
                          'tiles': [self.tile(p[0], p[1], p[2]) for p in sorted(c, key=lambda p: (p[1], p[2]))],
                          'near': self.near_info(centre['x'], centre['z']),
                          'nearest_bank': self.nearest_bank(c[0][0], centre['x'], centre['z'])})
        N = []
        for nname, n in sorted(self.npcs.items()):
            if 'Attack' not in n['ops'] or len(n['spawns']) < 2:
                continue
            pts = [(lv, x, z, nname) for (lv, x, z, s) in n['spawns']]
            for c in self.cluster(pts, 8):
                if len(c) < 2:
                    continue
                centre, radius, bbox = self.stats(c)
                N.append({'npc': nname, 'name': n['display'], 'combat_level': n['level'], 'floor': c[0][0], 'centre': centre, 'count': len(c), 'radius': radius, 'bbox': bbox,
                          'tiles': [self.tile(p[0], p[1], p[2]) for p in sorted(c, key=lambda p: (p[1], p[2]))],
                          'near': self.near_info(centre['x'], centre['z']),
                          'nearest_bank': self.nearest_bank(c[0][0], centre['x'], centre['z'])})
        S.sort(key=lambda s: (s['kind'], s['resource'], s['floor'], s['centre']['x'], s['centre']['z']))
        N.sort(key=lambda s: (s['npc'], s['floor'], s['centre']['x'], s['centre']['z']))
        return S, N

    # ------------------------------------------------------------ areas
    def areas(self, stands, npc_stands):
        A = []
        b = self.b
        regions = {a['name']: a for a in b.areas if a['size'] == 2}
        for a in b.areas:
            if a.get('surface'):
                rect = [a['x'] - 40, a['z'] - 40, a['x'] + 40, a['z'] + 40]
                kind, parent = 'label_underground', a['surface']
            elif a['size'] == 2:
                kids = [c for c in b.areas if c['size'] < 2 and not c.get('surface') and a['name'] in c['regions']]
                if kids:
                    rect = [min(c['x'] for c in kids) - 40, min(c['z'] for c in kids) - 40, max(c['x'] for c in kids) + 40, max(c['z'] for c in kids) + 40]
                else:
                    rect = [a['x'] - 96, a['z'] - 96, a['x'] + 96, a['z'] + 96]
                kind, parent = 'label_region', None
            else:
                half = 40 if a['size'] == 1 else 12
                rect = [a['x'] - half, a['z'] - half, a['x'] + half, a['z'] + half]
                kind = 'label_town' if a['size'] == 1 else 'label_place'
                reg = [r for r in a['regions'] if r in regions]
                parent = regions[reg[0]]['slug'] if reg else None
            A.append({'slug': a['slug'], 'name': a['name'], 'kind': kind, 'level': 0, 'rects': [rect], 'centre': {'x': a['x'], 'z': a['z']}, 'parent': parent,
                      'regions': sorted(a['regions']), 'source': 'maps/labels.txt' + (' (+6400 underground twin)' if a.get('surface') else ''), 'bounds_are': 'heuristic square around the label'})
        for name, d, f in self.dbrows('coord_pair_table'):
            rects = []
            level = 0
            for pair in d['coord_pair']:
                c1, c2 = pair.split(',')[:2]
                p1, p2 = parse_coord(c1), parse_coord(c2)
                if p1 and p2:
                    rects.append([p1[1], p1[2], p2[1], p2[2]])
                    level = p1[0]
            if rects:
                A.append({'slug': 'zone_' + name, 'name': pretty(name), 'kind': 'zone', 'level': level, 'rects': rects,
                          'centre': {'x': (rects[0][0] + rects[0][2]) // 2, 'z': (rects[0][1] + rects[0][3]) // 2}, 'parent': None, 'regions': [], 'source': f, 'bounds_are': 'exact from the content'})
        for bk in self.bank_list:
            A.append({'slug': bk['slug'], 'name': bk['name'], 'kind': 'bank', 'level': bk['level'], 'rects': [bk['rect']], 'centre': bk['centre'], 'parent': bk['parent'], 'regions': [],
                      'source': 'bank booth placements (+3 tiles)', 'bounds_are': 'bounding box of the booths'})
        def where_text(near):
            town = b.area_by_slug.get(near['town']) if near['town'] else None
            lab = b.area_by_slug.get(near['label']) if near['label'] else None
            ref, dirn = (town, near['town_direction']) if town and (near['town_distance'] or 0) <= 120 else (lab, near['direction'])
            if not ref:
                return 'unlabelled', lab
            return ('%s %s' % (dirn, ref['name']) if dirn != 'at' else 'at ' + ref['name']), lab
        for s in stands:
            res = self.items[s['resource']]['display'] if s['resource'] in self.items else s['resource']
            where, lab = where_text(s['near'])
            name = '%s %s (%s x%d)' % (res, where, s['kind'], s['count'])
            slug = re.sub(r'[^a-z0-9]+', '_', ('%s_%s_%d_%d' % (s['kind'], s['resource'], s['centre']['x'], s['centre']['z'])).lower()).strip('_')
            A.append({'slug': slug, 'name': name, 'kind': 'stand', '_owner': s, 'level': s['floor'], 'rects': [[s['bbox'][0] - 1, s['bbox'][1] - 1, s['bbox'][2] + 1, s['bbox'][3] + 1]],
                      'centre': s['centre'], 'parent': lab['slug'] if lab else None, 'regions': sorted(lab['regions']) if lab else [], 'source': 'resource_stands.json', 'bounds_are': 'bounding box of the cluster (+1)'})
            s['area'] = slug
        for s in npc_stands:
            where, lab = where_text(s['near'])
            name = '%s x%d %s' % (s['name'], s['count'], where)
            slug = re.sub(r'[^a-z0-9]+', '_', ('npcs_%s_%d_%d' % (s['npc'], s['centre']['x'], s['centre']['z'])).lower()).strip('_')
            A.append({'slug': slug, 'name': name, 'kind': 'npc_stand', '_owner': s, 'level': s['floor'], 'rects': [[s['bbox'][0] - 1, s['bbox'][1] - 1, s['bbox'][2] + 1, s['bbox'][3] + 1]],
                      'centre': s['centre'], 'parent': lab['slug'] if lab else None, 'regions': sorted(lab['regions']) if lab else [], 'source': 'resource_stands.json (npc_stands)', 'bounds_are': 'bounding box of the cluster (+1)'})
            s['area'] = slug
        seen = {}
        for a in A:
            base = a['slug']
            if base in seen:
                seen[base] += 1
                a['slug'] = '%s_%d' % (base, seen[base])
            else:
                seen[base] = 1
        for a in A:
            if '_owner' in a:
                a.pop('_owner')['area'] = a['slug']
        A.sort(key=lambda a: a['slug'])
        return A

    # ------------------------------------------------------------ item sources
    def handlers_reaching(self, labels):
        """NPCs whose Talk-to or use-on handler leads to one of these labels.

        A handler may go through another label first -- the Canifis tanner jumps into the Al
        Kharid script's menu -- so walk back through the label jumps until the set settles.
        """
        want = set(labels)
        for _ in range(4):
            grown = want | {k[1] for k, b in self.blocks.items() if k[0] == 'label'
                            and any(re.search(r'[@,]\s*%s\b' % re.escape(l), b['body']) for l in want)}
            if grown == want:
                break
            want = grown
        return sorted({k[1] for k, b in sorted(self.blocks.items())
                       if k[0].startswith('opnpc') and k[1] in self.npcs
                       and any(re.search(r'[@,]\s*%s\b' % re.escape(l), b['body']) for l in want)})

    def services(self):
        """item -> [{npc, from, n, cost}] for an NPC that turns items into another for coins.

        Neither of the two here is a skill recipe -- no level, no xp, no click of your own --
        so without them leather and the base dyes look like they come from nowhere and the
        crafting rows below them start from nothing.  A service is a label that takes coins
        and one other item and hands back a third: the tanner runs the trade through a proc
        priced by a ^constant per tanner, Aggie counts out the coins herself.
        """
        deals = {}
        for key, bl in sorted(self.blocks.items()):
            if key[0] != 'label':
                continue
            body, got = bl['body'], []
            base = re.search(r'\$cost\s*=\s*(\^[a-z_0-9]+)', body)
            per_npc = {m.group(1): m.group(2) for m in
                       re.finditer(r'npc_type\s*=\s*([a-z_0-9]+)\)\s*\$cost\s*=\s*(\^[a-z_0-9]+)', body)}
            for args in self.call_args(body, '~tan_leather'):
                got.append((self.item(args[1], key[1]), self.item(args[0], key[1]), 1,
                            base.group(1) if base else None, per_npc))
            dels = [a for a in self.call_args(body, 'inv_del') if a[0] == 'inv' and len(a) > 2]
            paid = [a for a in dels if a[1] == 'coins' and a[2].strip().isdigit()]
            gave = [a for a in dels if a[1] in self.items and a[1] != 'coins']
            made = [a for a in self.call_args(body, 'inv_add') if a[0] == 'inv' and a[1] in self.items and a[1] != 'coins']
            if paid and len(gave) == 1 and len(made) == 1:
                got.append((made[0][1], gave[0][1], int(gave[0][2]) if gave[0][2].strip().isdigit() else 1,
                            paid[0][2].strip(), {}))
            if got:
                deals[key[1]] = got
        if not deals:
            self.fail('no NPC turns an item into another for coins any more')
        npcs_by_label = {lab: self.handlers_reaching([lab]) for lab in sorted(deals)}
        out = defaultdict(list)
        for lab, rows in sorted(deals.items()):
            for prod, src, n, default, per_npc in rows:
                for npc in npcs_by_label[lab]:
                    cost = self.constant(per_npc.get(npc, default)) if (default or per_npc.get(npc)) else None
                    if cost is None:
                        self.fail('[label,%s]: no price for %s' % (lab, npc))
                    out[prod].append({'npc': npc, 'from': src, 'n': n, 'cost': cost})
        return out

    def chests(self):
        """item -> [{loc, key, always}]: a locked loc that a key opens for its loot.

        Read from the [oplocu,X] handlers that test last_useitem for the key and then, in the
        label they jump to, change the loc, delete the key and add the loot.  Loot added before
        the first random() in a block is certain, the rest is one roll: the crystal chest always
        gives an uncut dragonstone, the sinister chest a fixed bag of herbs.
        """
        out = defaultdict(list)
        for k, bl in sorted(self.blocks.items()):
            if k[0] != 'oplocu' or k[1] not in self.locs or bl['file'].startswith('scripts/quests/'):
                continue
            km = re.search(r'last_useitem\s*=\s*([a-z_0-9]+)', bl['body'])
            if not km or km.group(1) not in self.items:
                continue
            key = km.group(1)
            bodies, seen, todo = [], set(), [k]
            while todo and len(seen) < 12:
                kk = todo.pop(0)
                if kk in seen or kk not in self.blocks:
                    continue
                seen.add(kk)
                bodies.append(self.blocks[kk]['body'])
                todo += [('label', m.group(1)) for m in re.finditer(r'@([a-z_0-9]+)', self.blocks[kk]['body'])]
            txt = '\n'.join(bodies)
            if 'loc_change(' not in txt or not re.search(r'inv_del\(inv,\s*%s\b' % re.escape(key), txt):
                continue
            got = {}
            for b in bodies:
                cut = re.search(r'random\(', b)
                sure = {a[1] for a in self.call_args(b[:cut.start()] if cut else b, 'inv_add') if a[0] == 'inv'}
                for a in self.call_args(b, 'inv_add'):
                    if a[0] == 'inv' and a[1] in self.items and a[1] != key:
                        got[a[1]] = got.get(a[1], False) or a[1] in sure
            for it, always in got.items():
                out[it].append({'loc': k[1], 'key': key, 'always': always})
        if not out:
            self.fail('no keyed chest gives anything, which cannot be right')
        return out

    def handouts(self):
        """item -> [{npc, note}]: an NPC that hands the item over on request, free.

        Aluft Gianne issues the gnome cooking utensils when he sets you a dish, and again
        whenever you turn up without one; no shop stocks them.  The replacement branch is
        the reliable read, since it names exactly the utensils and nothing else.
        """
        rel = 'scripts/areas/area_gnome/scripts/gnome_restaurant.rs2'
        text = self.live_code(self.script(rel))
        items = sorted({m.group(1) for m in re.finditer(r'if\s*\(\$missing_[a-z_0-9]+\s*=\s*true\)\s*inv_add\(inv,\s*([a-z_0-9]+),', text)
                        if m.group(1) in self.items})
        if not items:
            self.fail('%s: no utensil is handed back when missing' % rel)
        labels = [k[1] for k, b in self.blocks.items() if b['file'] == rel and k[0] == 'label'
                  and any(re.search(r'inv_add\(inv,\s*%s,' % re.escape(i), b['body']) for i in items)]
        npcs = self.handlers_reaching(labels)
        if not npcs:
            self.fail('%s: no NPC reaches the labels that hand out %s' % (rel, ', '.join(items)))
        note = 'handed over when he sets you a dish, and again on request if you turn up without it'
        return {i: [{'npc': n, 'note': note} for n in npcs] for i in items}

    def minigames(self):
        """item -> [{game, level, xp}]: a minigame that pays the item out.

        The Fishing Trawler's reward proc rolls a fish per haul behind a level gate; sea
        turtle and manta ray come from nowhere else in this revision.
        """
        bl = self.blocks.get(('proc', 'trawler_reward'))
        if not bl:
            self.fail('no [proc,trawler_reward]')
        game = bl['file'].split('/')[2] if bl['file'].startswith('scripts/minigames/') else 'trawler'
        out = defaultdict(list)
        for m in re.finditer(r'if\s*\(\$level\s*>=\s*(\d+)[^{]*\{\s*inv_add\([a-z_0-9]+,\s*([a-z_0-9]+),\s*1\);\s*stat_advance\(fishing,\s*(\d+)\)', bl['body']):
            if m.group(2) in self.items:
                out[m.group(2)].append({'game': game, 'level': int(m.group(1)), 'xp': int(m.group(3)) / XP_DIV})
        if not out:
            self.fail('trawler_reward pays nothing out')
        return out

    def item_sources(self):
        out = {}
        services = self.service_map
        chests, handouts, minigames = self.chests(), self.handouts(), self.minigames()
        for iname, it in sorted(self.items.items()):
            src = []
            for inv, cnt in it['sold_at']:
                shop = self.b.shops[inv]
                for owner in shop['owners']:
                    cat = self.npcs[owner]['category']
                    ways = []
                    if 'Trade' in self.npcs[owner]['ops'] and (('opnpc3', owner) in self.blocks or (cat and ('opnpc3', '_' + cat) in self.blocks)):
                        ways.append('op Trade')
                    talk = self.reachable_text(('opnpc1', owner), 3) if ('opnpc1', owner) in self.blocks else ''
                    if 'openshop' in talk:
                        ways.append('talk')
                    src.append({'kind': 'shop', 'ref': owner, 'detail': {'inv': inv, 'shop': shop['title'], 'stock': cnt, 'opens_by': ways}})
            for npc_name, r in it['dropped_by']:
                pt = prob_text(r['prob'])
                src.append({'kind': 'drop', 'ref': npc_name, 'detail': {'rate': pt[0] if r['prob'] is not None else None, 'count': r['count'], 'via': list(r.get('via', ())), 'notes': list(r['notes'])}})
            if iname in self.recipe_products:
                for r in self.recipe_list:
                    if r.get('product') == iname:
                        src.append({'kind': 'skill', 'ref': r['skill'], 'detail': {'level': r['level'], 'inputs': r['inputs'], 'tool': r.get('tool'), 'station': r.get('station')}})
            if it['spawns']:
                src.append({'kind': 'spawn', 'ref': iname, 'detail': {'tiles': [dict(self.tile(lv, x, z), count=c) for (lv, x, z, c, s) in sorted(it['spawns'], key=lambda s: (s[0], s[1], s[2]))]}})
            for s in services.get(iname, []):
                src.append({'kind': 'service', 'ref': s['npc'], 'detail': {k: s[k] for k in ('from', 'n', 'cost')}})
            for c in chests.get(iname, []):
                src.append({'kind': 'chest', 'ref': c['loc'], 'detail': {'key': c['key'], 'always': c['always']}})
            for h in handouts.get(iname, []):
                src.append({'kind': 'handout', 'ref': h['npc'], 'detail': {'note': h['note']}})
            for g in minigames.get(iname, []):
                src.append({'kind': 'minigame', 'ref': g['game'], 'detail': {'level': g['level'], 'xp': g['xp']}})
            for q in sorted(it['quests']):
                src.append({'kind': 'quest', 'ref': q, 'detail': {'name': self.b.quests[q]['name']}})
            if src:
                src.sort(key=lambda s: (s['kind'], s['ref']))
                out[iname] = {'name': it['display'], 'id': it['id'], 'sources': src}
        return out

    # ------------------------------------------------------------ hostility / safety
    def zone_rects(self, needle):
        """The exact rectangles of the coord_pair zone whose name contains needle.

        The Wilderness has one: it is the only place the level exemption in a hunt config
        is switched off, so which spawns fall inside it is a safety fact, not a map one.
        """
        for name, d, f in self.dbrows('coord_pair_table'):
            if needle not in name:
                continue
            rects = []
            for pair in d['coord_pair']:
                c1, c2 = pair.split(',')[:2]
                p1, p2 = parse_coord(c1), parse_coord(c2)
                if p1 and p2:
                    rects.append([p1[1], p1[2], p2[1], p2[2]])
            if rects:
                return rects, f
        self.fail('no coord_pair zone named like %r' % needle)

    def hunt_is_guess(self, hunt):
        """True when the content's own comment above the block says it was reconstructed.

        The four standard modes live in the unpacked cache dump under lines such as
        "// guessing that cowardly means nottoostrong"; the bespoke ones in areas/ do not.
        """
        text = self.script(hunt['file'])
        m = re.search(r'((?:^//[^\n]*\n)*)\[%s\]' % re.escape(hunt['name']), text, re.M)
        return bool(m and 'guess' in m.group(1).lower())

    def tolerance_ticks(self):
        """How long a player can stand in one spot before hunters with check_afk ignore them.

        An engine constant, not content: Player.updateAfkZones saturates a counter at N
        ticks while the player stays within a 21x21 zone. Read from the engine source
        beside the content when it is there, else null -- never typed in.
        """
        path = os.path.join(common.CONTENT, '..', 'engine', 'src', 'engine', 'entity', 'Player.ts')
        if not os.path.exists(path):
            return None
        m = re.search(r'lastAfkZone\s*=\s*Math\.min\((\d+),\s*this\.lastAfkZone', read_text(path))
        return int(m.group(1)) if m else None

    def npc_safety(self):
        N = []
        by_name = {}
        wild_rects, wild_src = self.zone_rects('wilderness')
        in_wild = lambda x, z: any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in wild_rects)
        for nname, n in sorted(self.npcs.items()):
            p = n['params']
            cfg = self.b.npc_cfg[nname]['d']
            hm = n['hunt']
            hunt = self.hunt_cfg.get(hm) if hm else None
            hunt_info = None
            if hm and hunt:
                hd = hunt['d']
                hunt_info = {'type': hd.get('type'), 'newmode': hd.get('find_newmode'),
                             # the level exemption: skip players over twice the NPC's vislevel --
                             # but only outside the Wilderness; inside, every hunter attacks any level
                             'only_weaker': hd.get('check_nottoostrong', 'off') != 'off',
                             'level_check': hd.get('check_nottoostrong', 'off'),
                             # check_afk defaults on in the engine: after tolerance_ticks in one 21x21
                             # zone the player is ignored. Only a block that says off never tolerates
                             'tolerates': hd.get('check_afk', 'on') != 'off',
                             'keeps_hunting': hd.get('find_keephunting', 'off') == 'on',
                             'line_of_sight': hd.get('check_vis') == 'lineofsight',
                             # op* modes walk adjacent first (melee reach), ap* fire from range
                             'needs_adjacent': (hd.get('find_newmode') or '').startswith('op'),
                             'guessed': self.hunt_is_guess(dict(hunt, name=hm)),
                             'source': hunt['file']}
                aggressive = hd.get('type') == 'player' and (hd.get('find_newmode') or '') in ('opplayer2', 'applayer2')
            elif hm:
                aggressive = None   # hunt mode defined by the engine, not the content
            else:
                aggressive = False
            strength = int(n['str']) if n['str'] and str(n['str']).isdigit() else 1
            sb = int(p.get('strengthbonus', 0) or 0)
            melee = ((strength + 9) * (sb + 64) + 320) // 640 if n['hp'] else None
            rb = p.get('rangebonus')
            ranged_stat = cfg.get('ranged')
            ranged = (((int(ranged_stat) if ranged_stat and ranged_stat.isdigit() else 1) + 9) * (int(rb) + 64) + 320) // 640 if rb and rb.lstrip('-').isdigit() else None
            rec = {'npc': nname, 'name': n['display'], 'level': n['level'], 'attackable': 'Attack' in n['ops'], 'aggressive': aggressive, 'huntmode': hm, 'hunt': hunt_info,
                   'huntrange': int(cfg.get('huntrange', 0) or 0), 'attackrange': int(p['attackrange']) if p.get('attackrange', '').isdigit() else 1,
                   'poison': bool(p.get('poison_severity')), 'poison_severity': int(p['poison_severity']) if p.get('poison_severity', '').isdigit() else None,
                   'maxHit': {'melee': melee, 'ranged': ranged, 'formula': 'npc_melee_maxhit: ((strength+9)*(strengthbonus+64)+320)//640'},
                   'hitpoints': int(n['hp']) if n['hp'] and n['hp'].isdigit() else None, 'attackSpeed': int(p['attackrate']) if p.get('attackrate', '').isdigit() else None,
                   'wanderRange': int(n['wander']) if n['wander'] and n['wander'].isdigit() else 5,
                   # chase range only means something for NPCs that can fight; the rest cannot be targeted
                   'maxRange': (int(n['maxrange']) if n['maxrange'] and n['maxrange'].isdigit() else 7) if (n['hp'] or 'Attack' in n['ops']) and n['moverestrict'] != 'nomove' else None,
                   'moverestrict': n['moverestrict'], 'areas': sorted(n['areas'].keys()), 'spawns': len(n['spawns']), 'source': n['file'],
                   # aggressive says what the hunt block would do; hunts says whether it can even
                   # look, since huntrange 0 never scans no matter what the block says
                   'hunts': bool(aggressive) and int(cfg.get('huntrange', 0) or 0) > 0,
                   'wilderness_spawns': sum(1 for sp in n['spawns'] if in_wild(sp[1], sp[2])),
                   # nothing places these: they are configs left in the unpacked cache dump, not
                   # world content, and they are why two attackable rows have no hitpoints at all
                   'placed': bool(n['spawns'])}
            N.append(rec)
            by_name[nname] = rec
        E = []
        for nname, n in sorted(self.npcs.items()):
            if not n['file'].startswith('scripts/macro events/'):
                continue
            keys = [k for k in self.blocks if k[1] == nname or (n['category'] and k[1] == '_' + n['category'])]
            bodies = {k: self.blocks[k]['body'] for k in keys}
            allb = '\n'.join(bodies.values())
            files = sorted({self.blocks[k]['file'] for k in keys})
            folder = None
            for f in files:
                m = re.match(r'scripts/macro events/scripts/([a-z]+)/', f)
                if m:
                    folder = m.group(1)
            says = []
            for k in keys:
                code = '\n'.join(l for l in bodies[k].split('\n') if not l.strip().startswith('//'))
                for m in re.finditer(r'(?:npc_say|~chatnpc)\(\s*"([^"]*)"', code):
                    t = re.sub(r'<[^>]*>', '', m.group(1)).replace('|', ' ').strip()
                    if t and t not in says:
                        says.append(t)
            rewards = sorted({r for r in re.findall(r'inv_add\(inv,\s*([a-z_0-9]+)', allb) if r in self.items})
            E.append({'npc': nname, 'name': n['display'], 'trigger_skill': folder or 'general', 'attackable': 'Attack' in n['ops'],
                      'attacks_if_ignored': bool(re.search(r'npc_setmode\((?:op|ap)player2\)', allb)) or ('ai_opplayer2', nname) in self.blocks or bool(n['hunt']),
                      'teleports_player': bool(re.search(r'p_tele(?:port|jump)\(', allb)), 'poisons': bool(re.search(r'poison', allb, re.I)),
                      'damages': bool(re.search(r'p_damage|damage\(', allb)), 'talk_to': ('opnpc1', nname) in self.blocks, 'ops': n['ops'],
                      'says': says[:12], 'rewards': rewards, 'combat': {'level': n['level'], 'hp': n['hp'], 'maxHit': by_name[nname]['maxHit']['melee']},
                      'sources': files, 'config': n['file']})
        return N, E

    # ------------------------------------------------------------ chat verdicts
    def chat(self):
        rules = [
            ('cant_reach', r"can'?t reach|cannot reach"),
            ('inventory_full', r"inventory|too full|enough space|enough room|carry any more|no room"),
            ('no_coins', r"\bcoins?\b|afford|enough money|\bgold\b|\bgp\b"),
            ('out_of_stock', r"run out|out of stock|sold out|has no more"),
            ('need_level', r"level of|level to|enough level|high enough|not (?:yet )?experienced|need to be level|need (?:a|an) [A-Za-z]+ level|requires? (?:a|an)? ?[A-Za-z]+ level"),
            ('need_tool', r"you need (?:a|an|some|the) [a-z' -]+ to |you don'?t have (?:a|an|any) |without (?:a|an) |you'll need"),
            ('locked', r"locked|no key"),
            ('members', r"members"),
            ('quest_required', r"quest|complete|permission"),
            ('busy', r"already|being used|someone else|busy|in use"),
            ('nothing', r"nothing interesting|nothing of interest|find nothing|notice nothing|nothing happens|nothing here"),
            ('danger', r"poison|you have been|hurt|damage|you fall|hits you|stunned"),
            ('cant_do', r"can'?t do that|can'?t use|cannot do|can'?t light|won'?t|doesn'?t seem|not allowed|only .* can"),
            ('progress', r"^you (?:cook|cut|chop|mine|catch|smelt|make|get|take|open|close|climb|search|light|pick|buy|sell|spin|string|fletch|swing|cast|attempt|start|begin|manage|retrieve|place|put|add|remove|drop|enter|leave|walk|pay|smith|hammer|fire|craft|burn)"),
        ]
        counts = Counter()
        sources = defaultdict(set)
        for path in sorted(glob.glob(os.path.join(common.SCRIPTS, '**', '*.rs2'), recursive=True)):
            rel = relpath(path)
            text = read_text(path)
            for m in re.finditer(r'(?<![a-z_])(?:mes|~mesbox|~objbox)\(\s*"((?:[^"\\]|\\.)*)"', text):
                t = m.group(1).replace('\\"', '"').strip()
                if t:
                    counts[t] += 1
                    sources[t].add(rel)
        for name, cfg in self.b.enum_cfg.items():
            if name == 'displaymessage_enum':
                for v in cfg['multi'].get('val', []):
                    t = v.split(',', 1)[1].strip()
                    counts[t] += 1
                    sources[t].add(cfg['file'])
        grouped = defaultdict(list)
        for t, n in counts.items():
            cat = 'other'
            for c, rx in rules:
                if re.search(rx, t, re.I):
                    cat = c
                    break
            grouped[cat].append({'text': t, 'n': n, 'sources': sorted(sources[t])[:6], 'has_placeholders': '<' in t})
        for c in grouped:
            grouped[c].sort(key=lambda e: (-e['n'], e['text']))
        return {c: grouped[c] for c in sorted(grouped)}, [c for c, _ in rules] + ['other']

    # ------------------------------------------------------------ run
    def run(self):
        b = self.b
        b.log('tables: recipes')
        self.recipe_list = self.recipes()
        self.service_map = self.services()
        # an ingredient no row anywhere provides is a dead end; say so in
        # the header rather than leave it to be discovered one failed plan at a time
        b.log('tables: item sources')
        sources = self.item_sources()
        self.recipe_products = {r['product'] for r in self.recipe_list if r.get('product')}
        dead = sorted({i['item'] for r in self.recipe_list for i in r['inputs'] if i['item'] not in sources})
        self.write('recipes.json', {'unobtainable_inputs': {'items': dead,
                                    'meaning': 'inputs of a recipe here that no shop, drop, recipe, service, chest, handout, minigame, spawn or quest script in this revision provides; only cheat scripts or the raw cache dump mention them'},
                                    '_generated': self.header('recipes', 'woodcutting, mining, smelting, smithing, cooking (both the fire and the mixing before it), fletching (including headless and ogre arrows), firemaking, crafting (gems, leather, jewellery, spinning, pottery, glass, studded leather, battlestaves, dyed capes), fishing (including the big net table), herblore (identifying and brewing)',
                                                                'runecrafting (see the wiki), magic; agility courses; quest-only recipes; wine fermenting, which happens on a timer rather than on a click; the second ingredient of a part-filled kebab bowl, which the content does not name; tanning and the Draynor dye-maker, which are NPC services with no level or xp and sit in item_sources.json instead'),
                                    'xp_units': 'levels of xp (content values divided by 10)', 'recipes': self.recipe_list})
        b.log('tables: banks & landmarks')
        self.banks()
        landmarks = self.landmarks()
        stands, npc_stands = self.stands()
        areas = self.areas(stands, npc_stands)
        b.gen = {'recipes': self.recipe_list, 'stands': stands, 'npc_stands': npc_stands,
                   'landmarks': landmarks, 'banks': self.bank_list, 'services': self.service_map}   # reused by the map viewer and the chunk picker
        self.write('resource_stands.json', {'_generated': self.header('resource_stands', 'clusters of trees, rocks and fishing spots (Chebyshev gap 6) and of attackable NPC spawns (gap 8)',
                                                                       'resources that only exist as script-spawned objects; depleted-stage rocks/stumps are counted as their live loc'),
                                            'stands': stands, 'npc_stands': npc_stands})
        self.write('areas.json', {'_generated': self.header('areas', 'map labels (heuristic squares), coord_pair zones from db rows (exact), banks (booth bounding boxes), resource and npc stands (cluster bounding boxes)',
                                                            'exact town boundaries (the content has none); indoor rooms'), 'areas': areas})
        self.write('landmarks.json', {'_generated': self.header('landmarks', 'bank booths with stand tiles, ladders/stairs/trapdoors/caves with destinations, doors and gates with requirements, NPCs whose dialogue transports you, skill stations',
                                                                'shop doors (use the vendor spawn in shops.gen.json); agility shortcuts without teleports'), 'landmarks': landmarks})
        self.write('item_sources.json', {'_generated': self.header('item_sources', 'shops (by vendor debugname), NPC drops with rates (via sub-tables), skill recipes, NPC services such as tanning and dyes, keyed chests, utensils an NPC hands out, minigame payouts, ground spawns, quest scripts that reference the item',
                                                                   'pickpocketing and stall loot (thieving.gen.json); items only obtainable from random events'), 'items': sources})
        b.log('tables: npc safety')
        N, E = self.npc_safety()
        wild_rects, wild_src = self.zone_rects('wilderness')
        self.write('npc_safety.json', {'wilderness': {'rects': wild_rects, 'source': wild_src,
                                                       'rule': 'inside these rectangles a hunt block with level_check=outside_wilderness applies no level check: every hunter attacks any combat level; tolerates, line_of_sight and huntrange still apply',
                                                       'tolerance_ticks': self.tolerance_ticks(),
                                                       'tolerance_rule': 'a hunter with tolerates=true ignores a player who has stayed within one 21x21-tile zone for tolerance_ticks ticks (engine Player.updateAfkZones); leaving the zone resets it'},
                                        '_generated': self.header('npc_safety', 'every NPC config: attackable, aggressive and hunts (from its .hunt config and huntrange), the hunt checks by name with whether the block is a reconstruction, spawns inside the exact Wilderness zone, poison param, computed max hits, wander/max range, areas; random-event NPCs with behaviour flags read from their scripts',
                                                                 'magic max hits; an NPC whose hunt mode names a block no .hunt file defines gets aggressive=null'), 'npcs': N, 'random_events': E})
        b.log('tables: chat verdicts')
        grouped, cats = self.chat()
        self.write('chat_verdicts.json', {'_generated': self.header('chat_verdicts', 'every mes()/~mesbox()/~objbox() string in the scripts plus the displaymessage enum, classified by regex into categories',
                                                                    'npc_say / chatnpc dialogue lines; strings built entirely at runtime'), 'categories': cats, 'verdicts': grouped})
        import shutil
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'tables_README.md'), os.path.join(OUT, 'README.md'))
        b.log('tables: written to %s' % OUT)


def build_tables(b):
    Gen(b).run()
