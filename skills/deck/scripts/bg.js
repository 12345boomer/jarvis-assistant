#!/usr/bin/env node
// bg.js OUT.png "#0B1F3A" "#1E5BBF" [angle=135] [W=1920] [H=1080] [--glow]
// Full-bleed gradient background (optionally with a soft radial glow) for slides. pptxgenjs has no gradient fills,
// so backgrounds are images. Uses sharp (SVG -> PNG), no network.
const sharp = require('sharp');
const [out, a = '#0B1F3A', b = '#1E5BBF', angleArg = '135', wArg = '1920', hArg = '1080', ...flags] = process.argv.slice(2);
if (!out) { console.error('usage: bg.js OUT.png #from #to [angle] [w] [h] [--glow]'); process.exit(2); }
const W = +wArg, H = +hArg, angle = +angleArg, glow = flags.includes('--glow');
const rad = (angle * Math.PI) / 180, x2 = 50 + Math.cos(rad) * 50, y2 = 50 + Math.sin(rad) * 50, x1 = 100 - x2, y1 = 100 - y2;
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">
  <defs>
    <linearGradient id="g" x1="${x1}%" y1="${y1}%" x2="${x2}%" y2="${y2}%"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient>
    <radialGradient id="r" cx="78%" cy="22%" r="55%"><stop offset="0" stop-color="#ffffff" stop-opacity="0.16"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  ${glow ? '<rect width="100%" height="100%" fill="url(#r)"/>' : ''}
</svg>`;
sharp(Buffer.from(svg)).png().toFile(out).then(() => console.log(out)).catch(e => { console.error(e.message); process.exit(1); });
