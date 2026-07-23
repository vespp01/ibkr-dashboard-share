import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { PRIVACY_MODE_STORAGE_KEY } from './format';

export type Language = 'es' | 'en' | 'de';

type TranslationParams = Record<string, string | number | boolean | null | undefined>;

const STORAGE_KEY = 'ibkr-dashboard-language';

const LOCALES: Record<Language, string> = {
  es: 'es-ES',
  en: 'en-US',
  de: 'de-DE',
};

export const LANGUAGE_OPTIONS: Array<{ value: Language; nativeLabel: string; englishLabel: string }> = [
  { value: 'es', nativeLabel: 'Español', englishLabel: 'Spanish' },
  { value: 'en', nativeLabel: 'English', englishLabel: 'English' },
  { value: 'de', nativeLabel: 'Deutsch', englishLabel: 'German' },
];

const messages = {
  es: {
    common: {
      all: 'Todo',
      api: 'API',
      backend: 'backend',
      closed: 'Cerradas',
      configured: 'Configurado',
      copy: 'Copiar',
      current: 'Actual',
      docs: 'Documentación',
      error: 'Error',
      flex: 'Flex',
      language: 'Idioma',
      loading: 'Cargando...',
      localOnly: 'Local only',
      month: 'mes',
      nav: 'NAV',
      next: 'Siguiente',
      no: 'No',
      noData: 'Sin datos para mostrar.',
      notConfigured: 'No configurado',
      open: 'Abiertas',
      optional: 'Opcional',
      period: 'Periodo',
      previous: 'Anterior',
      ready: 'Listo',
      required: 'Obligatorio',
      settings: 'Settings',
      status: 'Estado',
      today: 'Este mes',
      total: 'Total',
      week: 'Sem',
      year: 'Año',
      yes: 'Sí',
    },
    periods: {
      all: 'Todo',
      ytd: 'YTD',
      oneYear: '1A',
      threeYears: '3A',
      fiveYears: '5A',
    },
    sidebar: {
      mainNavigation: 'Navegación principal',
      sourceStatus: 'Estado de fuentes',
      version: 'v0.2 · React + Vite',
      statusTitle: {
        api: 'FastAPI backend',
        flex: 'Flex Web Service',
        ibgw: 'IB Gateway',
        cpg: 'Client Portal Gateway',
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Cartera',
        operaciones: 'Operaciones',
        opciones: 'Opciones',
        dividendos: 'Dividendos',
        rentabilidad: 'Rentabilidad',
        comisiones: 'Comisiones',
        margen: 'Margen',
        settings: 'Settings',
      },
    },
    page: {
      resumen: {
        title: 'Dashboard',
        subtitle: 'Vista general del portfolio: KPIs, NAV y proyección de ingresos.',
      },
      cartera: {
        title: 'Cartera',
        subtitle: 'Posiciones actuales, distribución y P&L latente por símbolo.',
      },
      operaciones: {
        title: 'Operaciones',
        subtitle: 'Detalle por trade: filtra por estado, símbolo o estrategia.',
      },
      opciones: {
        title: 'Opciones',
        subtitle: 'Operaciones, eventos E/A/E, prima acumulada y riesgo de asignación.',
      },
      dividendos: {
        title: 'Dividendos',
        subtitle: 'Bruto, retenciones y neto: cronograma mensual y anual.',
      },
      rentabilidad: {
        title: 'Rentabilidad',
        subtitle: 'TWR y XIRR por divisa y periodo, aislando aportes.',
      },
      comisiones: {
        title: 'Comisiones',
        subtitle: 'Costes operativos: totales por periodo y desglose.',
      },
      margen: {
        title: 'Margen',
        subtitle: 'Leverage, cash debt por divisa, concentración, Reg-T e intereses pagados.',
      },
      settings: {
        title: 'Settings',
        subtitle: 'Idioma, privacidad local y checklist de conexión a IBKR.',
      },
    },
    dashboard: {
      staleWarning: 'Datos sin actualizar hace más de 30 días. Último dato: hace {days} días. Ejecuta un pull de Flex para refrescar.',
      footerAdvice: 'Los datos mostrados son históricos y no constituyen asesoramiento financiero.',
      footerTwr: 'TWR = Time-Weighted Return (mide la rentabilidad de la cartera aislando aportes y retiradas).',
      asOf: 'a {date}',
      asOfOneDay: 'a {date} · 1 día',
      asOfDays: 'a {date} · {days} días',
    },
    settings: {
      languageTitle: 'Idioma de la interfaz',
      languageDescription: 'La selección se guarda en este navegador y cambia etiquetas, ayuda de configuración y formatos regionales.',
      languageHint: 'El idioma no cambia los datos importados desde IBKR ni las abreviaturas financieras estándar.',
      privacyTitle: 'Privacidad antes de compartir',
      privacyModeTitle: 'Modo privacidad',
      privacyModeDescription: 'Oculta importes y porcentajes formateados para capturas, demos o sesiones de feedback.',
      privacyModeOn: 'Privacidad activada',
      privacyModeOff: 'Privacidad desactivada',
      privacyModeHint: 'Solo cambia la visualización en este navegador; no modifica la base de datos ni los XML de IBKR.',
      privacyItems: {
        local: 'La app corre localmente con Docker Compose; no necesita un servicio cloud para leer Flex.',
        secrets: 'No publiques .env, tokens Flex, bases SQLite ni extractos de IBKR.', // pragma: allowlist secret
        sample: 'Comparte capturas con datos anonimizados o una base de prueba si quieres feedback público.',
      },
      ibkrTitle: 'Conectar IBKR',
      ibkrDescription: 'Ruta mínima para que otra persona arranque el dashboard con su propia cuenta.',
      flexTitle: 'Crear Flex Queries',
      flexDescription: 'Crea una Activity Flex Query obligatoria y una Trade Confirmation Flex Query opcional para ejecuciones intradía.',
      commandsTitle: 'Arranque local',
      docsTitle: 'Guía detallada',
      docsDescription: 'La guía completa enumera cada sección de Flex, columnas, pruebas y troubleshooting de HTTP 604.',
      openDocs: 'Abrir guía Flex',
      steps: {
        token: 'IBKR Client Portal -> Performance & Reports -> Flex Queries -> Flex Web Service -> Generate Token.',
        env: 'Copia .env.example a .env y rellena IB_FLEX_TOKEN, IB_FLEX_TOKEN_EXPIRY e IB_FLEX_QUERY_ID.',
        activity: 'Activity Flex Query: nombre dashboard_full_daily, formato XML, periodo Year to Date y todas las secciones/columnas requeridas.',
        tc: 'Trade Confirmation Flex Query: nombre dashboard_tc_daily, formato XML y todos los campos de Trade Confirms. Rellena IB_FLEX_QUERY_ID_TC si la usas.',
        start: 'Ejecuta Docker Compose, luego el bootstrap una vez para cargar el histórico inicial.',
      },
      requiredSections: 'Secciones clave Activity Flex',
      sections: 'Account Information, NAV in Base, Change in NAV, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Transfers.',
      healthTitle: 'Comprobaciones rápidas',
      setupStatusTitle: 'Estado real del setup',
      setupStatusDescription: 'Lectura no secreta del backend para saber si Flex, TC y la base local están listos.',
      dashboardCardsTitle: 'Cajas del dashboard',
      dashboardCardsDescription: 'Elige qué bloques aparecen en el Dashboard. Se guarda solo en este navegador.',
      resetRecommended: 'Vista recomendada',
      showAll: 'Mostrar todo',
      activityQuery: 'Activity Flex Query',
      tcQuery: 'Trade Confirmation Query',
      latestFlexRun: 'Último pull Flex',
      latestData: 'Último dato',
      baseCurrency: 'Divisa base',
      tokenExpiry: 'Caducidad token',
      rowsImported: 'Filas importadas',
      databaseRows: 'Filas DB',
      stale: 'Desactualizado',
      fresh: 'Actualizado',
      healthItems: {
        frontend: 'Frontend: http://localhost:5173',
        api: 'API: http://localhost:8001/health',
        swagger: 'Swagger: http://localhost:8001/docs',
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Resumen de cuenta',
        description: 'NAV, cushion, buying power y excess liquidity.',
      },
      tradingQuality: {
        title: 'Trading Quality',
        description: 'Profit factor, win rate, expectancy, payoff, drawdown y Sharpe de P&L realizado.',
      },
      pnlAttribution: {
        title: 'Atribución de P&L',
        description: 'Desglose de realized, MTM, dividendos, intereses, comisiones, impuestos y FX.',
      },
      tradeCalendar: {
        title: 'Calendario',
        description: 'Mapa mensual de P&L realizado por día.',
      },
      activityTiles: {
        title: 'Actividad del mes',
        description: 'Tickers operados, crédito abierto y profit factor mensual.',
      },
      coreKpis: {
        title: 'KPIs de contexto',
        description: 'TWR, dividendos, PADI, yield, P&L no realizado y posiciones.',
      },
      allTimeReturn: {
        title: 'Retorno total',
        description: 'NAV menos aportes netos desde el inicio.',
      },
      realizedMonthly: {
        title: 'Matriz realized mensual',
        description: 'P&L FIFO realizado por mes y año.',
      },
      totalReturnMonthly: {
        title: 'Matriz total return mensual',
        description: 'Retorno total mensual cuando hay NAV diario suficiente.',
      },
    },
  },
  en: {
    common: {
      all: 'All',
      api: 'API',
      backend: 'backend',
      closed: 'Closed',
      configured: 'Configured',
      copy: 'Copy',
      current: 'Current',
      docs: 'Documentation',
      error: 'Error',
      flex: 'Flex',
      language: 'Language',
      loading: 'Loading...',
      localOnly: 'Local only',
      month: 'month',
      nav: 'NAV',
      next: 'Next',
      no: 'No',
      noData: 'No data to display.',
      notConfigured: 'Not configured',
      open: 'Open',
      optional: 'Optional',
      period: 'Period',
      previous: 'Previous',
      ready: 'Ready',
      required: 'Required',
      settings: 'Settings',
      status: 'Status',
      today: 'This month',
      total: 'Total',
      week: 'Wk',
      year: 'Year',
      yes: 'Yes',
    },
    periods: {
      all: 'All',
      ytd: 'YTD',
      oneYear: '1Y',
      threeYears: '3Y',
      fiveYears: '5Y',
    },
    sidebar: {
      mainNavigation: 'Main navigation',
      sourceStatus: 'Data sources',
      version: 'v0.2 · React + Vite',
      statusTitle: {
        api: 'FastAPI backend',
        flex: 'Flex Web Service',
        ibgw: 'IB Gateway',
        cpg: 'Client Portal Gateway',
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Portfolio',
        operaciones: 'Trades',
        opciones: 'Options',
        dividendos: 'Dividends',
        rentabilidad: 'Performance',
        comisiones: 'Fees',
        margen: 'Margin',
        settings: 'Settings',
      },
    },
    page: {
      resumen: {
        title: 'Dashboard',
        subtitle: 'Portfolio overview: KPIs, NAV, and income projection.',
      },
      cartera: {
        title: 'Portfolio',
        subtitle: 'Current positions, allocation, and unrealized P&L by symbol.',
      },
      operaciones: {
        title: 'Trades',
        subtitle: 'Trade detail with status, symbol, and strategy filters.',
      },
      opciones: {
        title: 'Options',
        subtitle: 'Option trades, E/A/E events, accumulated premium, and assignment risk.',
      },
      dividendos: {
        title: 'Dividends',
        subtitle: 'Gross, withholding, and net dividends with monthly and yearly schedules.',
      },
      rentabilidad: {
        title: 'Performance',
        subtitle: 'TWR and XIRR by currency and period, isolating external cash flows.',
      },
      comisiones: {
        title: 'Fees',
        subtitle: 'Operating costs by period with breakdowns.',
      },
      margen: {
        title: 'Margin',
        subtitle: 'Leverage, cash debt by currency, concentration, Reg-T estimates, and interest paid.',
      },
      settings: {
        title: 'Settings',
        subtitle: 'Language, local privacy, and IBKR connection checklist.',
      },
    },
    dashboard: {
      staleWarning: 'Data has not updated for more than 30 days. Last data point: {days} days ago. Run a Flex pull to refresh.',
      footerAdvice: 'The displayed data is historical and is not financial advice.',
      footerTwr: 'TWR = Time-Weighted Return, measuring portfolio performance while isolating deposits and withdrawals.',
      asOf: 'as of {date}',
      asOfOneDay: 'as of {date} · 1 day',
      asOfDays: 'as of {date} · {days} days',
    },
    settings: {
      languageTitle: 'Interface language',
      languageDescription: 'The selection is saved in this browser and changes labels, setup help, and regional formats.',
      languageHint: 'Language does not alter imported IBKR data or standard financial abbreviations.',
      privacyTitle: 'Privacy before sharing',
      privacyModeTitle: 'Privacy mode',
      privacyModeDescription: 'Masks formatted amounts and percentages for screenshots, demos, or feedback sessions.',
      privacyModeOn: 'Privacy on',
      privacyModeOff: 'Privacy off',
      privacyModeHint: 'This only changes display in this browser; it does not modify the database or IBKR XML files.',
      privacyItems: {
        local: 'The app runs locally with Docker Compose; no cloud service is required to read Flex.',
        secrets: 'Do not publish .env, Flex tokens, SQLite databases, or IBKR statements.', // pragma: allowlist secret
        sample: 'Use anonymized screenshots or a test database when asking for public feedback.',
      },
      ibkrTitle: 'Connect IBKR',
      ibkrDescription: 'Minimum path for someone else to start the dashboard with their own account.',
      flexTitle: 'Create Flex Queries',
      flexDescription: 'Create one required Activity Flex Query and one optional Trade Confirmation Flex Query for intraday executions.',
      commandsTitle: 'Local startup',
      docsTitle: 'Detailed guide',
      docsDescription: 'The full guide lists every Flex section, columns, test commands, and HTTP 604 troubleshooting.',
      openDocs: 'Open Flex guide',
      steps: {
        token: 'IBKR Client Portal -> Performance & Reports -> Flex Queries -> Flex Web Service -> Generate Token.',
        env: 'Copy .env.example to .env and fill IB_FLEX_TOKEN, IB_FLEX_TOKEN_EXPIRY, and IB_FLEX_QUERY_ID.',
        activity: 'Activity Flex Query: name dashboard_full_daily, XML format, Year to Date period, and all required sections/columns.',
        tc: 'Trade Confirmation Flex Query: name dashboard_tc_daily, XML format, and every Trade Confirms field. Fill IB_FLEX_QUERY_ID_TC if you use it.',
        start: 'Run Docker Compose, then bootstrap once to load the initial history.',
      },
      requiredSections: 'Key Activity Flex sections',
      sections: 'Account Information, NAV in Base, Change in NAV, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Transfers.',
      healthTitle: 'Quick checks',
      setupStatusTitle: 'Real setup status',
      setupStatusDescription: 'Non-secret backend status for Flex, TC, and the local database.',
      dashboardCardsTitle: 'Dashboard boxes',
      dashboardCardsDescription: 'Choose which blocks appear on the Dashboard. Saved only in this browser.',
      resetRecommended: 'Recommended view',
      showAll: 'Show all',
      activityQuery: 'Activity Flex Query',
      tcQuery: 'Trade Confirmation Query',
      latestFlexRun: 'Latest Flex pull',
      latestData: 'Latest data',
      baseCurrency: 'Base currency',
      tokenExpiry: 'Token expiry',
      rowsImported: 'Rows imported',
      databaseRows: 'DB rows',
      stale: 'Stale',
      fresh: 'Fresh',
      healthItems: {
        frontend: 'Frontend: http://localhost:5173',
        api: 'API: http://localhost:8001/health',
        swagger: 'Swagger: http://localhost:8001/docs',
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Account summary',
        description: 'NAV, cushion, buying power, and excess liquidity.',
      },
      tradingQuality: {
        title: 'Trading Quality',
        description: 'Profit factor, win rate, expectancy, payoff, drawdown, and realized-P&L Sharpe.',
      },
      pnlAttribution: {
        title: 'P&L attribution',
        description: 'Breakdown of realized, MTM, dividends, interest, fees, taxes, and FX.',
      },
      tradeCalendar: {
        title: 'Calendar',
        description: 'Monthly realized-P&L heatmap by day.',
      },
      activityTiles: {
        title: 'Monthly activity',
        description: 'Traded tickers, open credit, and monthly profit factor.',
      },
      coreKpis: {
        title: 'Context KPIs',
        description: 'TWR, dividends, PADI, yield, unrealized P&L, and positions.',
      },
      allTimeReturn: {
        title: 'Total return',
        description: 'NAV minus cumulative net deposits since inception.',
      },
      realizedMonthly: {
        title: 'Monthly realized matrix',
        description: 'FIFO realized P&L by month and year.',
      },
      totalReturnMonthly: {
        title: 'Monthly total-return matrix',
        description: 'Monthly total return when daily NAV coverage is sufficient.',
      },
    },
  },
  de: {
    common: {
      all: 'Alle',
      api: 'API',
      backend: 'Backend',
      closed: 'Geschlossen',
      configured: 'Konfiguriert',
      copy: 'Kopieren',
      current: 'Aktuell',
      docs: 'Dokumentation',
      error: 'Fehler',
      flex: 'Flex',
      language: 'Sprache',
      loading: 'Laden...',
      localOnly: 'Nur lokal',
      month: 'Monat',
      nav: 'NAV',
      next: 'Weiter',
      no: 'Nein',
      noData: 'Keine Daten zum Anzeigen.',
      notConfigured: 'Nicht konfiguriert',
      open: 'Offen',
      optional: 'Optional',
      period: 'Zeitraum',
      previous: 'Zurueck',
      ready: 'Bereit',
      required: 'Erforderlich',
      settings: 'Einstellungen',
      status: 'Status',
      today: 'Dieser Monat',
      total: 'Total',
      week: 'KW',
      year: 'Jahr',
      yes: 'Ja',
    },
    periods: {
      all: 'Alle',
      ytd: 'YTD',
      oneYear: '1J',
      threeYears: '3J',
      fiveYears: '5J',
    },
    sidebar: {
      mainNavigation: 'Hauptnavigation',
      sourceStatus: 'Datenquellen',
      version: 'v0.2 · React + Vite',
      statusTitle: {
        api: 'FastAPI Backend',
        flex: 'Flex Web Service',
        ibgw: 'IB Gateway',
        cpg: 'Client Portal Gateway',
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Portfolio',
        operaciones: 'Trades',
        opciones: 'Optionen',
        dividendos: 'Dividenden',
        rentabilidad: 'Performance',
        comisiones: 'Gebuehren',
        margen: 'Margin',
        settings: 'Einstellungen',
      },
    },
    page: {
      resumen: {
        title: 'Dashboard',
        subtitle: 'Portfolio-Ueberblick: KPIs, NAV und Ertragsprojektion.',
      },
      cartera: {
        title: 'Portfolio',
        subtitle: 'Aktuelle Positionen, Allokation und unrealisierter P&L nach Symbol.',
      },
      operaciones: {
        title: 'Trades',
        subtitle: 'Trade-Details mit Filtern nach Status, Symbol und Strategie.',
      },
      opciones: {
        title: 'Optionen',
        subtitle: 'Options-Trades, E/A/E-Events, kumulierte Praemie und Zuteilungsrisiko.',
      },
      dividendos: {
        title: 'Dividenden',
        subtitle: 'Brutto, Quellensteuer und Netto mit Monats- und Jahresplan.',
      },
      rentabilidad: {
        title: 'Performance',
        subtitle: 'TWR und XIRR nach Waehrung und Zeitraum, bereinigt um Cashflows.',
      },
      comisiones: {
        title: 'Gebuehren',
        subtitle: 'Operative Kosten nach Zeitraum mit Aufschluesselung.',
      },
      margen: {
        title: 'Margin',
        subtitle: 'Leverage, Cash Debt je Waehrung, Konzentration, Reg-T und gezahlte Zinsen.',
      },
      settings: {
        title: 'Einstellungen',
        subtitle: 'Sprache, lokale Privatsphaere und IBKR-Verbindungscheckliste.',
      },
    },
    dashboard: {
      staleWarning: 'Die Daten wurden seit mehr als 30 Tagen nicht aktualisiert. Letzter Datenpunkt: vor {days} Tagen. Fuehre einen Flex-Pull aus.',
      footerAdvice: 'Die angezeigten Daten sind historisch und keine Finanzberatung.',
      footerTwr: 'TWR = Time-Weighted Return, misst die Portfolio-Performance bereinigt um Ein- und Auszahlungen.',
      asOf: 'Stand {date}',
      asOfOneDay: 'Stand {date} · 1 Tag',
      asOfDays: 'Stand {date} · {days} Tage',
    },
    settings: {
      languageTitle: 'Sprache der Oberflaeche',
      languageDescription: 'Die Auswahl wird in diesem Browser gespeichert und aendert Labels, Setup-Hilfe und regionale Formate.',
      languageHint: 'Die Sprache aendert weder importierte IBKR-Daten noch gaengige Finanzabkuerzungen.',
      privacyTitle: 'Privatsphaere vor dem Teilen',
      privacyModeTitle: 'Privatsphaere-Modus',
      privacyModeDescription: 'Blendet formatierte Betraege und Prozente fuer Screenshots, Demos oder Feedback-Sitzungen aus.',
      privacyModeOn: 'Privatsphaere an',
      privacyModeOff: 'Privatsphaere aus',
      privacyModeHint: 'Aendert nur die Anzeige in diesem Browser; Datenbank und IBKR-XML-Dateien bleiben unveraendert.',
      privacyItems: {
        local: 'Die App laeuft lokal mit Docker Compose; zum Lesen von Flex ist kein Cloud-Dienst erforderlich.',
        secrets: 'Veroeffentliche keine .env-Datei, Flex-Tokens, SQLite-Datenbanken oder IBKR-Auszuege.', // pragma: allowlist secret
        sample: 'Nutze anonymisierte Screenshots oder eine Testdatenbank, wenn du oeffentlich Feedback einholst.',
      },
      ibkrTitle: 'IBKR verbinden',
      ibkrDescription: 'Minimaler Ablauf, damit jemand anderes das Dashboard mit dem eigenen Konto starten kann.',
      flexTitle: 'Flex Queries erstellen',
      flexDescription: 'Erstelle eine erforderliche Activity Flex Query und optional eine Trade Confirmation Flex Query fuer Intraday-Ausfuehrungen.',
      commandsTitle: 'Lokaler Start',
      docsTitle: 'Detaillierte Anleitung',
      docsDescription: 'Die vollstaendige Anleitung listet alle Flex-Sektionen, Spalten, Testbefehle und HTTP-604-Fehlerbehebung.',
      openDocs: 'Flex-Anleitung oeffnen',
      steps: {
        token: 'IBKR Client Portal -> Performance & Reports -> Flex Queries -> Flex Web Service -> Generate Token.',
        env: 'Kopiere .env.example nach .env und setze IB_FLEX_TOKEN, IB_FLEX_TOKEN_EXPIRY und IB_FLEX_QUERY_ID.',
        activity: 'Activity Flex Query: Name dashboard_full_daily, XML-Format, Zeitraum Year to Date und alle erforderlichen Sektionen/Spalten.',
        tc: 'Trade Confirmation Flex Query: Name dashboard_tc_daily, XML-Format und alle Felder in Trade Confirms. Setze IB_FLEX_QUERY_ID_TC, wenn du sie nutzt.',
        start: 'Starte Docker Compose und fuehre danach einmal Bootstrap aus, um die Historie zu laden.',
      },
      requiredSections: 'Wichtige Activity-Flex-Sektionen',
      sections: 'Account Information, NAV in Base, Change in NAV, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Transfers.',
      healthTitle: 'Schnellchecks',
      setupStatusTitle: 'Echter Setup-Status',
      setupStatusDescription: 'Nicht geheime Backend-Sicht auf Flex, TC und lokale Datenbank.',
      dashboardCardsTitle: 'Dashboard-Boxen',
      dashboardCardsDescription: 'Waehle, welche Bloecke im Dashboard erscheinen. Wird nur in diesem Browser gespeichert.',
      resetRecommended: 'Empfohlene Ansicht',
      showAll: 'Alles anzeigen',
      activityQuery: 'Activity Flex Query',
      tcQuery: 'Trade Confirmation Query',
      latestFlexRun: 'Letzter Flex-Pull',
      latestData: 'Letzte Daten',
      baseCurrency: 'Basiswaehrung',
      tokenExpiry: 'Token-Ablauf',
      rowsImported: 'Importierte Zeilen',
      databaseRows: 'DB-Zeilen',
      stale: 'Veraltet',
      fresh: 'Aktuell',
      healthItems: {
        frontend: 'Frontend: http://localhost:5173',
        api: 'API: http://localhost:8001/health',
        swagger: 'Swagger: http://localhost:8001/docs',
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Konto-Uebersicht',
        description: 'NAV, Cushion, Buying Power und Excess Liquidity.',
      },
      tradingQuality: {
        title: 'Trading Quality',
        description: 'Profit Factor, Win Rate, Expectancy, Payoff, Drawdown und Sharpe auf realisiertem P&L.',
      },
      pnlAttribution: {
        title: 'P&L-Attribution',
        description: 'Aufschluesselung von realized, MTM, Dividenden, Zinsen, Gebuehren, Steuern und FX.',
      },
      tradeCalendar: {
        title: 'Kalender',
        description: 'Monatliche Heatmap des realisierten P&L pro Tag.',
      },
      activityTiles: {
        title: 'Monatsaktivitaet',
        description: 'Gehandelte Ticker, offener Credit und monatlicher Profit Factor.',
      },
      coreKpis: {
        title: 'Kontext-KPIs',
        description: 'TWR, Dividenden, PADI, Yield, unrealisierter P&L und Positionen.',
      },
      allTimeReturn: {
        title: 'Total Return',
        description: 'NAV minus kumulierte Nettoeinzahlungen seit Start.',
      },
      realizedMonthly: {
        title: 'Monatliche Realized-Matrix',
        description: 'FIFO-realisierter P&L nach Monat und Jahr.',
      },
      totalReturnMonthly: {
        title: 'Monatliche Total-Return-Matrix',
        description: 'Monatlicher Total Return bei ausreichender taeglicher NAV-Abdeckung.',
      },
    },
  },
} as const;

interface I18nValue {
  language: Language;
  locale: string;
  privacyMode: boolean;
  setLanguage: (language: Language) => void;
  setPrivacyMode: (enabled: boolean) => void;
  t: (key: string, params?: TranslationParams) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

function isLanguage(value: string | null | undefined): value is Language {
  return value === 'es' || value === 'en' || value === 'de';
}

function browserLanguage(): Language {
  if (typeof window === 'undefined') return 'es';
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (isLanguage(stored)) return stored;

  const preferred = window.navigator.languages?.[0] ?? window.navigator.language;
  const prefix = preferred?.slice(0, 2).toLowerCase();
  if (isLanguage(prefix)) return prefix;
  return 'es';
}

function browserPrivacyMode(): boolean {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(PRIVACY_MODE_STORAGE_KEY) === 'true';
}

function lookup(catalog: unknown, key: string): string {
  const value = key.split('.').reduce<unknown>((node, segment) => {
    if (node && typeof node === 'object' && segment in node) {
      return (node as Record<string, unknown>)[segment];
    }
    return undefined;
  }, catalog);

  return typeof value === 'string' ? value : key;
}

function interpolate(template: string, params?: TranslationParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ''));
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => browserLanguage());
  const [privacyMode, setPrivacyModeState] = useState<boolean>(() => browserPrivacyMode());

  const setLanguage = (nextLanguage: Language) => {
    setLanguageState(nextLanguage);
  };

  const setPrivacyMode = (enabled: boolean) => {
    setPrivacyModeState(enabled);
  };

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, language);
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    window.localStorage.setItem(PRIVACY_MODE_STORAGE_KEY, privacyMode ? 'true' : 'false');
  }, [privacyMode]);

  const value = useMemo<I18nValue>(() => {
    const catalog = messages[language];
    return {
      language,
      locale: LOCALES[language],
      privacyMode,
      setLanguage,
      setPrivacyMode,
      t: (key, params) => interpolate(lookup(catalog, key), params),
    };
  }, [language, privacyMode]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside I18nProvider');
  return value;
}
