import { test, expect, type Page } from '@playwright/test';

// Smoke: the dashboard shell loads and all sidebar tabs render
// without a runtime error / blank screen. Runs against a live stack with data.
//
// Keep this smoke deterministic regardless of the browser host locale.
// Nav keys shown while Fiscal is not configured (it is hidden until /tax/scenarios
// reports a configured integration). Each button carries data-testid="nav-<key>".
const TAB_KEYS = [
  'resumen',
  'cartera',
  'operaciones',
  'opciones',
  'dividendos',
  'rentabilidad',
  'comisiones',
  'margen',
  'integraciones',
] as const;

const navItem = (page: Page, key: string) => page.getByTestId(`nav-${key}`);

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
    ).toHaveCount(TAB_KEYS.length);
    // Fiscal stays hidden while the tax integration is not configured.
    await expect(navItem(page, 'fiscal')).toHaveCount(0);
    expect(errors, `runtime errors: ${errors.join('; ')}`).toHaveLength(0);
  });

  for (const label of TAB_KEYS) {
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
