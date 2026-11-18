import type { OptionsPnlAttributionTotals } from '../lib/api';
import { formatCurrency, formatPercent } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface Props {
  totals: OptionsPnlAttributionTotals;
  currency: string;
  /** When true (Resumen), prepend an "Options edge ratio" card.
   *  When false (Opciones tab, where the user is already in context),
   *  omit it — the strip leads with the short-premium edge instead. */
  showEdgeRatio?: boolean;
}

const COPY = {
  es: {
    edgeRatioTitle: 'short_premium / grand_total: qué fracción de tu P&L total viene de opciones vendidas (prima), frente a opciones compradas y acciones.',
    edgeRatio: 'Peso de la prima vendida',
    of: 'de',
    edgeRatioBreakdown: 'short_premium / grand_total',
    shortPremiumTitle: 'OPT cerrados que se abrieron vendiendo (SELL abre, BUY cierra), fifo_pnl_realized en base.',
    shortPremium: 'Prima vendida',
    shortPremiumBreakdown: 'OPT vendidos y cerrados',
    longPremiumTitle: 'OPT cerrados que se abrieron comprando (BUY abre, SELL cierra), fifo_pnl_realized en base. Es apuesta direccional, no venta de prima.',
    longPremium: 'Opciones compradas',
    longPremiumBreakdown: 'OPT comprados y cerrados — direccional',
    wheelStockTitle: 'STK cerrados de acciones recibidas por asignación o ejercicio — direccional, no vol.',
    wheelDivs: '+ divs wheel',
    otherEquityTitle: 'Subyacentes sin opciones — fuera del scope de la estrategia.',
    otherEquity: 'Otros equity + divs',
    stock: 'stk',
    divs: 'divs',
    capitalCostTitle: 'Cost-of-collateral: interés pagado − recibido sobre el cash parado.',
    capitalCost: 'Coste de capital',
    capitalCostBreakdown: 'Broker interest neto · acciona si negativo grande',
  },
  en: {
    edgeRatioTitle: 'short_premium / grand_total: how much of total P&L comes from sold option premium, as opposed to long options and stock.',
    edgeRatio: 'Sold-premium share',
    of: 'of',
    edgeRatioBreakdown: 'short_premium / grand_total',
    shortPremiumTitle: 'Closed OPT opened by a SELL (SELL opens, BUY closes), fifo_pnl_realized in base currency.',
    shortPremium: 'Sold premium',
    shortPremiumBreakdown: 'Closed short options',
    longPremiumTitle: 'Closed OPT opened by a BUY (BUY opens, SELL closes), fifo_pnl_realized in base currency. A directional bet, not premium selling.',
    longPremium: 'Long options',
    longPremiumBreakdown: 'Closed long options — directional',
    wheelStockTitle: 'Closed STK from shares received through option assignment or exercise: directional, not volatility.',
    wheelDivs: '+ wheel divs',
    otherEquityTitle: 'Underlyings without options: outside the strategy scope.',
    otherEquity: 'Other equity + divs',
    stock: 'stk',
    divs: 'divs',
    capitalCostTitle: 'Cost of collateral: interest paid minus received on idle cash.',
    capitalCost: 'Cost of capital',
    capitalCostBreakdown: 'Net broker interest · act if materially negative',
  },
  de: {
    edgeRatioTitle: 'short_premium / grand_total: welcher Anteil des Gesamt-P&L aus verkaufter Optionspraemie stammt, im Gegensatz zu Long-Optionen und Aktien.',
    edgeRatio: 'Anteil verkaufter Praemie',
    of: 'von',
    edgeRatioBreakdown: 'short_premium / grand_total',
    shortPremiumTitle: 'Geschlossene OPT, per SELL eroeffnet (SELL eroeffnet, BUY schliesst), fifo_pnl_realized in Basiswaehrung.',
    shortPremium: 'Verkaufte Praemie',
    shortPremiumBreakdown: 'Geschlossene Short-Optionen',
    longPremiumTitle: 'Geschlossene OPT, per BUY eroeffnet (BUY eroeffnet, SELL schliesst), fifo_pnl_realized in Basiswaehrung. Eine direktionale Wette, kein Praemienverkauf.',
    longPremium: 'Long-Optionen',
    longPremiumBreakdown: 'Geschlossene Long-Optionen — direktional',
    wheelStockTitle: 'Geschlossene STK aus Aktien, die per Andienung oder Ausuebung erhalten wurden: direktional, nicht Volatilitaet.',
    wheelDivs: '+ Wheel-Divs',
    otherEquityTitle: 'Underlyings ohne Optionen: ausserhalb des Strategie-Scopes.',
    otherEquity: 'Other Equity + Divs',
    stock: 'stk',
    divs: 'divs',
    capitalCostTitle: 'Collateral-Kosten: gezahlte minus erhaltene Zinsen auf ungenutztes Cash.',
    capitalCost: 'Kapitalkosten',
    capitalCostBreakdown: 'Netto-Brokerzins · handeln, wenn stark negativ',
  },
} as const;

function fmtCurrency(v: number | undefined | null, ccy: string, locale: string): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return formatCurrency(v, ccy, locale);
}

/**
 * Options P&L attribution strip: sold premium, long options, wheel stock, other equity, cost of capital.
 *
 * Shared by Resumen (Dashboard.tsx) and Opciones (OptionsPanel.tsx) so the
 * two pages can't drift on labels or sign-convention.
 */
export function AttributionStrip({ totals, currency, showEdgeRatio = false }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const t = totals;
  const edgeRatio = t.grand_total_base !== 0 ? (t.short_premium_base / t.grand_total_base) : 0;
  const otherEquity = t.non_options_stock_base + t.non_options_dividends_base;

  return (
    <div className="stats-grid">
      {showEdgeRatio ? (
        <div
          className="stat-card"
          title={copy.edgeRatioTitle}
        >
          <span className="label">{copy.edgeRatio}</span>
          <div className="value-row">
            <span className="value">{formatPercent(edgeRatio * 100, false, locale)}</span>
            <span className="value-suffix">{copy.of} {fmtCurrency(t.grand_total_base, currency, locale)}</span>
          </div>
          <div className="breakdown">
            <span className="sub">{copy.edgeRatioBreakdown}</span>
          </div>
        </div>
      ) : null}

      <div className="stat-card" title={copy.shortPremiumTitle}>
        <span className="label">{copy.shortPremium}</span>
        <div className="value-row">
          <span className={`value ${t.short_premium_base >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
            {fmtCurrency(t.short_premium_base, currency, locale)}
          </span>
        </div>
        <div className="breakdown">
          <span className="sub">{copy.shortPremiumBreakdown}</span>
        </div>
      </div>

      <div className="stat-card" title={copy.longPremiumTitle}>
        <span className="label">{copy.longPremium}</span>
        <div className="value-row">
          <span className={`value ${(t.long_premium_base ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
            {fmtCurrency(t.long_premium_base, currency, locale)}
          </span>
        </div>
        <div className="breakdown">
          <span className="sub">{copy.longPremiumBreakdown}</span>
        </div>
      </div>

      <div className="stat-card" title={copy.wheelStockTitle}>
        <span className="label">Wheel stock</span>
        <div className="value-row">
          <span className={`value ${t.wheel_stock_base >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
            {fmtCurrency(t.wheel_stock_base, currency, locale)}
          </span>
        </div>
        <div className="breakdown">
          <span className="sub">{copy.wheelDivs} {fmtCurrency(t.wheel_dividends_base, currency, locale)}</span>
        </div>
      </div>

      <div className="stat-card" title={copy.otherEquityTitle}>
        <span className="label">{copy.otherEquity}</span>
        <div className="value-row">
          <span className={`value ${otherEquity >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
            {fmtCurrency(otherEquity, currency, locale)}
          </span>
        </div>
        <div className="breakdown">
          <span className="sub">
            {copy.stock} {fmtCurrency(t.non_options_stock_base, currency, locale)} · {copy.divs} {fmtCurrency(t.non_options_dividends_base, currency, locale)}
          </span>
        </div>
      </div>

      <div className="stat-card" title={copy.capitalCostTitle}>
        <span className="label">{copy.capitalCost}</span>
        <div className="value-row">
          <span className={`value ${t.interest_base >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
            {fmtCurrency(t.interest_base, currency, locale)}
          </span>
        </div>
        <div className="breakdown">
          <span className="sub">{copy.capitalCostBreakdown}</span>
        </div>
      </div>
    </div>
  );
}
