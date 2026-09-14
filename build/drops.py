"""A small interpreter for the RuneScript subset used by NPC death (drop) scripts.

It walks [ai_queue3,...] handlers, follows @label / ~proc / gosub jumps, and
turns `random(N)` + if/else-if chains and switch statements into drop
probabilities.  Value-returning procs (e.g. ~randomherb) become sub-tables.
"""
import os
import re
import glob
from fractions import Fraction
import common
from common import read_text, relpath

HEADER_RE = re.compile(r'^\[([a-z_0-9]+),([^\]]+)\]', re.M)


def load_script_blocks():
    """Returns dict (trigger, name) -> {'body': str, 'file': relpath}."""
    blocks = {}
    for path in sorted(glob.glob(os.path.join(common.SCRIPTS, '**', '*.rs2'), recursive=True)):
        text = read_text(path).replace('\r', '')
        rel = relpath(path)
        matches = list(HEADER_RE.finditer(text))
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end]
            # strip proc signature "(params)(returns)" directly after header, remembering the parameter names
            params = []
            sig = re.match(r'^\s*\(([^)]*)\)', body)
            if sig:
                params = [p.strip().split('$')[-1] for p in sig.group(1).split(',') if '$' in p]
            body = re.sub(r'^(\s*\([^)]*\)){1,2}', '', body, count=1)
            key = (m.group(1), m.group(2).strip())
            blocks[key] = {'body': body, 'file': rel, 'params': params}
    return blocks


# ---------------------------------------------------------------- parsing

def _skip_ws(text, i):
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j == -1 else j + 1
        else:
            break
    return i


def _read_balanced(text, i, open_c='(', close_c=')'):
    """text[i] == open_c; returns (inner, index after close)."""
    depth = 0
    n = len(text)
    j = i
    in_str = False
    while j < n:
        c = text[j]
        if in_str:
            if c == '\\':
                j += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return text[i + 1:], n


def _word_at(text, i, word):
    if not text.startswith(word, i):
        return False
    j = i + len(word)
    return j >= len(text) or not (text[j].isalnum() or text[j] == '_')


def _parse_block_after(text, j):
    """Expects '{' at/after j (after whitespace); returns (stmts, index after '}')."""
    n = len(text)
    j = _skip_ws(text, j)
    if j < n and text[j] == '{':
        body, j = parse_statements(text, j + 1)
        if j < n and text[j] == '}':
            j += 1
        return body, j
    return [], j


def parse_statements(text, i=0, in_switch=False):
    """Returns (stmts, i). Stops at '}' (not consumed) or at 'case' when in_switch."""
    stmts = []
    n = len(text)
    while True:
        i = _skip_ws(text, i)
        if i >= n:
            return stmts, i
        c = text[i]
        if c == '}':
            return stmts, i
        if in_switch and _word_at(text, i, 'case'):
            return stmts, i
        if _word_at(text, i, 'if'):
            chain = []
            else_block = None
            j = _skip_ws(text, i + 2)
            cond, j = _read_balanced(text, j)
            body, j = _parse_block_after(text, j)
            chain.append((cond.strip(), body))
            while True:
                k = _skip_ws(text, j)
                if _word_at(text, k, 'else'):
                    k = _skip_ws(text, k + 4)
                    if _word_at(text, k, 'if'):
                        k = _skip_ws(text, k + 2)
                        cond, k = _read_balanced(text, k)
                        body, k = _parse_block_after(text, k)
                        chain.append((cond.strip(), body))
                        j = k
                        continue
                    body, k = _parse_block_after(text, k)
                    else_block = body
                    j = k
                break
            stmts.append(('if', chain, else_block))
            i = j
            continue
        if _word_at(text, i, 'while'):
            j = _skip_ws(text, i + 5)
            cond, j = _read_balanced(text, j)
            body, j = _parse_block_after(text, j)
            stmts.append(('while', cond.strip(), body))
            i = j
            continue
        m = re.match(r'switch_[a-z]+', text[i:i + 20])
        if m:
            j = _skip_ws(text, i + len(m.group(0)))
            expr, j = _read_balanced(text, j)
            j = _skip_ws(text, j)
            cases = []
            if j < n and text[j] == '{':
                j += 1
                while True:
                    j = _skip_ws(text, j)
                    if j >= n:
                        break
                    if text[j] == '}':
                        j += 1
                        break
                    if _word_at(text, j, 'case'):
                        colon = text.find(':', j)
                        if colon == -1:
                            j = n
                            break
                        labels = text[j + 4:colon].strip()
                        body, j = parse_statements(text, colon + 1, in_switch=True)
                        cases.append((labels, body))
                    else:
                        j += 1
            stmts.append(('switch', expr.strip(), cases))
            i = j
            continue
        if c == '{':
            body, j = _parse_block_after(text, i)
            stmts.extend(body)
            i = j
            continue
        # simple statement: read to ';' at depth 0
        depth = 0
        j = i
        in_str = False
        while j < n:
            ch = text[j]
            if in_str:
                if ch == '\\':
                    j += 1
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ';' and depth <= 0:
                break
            elif ch in '{}' and depth <= 0:
                break
            j += 1
        stmt = text[i:j].strip()
        if stmt:
            stmts.append(('stmt', stmt))
        if j < n and text[j] == ';':
            i = j + 1
        else:
            i = j


def split_args(s):
    out = []
    depth = 0
    cur = []
    in_str = False
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            out.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append(''.join(cur).strip())
    return [a for a in out if a != '']


# ---------------------------------------------------------------- evaluation

class Entry:
    __slots__ = ('item', 'table', 'count', 'prob', 'notes', 'kind')

    def __init__(self, item, table, count, prob, notes, kind):
        self.item = item
        self.table = table
        self.count = count
        self.prob = prob
        self.notes = tuple(notes)
        self.kind = kind

    def key(self):
        return (self.item, self.table, self.count, self.notes, self.kind)


class Ctx:
    __slots__ = ('prob', 'notes', 'vars', 'random_involved')

    def __init__(self, prob=Fraction(1), notes=(), vars=None, random_involved=False):
        self.prob = prob
        self.notes = tuple(notes)
        self.vars = vars if vars is not None else {}
        self.random_involved = random_involved

    def child(self, p=None, note=None, random_involved=False):
        c = Ctx(self.prob, self.notes, self.vars, self.random_involved)
        if p is not None:
            c.prob = None if self.prob is None else self.prob * p
        if note:
            c.notes = self.notes + (note,)
        if random_involved:
            c.random_involved = True
        return c


class ReturnSignal(Exception):
    pass


CLUE_RE = re.compile(r'~trail_(easy|medium|hard)cluedrop\s*\(\s*(\d+)')
RANDOM_ASSIGN_RE = re.compile(r'^(?:def_int\s+)?\$(\w+)\s*=\s*random\s*\(\s*(\d+)\s*\)\s*$')
OBJVAR_ASSIGN_RE = re.compile(r'^(?:def_namedobj\s+|def_obj\s+)?\$(\w+)\s*=\s*([a-z_][a-z0-9_]*|~[a-z_0-9]+(?:\([^)]*\))?)\s*$')
COND_RE = re.compile(r'\$(\w+)\s*(<=|>=|<|>|=|!)\s*(\d+)')
IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')


class DropEvaluator:
    def __init__(self, blocks):
        self.blocks = blocks
        self.parsed = {}
        self.table_cache = {}

    def get_stmts(self, key):
        if key not in self.parsed:
            blk = self.blocks.get(key)
            self.parsed[key] = parse_statements(blk['body'])[0] if blk else None
        return self.parsed[key]

    # -- public API
    def evaluate_handler(self, key):
        """Evaluate a death handler; returns (entries, referenced_tables, visited_blocks)."""
        state = {'out': [], 'objvars': {}, 'tables': set(), 'visited': [], 'mode': 'drop'}
        stmts = self.get_stmts(key)
        if stmts is None:
            return [], set(), []
        state['visited'].append(key)
        try:
            self.run(stmts, Ctx(), state, 0)
        except ReturnSignal:
            pass
        return self.merge(state['out']), state['tables'], state['visited']

    def evaluate_table(self, name):
        """Evaluate a value-returning proc as a drop table."""
        if name in self.table_cache:
            return self.table_cache[name]
        key = ('proc', name)
        state = {'out': [], 'objvars': {}, 'tables': set(), 'visited': [key], 'mode': 'value'}
        stmts = self.get_stmts(key)
        result = {'name': name, 'entries': [], 'tables': set(),
                  'file': self.blocks.get(key, {}).get('file')}
        self.table_cache[name] = result
        if stmts is not None:
            try:
                self.run(stmts, Ctx(), state, 0)
            except ReturnSignal:
                pass
            result['entries'] = self.merge(state['out'])
            result['tables'] = state['tables']
        return result

    @staticmethod
    def merge(entries):
        merged = {}
        order = []
        for e in entries:
            k = e.key()
            if k in merged:
                m = merged[k]
                if m.prob is not None and e.prob is not None:
                    m.prob += e.prob
                else:
                    m.prob = None
            else:
                merged[k] = Entry(e.item, e.table, e.count, e.prob, e.notes, e.kind)
                order.append(k)
        return [merged[k] for k in order]

    # -- helpers
    def emit(self, state, ctx, item_expr, count_expr, extra_note=None, kind=None):
        notes = list(ctx.notes)
        if extra_note:
            notes.append(extra_note)
        count = self.count_text(count_expr)
        item_expr = item_expr.strip()
        if item_expr.startswith('~'):
            tname = re.match(r'~([a-z_0-9]+)', item_expr).group(1)
            state['tables'].add(tname)
            if count.startswith('^') or count.startswith('$'):
                count = ''
            state['out'].append(Entry(None, tname, count, ctx.prob, notes, kind or self.kind_for(ctx)))
            return
        if item_expr.startswith('$'):
            dist = state['objvars'].get(item_expr[1:])
            if dist:
                extra = notes[len(ctx.notes):]
                for (it, p, nts) in dist:
                    if it.startswith('~'):
                        tname = re.match(r'~([a-z_0-9]+)', it).group(1)
                        state['tables'].add(tname)
                        state['out'].append(Entry(None, tname, count, p, list(nts) + extra, 'main'))
                    else:
                        state['out'].append(Entry(it, None, count, p, list(nts) + extra, 'main'))
            return
        if item_expr.startswith('npc_param(death_drop)'):
            state['out'].append(Entry('DEATH_DROP', None, count, ctx.prob, notes,
                                      'always' if not ctx.random_involved else 'main'))
            return
        if item_expr.startswith('npc_param('):
            return
        if item_expr == 'null' or not IDENT_RE.match(item_expr):
            return
        state['out'].append(Entry(item_expr, None, count, ctx.prob, notes, kind or self.kind_for(ctx)))

    @staticmethod
    def kind_for(ctx):
        if ctx.prob == 1 and not ctx.random_involved:
            return 'always'
        return 'main'

    @staticmethod
    def count_text(expr):
        if expr is None:
            return '1'
        expr = expr.strip()
        if re.match(r'^\d+$', expr):
            return expr
        m = re.match(r'~random_range\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', expr)
        if m:
            return '%s-%s' % (m.group(1), m.group(2))
        m = re.match(r'calc\s*\(\s*random\s*\(\s*(\d+)\s*\)\s*\+\s*(\d+)\s*\)', expr)
        if m:
            lo = int(m.group(2))
            return '%d-%d' % (lo, lo + int(m.group(1)) - 1)
        m = re.match(r'random\s*\(\s*(\d+)\s*\)', expr)
        if m:
            return '0-%d' % (int(m.group(1)) - 1)
        return expr

    def inline(self, key, ctx, state, depth):
        if depth > 12 or key in state['visited'][-6:]:
            return
        stmts = self.get_stmts(key)
        if stmts is None:
            return
        state['visited'].append(key)
        try:
            self.run(stmts, ctx, state, depth + 1)
        except ReturnSignal:
            pass

    # -- interpreter
    def run(self, stmts, ctx, state, depth):
        for st in stmts:
            kind = st[0]
            if kind == 'stmt':
                self.run_stmt(st[1], ctx, state, depth)
            elif kind == 'if':
                self.run_if(st[1], st[2], ctx, state, depth)
            elif kind == 'switch':
                self.run_switch(st[1], st[2], ctx, state, depth)
            elif kind == 'while':
                self.run(st[2], ctx.child(None, 'in loop'), state, depth)

    def run_stmt(self, text, ctx, state, depth):
        text = text.strip()
        m = RANDOM_ASSIGN_RE.match(text)
        if m:
            ctx.vars[m.group(1)] = int(m.group(2))
            return
        m = OBJVAR_ASSIGN_RE.match(text)
        if m and m.group(2) not in ('null', 'true', 'false') and not text.startswith('def_int'):
            name, val = m.group(1), m.group(2)
            state['objvars'].setdefault(name, []).append((val, ctx.prob, ctx.notes))
            return
        if text.startswith('obj_add(') or text.startswith('obj_add ('):
            args = split_args(_read_balanced(text, text.index('('))[0])
            if len(args) >= 2:
                self.emit(state, ctx, args[1], args[2] if len(args) > 2 else '1')
            return
        if text.startswith('inv_add('):
            args = split_args(_read_balanced(text, text.index('('))[0])
            if len(args) >= 2:
                self.emit(state, ctx, args[1], args[2] if len(args) > 2 else '1', 'placed in inventory')
            return
        if text.startswith('return'):
            rest = text[6:].strip()
            if rest.startswith('('):
                args = split_args(_read_balanced(rest, 0)[0])
                if state['mode'] == 'value' and args:
                    if len(args) == 1:
                        self.emit(state, ctx, args[0], '1')
                    else:
                        self.emit(state, ctx, args[0], args[1])
            raise ReturnSignal()
        m = CLUE_RE.match(text)
        if m:
            tier, n = m.group(1), int(m.group(2))
            p = None if ctx.prob is None else ctx.prob * Fraction(1, n)
            state['out'].append(Entry('CLUE_' + tier.upper(), None, '1', p,
                                      list(ctx.notes) + ['members'], 'tertiary'))
            return
        if text.startswith('~'):
            name = re.match(r'~([a-z_0-9]+)', text).group(1)
            if name.startswith('trail_'):
                return
            self.inline(('proc', name), ctx, state, depth)
            return
        if text.startswith('@'):
            name = re.match(r'@([a-z_0-9]+)', text).group(1)
            self.inline(('label', name), ctx, state, depth)
            return
        if text.startswith('gosub('):
            name = _read_balanced(text, text.index('('))[0].strip()
            self.inline(('proc', name), ctx, state, depth)
            return

    def classify_cond(self, cond, ctx):
        """Returns (found (var, op, K) or None, notes, skip)."""
        notes = []
        skip = False
        found = None
        parts = re.split(r'\s[&|]\s', cond)
        for part in parts:
            p = part.strip().strip('()').strip()
            if not p:
                continue
            if 'npc_findhero' in p and 'false' in p:
                skip = True
                continue
            if 'npc_findhero' in p or 'finduid' in p:
                continue
            if re.search(r'map_members\s*=\s*\^?true', p):
                notes.append('members')
                continue
            if re.search(r'map_members\s*=\s*\^?false', p):
                notes.append('free-to-play only')
                continue
            m = COND_RE.search(p)
            if m and m.group(1) in ctx.vars and found is None:
                found = (m.group(1), m.group(2), int(m.group(3)))
                continue
            if 'ring_of_wealth' in p:
                notes.append('with Ring of wealth')
                continue
            notes.append('if ' + p)
        return found, notes, skip

    def run_if(self, chain, else_block, ctx, state, depth):
        lo = {}
        all_notes = []
        for cond, body in chain:
            found, notes, skip = self.classify_cond(cond, ctx)
            all_notes.extend(notes)
            if skip:
                continue
            if found:
                var, op, k = found
                n = ctx.vars[var]
                base = lo.get(var, 0)
                if op == '<':
                    p = Fraction(max(k - base, 0), n)
                    lo[var] = max(base, k)
                elif op == '<=':
                    p = Fraction(max(k + 1 - base, 0), n)
                    lo[var] = max(base, k + 1)
                elif op == '=':
                    p = Fraction(1, n)
                    if k == base:
                        lo[var] = base + 1
                elif op == '!':
                    p = Fraction(n - 1, n)
                elif op == '>':
                    p = Fraction(max(n - 1 - k, 0), n)
                    lo[var] = n
                else:  # >=
                    p = Fraction(max(n - k, 0), n)
                    lo[var] = n
                child = ctx.child(p, None, True)
            else:
                child = ctx.child(None, None)
            for nt in notes:
                child.notes = child.notes + (nt,)
            try:
                self.run(body, child, state, depth)
            except ReturnSignal:
                pass
        if else_block is not None:
            if lo:
                var, base = next(iter(lo.items()))
                n = ctx.vars[var]
                child = ctx.child(Fraction(max(n - base, 0), n), None, True)
            else:
                child = ctx.child(None, None)
                if 'members' in all_notes:
                    child.notes = child.notes + ('free-to-play only',)
                elif 'free-to-play only' in all_notes:
                    child.notes = child.notes + ('members',)
                elif all_notes:
                    child.notes = child.notes + ('otherwise',)
            try:
                self.run(else_block, child, state, depth)
            except ReturnSignal:
                pass

    def run_switch(self, expr, cases, ctx, state, depth):
        m = re.match(r'random\s*\(\s*(\d+)\s*\)', expr.strip())
        n = int(m.group(1)) if m else None
        if n is None:
            mv = re.match(r'\$(\w+)$', expr.strip())
            if mv and mv.group(1) in ctx.vars:
                n = ctx.vars[mv.group(1)]
        used = 0
        for labels, body in cases:
            labs = [l.strip() for l in labels.split(',') if l.strip()]
            if n is not None:
                if 'default' in labs:
                    p = Fraction(max(n - used, 0), n)
                else:
                    cnt = len([l for l in labs if re.match(r'^-?\d+$', l)]) or 1
                    p = Fraction(cnt, n)
                    used += cnt
                child = ctx.child(p, None, True)
            else:
                child = ctx.child(None, 'case ' + labels.strip())
            try:
                self.run(body, child, state, depth)
            except ReturnSignal:
                pass


def prob_text(p):
    """Fraction -> ('1/128', '0.78%')"""
    if p is None:
        return ('?', '')
    if p >= 1:
        return ('Always', '100%')
    if p == 0:
        return ('0', '0%')
    num, den = p.numerator, p.denominator
    pct = float(p) * 100
    pct_s = ('%.2f' % pct).rstrip('0').rstrip('.') + '%'
    return ('%d/%d' % (num, den), pct_s)
