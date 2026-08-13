// scripts/make-badge-icons.cjs
// Генерирует простые PNG-иконки для MSB Profile Badge (16/48/128).
// Без зависимостей — только Node built-ins (zlib, fs, path).

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const OUT_DIR = path.resolve(__dirname, '..', 'extensions', 'msb-profile-badge', 'icons');
const BG = [47, 111, 237, 255];    // MSB blue
const FG = [255, 255, 255, 255];   // white

if (!fs.existsSync(OUT_DIR)) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
}

// Растровая "M" 5x7 — классика пиксельных шрифтов
const M_5x7 = [
  '1...1',
  '11.11',
  '1.1.1',
  '1...1',
  '1...1',
  '1...1',
  '1...1',
];

// Растровая "M" 7x9 для больших иконок
const M_7x9 = [
  '1...1.',
  '1...1.',
  '11.11.',
  '1.1.1.',
  '1...1.',
  '1...1.',
  '1...1.',
  '1...1.',
  '1...1.',
];

function buildPixels(size) {
  // RGBA buffer
  const buf = Buffer.alloc(size * size * 4);

  // Фон — сплошной синий
  for (let i = 0; i < buf.length; i += 4) {
    buf[i] = BG[0];
    buf[i + 1] = BG[1];
    buf[i + 2] = BG[2];
    buf[i + 3] = BG[3];
  }

  // Выбираем растр в зависимости от размера
  const glyph = size >= 32 ? M_7x9 : M_5x7;
  const gw = glyph[0].length;
  const gh = glyph.length;

  // Подгоняем масштаб так, чтобы буква занимала ~70% иконки
  const targetH = Math.floor(size * 0.7);
  const scale = Math.max(1, Math.floor(targetH / gh));
  const letterH = gh * scale;
  const letterW = gw * scale;
  const offX = Math.floor((size - letterW) / 2);
  const offY = Math.floor((size - letterH) / 2);

  for (let gy = 0; gy < gh; gy++) {
    for (let gx = 0; gx < gw; gx++) {
      if (glyph[gy][gx] !== '1') continue;
      // Рисуем scale x scale пикселей для каждой клетки растра
      for (let sy = 0; sy < scale; sy++) {
        for (let sx = 0; sx < scale; sx++) {
          const px = offX + gx * scale + sx;
          const py = offY + gy * scale + sy;
          if (px < 0 || py < 0 || px >= size || py >= size) continue;
          const i = (py * size + px) * 4;
          buf[i] = FG[0];
          buf[i + 1] = FG[1];
          buf[i + 2] = FG[2];
          buf[i + 3] = FG[3];
        }
      }
    }
  }

  return buf;
}

// ── Минимальный PNG-энкодер (без фильтра, IHDR + IDAT + IEND) ──
function crc32(buf) {
  let c;
  if (!crc32.table) {
    crc32.table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) {
        c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      }
      crc32.table[n] = c >>> 0;
    }
  }
  c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c = crc32.table[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

function encodePNG(width, height, rgba) {
  // PNG signature
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  // IHDR
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;  // bit depth
  ihdr[9] = 6;  // color type RGBA
  ihdr[10] = 0; // compression
  ihdr[11] = 0; // filter
  ihdr[12] = 0; // interlace

  // IDAT — каждая строка начинается с байта фильтра (0 = None)
  const rowSize = width * 4;
  const raw = Buffer.alloc((rowSize + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (rowSize + 1)] = 0;
    rgba.copy(raw, y * (rowSize + 1) + 1, y * rowSize, y * rowSize + rowSize);
  }
  const idat = zlib.deflateSync(raw, { level: 9 });

  return Buffer.concat([
    sig,
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

for (const size of [16, 48, 128]) {
  const pixels = buildPixels(size);
  const png = encodePNG(size, size, pixels);
  const file = path.join(OUT_DIR, `icon${size}.png`);
  fs.writeFileSync(file, png);
  console.log(`wrote ${file} (${png.length} bytes)`);
}
