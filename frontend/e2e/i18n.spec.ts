import { expect, test, type Page } from '@playwright/test';

type Language = 'en' | 'de';
type AppLanguage = Language;

const MAIN_TAB_COUNT = 8;
const SPANISH_VISIBLE_RE =
  /[áéíóúñÑ¿¡]|\b(Cargando|Error cargando|Sin datos|No hay|Subyacente|Operaciones|Opciones|Dividendos|Rentabilidad|Comisiones|Margen|Cartera|Fecha|Vence|Acción|Comisión|Estado|Riesgo|Estrategia|Posiciones|Retorno|periodo|cierre|cotización|Intrínseco|Prima neta|revisa el backend|base de datos)\b/i;

async function loadWithLanguage(page: Page, language: AppLanguage) {
  await page.addInitScript((nextLanguage) => {
    window.localStorage.setItem('ibkr-dashboard-language', nextLanguage);
  }, language);
  await page.goto('/');
  await page.waitForLoadState('networkidle');
}

async function mainText(page: Page): Promise<string> {
  const text = await page.locator('main').innerText();
  // The language picker intentionally shows each language in its native name.
  return text.replace(/\bEspañol\b/g, '');
}

async function expectNoSpanish(page: Page, context: string) {
  const text = await mainText(page);
  const match = text.match(SPANISH_VISIBLE_RE);
  expect(
    match,
    `${context}: Spanish-looking UI text found: ${match?.[0] ?? ''}`,
  ).toBeNull();
}

test.describe('i18n coverage', () => {
  for (const language of ['en', 'de'] as const) {
    test(`${language}: main pages, Settings, and Options subtabs have no Spanish UI text`, async ({ page }) => {
      await loadWithLanguage(page, language);

      const mainTabs = page.locator('.sidebar-nav .nav-item');
      await expect(mainTabs).toHaveCount(MAIN_TAB_COUNT);

      for (let index = 0; index < MAIN_TAB_COUNT; index += 1) {
        await mainTabs.nth(index).click();
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(100);
        const title = await page.locator('h1').innerText();
        await expectNoSpanish(page, `${language} ${title}`);
      }

      await page.locator('.sidebar-footer .nav-item').click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(100);
      await expectNoSpanish(page, `${language} Settings`);

      await mainTabs.nth(3).click();
      await page.waitForLoadState('networkidle');
      const optionSubtabs = page.locator('.filter-chip-grid').first().locator('button');
      const optionSubtabCount = await optionSubtabs.count();
      for (let index = 0; index < optionSubtabCount; index += 1) {
        await optionSubtabs.nth(index).click();
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(100);
        const label = await optionSubtabs.nth(index).innerText();
        await expectNoSpanish(page, `${language} Options / ${label}`);
      }
    });
  }
});
