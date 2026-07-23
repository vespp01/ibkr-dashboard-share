import type { OptionsPnlAttributionTotals } from '../lib/api';
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
    edgeRatioTitle: 'short_premium / grand_total: qué fracción de tu P&L total viene de la edge real de opciones vs colateral direccional. La trampa del wheel-trader es contarse el equity como skill.',
    edgeRatio: 'Options edge ratio',
    of: 'de',
    edgeRatioBreakdown: 'short_premium / grand_total · doctrina Sinclair',
    shortPremiumTitle: 'OPT cerrados, fifo_pnl_realized en base. La edge real de la rueda.',
    shortPremium: 'Short-premium edge',
    shortPremiumBreakdown: 'OPT cerrados — la edge de opciones',
    wheelStockTitle: 'STK cerrados sobre subyacentes con OPT — direccional, no vol.',
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
    edgeRatioTitle: 'short_premium / grand_total: how much of total P&L comes from the real options edge versus directional collateral. The wheel-trader trap is counting equity as skill.',
    edgeRatio: 'Options edge ratio',
    of: 'of',
    edgeRatioBreakdown: 'short_premium / grand_total · Sinclair doctrine',
    shortPremiumTitle: 'Closed OPT, fifo_pnl_realized in base currency. The real wheel edge.',
    shortPremium: 'Short-premium edge',
    shortPremiumBreakdown: 'Closed OPT — options edge',
    wheelStockTitle: 'Closed STK on option underlyings: directional, not volatility.',
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
    edgeRatioTitle: 'short_premium / grand_total: welcher Anteil des Gesamt-P&L aus der echten Options-Edge statt aus direktionalem Kollateral kommt. Die Wheel-Trader-Falle ist, Equity als Skill zu zaehlen.',
    edgeRatio: 'Options-Edge-Ratio',
    of: 'von',
    edgeRatioBreakdown: 'short_premium / grand_total · Sinclair-Doktrin',
    shortPremiumTitle: 'Geschlossene OPT, fifo_pnl_realized in Basiswaehrung. Die echte Wheel-Edge.',
    shortPremium: 'Short-Premium-Edge',
    shortPremiumBreakdown: 'Geschlossene OPT — Options-Edge',
    wheelStockTitle: 'Geschlossene STK auf Options-Underlyings: direktional, nicht Volatilitaet.',
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
  return `${ccy} ${v.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * Sinclair "decompose before any skill claim" attribution strip.
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
            <span className="value">{(edgeRatio * 100).toFixed(1)}%</span>
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
