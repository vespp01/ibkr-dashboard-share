import { test, expect, type Page } from '@playwright/test';

// Privacy mode promises to mask amounts and percentages for screenshots and
// demos, and no page may scroll horizontally on a phone. Both regressed
// silently before (amounts leaked on several pages, some overflowed at
// 390px). Runs against a LIVE stack with data, like smoke.

// "CHF 1.234", "+CHF 46", "1,4k CHF", "12,50 %"…
const AMOUNT_OR_PERCENT = /(CHF|USD|EUR|GBP|CAD|HKD)\s?[+\-−]?\d|[+\-−]?\d[\d.,']*\s?[kM]?\s?(CHF|USD|EUR)\b|\d+[.,]\d+\s?%/;

async function openApp(page: Page, privacy: boolean) {
  await page.addInitScript((enabled) => {
    window.localStorage.setItem('ibkr-dashboard-language', 'es');
    window.localStorage.setItem('ibkr-dashboard-privacy-mode', enabled ? 'true' : 'false');
  }, privacy);
  await page.goto('/');
  await expect(page.locator('.sidebar-nav button.nav-item').first()).toBeVisible();
}

async function visitEachTab(page: Page, check: (label: string) => Promise<void>) {
  const tabs = page.locator('.sidebar-nav button.nav-item');
  const count = await tabs.count();
  for (let i = 0; i < count; i += 1) {
    const tab = tabs.nth(i);
    const label = (await tab.innerText()).trim();
    await tab.click();
    await expect(tab).toHaveClass(/active/);
    await page.waitForLoadState('networkidle');
    await check(label);
  }
}

test.describe('Privacy mode and mobile layout', () => {
  test('privacy mode shows no amount or percentage on any tab', async ({ page }) => {
    await openApp(page, true);
    await visitEachTab(page, async (label) => {
      const leaks = await page.evaluate((source) => {
        const pattern = new RegExp(source);
        const found: string[] = [];
        const root = document.querySelector('main') ?? document.body;
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
          const text = walker.currentNode.textContent?.trim() ?? '';
          const el = walker.currentNode.parentElement;
          if (!text || !el || el.closest('.footer')) continue;
          const style = getComputedStyle(el);
          if (style.display === 'none' || style.visibility === 'hidden') continue;
          if (pattern.test(text)) found.push(text.slice(0, 80));
        }
        return found;
      }, AMOUNT_OR_PERCENT.source);
      expect(leaks, `${label}: visible amounts with privacy on`).toEqual([]);
    });
  });

  test('no tab scrolls horizontally at 390px', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openApp(page, false);
    await visitEachTab(page, async (label) => {
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth, `${label}: document wider than the viewport`).toBeLessThanOrEqual(390);
    });
  });
});
