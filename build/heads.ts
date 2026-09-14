// Render an icon for every NPC using the Client-TS renderer under bun:
//   - NPCs with a chathead (head1..headN models) get the chathead, framed like the
//     in-game chat interface (zoom 796, xan 40, yan 166) and lit the same way.
//   - other NPCs (monsters, animals) get a small front view of their body model.
// Output: site/icons/npcs/<id>.png (48x48, transparent background)
//
// Prerequisites are the same as build/icons.ts.  Run:  bun run ../../build/heads.ts
import fs from 'node:fs';
import path from 'node:path';
import { loadClient, writePng, here } from './client254.ts';

const outDir = path.resolve(here, '../site/icons/npcs');
const SIZE = 48;          // output icon size
const FIT = 42;           // largest extent of the rendered subject inside the icon

const { NpcType, Model, Pix3D, Pix2D } = await loadClient();

function renderInto(model: any, size: number, zoom: number, xan: number, yan: number, yCentre: number): Int32Array {
    const pixels = new Int32Array(size * size);
    Pix3D.lowDetail = false;
    Pix2D.setPixels(pixels, size, size);
    Pix2D.fillRect(0, 0, size, size, 0);
    Pix3D.setRenderClipping();
    Pix3D.originX = size >> 1;
    Pix3D.originY = size >> 1;
    const eyeY = (Pix3D.sinTable[xan] * zoom) >> 16;
    const eyeZ = (Pix3D.cosTable[xan] * zoom) >> 16;
    model.objRender(0, yan, 0, xan, 0, eyeY + yCentre, eyeZ);
    return pixels;
}
function bbox(px: Int32Array, size: number): [number, number, number, number] | null {
    let x0 = size, y0 = size, x1 = -1, y1 = -1;
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        if (px[x + y * size] !== 0) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    }
    return x1 < 0 ? null : [x0, y0, x1, y1];
}
/** Render twice: once to measure, once at a zoom that makes the subject fit FIT px, then crop+centre. */
function renderFitted(model: any, xan: number, yan: number, baseZoom: number, yCentre: number): Uint8Array | null {
    const BIG = 160;
    let px = renderInto(model, BIG, baseZoom, xan, yan, yCentre);
    let bb = bbox(px, BIG);
    if (!bb) return null;
    let extent = Math.max(bb[2] - bb[0] + 1, bb[3] - bb[1] + 1);
    if (extent >= BIG - 2) {
        px = renderInto(model, BIG, baseZoom * 3, xan, yan, yCentre);
        bb = bbox(px, BIG);
        if (!bb) return null;
        extent = Math.max(bb[2] - bb[0] + 1, bb[3] - bb[1] + 1);
        baseZoom *= 3;
    }
    const zoom = Math.max(1, Math.round(baseZoom * extent / FIT));
    px = renderInto(model, BIG, zoom, xan, yan, yCentre);
    bb = bbox(px, BIG);
    if (!bb) return null;
    const w = bb[2] - bb[0] + 1, h = bb[3] - bb[1] + 1;
    const rgba = new Uint8Array(SIZE * SIZE * 4);
    const ox = ((SIZE - w) >> 1) - bb[0], oy = ((SIZE - h) >> 1) - bb[1];
    for (let y = bb[1]; y <= bb[3]; y++) for (let x = bb[0]; x <= bb[2]; x++) {
        const p = px[x + y * BIG];
        const tx = x + ox, ty = y + oy;
        if (p === 0 || tx < 0 || ty < 0 || tx >= SIZE || ty >= SIZE) continue;
        const i = (tx + ty * SIZE) * 4;
        rgba[i] = (p >> 16) & 0xff; rgba[i + 1] = (p >> 8) & 0xff; rgba[i + 2] = p & 0xff; rgba[i + 3] = 255;
    }
    return rgba;
}

fs.mkdirSync(outDir, { recursive: true });
let heads = 0, bodies = 0, failed = 0;
const kinds: Record<number, string> = {};
for (let id = 0; id < NpcType.numDefinitions; id++) {
    try {
        const npc = NpcType.list(id);
        if (!npc || !npc.name) continue;
        let rgba: Uint8Array | null = null;
        if (npc.head) {
            const head = npc.getHead();
            if (head) {
                const lit = Model.copyForAnim(head, true, true, false);
                lit.calculateNormals(64, 768, -50, -10, -50, true);
                lit.calcBoundingCylinder();
                rgba = renderFitted(lit, 40, 166, 796, (lit.minY / 2) | 0);
                if (rgba) { heads++; kinds[id] = 'head'; }
            }
        }
        if (!rgba && npc.model) {
            const body = npc.getTempModel(-1, -1, null);
            if (body) {
                body.calcBoundingCylinder();
                const height = Math.abs(body.minY) || 100;
                const radius = body.radius || 50;
                const baseZoom = Math.max(height, radius * 2) * 512 / 100 + 200;
                rgba = renderFitted(body, 120, 0, baseZoom, (body.minY / 2) | 0);
                if (rgba) { bodies++; kinds[id] = 'body'; }
            }
        }
        if (rgba) writePng(path.join(outDir, id + '.png'), rgba, SIZE, SIZE);
    } catch (err) {
        failed++;
        if (failed <= 5) console.error('npc', id, err);
    }
}
fs.writeFileSync(path.join(outDir, 'kinds.json'), JSON.stringify(kinds));
console.log(`rendered ${heads} chatheads and ${bodies} body icons (${failed} failed) -> ${outDir}`);
