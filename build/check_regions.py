"""Compare each NPC's graph region tag with the region implied by its precise areas in npc_safety.json."""
import json, sys
site = sys.argv[1] if len(sys.argv) > 1 else 'site'
G = json.load(open(site + '/data/graph.json', encoding='utf-8'))
A = {a['slug']: a for a in json.load(open(site + '/data/tables/areas.json', encoding='utf-8'))['areas']}
N = json.load(open(site + '/data/tables/npc_safety.json', encoding='utf-8'))['npcs']
graph_r = {n['id'][4:]: set(n['r']) for n in G['nodes'] if n['type'] == 'npc'}
bad = 0
for n in N:
    implied = set()
    for slug in n['areas']:
        if slug in A:
            implied |= set(A[slug]['regions'])
    g = graph_r.get(n['npc'])
    if g is None:
        continue
    if implied != g:
        bad += 1
        print('%-32s areas=%-40s safety=%s graph=%s' % (n['npc'], ','.join(n['areas'])[:40], sorted(implied), sorted(g)))
print('npcs checked: %d, disagreements: %d' % (len(N), bad))
