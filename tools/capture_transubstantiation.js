// Capture a forged 3D scene to frames, probe it from inside the page, and — with --live —
// MUTATE IT WHILE IT RENDERS.
//
// Two jobs, and the second is the one that matters:
//   1. drive window.__step(tick) and screenshot each tick -> frames/frame_%04d.png
//   2. probe the page's own state and write browser_probe.json, so Python can compare the
//      browser's digest against its own. A golden run only Python can produce proves Python
//      agrees with itself; the probe proves the two builds are the same thing.
//
// --live <ops.json> sends each operator through window.__mutate(op) BETWEEN FRAMES, from
// outside the page. That is the whole point of this harness: the mutation lands in a scene
// that is already running, and every applied op comes back with the digest the page computed
// AFTER applying it. meshscene.py --verify replays that log and must reproduce every digest;
// a mutation nobody can replay is a session, not an artifact.
//
// Frame index comes from the loop counter, never from the clock (YGGDRASIL_RENDER_PLAYBOOK).
// Run: NODE_PATH=$(npm root -g) node tools/capture_transubstantiation.js <dir> [frames] [--live ops.json]
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { pathToFileURL } = require('url');

(async () => {
  const argv = process.argv.slice(2);
  const liveIndex = argv.indexOf('--live');
  const liveOps = liveIndex >= 0 ? argv[liveIndex + 1] : null;
  const pickIndex = argv.indexOf('--pick');
  const pickArg = pickIndex >= 0 ? argv[pickIndex + 1] : null;
  const positional = argv.filter((a, i) => (!a.startsWith('--') &&
    i !== liveIndex + 1 && i !== pickIndex + 1) || i === 0);
  const dir = positional[0];
  const FRAMES = parseInt(positional[1] || '240', 10);
  if (!dir) { console.error('usage: capture_transubstantiation.js <scene-dir> [frames] [--live ops.json]'); process.exit(2); }

  const sceneMesh = path.resolve(dir, 'meshscene.html');
  const sceneFlame = path.resolve(dir, 'flame3d.html');
  const scene = fs.existsSync(sceneMesh) ? sceneMesh : sceneFlame;
  if (!fs.existsSync(scene)) { console.error('no scene at ' + sceneMesh + ' or ' + sceneFlame); process.exit(2); }
  const isMesh = scene === sceneMesh;
  const framesDir = path.resolve(dir, 'frames');
  fs.mkdirSync(framesDir, { recursive: true });

  const ops = liveOps ? JSON.parse(fs.readFileSync(path.resolve(liveOps), 'utf-8')) : [];
  // evenly spaced inside the run, so the first op lands after the scene is legible and the
  // last one still has frames left to show its effect
  const opFrame = (i) => Math.floor(FRAMES * (i + 1) / (ops.length + 1));

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--force-device-scale-factor=1',
           '--hide-scrollbars', '--disable-lcd-text'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    await page.goto(pathToFileURL(scene).href, { waitUntil: 'load', timeout: 60000 });
    await page.waitForFunction('typeof window.__step === "function"', { timeout: 20000 });

    const total = await page.evaluate(() => window.__TOTAL);
    const probeStates = {};
    const goldenFile = path.resolve(dir, isMesh ? 'golden_live.json' : 'golden3d.json');
    const golden = JSON.parse(fs.readFileSync(goldenFile, 'utf-8'));
    // A pick is a human input, so it is captured before any op runs and written beside the op
    // log: the click, the neuron it resolved to, that neuron's world position, and the mesh
    // digest at that moment. --verify re-derives all of it from the base mesh.
    let picked = null;
    if (isMesh && pickArg) {
      const xy = pickArg.split(',').map(Number);
      if (xy.length !== 2 || !isFinite(xy[0]) || !isFinite(xy[1])) {
        console.error('--pick wants a screen point as x,y'); process.exit(2);
      }
      await page.evaluate(() => window.__step(0));
      picked = await page.evaluate((x, y) => window.__pickNeuron(x, y), xy[0], xy[1]);
      picked.click = xy;
      fs.writeFileSync(path.resolve(dir, 'picked.json'), JSON.stringify(picked, null, 1));
      console.log('PICK click ' + xy[0] + ',' + xy[1] + ' -> neuron ' + picked.index +
                  ' at ' + picked.screen.join(',') + ' (' + picked.distance_px + ' px from cursor)');
    }
    if (isMesh) {
      const baseDigits = await page.evaluate(() => window.__digest());
      console.log('probe: base mesh ' + baseDigits + ' (golden ' + golden.mesh_digest + ')');
    } else {
      const geo = await page.evaluate(() => window.__geo());
      console.log('probe: segments=' + geo.segments + ' leaves=' + geo.leaves +
                  ' digest=' + geo.digest);
    }
    // Capture BASE mesh digest and field sha256 BEFORE any live ops are applied
    // so that --verify can confirm the inputs match what the browser actually used
    let baseMeshDigest = null;
    let fieldSha256 = null;
    if (isMesh) {
      baseMeshDigest = golden.mesh_digest;  // this is the base mesh before live ops
      const fieldPath = path.resolve(dir, 'field.json');
      fieldSha256 = crypto.createHash('sha256').update(fs.readFileSync(fieldPath)).digest('hex');
      console.log('probe: base mesh ' + baseMeshDigest + ', field sha256 ' + fieldSha256);
    }
    for (const key of Object.keys(golden.states)) {
      probeStates[key] = await page.evaluate((t) => window.__state(t), parseInt(key.slice(1), 10));
    }

    let sent = 0;
    for (let i = 0; i < FRAMES; i++) {
      const tick = Math.round((total * i) / FRAMES);
      const name = path.join(framesDir, 'frame_' + String(i).padStart(4, '0') + '.png');
      await page.evaluate((t) => window.__step(t), tick);
      if (isMesh && sent < ops.length && i === opFrame(sent)) {
        let op = ops[sent];
        if (op.neuron === 'pick') {
          if (!picked) { console.error('op ' + sent + ' wants the picked neuron but --pick was not given'); process.exit(2); }
          op = Object.assign({}, op, { neuron: picked.index });
        }
        const record = await page.evaluate((o) => window.__mutate(o), op);
        console.log('MUTATE frame ' + i + ' seq ' + record.seq + ' ' + record.op.kind +
                    ' -> ' + record.digest + ' (' + record.neurons + 'n/' +
                    record.synapses + 's)');
        sent += 1;
      }
      await page.screenshot({ path: name, type: 'png', captureBeyondViewport: false });
      if (i % 40 === 0) console.log('frame ' + i + '/' + FRAMES + ' tick=' + tick);
    }

    const probe = { scene: path.basename(scene), total: total, states: probeStates,
                    errors: errors };
    if (isMesh) {
      const applied = await page.evaluate(() => window.__applied());
      probe.mesh_digest = await page.evaluate(() => window.__digest());
      probe.applied = applied.length;
      // Capture base mesh and field hash BEFORE live ops
      probe.base_mesh_digest = baseMeshDigest;
      probe.field_sha256 = fieldSha256;
      // Provenance, not decoration: meshscene.py --verify compares this against the page and
      // the op log on disk, so editing either after the capture turns the probe into a refusal
      // instead of evidence for a scene nobody rendered.
      const prov = await page.evaluate(() => window.__scene_provenance());
      probe.provenance = Object.assign({
        html_sha: crypto.createHash('sha256').update(fs.readFileSync(scene)).digest('hex'),
      }, prov);
      fs.writeFileSync(path.resolve(dir, 'applied_ops.jsonl'),
                       applied.map((r) => JSON.stringify(r)).join('\n') + (applied.length ? '\n' : ''));
      console.log('applied ' + applied.length + ' live op(s); final mesh ' + probe.mesh_digest);
    }
    fs.writeFileSync(path.resolve(dir, 'browser_probe.json'), JSON.stringify(probe, null, 1));
    console.log('captured ' + FRAMES + ' frames -> ' + framesDir +
                ' (errors: ' + errors.length + ')');
    if (errors.length) { console.error('CONSOLE ERRORS: ' + errors.slice(0, 5).join(' | ')); process.exitCode = 1; }
  } finally {
    await browser.close();
  }
})();
