// Sample one image into a field artifact: the only place pixels are ever read.
//
// Why the browser does the decoding: the estate's renderer is Chromium, so the component that
// samples an image and the component that renders the result are the same engine. No new
// dependency (no PIL, no sharp), and no chance of "what I sampled" and "what I render"
// disagreeing about colour space.
//
// The sine table ships INSIDE the field artifact. Warp needs a periodic displacement, and
// sin() differs in the last bits between V8 and CPython; a digest over 1,200 neurons finds
// that difference every time. So the table is computed once (here), quantised to Q15, and
// both languages index the same integers. A table is a measurement; sin() is a negotiation.
//
// Run: NODE_PATH=$(npm root -g) node tools/sample_image.js --in <img> --out field.json
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { pathToFileURL } = require('url');

const GRID_W = 96, GRID_H = 54, SINE_ENTRIES = 256;

(async () => {
  const args = process.argv.slice(2);
  const get = (flag) => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : null; };
  const input = get('--in');
  const output = get('--out') || 'field.json';
  if (!input) { console.error('usage: sample_image.js --in <image> --out <field.json>'); process.exit(2); }
  const imgPath = path.resolve(input);
  if (!fs.existsSync(imgPath)) { console.error('no image at ' + imgPath); process.exit(2); }
  const bytes = fs.readFileSync(imgPath);
  const sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  // a file:// page is an opaque origin and Chromium refuses to decode its own subresources
  // from about:blank, so the image goes in as a data URL -- the bytes still come from the
  // file whose sha256 is recorded above.
  const ext = path.extname(imgPath).toLowerCase();
  const mime = ext === '.png' ? 'image/png' : (ext === '.webp' ? 'image/webp' : 'image/jpeg');
  const dataUrl = 'data:' + mime + ';base64,' + bytes.toString('base64');

  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const page = await browser.newPage();
    const field = await page.evaluate(async ({ url, gw, gh, entries }) => {
      const img = new Image();
      img.src = url;
      await img.decode();
      const cv = document.createElement('canvas');
      cv.width = gw; cv.height = gh;
      const cx = cv.getContext('2d', { willReadFrequently: true });
      cx.drawImage(img, 0, 0, gw, gh);
      const data = cx.getImageData(0, 0, gw, gh).data;
      const luma = new Array(gw * gh), hue = new Array(gw * gh), edge = new Array(gw * gh);
      for (let i = 0, p = 0; i < data.length; i += 4, p++) {
        const r = data[i], g = data[i + 1], b = data[i + 2];
        luma[p] = Math.round(0.2126 * r + 0.7152 * g + 0.0722 * b);
        const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
        let h = 0;
        if (d === 0) h = 0;
        else if (mx === r) h = 60 * (((g - b) / d) % 6);
        else if (mx === g) h = 60 * ((b - r) / d + 2);
        else h = 60 * ((r - g) / d + 4);
        hue[p] = ((Math.round(h) % 360) + 360) % 360;
      }
      // Sobel magnitude, integer, clamped: an edge field is what decides which neurons exist
      for (let y = 0; y < gh; y++) {
        for (let x = 0; x < gw; x++) {
          const at = (xx, yy) => luma[Math.min(gh - 1, Math.max(0, yy)) * gw +
                                     Math.min(gw - 1, Math.max(0, xx))];
          const gx = -at(x - 1, y - 1) - 2 * at(x - 1, y) - at(x - 1, y + 1)
                     + at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1);
          const gy = -at(x - 1, y - 1) - 2 * at(x, y - 1) - at(x + 1, y - 1)
                     + at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1);
          edge[y * gw + x] = Math.min(255, Math.round(Math.hypot(gx, gy) / 4));
        }
      }
      let mean = 0;
      for (const v of luma) mean += v;
      mean /= luma.length;
      let variance = 0;
      for (const v of luma) variance += (v - mean) * (v - mean);
      variance = Math.sqrt(variance / luma.length);
      const sine = [];
      for (let i = 0; i < entries; i++) sine.push(Math.round(Math.sin(i * 2 * Math.PI / entries) * 32767));
      // palette: 24 hue bins, weight = pixels landing in the bin
      const bins = new Array(24).fill(0);
      for (const h of hue) bins[Math.floor(h / 15) % 24]++;
      const palette = bins.map((w, i) => ({ h: i * 15 + 7, w: +(w / (gw * gh)).toFixed(5) }))
        .filter((e) => e.w > 0);
      return { luma, hue, edge, variance: +variance.toFixed(4), sine, palette,
               mean: +mean.toFixed(4), imgW: img.naturalWidth, imgH: img.naturalHeight };
    }, { url: dataUrl, gw: GRID_W, gh: GRID_H, entries: SINE_ENTRIES });

    const doc = {
      shape: 'shardex-image-field-v1',
      source: { path: imgPath.replace(/\\/g, '/'), sha256: sha256,
                w: field.imgW, h: field.imgH },
      grid: { w: GRID_W, h: GRID_H },
      variance: field.variance, mean: field.mean,
      luma: field.luma, hue: field.hue, edge: field.edge,
      palette: field.palette, sine_q15: field.sine,
      sampler: 'chromium getImageData, 96x54, luma 0.2126/0.7152/0.0722',
    };
    fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
    fs.writeFileSync(path.resolve(output), JSON.stringify(doc));
    console.log('sampled ' + imgPath);
    console.log('  ' + field.imgW + 'x' + field.imgH + ' -> ' + GRID_W + 'x' + GRID_H +
                ' mean luma ' + field.mean + ' variance(std) ' + field.variance);
    console.log('  sha256 ' + sha256.slice(0, 16) + '  palette bins ' + field.palette.length +
                '  sine entries ' + field.sine.length);
    console.log('  -> ' + path.resolve(output));
    if (field.variance < 40) {
      console.error('REFUSED-WOULD-BE: variance ' + field.variance + ' < 40 (flat image)');
      process.exitCode = 3;
    }
  } finally {
    await browser.close();
  }
})();
