import { expect, test, type Page } from '@playwright/test';

type Language = 'en' | 'de';
type AppLanguage = Language;

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
  const text = await page.locator('main').evaluate((element) => {
    const clone = element.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('[data-i18n-exempt="true"]').forEach((node) => node.remove());
    const chunks: string[] = [];
    const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) chunks.push(walker.currentNode.textContent ?? '');
    return chunks.join('\n');
  });
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
      const mainTabCount = await mainTabs.count();
      expect(mainTabCount).toBeGreaterThan(0);

      for (let index = 0; index < mainTabCount; index += 1) {
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

      await page.getByTestId('nav-opciones').click();
      await page.waitForLoadState('networkidle');
      const optionSubtabs = page.locator('[data-testid^="options-subtab-"]');
      const optionSubtabCount = await optionSubtabs.count();
      expect(optionSubtabCount).toBe(3);
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
