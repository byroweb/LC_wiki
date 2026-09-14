// Render every item's 32x32 inventory icon exactly the way the client does, by
// driving the Client-TS renderer (Model / Pix3D / ObjType.getSprite) under bun.
//
// Prerequisites:
//   1. source/client  = LostCityRS/Client-TS (same revision as the content)
//   2. source/engine  = LostCityRS/Engine-TS with the cache packed:
//        cd source/engine && bun install && bun run tools/pack/Build.ts
// Run (from source/client so its dependencies resolve):  bun run ../../build/icons.ts
import fs from 'node:fs';
import path from 'node:path';
import { loadClient, writePng, here } from './client254.ts';

const outDir = path.resolve(here, '../site/icons/items');
const { ObjType, modelCount } = await loadClient();
console.log(`loaded ${modelCount} models, ${ObjType.numDefinitions} item definitions`);

fs.mkdirSync(outDir, { recursive: true });
let ok = 0, failed = 0;
const rgba = new Uint8Array(32 * 32 * 4);
for (let id = 0; id < ObjType.numDefinitions; id++) {
    try {
        const obj = ObjType.list(id);
        if (!obj || !obj.name) continue;
        const icon = ObjType.getSprite(id, 1, 0);
        if (!icon) { failed++; continue; }
        for (let i = 0; i < 32 * 32; i++) {
            const p = icon.data[i];
            if (p === 0) {
                rgba[i * 4] = rgba[i * 4 + 1] = rgba[i * 4 + 2] = rgba[i * 4 + 3] = 0;
            } else {
                rgba[i * 4] = (p >> 16) & 0xff;
                rgba[i * 4 + 1] = (p >> 8) & 0xff;
                rgba[i * 4 + 2] = p & 0xff;
                rgba[i * 4 + 3] = 255;
            }
        }
        writePng(path.join(outDir, id + '.png'), rgba, 32, 32);
        ok++;
    } catch (err) {
        failed++;
        if (failed <= 5) console.error('icon', id, err);
    }
}
console.log(`rendered ${ok} item icons (${failed} failed) -> ${outDir}`);
