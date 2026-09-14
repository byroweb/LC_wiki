// Shared loader for the Client-TS renderer (revision 254 layout) under bun.
// Loads the packed cache from source/engine/data/pack, preloads every model from
// ondemand.zip and initialises the software renderer so ObjType/NpcType can draw.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { createRequire } from 'node:module';

export const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
export const clientDir = path.resolve(here, '../source/client');
export const packDir = path.resolve(here, '../source/engine/data/pack');

export async function loadClient() {
    // the client's Jpeg helper creates a <canvas> at import time; give it a harmless stub
    (globalThis as any).document = { createElement: () => ({ getContext: () => null, width: 0, height: 0 }) };
    (globalThis as any).window = globalThis;

    const { default: Jagfile } = await import(path.join(clientDir, 'src/io/Jagfile.ts'));
    const { default: ObjType } = await import(path.join(clientDir, 'src/config/ObjType.ts'));
    const { default: NpcType } = await import(path.join(clientDir, 'src/config/NpcType.ts'));
    const { default: Model } = await import(path.join(clientDir, 'src/dash3d/Model.ts'));
    const { default: Pix3D } = await import(path.join(clientDir, 'src/dash3d/Pix3D.ts'));
    const { default: Pix2D } = await import(path.join(clientDir, 'src/graphics/Pix2D.ts'));
    let unzipSync: (data: Uint8Array) => Record<string, Uint8Array>;
    try {
        unzipSync = createRequire(path.join(clientDir, 'package.json'))('fflate').unzipSync;
    } catch (_e) {
        unzipSync = createRequire(path.join(packDir, '../../package.json'))('fflate').unzipSync;
    }

    const jag = (name: string) => new Jagfile(new Uint8Array(fs.readFileSync(path.join(packDir, 'client', name))));
    const config = jag('config');
    Pix3D.unpackTextures(jag('textures'));
    Pix3D.initColourTable(0.8);
    Pix3D.initPool(20);

    // models: ondemand.zip entries "1.<id>", each gzip + 2-byte version trailer
    const zip = unzipSync(new Uint8Array(fs.readFileSync(path.join(packDir, 'ondemand.zip'))));
    const models: [number, Uint8Array][] = [];
    for (const name of Object.keys(zip)) {
        if (!name.startsWith('1.')) continue;
        let data: Uint8Array = zip[name];
        if (data.length > 2 && data[0] === 0x1f && data[1] === 0x8b) {
            data = new Uint8Array(zlib.gunzipSync(data.subarray(0, data.length - 2)));
        }
        models.push([parseInt(name.slice(2)), data]);
    }
    const total = Math.max(...models.map(m => m[0])) + 1;
    Model.init(total, { requestModel() { /* everything is preloaded */ } });
    for (const [id, data] of models) Model.unpack(id, data);

    ObjType.init(config, true);
    NpcType.init(config);
    return { ObjType, NpcType, Model, Pix3D, Pix2D, modelCount: models.length };
}

// ---- minimal PNG encoder (RGBA)
const crcTable = new Int32Array(256);
for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crcTable[n] = c;
}
function crc32(buf: Uint8Array): number {
    let c = -1;
    for (let i = 0; i < buf.length; i++) c = crcTable[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
    return (c ^ -1) >>> 0;
}
function chunk(type: string, data: Uint8Array): Uint8Array {
    const out = new Uint8Array(12 + data.length);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, data.length);
    out.set(new TextEncoder().encode(type), 4);
    out.set(data, 8);
    dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
    return out;
}
export function writePng(file: string, rgba: Uint8Array, w: number, h: number): void {
    const raw = new Uint8Array((w * 4 + 1) * h);
    for (let y = 0; y < h; y++) {
        raw[y * (w * 4 + 1)] = 0;
        raw.set(rgba.subarray(y * w * 4, (y + 1) * w * 4), y * (w * 4 + 1) + 1);
    }
    const ihdr = new Uint8Array(13);
    const dv = new DataView(ihdr.buffer);
    dv.setUint32(0, w);
    dv.setUint32(4, h);
    ihdr[8] = 8; ihdr[9] = 6;
    const idat = new Uint8Array(zlib.deflateSync(raw, { level: 9 }));
    const sig = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    fs.writeFileSync(file, Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', idat), chunk('IEND', new Uint8Array(0))]));
}
