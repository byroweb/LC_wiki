"""Shared helpers: paths, config-file parsing, pack (id<->name) tables."""
import os
import re
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'source')
CONTENT = os.path.join(SOURCE, 'content')
SCRIPTS = os.path.join(CONTENT, 'scripts')
SITE = os.path.join(ROOT, 'site')
STATIC = os.path.join(ROOT, 'build', 'static')


def set_content(path):
    """Point the whole build at a different Content checkout (build.py --content <path>)."""
    global CONTENT, SCRIPTS
    CONTENT = os.path.abspath(path)
    SCRIPTS = os.path.join(CONTENT, 'scripts')
    if not os.path.isdir(SCRIPTS):
        raise SystemExit('not a Content checkout (no scripts/ folder): %s' % CONTENT)

_COMMENT_RE = re.compile(r'(^|\s)//.*$')
_HEADER_RE = re.compile(r'^\[([^\]]+)\]$')


def read_text(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def strip_comment(line):
    return _COMMENT_RE.sub('', line)


def relpath(path):
    return os.path.relpath(path, CONTENT).replace('\\', '/')


def parse_config_files(ext):
    """Parse every scripts/**/*.<ext> file.

    Returns dict config_name -> {
        'name': str, 'file': relative path, 'kv': [(k, v), ...],
        'd': {k: last value}, 'multi': {k: [values]}, 'params': {k: v}
    }
    """
    out = {}
    pattern = os.path.join(SCRIPTS, '**', '*.' + ext)
    for path in sorted(glob.glob(pattern, recursive=True)):
        rel = relpath(path)
        cur = None
        for raw in read_text(path).splitlines():
            line = strip_comment(raw).strip()
            if not line:
                continue
            m = _HEADER_RE.match(line)
            if m:
                cur = {'name': m.group(1), 'file': rel, 'kv': [], 'd': {}, 'multi': {}, 'params': {}}
                out[m.group(1)] = cur
                continue
            if cur is None or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip()
            cur['kv'].append((k, v))
            cur['d'][k] = v
            cur['multi'].setdefault(k, []).append(v)
            if k == 'param' and ',' in v:
                pk, pv = v.split(',', 1)
                cur['params'][pk.strip()] = pv.strip()
    return out


def parse_pack(name):
    """pack/<name>.pack -> (id->name, name->id)"""
    id2name = {}
    for line in read_text(os.path.join(CONTENT, 'pack', name + '.pack')).splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        i, n = line.split('=', 1)
        id2name[int(i)] = n
    return id2name, {n: i for i, n in id2name.items()}


def pretty(name):
    """Fallback display name from a config name."""
    return name.replace('_', ' ').strip().capitalize()


def hexcolour(v):
    v = v.strip()
    if v.lower().startswith('0x'):
        return int(v, 16)
    return int(v)


def parse_labels():
    """maps/labels.txt -> [{'name','x','z','size'}]"""
    labels = []
    for line in read_text(os.path.join(CONTENT, 'maps', 'labels.txt')).splitlines():
        line = line.strip()
        if not line or not line.startswith('='):
            continue
        parts = line[1:].split(',')
        if len(parts) < 4:
            continue
        labels.append({
            'name': parts[0].replace('/', ' ').strip(),
            'x': int(parts[1]), 'z': int(parts[2]), 'size': int(parts[3]),
        })
    return labels
