#!/usr/bin/env node
/**
 * Generates styled terminal screenshots for CLI documentation.
 *
 * Uses HTML+CSS rendered via Playwright — no actual CLI execution needed.
 * Content is representative mock output, not literal captures.
 *
 * Usage:
 *   node scripts/dev/render-cli-screenshots.js              # all screenshots
 *   node scripts/dev/render-cli-screenshots.js test-connection  # one screenshot
 *
 * Output: docs/images/cli-{name}.png
 *
 * Theme: Catppuccin Mocha
 * Retina: deviceScaleFactor 2
 */

const { chromium } = require('playwright');
const path = require('path');

const OUTPUT_DIR = path.join(__dirname, '..', 'docs', 'images');

// --- Catppuccin Mocha palette ---
const theme = {
  bg:      '#1e1e2e',
  header:  '#313244',
  text:    '#cdd6f4',
  blue:    '#89b4fa',
  green:   '#a6e3a1',
  yellow:  '#f9e2af',
  red:     '#f38ba8',
  pink:    '#f5c2e7',
  gray:    '#6c7086',
  white:   '#cdd6f4',
  dotRed:  '#f38ba8',
  dotYellow: '#f9e2af',
  dotGreen:  '#a6e3a1',
};

const CSS = `
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: ${theme.bg}; padding: 0; }
  .terminal {
    font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
    font-size: 15px;
    line-height: 1.6;
    color: ${theme.text};
    background: ${theme.bg};
    border-radius: 12px;
    overflow: hidden;
    display: inline-block;
    min-width: 720px;
    max-width: 820px;
  }
  .header {
    background: ${theme.header};
    padding: 12px 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .dot { width: 14px; height: 14px; border-radius: 50%; }
  .dot-red { background: ${theme.dotRed}; }
  .dot-yellow { background: ${theme.dotYellow}; }
  .dot-green { background: ${theme.dotGreen}; }
  .header-title {
    margin-left: 8px;
    color: ${theme.gray};
    font-size: 13px;
  }
  .body { padding: 20px 24px 24px; }
  .line { white-space: pre; }
  .prompt { color: ${theme.blue}; }
  .success { color: ${theme.green}; }
  .label { color: ${theme.yellow}; }
  .error { color: ${theme.red}; }
  .dim { color: ${theme.gray}; }
  .bold { color: ${theme.white}; font-weight: 600; }
  .pink { color: ${theme.pink}; }
  .line { min-height: 1.6em; }
  .blank { height: 0.8em; }
`;

function html(title, bodyContent) {
  return `<!DOCTYPE html>
<html><head><style>${CSS}</style></head>
<body>
<div class="terminal">
  <div class="header">
    <div class="dot dot-red"></div>
    <div class="dot dot-yellow"></div>
    <div class="dot dot-green"></div>
    <span class="header-title">${title}</span>
  </div>
  <div class="body">${bodyContent}</div>
</div>
</body></html>`;
}

function line(content, cls) {
  if (!content) return '<div class="blank"></div>';
  if (cls) return `<div class="line ${cls}">${content}</div>`;
  return `<div class="line">${content}</div>`;
}

// --- Screenshot definitions ---

const screenshots = {
  'test-connection': {
    title: 'wp ojs-sync test-connection',
    body: () => [
      line('<span class="prompt">$</span> wp ojs-sync test-connection'),
      line(),
      line('<span class="bold">OJS URL:</span> http://ojs.example.com/index.php/journal'),
      line('<span class="bold">API Key:</span> Configured'),
      line(),
      line('<span class="bold">Step 1: Ping</span> <span class="dim">(reachability, no auth)...</span>'),
      line('  <span class="success">OK:</span> OJS is reachable.'),
      line('<span class="bold">Step 2: Preflight</span> <span class="dim">(auth + IP + compatibility)...</span>'),
      line('  <span class="success">OK:</span> Authenticated, IP allowed, and compatible.'),
      line('      <span class="success">OK:</span> <span class="dim">Repo::user()-&gt;getByEmail()</span>'),
      line('      <span class="success">OK:</span> <span class="dim">Repo::user()-&gt;newDataObject()</span>'),
      line('      <span class="success">OK:</span> <span class="dim">IndividualSubscriptionDAO::insertObject()</span>'),
      line('      <span class="success">OK:</span> <span class="dim">SubscriptionTypeDAO::getById()</span>'),
      line('          <span class="dim">... 15 more checks passed</span>'),
      line(),
      line('<span class="success">Success:</span> Connection test passed. OJS is ready for sync.'),
    ].join('\n'),
  },

  'sync': {
    title: 'wp ojs-sync sync',
    body: () => [
      line('<span class="prompt">$</span> wp ojs-sync sync --member=jane.smith@example.com'),
      line(),
      line('<span class="success">Success:</span> Synced: jane.smith@example.com -&gt; OJS user 42, subscription 38'),
      line(),
      line('<span class="prompt">$</span> wp ojs-sync sync --bulk --dry-run'),
      line(),
      line('<span class="bold">Bulk sync dry run</span>'),
      line('Resolving active members...'),
      line('Found <span class="label">684</span> active members to sync.'),
      line(),
      line('<span class="dim">[dry-run]</span> Would sync <span class="bold">jane.smith@example.com</span> <span class="dim">(new user)</span>'),
      line('<span class="dim">[dry-run]</span> Would sync <span class="bold">bob.jones@example.com</span> <span class="dim">(existing, update subscription)</span>'),
      line('<span class="dim">[dry-run]</span> Would sync <span class="bold">alice.wu@example.com</span> <span class="dim">(new user)</span>'),
      line('    <span class="dim">... 681 more members</span>'),
      line(),
      line('<span class="success">Dry run complete.</span> 684 members would be synced (412 new, 272 update).'),
    ].join('\n'),
  },

  'status': {
    title: 'wp ojs-sync status',
    body: () => [
      line('<span class="prompt">$</span> wp ojs-sync status'),
      line(),
      line('<span class="bold">Action Scheduler Queue (wpojs-sync)</span>'),
      line('<span class="dim">=========================================</span>'),
      line('Resolving active members...'),
      line(),
      line('<span class="bold">Active WP members:</span>    <span class="label">684</span>'),
      line('<span class="bold">Members synced to OJS:</span> 684'),
      line('<span class="bold">Failures in last 24h:</span>  0'),
      line(),
      line('<span class="bold">Cron Schedule</span>'),
      line('<span class="dim">===============</span>'),
      line('Reconciliation: 2026-03-08 19:21:48'),
      line('Daily digest:   2026-03-08 19:21:48'),
      line('Log cleanup:    2026-03-08 19:21:48'),
      line(),
      line('<span class="bold">Status    Count</span>'),
      line('Pending   0'),
      line('Running   0'),
      line('Failed    0'),
      line('Complete  684'),
    ].join('\n'),
  },

  'reconcile': {
    title: 'wp ojs-sync reconcile',
    body: () => [
      line('<span class="prompt">$</span> wp ojs-sync reconcile'),
      line(),
      line('<span class="bold">Running reconciliation...</span>'),
      line('Resolving active members <span class="dim">(this may take a few minutes)</span>...'),
      line('Checking <span class="label">684</span> members in batches of 100...'),
      line(),
      line('  <span class="label">Queued activate</span> for <span class="bold">bob.jones@example.com</span> <span class="dim">(no active OJS subscription)</span>'),
      line('  <span class="label">Queued activate</span> for <span class="bold">sarah.lee@example.com</span> <span class="dim">(no active OJS subscription)</span>'),
      line('  <span class="label">Queued expire</span> for <span class="bold">old.member@example.com</span> <span class="dim">(WP subscription cancelled)</span>'),
      line(),
      line('<span class="success">Reconciliation complete.</span>'),
      line('  Checked: <span class="label">684</span> members'),
      line('  Queued:  <span class="label">3</span> actions (2 activate, 1 expire)'),
      line('  OK:      681 already in sync'),
    ].join('\n'),
  },
};

async function render(name, spec) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  const content = html(spec.title, spec.body());
  await page.setContent(content, { waitUntil: 'domcontentloaded' });

  const terminal = await page.$('.terminal');
  const outPath = path.join(OUTPUT_DIR, `cli-${name}.png`);
  await terminal.screenshot({ path: outPath });
  console.log(`  ${outPath}`);
  await browser.close();
}

async function main() {
  const filter = process.argv[2];
  const names = filter ? [filter] : Object.keys(screenshots);

  console.log('Rendering CLI screenshots...');
  for (const name of names) {
    if (!screenshots[name]) {
      console.error(`Unknown screenshot: ${name}`);
      console.error(`Available: ${Object.keys(screenshots).join(', ')}`);
      process.exit(1);
    }
    await render(name, screenshots[name]);
  }
  console.log('Done.');
}

main().catch(err => { console.error(err); process.exit(1); });
