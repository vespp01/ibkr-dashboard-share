import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api, type PeriodFilters } from '../lib/api';
import { AttributionStrip } from './AttributionStrip';
import { KPICard } from './KPICard';
import { ChartTooltip } from './ChartTooltip';
import { useI18n } from '../lib/i18n';

type View = 'open' | 'lab' | 'trades' | 'eae' | 'risk' | 'underlying';
type Status = 'all' | 'open' | 'closed';
type Right = '' | 'C' | 'P';
type StrategyId = 'long-call' | 'short-put' | 'bull-put-spread' | 'bear-call-spread' | 'iron-condor' | 'long-straddle';

interface LabLeg {
  side: 'BUY' | 'SELL';
  right: 'C' | 'P';
  strike: number;
  premium: number;
  contracts: number;
}

interface Props {
  filters: PeriodFilters;
  currency: string;
}

function fmtCurrency(v: number | undefined | null, ccy: string): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `${ccy} ${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtNumber(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtPercent(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDate(iso: string | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

function fmtTime(iso: string | undefined): string {
  if (!iso || iso.length < 19) return '';
  return iso.slice(11, 16);
}

function dteClass(dte: number | undefined | null): string {
  if (dte === undefined || dte === null) return '';
  if (dte <= 7) return 'pnl-neg';
  if (dte <= 30) return 'pnl-warn';
  return '';
}

const STRATEGIES: Array<{ id: StrategyId; label: string }> = [
  { id: 'long-call', label: 'Long Call' },
  { id: 'short-put', label: 'Cash-Secured Put' },
  { id: 'bull-put-spread', label: 'Bull Put Spread' },
  { id: 'bear-call-spread', label: 'Bear Call Spread' },
  { id: 'iron-condor', label: 'Iron Condor' },
  { id: 'long-straddle', label: 'Long Straddle' },
];

const COPY = {
  es: {
    contractsDelta: 'contratos · cada open o close cuenta 1',
    netPremium: 'Prima neta',
    netPremiumDelta: 'Cobros - pagos (net_cash, ya neto de comisiones)',
    realizedPnl: 'P&L realizado',
    commissionsIncluded: 'comisiones (incl.)',
    winRateClosed: 'Win rate (cierres)',
    closedOnlyBase: 'base = solo cierres',
    openShorts: 'Shorts abiertos',
    manageZone: 'Zona MANAGE (<=21 DTE)',
    of: 'de',
    openShortsLower: 'shorts abiertos',
    tastytradeDoctrine: 'doctrina tastytrade 21-DTE',
    intrinsicRisk: 'Riesgo intrínseco',
    concentrationHhi: 'Concentración HHI',
    effectiveNames: 'effective names',
    topExposure: 'Top exposure',
    optionBookConcentration: 'Concentración del libro de opciones',
    concentrationSubtitle: 'HHI sobre valor absoluto de mercado del último snapshot OPT; 1.00 = un solo subyacente.',
    concentrationError: 'Error cargando concentración de opciones: revisa el backend.',
    absExposure: 'Exposición abs.',
    share: 'Peso',
    positions: 'Posiciones',
    netValue: 'Valor neto',
    dteDistribution: 'Distribución DTE de shorts abiertos',
    dteBuckets: 'Buckets tastytrade',
    monthlyPremium: 'Prima neta acumulada por mes',
    received: 'Cobros',
    paid: 'Pagos',
    net: 'Neto',
    accumulated: 'Acumulado',
    monthlyNet: 'Neto mensual',
    attributionTitle: 'Atribución de P&L (Sinclair · "decompose before any skill claim")',
    attributionSubtitle: 'separa la edge real de opciones del retorno colateral (acciones asignadas, dividendos, intereses).',
    underlying: 'Subyacente',
    optPnlTitle: 'Suma de fifo_pnl_realized de OPT cerrados',
    stkPnlTitle: 'Suma de fifo_pnl_realized de STK cerrados',
    divsNetTitle: 'Dividendos netos (gross + WHT)',
    assignmentTitle: 'Destino de las opciones cerradas (u/ScottishTrader wheel-tracker)',
    expiration: 'Expiración',
    manualClose: 'Cierre manual',
    overDisposals: 'sobre',
    boughtToCloseTitle: 'BUY-TO-CLOSE de un short',
    soldToCloseTitle: 'SELL-TO-CLOSE de un long',
    expired: 'Expiró',
    expiredTitle: 'Expiró sin valor: máxima edge para short premium',
    assigned: 'Asignado',
    assignedTitle: 'Counterparty ejerció: tienes que comprar/vender shares',
    exercise: 'Ejercicio',
    exerciseTitle: 'Tú ejerciste: compra/venta de shares',
    transferTitle: '(asignado + ejercicio) / total: got picked off rate',
    tabOpenPositions: 'Abiertas',
    tabTrades: 'Operaciones',
    tabEae: 'Eventos E/A/E',
    tabRisk: 'Riesgo de asignación',
    tabUnderlying: 'Por subyacente',
    labDescription: 'Calculadora local a vencimiento. Edita strikes/primas manualmente; la cadena real y cotizaciones quedan para la siguiente iteración.',
    loadTemplate: 'Cargar plantilla',
    strategy: 'Estrategia',
    creditReceived: 'crédito recibido',
    debitPaid: 'débito pagado',
    maxProfit: 'Beneficio máximo',
    maxLoss: 'Pérdida máxima',
    unboundedUpside: 'upside ilimitado',
    expirationPayoff: 'payoff a vencimiento',
    unboundedUpsideRisk: 'riesgo de upside ilimitado',
    breakeven: 'Breakeven',
    linearInterpolation: 'interpolación lineal',
    side: 'Lado',
    type: 'Tipo',
    premium: 'Prima',
    contracts: 'Contratos',
    optionTrades: 'Operaciones de opciones',
    openPositionsTitle: 'Opciones abiertas',
    openPositionsSubtitle: 'Último snapshot OPT: valor de mercado, P&L no realizado y DTE por contrato.',
    openPositionsError: 'Error cargando opciones abiertas: revisa el backend.',
    openPositionsEmpty: 'No tienes posiciones abiertas en opciones al último snapshot.',
    long: 'Larga',
    short: 'Corta',
    marketValue: 'Valor mercado',
    unrealized: 'No realizado',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    all: 'All',
    open: 'Open',
    closed: 'Closed',
    underlyingSelect: 'Subyacente',
    loading: 'Cargando...',
    tradesError: 'Error cargando operaciones de opciones: revisa el backend.',
    tradesEmpty: 'No hay operaciones de opciones en este periodo / filtro. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    date: 'Fecha',
    expires: 'Vence',
    dteTodayTitle: 'Days-to-expiry hoy',
    action: 'Acción',
    commission: 'Comisión',
    rocTitle: 'Return on capital at risk = pnl / (strike x multiplier x |qty|). Denominador correcto para CSP/CC; sobreestima long debits.',
    captureTitle: '% de la prima inicial conservada: solo BUY-TO-CLOSE de shorts. Doctrina tastytrade "manage at 50%".',
    status: 'Estado',
    capitalAtRisk: 'Cap. en riesgo',
    openBadge: 'Abierta',
    closedBadge: 'Cerrada',
    cashNet: 'Cash neto',
    eaeError: 'Error cargando eventos E/A/E: revisa el backend.',
    eaeEmpty: 'Sin eventos de ejercicio / asignación / expiración en este periodo. Aparecerán automáticamente cuando IBKR los emita.',
    contract: 'Contrato',
    price: 'Precio',
    assignmentRiskDescription: 'Shorts abiertos al cierre {date} vs último cierre del subyacente. Sin cotización live; ITM = cierre EOD vs strike.',
    riskError: 'Error cargando riesgo de asignación: revisa el backend.',
    riskEmpty: 'No tienes posiciones cortas en opciones al último snapshot.',
    underlyingClose: 'Cierre subyacente',
    intrinsicPerContract: 'Intrínseco / contr.',
    intrinsicTotal: 'Intrínseco total',
    pnlByUnderlying: 'P&L por subyacente',
    pnlByUnderlyingSubtitle: 'Ordenado por |P&L realizado|. Win-rate = win / (win + loss) de trades cerrados.',
    underlyingError: 'Error cargando P&L por subyacente: revisa el backend.',
    noData: 'Sin datos en este periodo. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    commissions: 'Comisiones',
  },
  en: {
    contractsDelta: 'contracts · each open or close counts as 1',
    netPremium: 'Net premium',
    netPremiumDelta: 'Credits - debits (net_cash, already net of commissions)',
    realizedPnl: 'Realized P&L',
    commissionsIncluded: 'commissions (incl.)',
    winRateClosed: 'Win rate (closes)',
    closedOnlyBase: 'base = closes only',
    openShorts: 'Open shorts',
    manageZone: 'MANAGE zone (<=21 DTE)',
    of: 'of',
    openShortsLower: 'open shorts',
    tastytradeDoctrine: 'tastytrade 21-DTE doctrine',
    intrinsicRisk: 'Intrinsic risk',
    concentrationHhi: 'Concentration HHI',
    effectiveNames: 'effective names',
    topExposure: 'Top exposure',
    optionBookConcentration: 'Option book concentration',
    concentrationSubtitle: 'HHI over absolute market value from the latest OPT snapshot; 1.00 = one underlying.',
    concentrationError: 'Error loading option concentration: check the backend.',
    absExposure: 'Abs. exposure',
    share: 'Share',
    positions: 'Positions',
    netValue: 'Net value',
    dteDistribution: 'Open-short DTE distribution',
    dteBuckets: 'tastytrade buckets',
    monthlyPremium: 'Monthly cumulative net premium',
    received: 'Credits',
    paid: 'Debits',
    net: 'Net',
    accumulated: 'Accumulated',
    monthlyNet: 'Monthly net',
    attributionTitle: 'P&L attribution (Sinclair · "decompose before any skill claim")',
    attributionSubtitle: 'separates the real options edge from collateral return (assigned shares, dividends, interest).',
    underlying: 'Underlying',
    optPnlTitle: 'Sum of fifo_pnl_realized from closed OPT',
    stkPnlTitle: 'Sum of fifo_pnl_realized from closed STK',
    divsNetTitle: 'Net dividends (gross + WHT)',
    assignmentTitle: 'Closed option outcomes (u/ScottishTrader wheel-tracker)',
    expiration: 'Expiration',
    manualClose: 'Manual close',
    overDisposals: 'over',
    boughtToCloseTitle: 'BUY-TO-CLOSE of a short',
    soldToCloseTitle: 'SELL-TO-CLOSE of a long',
    expired: 'Expired',
    expiredTitle: 'Expired worthless: maximum edge for short premium',
    assigned: 'Assigned',
    assignedTitle: 'Counterparty exercised: you must buy/sell shares',
    exercise: 'Exercise',
    exerciseTitle: 'You exercised: share purchase/sale',
    transferTitle: '(assigned + exercise) / total: got picked off rate',
    tabOpenPositions: 'Open',
    tabTrades: 'Trades',
    tabEae: 'E/A/E events',
    tabRisk: 'Assignment risk',
    tabUnderlying: 'By underlying',
    labDescription: 'Local expiration calculator. Edit strikes/premiums manually; live chains and quotes are for the next iteration.',
    loadTemplate: 'Load template',
    strategy: 'Strategy',
    creditReceived: 'credit received',
    debitPaid: 'debit paid',
    maxProfit: 'Max profit',
    maxLoss: 'Max loss',
    unboundedUpside: 'unbounded upside',
    expirationPayoff: 'expiration payoff',
    unboundedUpsideRisk: 'unbounded upside risk',
    breakeven: 'Breakeven',
    linearInterpolation: 'linear interpolation',
    side: 'Side',
    type: 'Type',
    premium: 'Premium',
    contracts: 'Contracts',
    optionTrades: 'Option trades',
    openPositionsTitle: 'Open option positions',
    openPositionsSubtitle: 'Latest OPT snapshot: market value, unrealized P&L and DTE by contract.',
    openPositionsError: 'Error loading open option positions: check the backend.',
    openPositionsEmpty: 'No open option positions at the latest snapshot.',
    long: 'Long',
    short: 'Short',
    marketValue: 'Market value',
    unrealized: 'Unrealized',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    all: 'All',
    open: 'Open',
    closed: 'Closed',
    underlyingSelect: 'Underlying',
    loading: 'Loading...',
    tradesError: 'Error loading option trades: check the backend.',
    tradesEmpty: 'No option trades in this period/filter. If the database is new, run bootstrap or a pipeline pull.',
    date: 'Date',
    expires: 'Expires',
    dteTodayTitle: 'Days to expiry today',
    action: 'Action',
    commission: 'Commission',
    rocTitle: 'Return on capital at risk = pnl / (strike x multiplier x |qty|). Correct denominator for CSP/CC; overstates long debits.',
    captureTitle: '% of initial premium retained: BUY-TO-CLOSE shorts only. tastytrade "manage at 50%" doctrine.',
    status: 'Status',
    capitalAtRisk: 'Capital at risk',
    openBadge: 'Open',
    closedBadge: 'Closed',
    cashNet: 'Net cash',
    eaeError: 'Error loading E/A/E events: check the backend.',
    eaeEmpty: 'No exercise / assignment / expiration events in this period. They will appear automatically when IBKR emits them.',
    contract: 'Contract',
    price: 'Price',
    assignmentRiskDescription: 'Open shorts at close {date} vs last underlying close. No live quote; ITM = EOD close vs strike.',
    riskError: 'Error loading assignment risk: check the backend.',
    riskEmpty: 'No short option positions at the latest snapshot.',
    underlyingClose: 'Underlying close',
    intrinsicPerContract: 'Intrinsic / contract',
    intrinsicTotal: 'Intrinsic total',
    pnlByUnderlying: 'P&L by underlying',
    pnlByUnderlyingSubtitle: 'Sorted by |realized P&L|. Win rate = win / (win + loss) for closed trades.',
    underlyingError: 'Error loading P&L by underlying: check the backend.',
    noData: 'No data in this period. If the database is new, run bootstrap or a pipeline pull.',
    commissions: 'Commissions',
  },
  de: {
    contractsDelta: 'Kontrakte · jedes Open oder Close zaehlt als 1',
    netPremium: 'Netto-Praemie',
    netPremiumDelta: 'Credits - Debits (net_cash, bereits netto nach Kommissionen)',
    realizedPnl: 'Realisierter P&L',
    commissionsIncluded: 'Kommissionen (inkl.)',
    winRateClosed: 'Win Rate (Closes)',
    closedOnlyBase: 'Basis = nur Closes',
    openShorts: 'Offene Shorts',
    manageZone: 'MANAGE-Zone (<=21 DTE)',
    of: 'von',
    openShortsLower: 'offene Shorts',
    tastytradeDoctrine: 'tastytrade 21-DTE-Doktrin',
    intrinsicRisk: 'Intrinsisches Risiko',
    concentrationHhi: 'Konzentration HHI',
    effectiveNames: 'effective names',
    topExposure: 'Top Exposure',
    optionBookConcentration: 'Optionsbuch-Konzentration',
    concentrationSubtitle: 'HHI ueber absoluten Marktwert aus dem letzten OPT-Snapshot; 1.00 = ein Underlying.',
    concentrationError: 'Fehler beim Laden der Options-Konzentration: Backend pruefen.',
    absExposure: 'Abs. Exposure',
    share: 'Anteil',
    positions: 'Positionen',
    netValue: 'Nettowert',
    dteDistribution: 'DTE-Verteilung offener Shorts',
    dteBuckets: 'tastytrade Buckets',
    monthlyPremium: 'Kumulierte monatliche Netto-Praemie',
    received: 'Credits',
    paid: 'Debits',
    net: 'Netto',
    accumulated: 'Kumuliert',
    monthlyNet: 'Monatlich netto',
    attributionTitle: 'P&L-Attribution (Sinclair · "decompose before any skill claim")',
    attributionSubtitle: 'trennt die echte Options-Edge vom Kollateral-Return (zugeteilte Aktien, Dividenden, Zinsen).',
    underlying: 'Underlying',
    optPnlTitle: 'Summe von fifo_pnl_realized aus geschlossenen OPT',
    stkPnlTitle: 'Summe von fifo_pnl_realized aus geschlossenen STK',
    divsNetTitle: 'Netto-Dividenden (gross + WHT)',
    assignmentTitle: 'Ausgang geschlossener Optionen (u/ScottishTrader wheel-tracker)',
    expiration: 'Expiration',
    manualClose: 'Manueller Close',
    overDisposals: 'ueber',
    boughtToCloseTitle: 'BUY-TO-CLOSE eines Shorts',
    soldToCloseTitle: 'SELL-TO-CLOSE eines Longs',
    expired: 'Expired',
    expiredTitle: 'Wertlos verfallen: maximale Edge fuer Short Premium',
    assigned: 'Assigned',
    assignedTitle: 'Counterparty hat ausgeuebt: du musst Aktien kaufen/verkaufen',
    exercise: 'Exercise',
    exerciseTitle: 'Du hast ausgeuebt: Aktienkauf/-verkauf',
    transferTitle: '(assigned + exercise) / total: got picked off rate',
    tabOpenPositions: 'Offen',
    tabTrades: 'Trades',
    tabEae: 'E/A/E-Events',
    tabRisk: 'Zuteilungsrisiko',
    tabUnderlying: 'Nach Underlying',
    labDescription: 'Lokaler Rechner zum Verfall. Strikes/Praemien manuell bearbeiten; echte Chains und Quotes kommen in der naechsten Iteration.',
    loadTemplate: 'Vorlage laden',
    strategy: 'Strategie',
    creditReceived: 'Credit erhalten',
    debitPaid: 'Debit gezahlt',
    maxProfit: 'Max. Gewinn',
    maxLoss: 'Max. Verlust',
    unboundedUpside: 'unbegrenztes Upside',
    expirationPayoff: 'Payoff zum Verfall',
    unboundedUpsideRisk: 'unbegrenztes Upside-Risiko',
    breakeven: 'Breakeven',
    linearInterpolation: 'lineare Interpolation',
    side: 'Seite',
    type: 'Typ',
    premium: 'Praemie',
    contracts: 'Kontrakte',
    optionTrades: 'Options-Trades',
    openPositionsTitle: 'Offene Optionspositionen',
    openPositionsSubtitle: 'Letzter OPT-Snapshot: Marktwert, unrealisierter P&L und DTE je Kontrakt.',
    openPositionsError: 'Fehler beim Laden offener Optionspositionen: Backend pruefen.',
    openPositionsEmpty: 'Keine offenen Optionspositionen im letzten Snapshot.',
    long: 'Long',
    short: 'Short',
    marketValue: 'Marktwert',
    unrealized: 'Unrealisiert',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    all: 'Alle',
    open: 'Offen',
    closed: 'Geschlossen',
    underlyingSelect: 'Underlying',
    loading: 'Laden...',
    tradesError: 'Fehler beim Laden der Options-Trades: Backend pruefen.',
    tradesEmpty: 'Keine Options-Trades in diesem Zeitraum/Filter. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    date: 'Datum',
    expires: 'Verfall',
    dteTodayTitle: 'Days to expiry heute',
    action: 'Aktion',
    commission: 'Kommission',
    rocTitle: 'Return on capital at risk = pnl / (strike x multiplier x |qty|). Korrekter Nenner fuer CSP/CC; ueberzeichnet long debits.',
    captureTitle: '% der initialen Praemie behalten: nur BUY-TO-CLOSE von Shorts. tastytrade "manage at 50%"-Doktrin.',
    status: 'Status',
    capitalAtRisk: 'Capital at risk',
    openBadge: 'Offen',
    closedBadge: 'Geschlossen',
    cashNet: 'Netto-Cash',
    eaeError: 'Fehler beim Laden der E/A/E-Events: Backend pruefen.',
    eaeEmpty: 'Keine Exercise-/Assignment-/Expiration-Events in diesem Zeitraum. Sie erscheinen automatisch, wenn IBKR sie liefert.',
    contract: 'Kontrakt',
    price: 'Preis',
    assignmentRiskDescription: 'Offene Shorts zum Schluss {date} vs letzter Underlying-Schluss. Keine Live-Quote; ITM = EOD-Schluss vs Strike.',
    riskError: 'Fehler beim Laden des Zuteilungsrisikos: Backend pruefen.',
    riskEmpty: 'Keine kurzen Optionspositionen im letzten Snapshot.',
    underlyingClose: 'Underlying-Schluss',
    intrinsicPerContract: 'Intrinsisch / Kontrakt',
    intrinsicTotal: 'Intrinsisch total',
    pnlByUnderlying: 'P&L nach Underlying',
    pnlByUnderlyingSubtitle: 'Sortiert nach |realisiertem P&L|. Win Rate = win / (win + loss) fuer geschlossene Trades.',
    underlyingError: 'Fehler beim Laden von P&L nach Underlying: Backend pruefen.',
    noData: 'Keine Daten in diesem Zeitraum. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    commissions: 'Kommissionen',
  },
} as const;

function roundStrike(value: number): number {
  return Math.round(value / 5) * 5;
}

function templateLegs(strategy: StrategyId, spot: number): LabLeg[] {
  const atm = roundStrike(spot);
  const putShort = roundStrike(spot * 0.97);
  const putLong = roundStrike(spot * 0.92);
  const callShort = roundStrike(spot * 1.03);
  const callLong = roundStrike(spot * 1.08);
  switch (strategy) {
    case 'long-call':
      return [{ side: 'BUY', right: 'C', strike: atm, premium: Math.max(1, spot * 0.025), contracts: 1 }];
    case 'short-put':
      return [{ side: 'SELL', right: 'P', strike: putShort, premium: Math.max(1, spot * 0.018), contracts: 1 }];
    case 'bull-put-spread':
      return [
        { side: 'SELL', right: 'P', strike: putShort, premium: Math.max(1, spot * 0.018), contracts: 1 },
        { side: 'BUY', right: 'P', strike: putLong, premium: Math.max(0.25, spot * 0.008), contracts: 1 },
      ];
    case 'bear-call-spread':
      return [
        { side: 'SELL', right: 'C', strike: callShort, premium: Math.max(1, spot * 0.018), contracts: 1 },
        { side: 'BUY', right: 'C', strike: callLong, premium: Math.max(0.25, spot * 0.008), contracts: 1 },
      ];
    case 'iron-condor':
      return [
        { side: 'SELL', right: 'P', strike: putShort, premium: Math.max(1, spot * 0.015), contracts: 1 },
        { side: 'BUY', right: 'P', strike: putLong, premium: Math.max(0.25, spot * 0.006), contracts: 1 },
        { side: 'SELL', right: 'C', strike: callShort, premium: Math.max(1, spot * 0.015), contracts: 1 },
        { side: 'BUY', right: 'C', strike: callLong, premium: Math.max(0.25, spot * 0.006), contracts: 1 },
      ];
    case 'long-straddle':
      return [
        { side: 'BUY', right: 'C', strike: atm, premium: Math.max(1, spot * 0.025), contracts: 1 },
        { side: 'BUY', right: 'P', strike: atm, premium: Math.max(1, spot * 0.025), contracts: 1 },
      ];
  }
}

function legPnlAtExpiration(leg: LabLeg, price: number): number {
  const intrinsic = leg.right === 'C'
    ? Math.max(price - leg.strike, 0)
    : Math.max(leg.strike - price, 0);
  const direction = leg.side === 'BUY' ? 1 : -1;
  const premium = leg.side === 'SELL' ? leg.premium : -leg.premium;
  return (premium + direction * intrinsic) * leg.contracts * 100;
}

function analyzePayoff(legs: LabLeg[], spot: number) {
  const strikes = legs.map((leg) => leg.strike).filter((value) => Number.isFinite(value) && value > 0);
  const low = 0.01;
  const high = Math.max(spot * 1.4, ...strikes) * 1.1;
  const sampleCount = 151;
  const points = Array.from({ length: sampleCount }, (_, index) => {
    const price = low + ((high - low) * index) / (sampleCount - 1);
    const pnl = legs.reduce((sum, leg) => sum + legPnlAtExpiration(leg, price), 0);
    return { price, pnl };
  });
  const pnls = points.map((point) => point.pnl);
  const breakevens: number[] = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const next = points[i];
    if ((prev.pnl <= 0 && next.pnl >= 0) || (prev.pnl >= 0 && next.pnl <= 0)) {
      const span = next.pnl - prev.pnl;
      const ratio = span === 0 ? 0 : Math.abs(prev.pnl / span);
      breakevens.push(prev.price + (next.price - prev.price) * ratio);
    }
  }
  const netPremium = legs.reduce((sum, leg) => sum + (leg.side === 'SELL' ? 1 : -1) * leg.premium * leg.contracts * 100, 0);
  const upsideSlope = legs.reduce((sum, leg) => {
    if (leg.right !== 'C') return sum;
    return sum + (leg.side === 'BUY' ? 1 : -1) * leg.contracts * 100;
  }, 0);
  return {
    points,
    netPremium,
    maxProfit: Math.max(...pnls),
    maxLoss: Math.min(...pnls),
    profitUnbounded: upsideSlope > 0,
    lossUnbounded: upsideSlope < 0,
    breakevens,
  };
}

export function OptionsPanel({ filters, currency }: Props) {
  const { language } = useI18n();
  const copy = COPY[language];
  const [view, setView] = useState<View>('open');
  const [status, setStatus] = useState<Status>('all');
  const [right, setRight] = useState<Right>('');
  const [underlying, setUnderlying] = useState<string>('');
  const [labStrategy, setLabStrategy] = useState<StrategyId>('bull-put-spread');
  const [labSpot, setLabSpot] = useState<number>(100);
  const [labLegs, setLabLegs] = useState<LabLeg[]>(() => templateLegs('bull-put-spread', 100));

  const summary = useQuery({
    queryKey: ['options.summary', filters],
    queryFn: () => api.optionsSummary(filters),
  });

  const trades = useQuery({
    queryKey: ['options.trades', filters, status, right, underlying],
    queryFn: () => api.optionsTrades({
      ...filters,
      status,
      ...(right ? { right } : {}),
      ...(underlying ? { underlying } : {}),
      limit: 200,
    }),
    enabled: view === 'trades' || view === 'underlying',
  });

  const eae = useQuery({
    queryKey: ['options.eae', filters],
    queryFn: () => api.optionsEae(filters),
    enabled: view === 'eae',
  });

  const premium = useQuery({
    queryKey: ['options.premium', filters],
    queryFn: () => api.optionsPremiumSeries({ ...filters, bucket: 'month' }),
  });

  const byUnderlying = useQuery({
    queryKey: ['options.byUnderlying', filters],
    queryFn: () => api.optionsByUnderlying(filters),
    enabled: view === 'underlying',
  });

  const openPositions = useQuery({
    queryKey: ['options.openPositions', filters.account_id],
    queryFn: () => api.optionsOpenPositions({ account_id: filters.account_id }),
  });

  const risk = useQuery({
    queryKey: ['options.risk'],
    queryFn: () => api.optionsAssignmentRisk(),
    enabled: view === 'risk',
  });

  const concentration = useQuery({
    queryKey: ['options.concentration', filters.account_id],
    queryFn: () => api.optionsConcentration({ account_id: filters.account_id }),
  });

  const dte = useQuery({
    queryKey: ['options.dte'],
    queryFn: () => api.optionsDteBuckets(),
  });

  const attribution = useQuery({
    queryKey: ['options.attribution', filters],
    queryFn: () => api.optionsPnlAttribution(filters),
  });

  const assignmentRate = useQuery({
    queryKey: ['options.assignmentRate', filters],
    queryFn: () => api.optionsAssignmentRate(filters),
  });

  const underlyings = useMemo(() => {
    const rows = trades.data?.rows ?? [];
    return Array.from(new Set(rows.map((r) => r.underlying).filter(Boolean) as string[])).sort();
  }, [trades.data]);

  const s = summary.data;
  const conc = concentration.data;
  const lab = useMemo(() => analyzePayoff(labLegs, labSpot), [labLegs, labSpot]);

  function updateLabLeg(index: number, patch: Partial<LabLeg>) {
    setLabLegs((current) => current.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)));
  }

  return (
    <div className="trades-panel">
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <KPICard
          label={copy.openPositionsTitle}
          value={openPositions.data ? `${openPositions.data.summary.positions}` : '—'}
          delta={
            openPositions.data
              ? `${openPositions.data.summary.long_positions} ${copy.long.toLowerCase()} · ${openPositions.data.summary.short_positions} ${copy.short.toLowerCase()}`
              : undefined
          }
        />
        <KPICard
          label={copy.unrealized}
          value={openPositions.data ? fmtCurrency(openPositions.data.summary.unrealized_pnl_base, currency) : '—'}
          delta={
            openPositions.data
              ? `${copy.marketValue} ${fmtCurrency(openPositions.data.summary.net_position_value_base, currency)}`
              : undefined
          }
          deltaKind={
            openPositions.data && openPositions.data.summary.unrealized_pnl_base > 0
              ? 'pos'
              : openPositions.data && openPositions.data.summary.unrealized_pnl_base < 0
                ? 'neg'
                : 'neutral'
          }
        />
        <KPICard
          label="Trades"
          value={s ? `${s.trades.total}` : '—'}
          delta={s ? `${s.trades.contracts_traded} ${copy.contractsDelta}` : undefined}
        />
        <KPICard
          label={copy.netPremium}
          value={s ? fmtCurrency(s.trades.net_premium_base, currency) : '—'}
          delta={copy.netPremiumDelta}
          deltaKind={s && s.trades.net_premium_base >= 0 ? 'pos' : 'neg'}
        />
        <KPICard
          label={copy.realizedPnl}
          value={s ? fmtCurrency(s.trades.realized_pnl_base, currency) : '—'}
          delta={s ? `fifo_pnl_realized · ${copy.commissionsIncluded} ${fmtCurrency(s.trades.commissions_base, currency)}` : undefined}
          deltaKind={s && s.trades.realized_pnl_base >= 0 ? 'pos' : 'neg'}
        />
        <KPICard
          label={copy.winRateClosed}
          value={s ? fmtPercent(s.trades.win_rate) : '—'}
          delta={s ? `${s.trades.win}W · ${s.trades.loss}L · ${s.trades.be}BE · ${copy.closedOnlyBase}` : undefined}
        />
        <KPICard
          label="Expectancy/trade"
          value={s ? fmtCurrency(s.trades.expectancy_base, currency) : '—'}
          delta={
            s
              ? `PF ${s.trades.profit_factor != null ? s.trades.profit_factor.toFixed(2) : '—'} · avg+ ${fmtCurrency(s.trades.avg_winner_base, currency)} · avg− ${fmtCurrency(s.trades.avg_loser_base, currency)}`
              : undefined
          }
          deltaKind={s && (s.trades.expectancy_base ?? 0) >= 0 ? 'pos' : 'neg'}
        />
        <KPICard
          label={copy.openShorts}
          value={s ? `${s.open_risk.shorts}` : '—'}
          delta={s ? `${s.open_risk.itm} ITM · ${s.open_risk.expiring_within_7d} ≤7d` : undefined}
          deltaKind={s && s.open_risk.itm > 0 ? 'neg' : 'neutral'}
        />
        <KPICard
          label={copy.manageZone}
          value={dte.data ? `${dte.data.summary.manage_count}` : '—'}
          delta={
            dte.data
              ? `${copy.of} ${dte.data.summary.shorts_open} ${copy.openShortsLower} · ${copy.tastytradeDoctrine}`
              : undefined
          }
          deltaKind={dte.data && dte.data.summary.manage_count > 0 ? 'neg' : 'neutral'}
        />
        <KPICard
          label={copy.intrinsicRisk}
          value={s ? fmtCurrency(s.open_risk.intrinsic_at_risk_base, currency) : '—'}
          delta={s?.snapshot_date ? `EOD ${s.snapshot_date}` : undefined}
          deltaKind={s && s.open_risk.intrinsic_at_risk_base > 0 ? 'neg' : 'neutral'}
        />
        <KPICard
          label={copy.concentrationHhi}
          value={conc?.summary.hhi != null ? conc.summary.hhi.toFixed(2) : '—'}
          delta={
            conc?.summary.effective_names != null
              ? `${fmtNumber(conc.summary.effective_names, 1)} ${copy.effectiveNames} · ${copy.topExposure} ${conc.summary.top_underlying ?? '—'} ${fmtPercent(conc.summary.top_share)}`
              : undefined
          }
          deltaKind={
            (conc?.summary.top_share ?? 0) >= 0.65
              ? 'neg'
              : (conc?.summary.top_share ?? 0) >= 0.4
                ? 'warn'
                : 'neutral'
          }
        />
      </div>

      {concentration.isError ? (
        <div className="trades-empty error-state" style={{ marginBottom: 16 }}>
          {copy.concentrationError}
        </div>
      ) : conc && conc.rows.length > 0 ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.optionBookConcentration}</div>
            <small className="muted">
              {copy.concentrationSubtitle} Snapshot EOD {conc.snapshot_date ?? '—'}.
            </small>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart
              data={conc.rows.slice(0, 8).map((row) => ({
                underlying: row.underlying,
                share: (row.exposure_share ?? 0) * 100,
              }))}
              margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
              <XAxis dataKey="underlying" stroke="#888" fontSize={11} />
              <YAxis stroke="#888" fontSize={11} tickFormatter={(value) => `${Number(value).toFixed(0)}%`} />
              <Tooltip
                content={<ChartTooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />}
              />
              <Bar dataKey="share" name={copy.share} fill="#a855f7" />
            </BarChart>
          </ResponsiveContainer>
          <div className="table-scroll">
            <table className="data-table trades-table">
              <thead>
                <tr>
                  <th>{copy.underlying}</th>
                  <th>{copy.share}</th>
                  <th>{copy.absExposure}</th>
                  <th>{copy.netValue}</th>
                  <th>{copy.positions}</th>
                  <th>{copy.contracts}</th>
                </tr>
              </thead>
              <tbody>
                {conc.rows.map((row) => (
                  <tr key={row.underlying}>
                    <td><span className="position-symbol">{row.underlying}</span></td>
                    <td>{fmtPercent(row.exposure_share)}</td>
                    <td>{fmtCurrency(row.abs_exposure_base, currency)}</td>
                    <td className={row.net_position_value_base > 0 ? 'pnl-pos' : row.net_position_value_base < 0 ? 'pnl-neg' : ''}>
                      {fmtCurrency(row.net_position_value_base, currency)}
                    </td>
                    <td>{row.positions}</td>
                    <td>{fmtNumber(row.contracts, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {dte.data && dte.data.summary.shorts_open > 0 ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.dteDistribution}</div>
            <small className="muted">
              {copy.dteBuckets}: <strong>Safe</strong> &gt;45 · <strong>Watch</strong> 22–45 · <strong>Manage</strong> 8–21 · <strong>Critical</strong> ≤7. Snapshot EOD {dte.data.snapshot_date ?? '—'}.
            </small>
          </div>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart
              data={dte.data.buckets.map((b) => ({
                label: b.label,
                count: b.count,
              }))}
              margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
              <XAxis dataKey="label" stroke="#888" fontSize={11} />
              <YAxis stroke="#888" fontSize={11} allowDecimals={false} />
              <Tooltip
                content={
                  <ChartTooltip
                    formatter={(v, n) => `${n}: ${v}`}
                  />
                }
              />
              <Bar dataKey="count" fill="#22d3ee" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : null}

      {premium.data && premium.data.series.length > 0 ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.monthlyPremium}</div>
            <small className="muted">
              {copy.received}: {fmtCurrency(premium.data.summary.total_in, currency)} · {copy.paid}: {fmtCurrency(premium.data.summary.total_out, currency)} · {copy.net}: <strong>{fmtCurrency(premium.data.summary.total_net, currency)}</strong>
            </small>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={premium.data.series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
              <XAxis dataKey="bucket" stroke="#888" fontSize={11} />
              <YAxis stroke="#888" fontSize={11} />
              <Tooltip
                content={<ChartTooltip formatter={(v, n) => `${n}: ${typeof v === 'number' ? fmtCurrency(v, currency) : v}`} />}
              />
              <Line
                type="monotone"
                dataKey="cumulative_net_base"
                name={copy.accumulated}
                stroke="#22d3ee"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="net_premium_base"
                name={copy.monthlyNet}
                stroke="#a855f7"
                strokeWidth={1.5}
                dot={{ r: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}

      {attribution.data && (attribution.data.totals.grand_total_base !== 0 || attribution.data.by_underlying.length > 0) ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.attributionTitle}</div>
            <small className="muted">
              Total <strong>{fmtCurrency(attribution.data.totals.grand_total_base, currency)}</strong> ·
              {' '}{copy.attributionSubtitle}
            </small>
          </div>
          <div style={{ marginBottom: 12 }}>
            <AttributionStrip totals={attribution.data.totals} currency={currency} />
          </div>
          {attribution.data.by_underlying.length > 0 ? (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.underlying}</th>
                    <th title={copy.optPnlTitle}>OPT P&L</th>
                    <th title={copy.stkPnlTitle}>STK P&L</th>
                    <th title={copy.divsNetTitle}>Divs net</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {attribution.data.by_underlying.map((r) => (
                    <tr key={r.underlying}>
                      <td><span className="position-symbol">{r.underlying}</span></td>
                      <td className={r.opt_pnl_base > 0 ? 'pnl-pos' : r.opt_pnl_base < 0 ? 'pnl-neg' : ''}>{fmtCurrency(r.opt_pnl_base, currency)}</td>
                      <td className={r.stk_pnl_base > 0 ? 'pnl-pos' : r.stk_pnl_base < 0 ? 'pnl-neg' : ''}>{fmtCurrency(r.stk_pnl_base, currency)}</td>
                      <td title={`gross ${fmtCurrency(r.dividends_gross_base, currency)} · WHT ${fmtCurrency(r.withholding_base, currency)}`}>
                        {fmtCurrency(r.dividends_net_base, currency)}
                      </td>
                      <td className={r.total_base > 0 ? 'pnl-pos' : r.total_base < 0 ? 'pnl-neg' : ''}>
                        <strong>{fmtCurrency(r.total_base, currency)}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {assignmentRate.data && assignmentRate.data.totals.total_disposals > 0 ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.assignmentTitle}</div>
            <small className="muted">
              Stock transfer <strong>{((assignmentRate.data.totals.stock_transfer_rate ?? 0) * 100).toFixed(0)}%</strong> ·
              {' '}{copy.expiration} <strong>{((assignmentRate.data.totals.expiration_rate ?? 0) * 100).toFixed(0)}%</strong> ·
              {' '}{copy.manualClose} <strong>{((assignmentRate.data.totals.manual_close_rate ?? 0) * 100).toFixed(0)}%</strong> ·
              {' '}{copy.overDisposals} {assignmentRate.data.totals.total_disposals} disposals
            </small>
          </div>
          <div className="table-scroll">
            <table className="data-table trades-table">
              <thead>
                <tr>
                  <th>{copy.underlying}</th>
                  <th title={copy.boughtToCloseTitle}>Bought to close</th>
                  <th title={copy.soldToCloseTitle}>Sold to close</th>
                  <th title={copy.expiredTitle}>{copy.expired}</th>
                  <th title={copy.assignedTitle}>{copy.assigned}</th>
                  <th title={copy.exerciseTitle}>{copy.exercise}</th>
                  <th>Total</th>
                  <th title={copy.transferTitle}>Transfer %</th>
                </tr>
              </thead>
              <tbody>
                {assignmentRate.data.by_underlying.map((r) => (
                  <tr key={r.underlying}>
                    <td><span className="position-symbol">{r.underlying}</span></td>
                    <td>{r.bought_to_close || '—'}</td>
                    <td>{r.sold_to_close || '—'}</td>
                    <td className={r.expiration > 0 ? 'pnl-pos' : ''}>{r.expiration || '—'}</td>
                    <td className={r.assignment > 0 ? 'pnl-neg' : ''}>{r.assignment || '—'}</td>
                    <td className={r.exercise > 0 ? 'pnl-neg' : ''}>{r.exercise || '—'}</td>
                    <td><strong>{r.total_disposals}</strong></td>
                    <td className={(r.stock_transfer_rate ?? 0) > 0.3 ? 'pnl-neg' : ''}>
                      {r.stock_transfer_rate != null ? `${(r.stock_transfer_rate * 100).toFixed(0)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="filter-chip-grid" style={{ marginBottom: 12 }}>
        <button className={view === 'open' ? 'active' : ''} onClick={() => setView('open')}>{copy.tabOpenPositions}</button>
        <button className={view === 'lab' ? 'active' : ''} onClick={() => setView('lab')}>Strategy Lab</button>
        <button className={view === 'trades' ? 'active' : ''} onClick={() => setView('trades')}>{copy.tabTrades}</button>
        <button className={view === 'eae' ? 'active' : ''} onClick={() => setView('eae')}>{copy.tabEae}</button>
        <button className={view === 'risk' ? 'active' : ''} onClick={() => setView('risk')}>{copy.tabRisk}</button>
        <button className={view === 'underlying' ? 'active' : ''} onClick={() => setView('underlying')}>{copy.tabUnderlying}</button>
      </div>

      {view === 'open' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.openPositionsTitle}</div>
            <small className="muted">
              {copy.openPositionsSubtitle} Snapshot EOD {openPositions.data?.snapshot_date ?? '—'}.
            </small>
          </div>
          {openPositions.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : openPositions.isError ? (
            <div className="trades-empty error-state">{copy.openPositionsError}</div>
          ) : !openPositions.data?.rows.length ? (
            <div className="trades-empty">{copy.openPositionsEmpty}</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.underlying}</th>
                    <th>{copy.side}</th>
                    <th>{copy.type}</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th title={copy.dteTodayTitle}>DTE</th>
                    <th>{copy.contracts}</th>
                    <th>Mark</th>
                    <th>{copy.marketValue}</th>
                    <th>{copy.unrealized}</th>
                    <th>{copy.pnlPct}</th>
                    <th>{copy.moneyness}</th>
                  </tr>
                </thead>
                <tbody>
                  {openPositions.data.rows.map((r) => (
                    <tr key={`${r.symbol}|${r.quantity}`}>
                      <td>
                        <span className="position-symbol">{r.underlying ?? r.symbol}</span>
                        <small>{r.symbol}</small>
                      </td>
                      <td>
                        <span className={`trade-badge ${r.side === 'long' ? 'open' : 'closed'}`}>
                          {r.side === 'long' ? copy.long : copy.short}
                        </span>
                      </td>
                      <td>
                        <span className={`trade-badge ${r.right === 'C' ? 'open' : 'closed'}`}>
                          {r.right === 'C' ? 'CALL' : r.right === 'P' ? 'PUT' : '—'}
                        </span>
                      </td>
                      <td>{fmtNumber(r.strike)}</td>
                      <td>{fmtDate(r.expiry ?? undefined)}</td>
                      <td className={dteClass(r.dte)}>{r.dte ?? '—'}</td>
                      <td>{fmtNumber(r.contracts, 0)}</td>
                      <td>{fmtNumber(r.mark_price, 4)}</td>
                      <td className={r.position_value_base > 0 ? 'pnl-pos' : r.position_value_base < 0 ? 'pnl-neg' : ''}>
                        {fmtCurrency(r.position_value_base, currency)}
                      </td>
                      <td className={(r.fifo_pnl_unrealized_base ?? 0) > 0 ? 'pnl-pos' : (r.fifo_pnl_unrealized_base ?? 0) < 0 ? 'pnl-neg' : ''}>
                        {fmtCurrency(r.fifo_pnl_unrealized_base, currency)}
                      </td>
                      <td className={(r.unrealized_pct ?? 0) > 0 ? 'pnl-pos' : (r.unrealized_pct ?? 0) < 0 ? 'pnl-neg' : ''}>
                        {fmtPercent(r.unrealized_pct)}
                      </td>
                      <td className={(r.moneyness_pct ?? 0) > 0 ? 'pnl-pos' : (r.moneyness_pct ?? 0) < 0 ? 'pnl-neg' : ''}>
                        {fmtPercent(r.moneyness_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'lab' && (
        <div className="chart-panel strategy-lab">
          <div className="panel-header">
            <div>
              <div className="panel-title">Option Strategy Lab</div>
              <small className="muted">
                {copy.labDescription}
              </small>
            </div>
            <button
              type="button"
              className="settings-link-button"
              onClick={() => setLabLegs(templateLegs(labStrategy, labSpot))}
            >
              {copy.loadTemplate}
            </button>
          </div>

          <div className="strategy-lab-controls">
            <label>
              <span>{copy.strategy}</span>
              <select
                value={labStrategy}
                onChange={(event) => {
                  const next = event.target.value as StrategyId;
                  setLabStrategy(next);
                  setLabLegs(templateLegs(next, labSpot));
                }}
              >
                {STRATEGIES.map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>{strategy.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Spot</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={labSpot}
                onChange={(event) => setLabSpot(Number(event.target.value) || 0.01)}
              />
            </label>
          </div>

          <div className="kpi-grid strategy-lab-kpis">
            <KPICard
              label={lab.netPremium >= 0 ? 'Net Credit' : 'Net Debit'}
              value={fmtCurrency(Math.abs(lab.netPremium), currency)}
              delta={lab.netPremium >= 0 ? copy.creditReceived : copy.debitPaid}
              deltaKind={lab.netPremium >= 0 ? 'pos' : 'neg'}
            />
            <KPICard
              label={copy.maxProfit}
              value={lab.profitUnbounded ? '+∞' : fmtCurrency(lab.maxProfit, currency)}
              delta={lab.profitUnbounded ? copy.unboundedUpside : copy.expirationPayoff}
              deltaKind={lab.maxProfit >= 0 ? 'pos' : 'neg'}
            />
            <KPICard
              label={copy.maxLoss}
              value={lab.lossUnbounded ? '-∞' : fmtCurrency(lab.maxLoss, currency)}
              delta={lab.lossUnbounded ? copy.unboundedUpsideRisk : copy.expirationPayoff}
              deltaKind={lab.lossUnbounded || lab.maxLoss < 0 ? 'neg' : 'neutral'}
            />
            <KPICard
              label={copy.breakeven}
              value={lab.breakevens.length ? lab.breakevens.map((value) => value.toFixed(2)).join(' / ') : '—'}
              delta={copy.linearInterpolation}
            />
          </div>

          <div className="strategy-lab-grid">
            <div className="strategy-lab-legs">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.side}</th>
                    <th>{copy.type}</th>
                    <th>Strike</th>
                    <th>{copy.premium}</th>
                    <th>{copy.contracts}</th>
                  </tr>
                </thead>
                <tbody>
                  {labLegs.map((leg, index) => (
                    <tr key={`${index}-${leg.side}-${leg.right}`}>
                      <td>
                        <select value={leg.side} onChange={(event) => updateLabLeg(index, { side: event.target.value as LabLeg['side'] })}>
                          <option value="BUY">BUY</option>
                          <option value="SELL">SELL</option>
                        </select>
                      </td>
                      <td>
                        <select value={leg.right} onChange={(event) => updateLabLeg(index, { right: event.target.value as LabLeg['right'] })}>
                          <option value="C">Call</option>
                          <option value="P">Put</option>
                        </select>
                      </td>
                      <td>
                        <input type="number" step="0.01" value={leg.strike} onChange={(event) => updateLabLeg(index, { strike: Number(event.target.value) || 0 })} />
                      </td>
                      <td>
                        <input type="number" step="0.01" value={leg.premium} onChange={(event) => updateLabLeg(index, { premium: Number(event.target.value) || 0 })} />
                      </td>
                      <td>
                        <input type="number" min="1" step="1" value={leg.contracts} onChange={(event) => updateLabLeg(index, { contracts: Math.max(1, Number(event.target.value) || 1) })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="strategy-lab-chart">
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={lab.points}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,.08)" />
                  <XAxis dataKey="price" stroke="#888" fontSize={11} tickFormatter={(value) => Number(value).toFixed(0)} />
                  <YAxis stroke="#888" fontSize={11} tickFormatter={(value) => fmtCurrency(Number(value), currency)} width={82} />
                  <Tooltip content={<ChartTooltip formatter={(value) => fmtCurrency(Number(value), currency)} />} />
                  <Line type="monotone" dataKey="pnl" name="Expiration P&L" stroke="#3dba8b" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {view === 'trades' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.optionTrades}</div>
            <div className="trades-filters">
              <div className="filter-chip-grid">
                <button className={status === 'all' ? 'active' : ''} onClick={() => setStatus('all')}>{copy.all}</button>
                <button className={status === 'open' ? 'active' : ''} onClick={() => setStatus('open')}>{copy.open}</button>
                <button className={status === 'closed' ? 'active' : ''} onClick={() => setStatus('closed')}>{copy.closed}</button>
              </div>
              <div className="filter-chip-grid">
                <button className={right === '' ? 'active' : ''} onClick={() => setRight('')}>Call/Put</button>
                <button className={right === 'C' ? 'active' : ''} onClick={() => setRight('C')}>Call</button>
                <button className={right === 'P' ? 'active' : ''} onClick={() => setRight('P')}>Put</button>
              </div>
              <select value={underlying} onChange={(e) => setUnderlying(e.target.value)} className="trades-select">
                <option value="">{copy.underlyingSelect} ▾</option>
                {underlyings.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
          </div>
          {trades.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : trades.isError ? (
            <div className="trades-empty error-state">{copy.tradesError}</div>
          ) : !trades.data?.rows.length ? (
            <div className="trades-empty">{copy.tradesEmpty}</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.date}</th>
                    <th>{copy.underlying}</th>
                    <th>{copy.type}</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th title={copy.dteTodayTitle}>DTE</th>
                    <th>{copy.action}</th>
                    <th>{copy.contracts}</th>
                    <th>{copy.premium}</th>
                    <th>{copy.commission}</th>
                    <th>{copy.realizedPnl}</th>
                    <th title={copy.rocTitle}>ROC %</th>
                    <th title={copy.captureTitle}>Capture %</th>
                    <th>{copy.status}</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.data.rows.map((r) => {
                    const pnl = r.fifo_pnl_realized_base;
                    return (
                      <tr key={r.trade_id}>
                        <td>
                          <span className="position-symbol">{fmtDate(r.trade_date)}</span>
                          <small>{fmtTime(r.trade_datetime)}</small>
                        </td>
                        <td>
                          <span className="position-symbol">{r.underlying ?? r.symbol}</span>
                          <small>{r.currency}</small>
                        </td>
                        <td>
                          <span className={`trade-badge ${r.right === 'C' ? 'open' : 'closed'}`}>
                            {r.right === 'C' ? 'CALL' : r.right === 'P' ? 'PUT' : '—'}
                          </span>
                        </td>
                        <td>{fmtNumber(r.strike)}</td>
                        <td>{fmtDate(r.expiry)}</td>
                        <td className={dteClass(r.dte_today)}>{r.dte_today ?? '—'}</td>
                        <td title={r.is_multi_leg ? `${r.strategy_legs} legs sharing order_id ${r.ib_order_id ?? ''}` : undefined}>
                          {r.is_multi_leg ? (
                            <>
                              <span className="trade-badge open" style={{ marginRight: 6, fontSize: '0.7rem' }}>
                                {r.strategy_legs}L
                              </span>
                              {r.strategy}
                              <small style={{ display: 'block' }}>{r.action}</small>
                            </>
                          ) : (
                            r.action
                          )}
                        </td>
                        <td>{r.quantity}</td>
                        <td>{fmtCurrency(r.proceeds_base, currency)}</td>
                        <td className="pnl-neg">{r.commission_base !== undefined ? fmtCurrency(r.commission_base, currency) : '—'}</td>
                        <td className={pnl !== undefined && pnl !== null ? (pnl > 0 ? 'pnl-pos' : pnl < 0 ? 'pnl-neg' : '') : ''}>
                          {pnl !== undefined && pnl !== null && pnl !== 0
                            ? `${pnl > 0 ? '+' : ''}${fmtCurrency(pnl, currency)}`
                            : (r.open_close === 'O' ? '—' : fmtCurrency(0, currency))}
                        </td>
                        <td
                          className={r.roc_pct != null ? (r.roc_pct > 0 ? 'pnl-pos' : r.roc_pct < 0 ? 'pnl-neg' : '') : ''}
                          title={r.capital_at_risk_base != null ? `${copy.capitalAtRisk} ${fmtCurrency(r.capital_at_risk_base, currency)}` : undefined}
                        >
                          {r.roc_pct != null ? `${r.roc_pct > 0 ? '+' : ''}${r.roc_pct.toFixed(2)}%` : '—'}
                        </td>
                        <td className={r.capture_pct != null ? (r.capture_pct >= 0.5 ? 'pnl-pos' : r.capture_pct >= 0 ? '' : 'pnl-neg') : ''}>
                          {r.capture_pct != null ? `${r.capture_pct > 0 ? '+' : ''}${(r.capture_pct * 100).toFixed(0)}%` : '—'}
                        </td>
                        <td>
                          <span className={`trade-badge ${r.is_open ? 'open' : 'closed'}`}>
                            {r.is_open ? copy.openBadge : copy.closedBadge}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'eae' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">Exercise · Assignment · Expiration</div>
            <small className="muted">
              {eae.data ? (
                <>
                  Exercise: <strong>{eae.data.summary.counts.exercise ?? 0}</strong> ·
                  Assignment: <strong>{eae.data.summary.counts.assignment ?? 0}</strong> ·
                  Expiration: <strong>{eae.data.summary.counts.expiration ?? 0}</strong> ·
                  {copy.cashNet} {fmtCurrency(eae.data.summary.cash_impact_total, currency)}
                </>
              ) : null}
            </small>
          </div>
          {eae.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : eae.isError ? (
            <div className="trades-empty error-state">{copy.eaeError}</div>
          ) : !eae.data?.rows.length ? (
            <div className="trades-empty">
              {copy.eaeEmpty}
            </div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.date}</th>
                    <th>{copy.type}</th>
                    <th>{copy.underlying}</th>
                    <th>{copy.contract}</th>
                    <th>Right</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th>Qty</th>
                    <th>{copy.price}</th>
                    <th>Cash</th>
                    <th>P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {eae.data.rows.map((r) => (
                    <tr key={r.eae_id}>
                      <td>{fmtDate(r.event_date)}</td>
                      <td>{r.transaction_type ?? '—'}</td>
                      <td><span className="position-symbol">{r.underlying ?? '—'}</span></td>
                      <td><small>{r.symbol}</small></td>
                      <td>{r.right ?? '—'}</td>
                      <td>{fmtNumber(r.strike)}</td>
                      <td>{fmtDate(r.expiry)}</td>
                      <td>{r.quantity}</td>
                      <td>{fmtNumber(r.trade_price)}</td>
                      <td className={(r.proceeds ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                        {fmtNumber(r.proceeds)}
                      </td>
                      <td className={(r.realized_pnl ?? 0) > 0 ? 'pnl-pos' : (r.realized_pnl ?? 0) < 0 ? 'pnl-neg' : ''}>
                        {r.realized_pnl !== undefined && r.realized_pnl !== null ? fmtNumber(r.realized_pnl) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'risk' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.tabRisk}</div>
            <small className="muted">
              {copy.assignmentRiskDescription.replace('{date}', risk.data?.snapshot_date ?? '—')}
            </small>
          </div>
          {risk.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : risk.isError ? (
            <div className="trades-empty error-state">{copy.riskError}</div>
          ) : !risk.data?.rows.length ? (
            <div className="trades-empty">{copy.riskEmpty}</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.underlying}</th>
                    <th>Right</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th>DTE</th>
                    <th>Qty</th>
                    <th>Mark</th>
                    <th>{copy.underlyingClose}</th>
                    <th>Moneyness</th>
                    <th>{copy.intrinsicPerContract}</th>
                    <th>{copy.intrinsicTotal}</th>
                  </tr>
                </thead>
                <tbody>
                  {risk.data.rows.map((r) => (
                    <tr key={`${r.symbol}|${r.expiry ?? ''}|${r.quantity ?? ''}`} className={r.itm ? 'risk-row-itm' : ''}>
                      <td><span className="position-symbol">{r.underlying ?? r.symbol}</span></td>
                      <td>
                        <span className={`trade-badge ${r.right === 'C' ? 'open' : 'closed'}`}>
                          {r.right === 'C' ? 'CALL' : 'PUT'}
                        </span>
                      </td>
                      <td>{fmtNumber(r.strike)}</td>
                      <td>{fmtDate(r.expiry)}</td>
                      <td className={dteClass(r.dte)}>{r.dte ?? '—'}</td>
                      <td>{r.quantity}</td>
                      <td>{fmtNumber(r.mark_price, 4)}</td>
                      <td>{r.last_underlying_close !== undefined && r.last_underlying_close !== null ? fmtNumber(r.last_underlying_close) : '—'}</td>
                      <td>
                        <span className={`trade-badge ${r.itm ? 'closed' : 'open'}`}>
                          {r.itm === null || r.itm === undefined ? 'n/d' : r.itm ? 'ITM' : 'OTM'}
                        </span>
                      </td>
                      <td className={r.itm ? 'pnl-neg' : ''}>{fmtNumber(r.intrinsic_per_contract)}</td>
                      <td className={r.itm ? 'pnl-neg' : ''}>{fmtCurrency(r.intrinsic_total_base, currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'underlying' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.pnlByUnderlying}</div>
            <small className="muted">{copy.pnlByUnderlyingSubtitle}</small>
          </div>
          {byUnderlying.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : byUnderlying.isError ? (
            <div className="trades-empty error-state">{copy.underlyingError}</div>
          ) : !byUnderlying.data?.rows.length ? (
            <div className="trades-empty">{copy.noData}</div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={Math.max(180, 30 * byUnderlying.data.rows.length + 40)}>
                <BarChart data={byUnderlying.data.rows} layout="vertical" margin={{ left: 20, right: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
                  <XAxis type="number" stroke="#888" fontSize={11} />
                  <YAxis type="category" dataKey="underlying" stroke="#888" fontSize={11} width={70} />
                  <Tooltip
                content={<ChartTooltip formatter={(v, n) => `${n}: ${typeof v === 'number' ? fmtCurrency(v, currency) : v}`} />}
              />
                  <Bar dataKey="realized_pnl_base" name={copy.realizedPnl} fill="#22d3ee" />
                </BarChart>
              </ResponsiveContainer>
              <div className="table-scroll" style={{ marginTop: 12 }}>
                <table className="data-table trades-table">
                  <thead>
                    <tr>
                      <th>{copy.underlying}</th>
                      <th>Trades</th>
                      <th>{copy.contracts}</th>
                      <th>{copy.netPremium}</th>
                      <th>{copy.realizedPnl}</th>
                      <th>{copy.commissions}</th>
                      <th>Win rate</th>
                      <th>W · L · BE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byUnderlying.data.rows.map((r) => (
                      <tr key={r.underlying}>
                        <td><span className="position-symbol">{r.underlying}</span></td>
                        <td>{r.trades}</td>
                        <td>{r.contracts}</td>
                        <td className={r.net_premium_base >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                          {fmtCurrency(r.net_premium_base, currency)}
                        </td>
                        <td className={r.realized_pnl_base > 0 ? 'pnl-pos' : r.realized_pnl_base < 0 ? 'pnl-neg' : ''}>
                          {fmtCurrency(r.realized_pnl_base, currency)}
                        </td>
                        <td className="pnl-neg">{fmtCurrency(r.commissions_base, currency)}</td>
                        <td>{fmtPercent(r.win_rate)}</td>
                        <td><small>{r.win} · {r.loss} · {r.be}</small></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
