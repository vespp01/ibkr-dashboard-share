import { test, expect, type Page } from '@playwright/test';

// VISION R4 smoke: the dashboard shell loads and all 8 sidebar tabs render
// without a runtime error / blank screen. Runs against a live stack with data.
//
// Keep this smoke deterministic regardless of the browser host locale.
const TABS = [
  'Dashboard',
  'Portfolio',
  'Trades',
  'Options',
  'Dividends',
  'Performance',
  'Fees',
  'Margin',
] as const;

// Scope to the sidebar nav so we don't match in-content buttons (e.g. the
// "Operaciones" sub-tab inside OptionsPanel).
const navItem = (page: Page, label: string) =>
  page.locator('.sidebar-nav button.nav-item', { hasText: label });

async function loadSmokeApp(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('ibkr-dashboard-language', 'en');
  });
  await page.goto('/');
}

// Collect uncaught runtime errors so any thrown exception (which would trip a
// React error boundary into a blank/fallback screen) fails the test loudly.
function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

test.describe('Dashboard smoke (R4)', () => {
  test('app shell + sidebar load', async ({ page }) => {
    const errors = trackPageErrors(page);
    await loadSmokeApp(page);
    await expect(page.locator('.sidebar-brand')).toContainText('IBKR Dashboard');
    await expect(
      page.locator('.sidebar-nav button.nav-item'),
    ).toHaveCount(TABS.length);
    expect(errors, `runtime errors: ${errors.join('; ')}`).toHaveLength(0);
  });

  for (const label of TABS) {
    test(`tab "${label}" renders without runtime error`, async ({ page }) => {
      const errors = trackPageErrors(page);
      await loadSmokeApp(page);

      const tab = navItem(page, label);
      await tab.click();

      // The clicked nav item becomes active...
      await expect(tab).toHaveClass(/active/);
      // ...and the main shell stays mounted (no white screen).
      await expect(page.locator('.app-shell')).toBeVisible();
      // Let React Query settle, then assert no thrown runtime error.
      await page.waitForLoadState('networkidle');
      expect(
        errors,
        `runtime errors on "${label}": ${errors.join('; ')}`,
      ).toHaveLength(0);
    });
  }
});
