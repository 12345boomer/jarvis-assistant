#!/usr/bin/env node
// icon.js OUT.png FaChartLine "#4FC3F7" [size=512]
// Renders a react-icons icon (Font Awesome "fa" set, Material "md", Heroicons "hi2", Lucide "lu") to a PNG for slides.
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const sharp = require('sharp');
const [out, name, color = '#FFFFFF', sizeArg = '512'] = process.argv.slice(2);
if (!out || !name) { console.error('usage: icon.js OUT.png IconName #color [size]'); process.exit(2); }
const sets = { Fa: 'react-icons/fa', Md: 'react-icons/md', Hi: 'react-icons/hi2', Lu: 'react-icons/lu', Bs: 'react-icons/bs', Io: 'react-icons/io5' };
const prefix = Object.keys(sets).find(p => name.startsWith(p));
if (!prefix) { console.error('icon name must start with Fa, Md, Hi, Lu, Bs or Io (react-icons)'); process.exit(2); }
const Icon = require(sets[prefix])[name];
if (!Icon) { console.error(`no icon ${name} in ${sets[prefix]}`); process.exit(2); }
const svg = renderToStaticMarkup(React.createElement(Icon, { color, size: +sizeArg }));
sharp(Buffer.from(svg)).png().toFile(out).then(() => console.log(out)).catch(e => { console.error(e.message); process.exit(1); });
