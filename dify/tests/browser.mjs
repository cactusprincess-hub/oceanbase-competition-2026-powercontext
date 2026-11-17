import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const browser = await chromium.launch({executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1280, height: 1000}, deviceScaleFactor: 1});
  await page.goto('http://localhost:8090');
  await page.getByRole('button', {name: '发送', exact: true}).waitFor({state: 'visible'});
  await page.waitForFunction(() => !document.querySelector('button').disabled);
  async function act(command) {
    await page.getByLabel('行动指令').fill(command);
    const response = page.waitForResponse(r => r.url().endsWith('/action'));
    await page.getByRole('button', {name: '发送', exact: true}).click();
    assert.equal((await response).status(), 200);
    await page.waitForFunction(() => !document.querySelector('button').disabled);
    assert.equal(await page.locator('#error').textContent(), '');
  }
  for (const command of ['开始', '去站台', '查看', '去仓库', '查看', '去广播室', '查看', '去大厅', '打开 231704']) await act(command);
  assert.equal(await page.locator('#status').textContent(), '已找回车票');
  assert.match(await page.locator('#story').textContent(), /得分：76/);
  await mkdir('results', {recursive: true});
  await page.screenshot({path: 'results/browser-desktop.png', fullPage: true});
  await page.reload();
  await page.waitForFunction(() => document.getElementById('status').textContent === '已找回车票');
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: 'results/browser-mobile.png', fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  console.log('PASS: browser full walkthrough, refresh recovery, desktop and mobile layout');
} finally { await browser.close(); }
