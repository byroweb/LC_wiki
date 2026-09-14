"""Resolve where map entrances (ladders, stairs, trapdoors, caves...) lead.

Walks the [oploc*,<loc>] scripts symbolically with the placed loc's coordinate
and rotation, following ~procs / @labels, and collects every p_teleport /
p_telejump destination it can determine.
"""
import os
import re
import glob
from drops import parse_statements, split_args, _read_balanced
import common
from common import read_text

COORD_RE = re.compile(r'^(\d)_(\d+)_(\d+)_(\d+)_(\d+)$')
TELEPORT_WORDS = ('p_teleport', 'p_telejump')


def parse_coord(s):
    m = COORD_RE.match(s.strip())
    if not m:
        return None
    l, mx, mz, lx, lz = (int(v) for v in m.groups())
    return (l, mx * 64 + lx, mz * 64 + lz)


def load_constants():
    out = {}
    for path in glob.glob(os.path.join(common.SCRIPTS, '**', '*.constant'), recursive=True):
        for line in read_text(path).splitlines():
            m = re.match(r'^\^([a-z_0-9]+)\s*=\s*(\S+)', line.strip())
            if m:
                c = parse_coord(m.group(2))
                if c:
                    out[m.group(1)] = c
                elif re.match(r'^-?\d+$', m.group(2)):
                    out[m.group(1)] = int(m.group(2))
    return out


class PortalResolver:
    def __init__(self, blocks):
        self.blocks = blocks
        self.constants = load_constants()
        self.parsed = {}
        self.teleporting = self._teleporting_blocks()

    def _teleporting_blocks(self):
        """Keys of blocks that (transitively) call p_teleport/p_telejump.

        Random-event ("macro event") scripts teleport players to mazes etc. from trees,
        rocks and flax; those are not entrances, so they are ignored entirely.
        """
        self.ignored = {k for k, b in self.blocks.items() if 'macro' in b['file'].lower()}
        direct = {k for k, b in self.blocks.items() if k not in self.ignored and any(w in b['body'] for w in TELEPORT_WORDS)}
        names = {}
        for k in direct:
            names.setdefault(k[1], set()).add(k[0])
        result = set(direct)
        changed = True
        while changed:
            changed = False
            tele_names = {k[1] for k in result if k[0] in ('proc', 'label')}
            tele_locs = {k[1] for k in result if k[0].startswith('oploc')}
            for k, b in self.blocks.items():
                if k in result or k in self.ignored:
                    continue
                body = b['body']
                hit = False
                for name in tele_names:
                    if ('~' + name) in body or ('@' + name) in body:
                        hit = True
                        break
                if not hit and 'loc_change(' in body:
                    for target in re.findall(r'loc_change\(\s*([a-z_][a-z0-9_]*)', body):
                        if target in tele_locs:
                            hit = True
                            break
                if hit:
                    result.add(k)
                    changed = True
        return result

    def stmts(self, key):
        if key not in self.parsed:
            blk = self.blocks.get(key)
            self.parsed[key] = parse_statements(blk['body'])[0] if blk else None
        return self.parsed[key]

    # ---------------------------------------------------------------- public
    def loc_has_teleport(self, locname, category=None):
        for op in ('oploc1', 'oploc2', 'oploc3', 'oploc4', 'oploc5'):
            if (op, locname) in self.teleporting:
                return True
            if category and (op, '_' + category) in self.teleporting:
                return True
        return False

    def resolve(self, locname, coord, angle, category=None):
        """Returns list of destination coords (level, x, z)."""
        out = []
        for op in ('oploc1', 'oploc2', 'oploc3', 'oploc4', 'oploc5'):
            for key in ((op, locname), (op, '_' + category) if category else None):
                if key is None or key not in self.teleporting:
                    continue
                env = {'coord': coord, 'angle': angle, 'vars': {}}
                self.run(self.stmts(key), env, out, 0)
        seen = []
        for d in out:
            if d and d not in seen:
                seen.append(d)
        return seen

    # ---------------------------------------------------------------- evaluation
    def eval_int(self, expr, env):
        expr = expr.strip()
        if re.match(r'^-?\d+$', expr):
            return int(expr)
        if expr in ('loc_angle', 'loc_angle()'):
            return env['angle']
        if expr.startswith('$'):
            v = env['vars'].get(expr[1:])
            return v if isinstance(v, int) else None
        if expr.startswith('^'):
            v = self.constants.get(expr[1:])
            return v if isinstance(v, int) else None
        return None

    def eval_coord(self, expr, env):
        expr = expr.strip()
        c = parse_coord(expr)
        if c:
            return c
        if expr in ('coord', 'coord()', 'loc_coord', 'loc_coord()'):
            return env['coord']
        if expr.startswith('$'):
            v = env['vars'].get(expr[1:])
            return v if isinstance(v, tuple) else None
        if expr.startswith('^'):
            v = self.constants.get(expr[1:])
            return v if isinstance(v, tuple) else None
        m = re.match(r'^movecoord\s*\(', expr)
        if m:
            args = split_args(_read_balanced(expr, expr.index('('))[0])
            if len(args) == 4:
                base = self.eval_coord(args[0], env)
                dx, dl, dz = (self.eval_int(a, env) for a in args[1:])
                if base is not None and None not in (dx, dl, dz):
                    return (base[0] + dl, base[1] + dx, base[2] + dz)
            return None
        m = re.match(r'^map_findsquare\s*\(', expr)
        if m:
            args = split_args(_read_balanced(expr, expr.index('('))[0])
            return self.eval_coord(args[0], env) if args else None
        return None

    def eval_value(self, expr, env):
        c = self.eval_coord(expr, env)
        if c is not None:
            return c
        return self.eval_int(expr, env)

    def eval_cond(self, cond, env):
        """True/False when decidable, None otherwise."""
        cond = cond.strip()
        if re.search(r'\s[&|]\s', cond):
            return None
        m = re.match(r'^(.+?)\s*(=|!)\s*(.+)$', cond)
        if not m:
            return None
        a = self.eval_value(m.group(1), env)
        b = self.eval_value(m.group(3), env)
        if a is None or b is None:
            return None
        return (a == b) if m.group(2) == '=' else (a != b)

    def run(self, stmts, env, out, depth):
        if stmts is None or depth > 10:
            return
        for st in stmts:
            kind = st[0]
            if kind == 'stmt':
                self.run_stmt(st[1], env, out, depth)
            elif kind == 'if':
                decided = False
                for cond, body in st[1]:
                    v = self.eval_cond(cond, env)
                    if v is True:
                        self.run(body, env, out, depth)
                        decided = True
                        break
                    if v is None:
                        self.run(body, dict(env, vars=dict(env['vars'])), out, depth)
                if not decided and st[2] is not None:
                    self.run(st[2], env, out, depth)
            elif kind == 'switch':
                val = self.eval_value(st[1], env)
                matched = None
                default = None
                for labels, body in st[2]:
                    labs = [l.strip() for l in labels.split(',')]
                    if 'default' in labs:
                        default = body
                        continue
                    if val is not None and any(self.eval_value(l, env) == val for l in labs):
                        matched = body
                        break
                if val is not None:
                    self.run(matched if matched is not None else default, env, out, depth)
                else:
                    for labels, body in st[2]:
                        self.run(body, dict(env, vars=dict(env['vars'])), out, depth)
            elif kind == 'while':
                self.run(st[2], env, out, depth)

    def run_stmt(self, text, env, out, depth):
        text = text.strip()
        m = re.match(r'^(?:def_\w+\s+)?\$(\w+)\s*=\s*(.+)$', text)
        if m:
            env['vars'][m.group(1)] = self.eval_value(m.group(2), env)
            return
        for w in TELEPORT_WORDS:
            if text.startswith(w):
                args = split_args(_read_balanced(text, text.index('('))[0])
                if args:
                    d = self.eval_coord(args[0], env)
                    if d is not None:
                        out.append(d)
                return
        if text.startswith('loc_change(') or text.startswith('loc_add('):
            # e.g. a closed trapdoor becomes trapdoor_open, whose own handler teleports
            args = split_args(_read_balanced(text, text.index('('))[0])
            target = args[0].strip() if text.startswith('loc_change(') else (args[1].strip() if len(args) > 1 else '')
            if re.match(r'^[a-z_][a-z0-9_]*$', target) and depth < 4:
                for op in ('oploc1', 'oploc2', 'oploc3'):
                    key = (op, target)
                    if key in self.teleporting:
                        self.run(self.stmts(key), dict(env, vars=dict(env['vars'])), out, depth + 1)
            return
        m = re.match(r'^([~@])([a-z_0-9]+)\s*(\(.*)?$', text, re.S)
        if m:
            key = ('proc' if m.group(1) == '~' else 'label', m.group(2))
            if key not in self.teleporting or key in self.ignored:
                return
            blk = self.blocks[key]
            args = split_args(_read_balanced(m.group(3), 0)[0]) if m.group(3) else []
            child = dict(env, vars=dict(env['vars']))
            for pname, arg in zip(blk['params'], args):
                child['vars'][pname] = self.eval_value(arg, env)
            self.run(self.stmts(key), child, out, depth + 1)
