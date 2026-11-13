// deck_template.js: the house style for Jarvis decks. Copy this file, change CONTENT and THEME, run:
//   deckjs deck.js            -> writes OUT (defaults to ~/Downloads/Jarvis/<slug>.pptx)
// Every helper below is reusable; a real deck is 10-16 slides mixing these slide types.
const pptxgen = require('pptxgenjs');
const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const HERE = __dirname;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'deck-'));
const OUT = process.env.DECK_OUT || path.join(os.homedir(), 'Downloads', 'Jarvis', 'template-demo.pptx');

// ---------- THEME (pick per audience; hex without '#', pptxgenjs rule) ----------
const THEME = {
  name: 'Navy / Electric',           // OFC + finance: navy, electric blue, white. OceanCrest: deep teal + sand. Casual: charcoal + amber.
  bg1: '0B1F3A', bg2: '1E5BBF',      // gradient ends for dark slides
  paper: 'F6F8FB', ink: '14213D',    // light slides
  accent: '4FC3F7', accent2: 'FFB347', muted: '8FA3BF', white: 'FFFFFF',
  font: 'Avenir Next', mono: 'Menlo',
};

// ---------- assets (generated locally, no network) ----------
function bg(name, a, b, angle = 135, glow = true) {
  const out = path.join(TMP, `${name}.png`);
  execFileSync('node', [path.join(HERE, 'bg.js'), out, '#' + a, '#' + b, String(angle), '1920', '1080', ...(glow ? ['--glow'] : [])]);
  return out;
}
function icon(name, color = THEME.accent) {
  const out = path.join(TMP, `${name}.png`);
  execFileSync('node', [path.join(HERE, 'icon.js'), out, name, '#' + color, '512']);
  return out;
}
const BG_DARK = bg('dark', THEME.bg1, THEME.bg2);
const BG_SECTION = bg('section', THEME.bg2, THEME.bg1, 45);
const BG_LIGHT = bg('light', 'FFFFFF', THEME.paper, 90, false);

// ---------- deck ----------
const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';               // 13.33 x 7.5 in
pres.author = 'Jarvis for Ben Eydlin';
const W = 13.33, H = 7.5;
let n = 0;

function footer(slide, dark) {
  n += 1;
  slide.addText(CONTENT.event, { x: 0.5, y: H - 0.45, w: 6, h: 0.3, fontFace: THEME.font, fontSize: 10, color: dark ? THEME.muted : THEME.muted, isTextBox: true, margin: 0 });
  slide.addText(String(n), { x: W - 1.0, y: H - 0.45, w: 0.5, h: 0.3, fontFace: THEME.mono, fontSize: 10, color: THEME.muted, align: 'right', isTextBox: true, margin: 0 });
}

function titleSlide({ title, subtitle, meta }) {
  const s = pres.addSlide(); s.background = { path: BG_DARK };
  s.addShape(pres.ShapeType.rect, { x: 0.8, y: 2.55, w: 0.12, h: 2.3, fill: { color: THEME.accent }, line: { color: THEME.accent } });
  s.addText(title, { x: 1.15, y: 2.3, w: 11, h: 1.6, fontFace: THEME.font, fontSize: 46, bold: true, color: THEME.white, valign: 'bottom', isTextBox: true, margin: 0 });
  s.addText(subtitle, { x: 1.15, y: 3.95, w: 11, h: 0.8, fontFace: THEME.font, fontSize: 22, color: THEME.accent, isTextBox: true, margin: 0 });
  s.addText(meta, { x: 1.15, y: 6.4, w: 11, h: 0.4, fontFace: THEME.font, fontSize: 13, color: THEME.muted, isTextBox: true, margin: 0 });
  s.addNotes(CONTENT.notes.title || 'Open: who you are, why this matters in one sentence.');
}

function sectionSlide({ title, kicker, notes }) {
  const s = pres.addSlide(); s.background = { path: BG_SECTION };
  s.addText(kicker || '', { x: 0.8, y: 2.6, w: 11, h: 0.4, fontFace: THEME.font, fontSize: 13, color: THEME.accent, charSpacing: 6, isTextBox: true, margin: 0 });
  s.addText(title, { x: 0.8, y: 3.0, w: 11.5, h: 1.4, fontFace: THEME.font, fontSize: 40, bold: true, color: THEME.white, isTextBox: true, margin: 0 });
  footer(s, true); s.addNotes(notes || title);
}

function bulletSlide({ title, bullets, iconName, notes, dark = false }) {
  const s = pres.addSlide(); s.background = { path: dark ? BG_DARK : BG_LIGHT };
  const ink = dark ? THEME.white : THEME.ink;
  s.addText(title, { x: 0.8, y: 0.55, w: 9.5, h: 0.9, fontFace: THEME.font, fontSize: 30, bold: true, color: ink, isTextBox: true, margin: 0 });
  s.addShape(pres.ShapeType.rect, { x: 0.8, y: 1.5, w: 1.1, h: 0.06, fill: { color: THEME.accent }, line: { color: THEME.accent } });
  if (iconName) s.addImage({ path: icon(iconName, THEME.accent), x: W - 3.3, y: 1.0, w: 2.4, h: 2.4 });
  const items = bullets.map((b, i) => ({ text: b, options: { bullet: { indent: 18 }, breakLine: i < bullets.length - 1, paraSpaceAfter: 10 } }));
  s.addText(items, { x: 0.8, y: 1.9, w: iconName ? 8.6 : 11.5, h: 4.4, fontFace: THEME.font, fontSize: bullets.length > 4 ? 18 : 21, color: ink, valign: 'top', isTextBox: true });
  footer(s, dark); s.addNotes(notes || title);
}

function statSlide({ title, stats, notes }) {      // 2-4 big numbers
  const s = pres.addSlide(); s.background = { path: BG_DARK };
  s.addText(title, { x: 0.8, y: 0.55, w: 11.5, h: 0.9, fontFace: THEME.font, fontSize: 30, bold: true, color: THEME.white, isTextBox: true, margin: 0 });
  const cw = (W - 1.6 - (stats.length - 1) * 0.3) / stats.length;
  stats.forEach((st, i) => {
    const x = 0.8 + i * (cw + 0.3);
    s.addShape(pres.ShapeType.roundRect, { x, y: 2.0, w: cw, h: 3.4, fill: { color: THEME.white, transparency: 90 }, line: { color: THEME.accent, transparency: 40 }, rectRadius: 0.12 });
    s.addText(st.value, { x: x + 0.3, y: 2.3, w: cw - 0.6, h: 1.5, fontFace: THEME.font, fontSize: 48, bold: true, color: THEME.accent2, isTextBox: true, margin: 0 });
    s.addText(st.label, { x: x + 0.3, y: 3.9, w: cw - 0.6, h: 1.2, fontFace: THEME.font, fontSize: 16, color: THEME.white, isTextBox: true, margin: 0 });
  });
  footer(s, true); s.addNotes(notes || title);
}

function chartSlide({ title, categories, series, notes, type = 'bar' }) {
  const s = pres.addSlide(); s.background = { path: BG_LIGHT };
  s.addText(title, { x: 0.8, y: 0.55, w: 11.5, h: 0.9, fontFace: THEME.font, fontSize: 30, bold: true, color: THEME.ink, isTextBox: true, margin: 0 });
  const t = { bar: pres.ChartType.bar, line: pres.ChartType.line, pie: pres.ChartType.doughnut }[type];
  s.addChart(t, series.map(sr => ({ name: sr.name, labels: categories, values: sr.values })), {
    x: 0.8, y: 1.6, w: 11.7, h: 5.2, chartColors: [THEME.bg2, THEME.accent, THEME.accent2, THEME.muted],
    showValue: true, dataLabelPosition: type === 'bar' ? 'outEnd' : 'bestFit', dataLabelFontSize: 11, dataLabelColor: THEME.ink,
    catAxisLabelColor: THEME.ink, valAxisLabelColor: THEME.muted, valGridLine: { color: 'DDE3EC', size: 0.5 }, catGridLine: { style: 'none' },
    showLegend: series.length > 1, legendPos: 'b', legendColor: THEME.ink, barGapWidthPct: 60,
  });
  footer(s, false); s.addNotes(notes || title);
}

function caseSlide({ company, year, headline, facts, lesson, notes }) {   // left: story, right: lesson callout
  const s = pres.addSlide(); s.background = { path: BG_LIGHT };
  s.addText(`CASE · ${company.toUpperCase()} · ${year}`, { x: 0.8, y: 0.5, w: 8, h: 0.35, fontFace: THEME.font, fontSize: 12, color: THEME.bg2, charSpacing: 4, isTextBox: true, margin: 0 });
  s.addText(headline, { x: 0.8, y: 0.9, w: 7.6, h: 1.2, fontFace: THEME.font, fontSize: 26, bold: true, color: THEME.ink, isTextBox: true, margin: 0 });
  const items = facts.map((b, i) => ({ text: b, options: { bullet: { indent: 18 }, breakLine: i < facts.length - 1, paraSpaceAfter: 8 } }));
  s.addText(items, { x: 0.8, y: 2.3, w: 7.6, h: 4.0, fontFace: THEME.font, fontSize: 17, color: THEME.ink, valign: 'top', isTextBox: true });
  s.addShape(pres.ShapeType.roundRect, { x: 8.9, y: 1.0, w: 3.7, h: 5.3, fill: { color: THEME.bg1 }, line: { color: THEME.bg1 }, rectRadius: 0.15 });
  s.addImage({ path: icon('FaLightbulb', THEME.accent2), x: 9.2, y: 1.3, w: 0.7, h: 0.7 });
  s.addText('THE LESSON', { x: 10.0, y: 1.4, w: 2.4, h: 0.5, fontFace: THEME.font, fontSize: 12, color: THEME.accent, charSpacing: 4, isTextBox: true, margin: 0 });
  s.addText(lesson, { x: 9.2, y: 2.2, w: 3.1, h: 3.8, fontFace: THEME.font, fontSize: 17, color: THEME.white, isTextBox: true, margin: 0 });
  footer(s, false); s.addNotes(notes || headline);
}

function quizSlide({ question, options, answer, notes }) {
  const s = pres.addSlide(); s.background = { path: BG_DARK };
  s.addImage({ path: icon('FaQuestionCircle', THEME.accent2), x: 0.8, y: 0.6, w: 0.8, h: 0.8 });
  s.addText(question, { x: 1.8, y: 0.5, w: 10.5, h: 1.3, fontFace: THEME.font, fontSize: 28, bold: true, color: THEME.white, valign: 'middle', isTextBox: true, margin: 0 });
  options.forEach((o, i) => {
    const y = 2.2 + i * 0.95;
    s.addShape(pres.ShapeType.roundRect, { x: 0.8, y, w: 11.7, h: 0.75, fill: { color: THEME.white, transparency: 88 }, line: { color: THEME.accent, transparency: 50 }, rectRadius: 0.1 });
    s.addText(`${'ABCD'[i]}   ${o}`, { x: 1.1, y, w: 11.2, h: 0.75, fontFace: THEME.font, fontSize: 19, color: THEME.white, valign: 'middle', isTextBox: true, margin: 0 });
  });
  footer(s, true); s.addNotes(`Answer: ${answer}. ${notes || ''}`);
}

function closingSlide({ title, subtitle, notes }) {
  const s = pres.addSlide(); s.background = { path: BG_SECTION };
  s.addText(title, { x: 0.8, y: 2.6, w: 11.7, h: 1.4, fontFace: THEME.font, fontSize: 44, bold: true, color: THEME.white, align: 'center', valign: 'middle', isTextBox: true, margin: 0 });
  s.addText(subtitle || '', { x: 0.8, y: 4.0, w: 11.7, h: 0.8, fontFace: THEME.font, fontSize: 20, color: THEME.accent, align: 'center', isTextBox: true, margin: 0 });
  s.addNotes(notes || 'Thank them, say what happens next.');
}

// ---------- CONTENT (replace entirely for a real deck) ----------
const CONTENT = {
  event: 'Opportunity Fund Club · UConn',
  notes: { title: 'Welcome. One line on why red flags matter: every blow-up left clues in the filings.' },
};

titleSlide({ title: 'Finance Red Flags', subtitle: 'How to tell when the numbers are hiding something', meta: 'Ben Eydlin · Opportunity Fund Club · October 2026' });
statSlide({ title: 'Why this matters', stats: [{ value: '$74B', label: 'Enron market value erased, 2001' }, { value: '$2.2B', label: 'Luckin sales that never happened, 2020' }, { value: '€1.9B', label: 'Wirecard cash that did not exist, 2020' }], notes: 'Three numbers, three stories we will come back to.' });
sectionSlide({ kicker: 'PART 1', title: 'The numbers', notes: 'Three flags you can check in ten minutes on any 10-K.' });
bulletSlide({ title: 'Flag 1: Profit up, cash down', iconName: 'FaChartLine', bullets: ['Net income grows while operating cash flow shrinks', 'Receivables rising faster than sales', 'Rule: two years of cash trailing profit means dig'], notes: 'Explain accrual vs cash in one sentence.' });
chartSlide({ title: 'Receivables outrunning sales (illustrative)', categories: ['2022', '2023', '2024', '2025'], series: [{ name: 'Sales growth %', values: [12, 14, 15, 16] }, { name: 'Receivables growth %', values: [10, 22, 41, 65] }], notes: 'Point at the gap opening up.' });
caseSlide({ company: 'Luckin Coffee', year: 2020, headline: 'Sales that never happened', facts: ['Fabricated about $310M of 2019 sales', 'Short report by Muddy Waters flagged store traffic', 'Stock fell 80% in a day; delisted from Nasdaq'], lesson: 'If the story needs numbers nobody can verify, that is the red flag.', notes: 'Ask who in the room had heard of Luckin.' });
quizSlide({ question: 'Which company is the red flag?', options: ['Sales +20%, receivables +22%', 'Sales +20%, receivables +65%', 'Sales +5%, receivables +3%'], answer: 'B', notes: 'Let them vote by hand.' });
closingSlide({ title: 'Questions?', subtitle: 'Next week: building a stock pitch', notes: 'Plug next meeting.' });

fs.mkdirSync(path.dirname(OUT), { recursive: true });
pres.writeFile({ fileName: OUT }).then(() => {
  execFileSync('python3', [path.join(HERE, 'add_transitions.py'), OUT, 'fade', '500']);
  console.log(JSON.stringify({ pptx: OUT, slides: n + 2 }));
});
