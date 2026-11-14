const puppeteer = require('puppeteer');
const { pathToFileURL } = require('url');
const path = require('path');
(async () => {
  const file = path.resolve(process.argv[2]);
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(pathToFileURL(file).href, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 1500));
    const title = await page.title();
    const want = process.argv[3];
    console.log(JSON.stringify({ title, matches: title.includes(want), pageerrors: errors.length }));
  } finally { await browser.close(); }
})();
