import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { PRIVACY_MODE_STORAGE_KEY, setFormatLocale, setPrivacyModeFlag } from './format';

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
      loadError: 'No se pudo cargar',
      retry: 'Reintentar',
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
      mtd: 'MTD',
      qtd: 'QTD',
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
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Cartera',
        fiscal: 'Fiscal',
        operaciones: 'Operaciones',
        opciones: 'Opciones',
        dividendos: 'Dividendos',
        rentabilidad: 'Rentabilidad',
        comisiones: 'Comisiones',
        margen: 'Margen',
        integraciones: 'Integraciones',
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
      fiscal: {
        title: 'Fiscal',
        subtitle: 'Escenarios fiscales españoles auditables, siempre sujetos a revisión humana.',
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
      integraciones: {
        title: 'Integraciones',
        subtitle: 'Estado y control de las conexiones con tus proyectos, el broker y servicios externos.',
      },
      settings: {
        title: 'Settings',
        subtitle: 'Idioma, privacidad local y checklist de conexión a IBKR.',
      },
    },
    dashboard: {
      staleWarning: 'Datos sin actualizar hace más de 30 días. Último dato: hace {days} días. Ejecuta un pull de Flex para refrescar.',
      staleData: 'Los datos no avanzan: el último dato es del {date} ({days} días hábiles). Los pulls pueden seguir saliendo bien; revisa el periodo de la query Activity en el Client Portal.',
      stalePull: 'No hay un pull de Flex correcto desde hace {hours} h. Revisa el token o el scheduler.',
      pageErrorTitle: 'Esta página no se pudo mostrar',
      pageErrorBody: 'Un dato inesperado rompió el render. El resto del dashboard sigue funcionando.',
      retry: 'Reintentar',
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
        activity: 'Activity Flex Query: nombre dashboard_full_daily, formato XML, periodo Last 30 Calendar Days y todas las secciones/columnas requeridas.',
        tc: 'Trade Confirmation Flex Query: nombre dashboard_tc_daily, formato XML y todos los campos de Trade Confirms. Rellena IB_FLEX_QUERY_ID_TC si la usas.',
        start: 'Ejecuta Docker Compose, luego el bootstrap una vez para cargar el histórico inicial.',
      },
      requiredSections: 'Secciones clave Activity Flex',
      sections: 'Account Information, NAV in Base, Change in NAV, MTD/YTD Performance Summary, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, Securities Info, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Option Exercises, Assignments, Expirations, Transfers.',
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
      notifications: {
        title: 'Notificaciones de Telegram',
        description: 'Configura qué mensajes envía el bot, su contenido y su frecuencia. Se aplica al instante.',
        loading: 'Cargando configuración…',
        telegramOn: 'Telegram conectado',
        telegramOff: 'Telegram sin configurar',
        telegramHint: 'Define TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env para que se envíen los mensajes.',
        on: 'Sí',
        off: 'No',
        frequency: 'Frecuencia',
        weekday: 'Día',
        dayOfMonth: 'Día del mes',
        time: 'Hora',
        sections: 'Contenido',
        alertBands: 'Avisar también al cruzar umbrales de riesgo',
        interval: 'Intervalo',
        requiresOverlayOff: 'Desactivado: requiere el live overlay (ENABLE_LIVE_OVERLAY=false)',
        weekdaysShort: 'L,M,X,J,V,S,D',
        save: 'Guardar',
        saving: 'Guardando…',
        saved: 'Guardado',
        saveError: 'Error al guardar',
        sendTest: 'Enviar prueba',
        testing: 'Enviando…',
        testSent: 'Prueba enviada',
        testFailed: 'Fallo al enviar',
        freq: { off: 'Off', daily: 'Diario', weekly: 'Semanal', biweekly: 'Quincenal', monthly: 'Mensual' },
        section: { dividends: 'Dividendos', operations: 'Operaciones', commissions: 'Comisiones' },
        msg: {
          daily_digest: { name: 'Resumen diario', help: 'Dividendos, operaciones y comisiones tras cada pull.' },
          performance_pulse: { name: 'Pulso de cartera', help: 'Rentabilidad MTD/YTD vs S&P 500 y APR de margen.' },
          token_expiry: { name: 'Caducidad token Flex', help: 'Aviso cuando el token Flex está por caducar.' },
          margin_eod: { name: 'Alerta de margen (EOD)', help: 'Estimación Reg-T de Flex al cierre; avisa al cruzar umbrales.' },
          margin_usage: { name: 'Uso de margen', help: 'Cuánto margen usas, a qué distancia del límite recomendado y qué % del patrimonio está en caja.' },
          staleness: { name: 'Pipeline sin actualizar', help: 'Avisa si el último pull es demasiado antiguo.' },
          price_alerts: { name: 'Alertas de precio', help: 'Cruces de umbral por regla (necesita TWS/live).' },
          margin_live: { name: 'Margen en tiempo real', help: 'Riesgo de margen en directo de IBKR (necesita TWS/live).' },
          margin_fx: { name: 'Coste de margen por divisa', help: 'Revisión de la divisa más barata para la deuda de margen.' },
        },
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Resumen de cuenta',
        description: 'NAV, TWR del periodo, retorno all-time e ingresos netos 12 m.',
      },
      riskStrip: {
        title: 'Franja de riesgo',
        description: 'Cushion, excess liquidity y leverage (estimación Reg-T) más liquidez (cash / NLV) con enlace a Margen.',
      },
      tradingQuality: {
        title: 'Calidad de trading',
        description: 'Profit factor, tasa de acierto, expectativa, payoff, Sharpe y drawdown sobre NAV. Plegado por defecto.',
      },
      tradeCalendar: {
        title: 'Calendario',
        description: 'Mapa mensual de P&L realizado por día.',
      },
      activityTiles: {
        title: 'Actividad del mes',
        description: 'Tickers operados, crédito abierto y profit factor mensual.',
      },
      allTimeReturn: {
        title: 'Retorno total',
        description: 'NAV menos aportes netos desde el inicio. Plegado por defecto.',
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
      loadError: 'Could not load',
      retry: 'Retry',
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
      mtd: 'MTD',
      qtd: 'QTD',
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
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Portfolio',
        fiscal: 'Tax',
        operaciones: 'Trades',
        opciones: 'Options',
        dividendos: 'Dividends',
        rentabilidad: 'Performance',
        comisiones: 'Fees',
        margen: 'Margin',
        integraciones: 'Integrations',
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
      fiscal: {
        title: 'Tax',
        subtitle: 'Auditable Spanish tax scenarios, always subject to human review.',
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
      integraciones: {
        title: 'Integrations',
        subtitle: 'Status and control of the links to your projects, the broker, and external services.',
      },
      settings: {
        title: 'Settings',
        subtitle: 'Language, local privacy, and IBKR connection checklist.',
      },
    },
    dashboard: {
      staleWarning: 'Data has not updated for more than 30 days. Last data point: {days} days ago. Run a Flex pull to refresh.',
      staleData: 'Data is not advancing: the latest data point is {date} ({days} business days). Pulls can still succeed; check the Activity query period in the Client Portal.',
      stalePull: 'No successful Flex pull for {hours} h. Check the token or the scheduler.',
      pageErrorTitle: 'This page could not be shown',
      pageErrorBody: 'An unexpected value broke the render. The rest of the dashboard still works.',
      retry: 'Retry',
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
        activity: 'Activity Flex Query: name dashboard_full_daily, XML format, Last 30 Calendar Days period, and all required sections/columns.',
        tc: 'Trade Confirmation Flex Query: name dashboard_tc_daily, XML format, and every Trade Confirms field. Fill IB_FLEX_QUERY_ID_TC if you use it.',
        start: 'Run Docker Compose, then bootstrap once to load the initial history.',
      },
      requiredSections: 'Key Activity Flex sections',
      sections: 'Account Information, NAV in Base, Change in NAV, MTD/YTD Performance Summary, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, Securities Info, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Option Exercises, Assignments, Expirations, Transfers.',
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
      notifications: {
        title: 'Telegram notifications',
        description: 'Choose which messages the bot sends, their content and frequency. Applied instantly.',
        loading: 'Loading configuration…',
        telegramOn: 'Telegram connected',
        telegramOff: 'Telegram not configured',
        telegramHint: 'Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env so messages are delivered.',
        on: 'On',
        off: 'Off',
        frequency: 'Frequency',
        weekday: 'Day',
        dayOfMonth: 'Day of month',
        time: 'Time',
        sections: 'Content',
        alertBands: 'Also alert on risk-threshold crossings',
        interval: 'Interval',
        requiresOverlayOff: 'Disabled: needs the live overlay (ENABLE_LIVE_OVERLAY=false)',
        weekdaysShort: 'Mo,Tu,We,Th,Fr,Sa,Su',
        save: 'Save',
        saving: 'Saving…',
        saved: 'Saved',
        saveError: 'Save failed',
        sendTest: 'Send test',
        testing: 'Sending…',
        testSent: 'Test sent',
        testFailed: 'Send failed',
        freq: { off: 'Off', daily: 'Daily', weekly: 'Weekly', biweekly: 'Biweekly', monthly: 'Monthly' },
        section: { dividends: 'Dividends', operations: 'Operations', commissions: 'Commissions' },
        msg: {
          daily_digest: { name: 'Daily digest', help: 'Dividends, operations and commissions after each pull.' },
          performance_pulse: { name: 'Portfolio pulse', help: 'MTD/YTD return vs S&P 500 and margin APR.' },
          token_expiry: { name: 'Flex token expiry', help: 'Warns when the Flex token is about to expire.' },
          margin_eod: { name: 'Margin alert (EOD)', help: 'Reg-T estimate from Flex at close; alerts on threshold crossings.' },
          margin_usage: { name: 'Margin usage', help: 'How much margin you use, how close to the recommended limit, and what % of NLV is cash.' },
          staleness: { name: 'Pipeline stale', help: 'Warns if the last pull is too old.' },
          price_alerts: { name: 'Price alerts', help: 'Per-rule threshold crossings (needs TWS/live).' },
          margin_live: { name: 'Live margin', help: 'Live IBKR margin risk (needs TWS/live).' },
          margin_fx: { name: 'Margin cost by currency', help: 'Reviews the cheapest currency for the margin debt.' },
        },
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Account summary',
        description: 'NAV, period TWR, all-time return and 12-month net income.',
      },
      riskStrip: {
        title: 'Risk strip',
        description: 'Cushion, excess liquidity and leverage (Reg-T estimate) plus liquidity (cash / NLV) with a link to Margin.',
      },
      tradingQuality: {
        title: 'Trading quality',
        description: 'Profit factor, win rate, expectancy, payoff, Sharpe and drawdown on NAV. Collapsed by default.',
      },
      tradeCalendar: {
        title: 'Calendar',
        description: 'Monthly realized-P&L heatmap by day.',
      },
      activityTiles: {
        title: 'Monthly activity',
        description: 'Traded tickers, open credit, and monthly profit factor.',
      },
      allTimeReturn: {
        title: 'All-time return',
        description: 'NAV minus net deposits since inception. Collapsed by default.',
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
      loadError: 'Konnte nicht geladen werden:',
      retry: 'Erneut versuchen',
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
      mtd: 'MTD',
      qtd: 'QTD',
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
      },
      nav: {
        resumen: 'Dashboard',
        cartera: 'Portfolio',
        fiscal: 'Steuern',
        operaciones: 'Trades',
        opciones: 'Optionen',
        dividendos: 'Dividenden',
        rentabilidad: 'Performance',
        comisiones: 'Gebuehren',
        margen: 'Margin',
        integraciones: 'Integrationen',
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
      fiscal: {
        title: 'Steuern',
        subtitle: 'Pruefbare spanische Steuerszenarien, immer mit menschlicher Kontrolle.',
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
      integraciones: {
        title: 'Integrationen',
        subtitle: 'Status und Steuerung der Verbindungen zu deinen Projekten, dem Broker und externen Diensten.',
      },
      settings: {
        title: 'Einstellungen',
        subtitle: 'Sprache, lokale Privatsphaere und IBKR-Verbindungscheckliste.',
      },
    },
    dashboard: {
      staleWarning: 'Die Daten wurden seit mehr als 30 Tagen nicht aktualisiert. Letzter Datenpunkt: vor {days} Tagen. Fuehre einen Flex-Pull aus.',
      staleData: 'Die Daten laufen nicht weiter: letzter Datenpunkt {date} ({days} Geschaeftstage). Pulls koennen trotzdem erfolgreich sein; pruefe den Zeitraum der Activity-Query im Client Portal.',
      stalePull: 'Seit {hours} h kein erfolgreicher Flex-Pull. Token oder Scheduler pruefen.',
      pageErrorTitle: 'Diese Seite konnte nicht angezeigt werden',
      pageErrorBody: 'Ein unerwarteter Wert hat das Rendern abgebrochen. Der Rest des Dashboards funktioniert weiter.',
      retry: 'Erneut versuchen',
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
        activity: 'Activity Flex Query: Name dashboard_full_daily, XML-Format, Zeitraum Last 30 Calendar Days und alle erforderlichen Sektionen/Spalten.',
        tc: 'Trade Confirmation Flex Query: Name dashboard_tc_daily, XML-Format und alle Felder in Trade Confirms. Setze IB_FLEX_QUERY_ID_TC, wenn du sie nutzt.',
        start: 'Starte Docker Compose und fuehre danach einmal Bootstrap aus, um die Historie zu laden.',
      },
      requiredSections: 'Wichtige Activity-Flex-Sektionen',
      sections: 'Account Information, NAV in Base, Change in NAV, MTD/YTD Performance Summary, Cash Report, Open Positions, Trades, Cash Transactions, Dividend Accruals, Interest Accruals, Conversion Rates, Securities Info, FIFO/MTM Performance, Transaction Taxes, Corporate Actions, Option Exercises, Assignments, Expirations, Transfers.',
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
      notifications: {
        title: 'Telegram-Benachrichtigungen',
        description: 'Wähle, welche Nachrichten der Bot sendet, Inhalt und Häufigkeit. Sofort wirksam.',
        loading: 'Konfiguration wird geladen…',
        telegramOn: 'Telegram verbunden',
        telegramOff: 'Telegram nicht konfiguriert',
        telegramHint: 'Setze TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID in .env, damit Nachrichten gesendet werden.',
        on: 'An',
        off: 'Aus',
        frequency: 'Häufigkeit',
        weekday: 'Tag',
        dayOfMonth: 'Tag des Monats',
        time: 'Uhrzeit',
        sections: 'Inhalt',
        alertBands: 'Auch bei Überschreiten von Risikoschwellen warnen',
        interval: 'Intervall',
        requiresOverlayOff: 'Deaktiviert: benötigt das Live-Overlay (ENABLE_LIVE_OVERLAY=false)',
        weekdaysShort: 'Mo,Di,Mi,Do,Fr,Sa,So',
        save: 'Speichern',
        saving: 'Speichern…',
        saved: 'Gespeichert',
        saveError: 'Speichern fehlgeschlagen',
        sendTest: 'Test senden',
        testing: 'Senden…',
        testSent: 'Test gesendet',
        testFailed: 'Senden fehlgeschlagen',
        freq: { off: 'Aus', daily: 'Täglich', weekly: 'Wöchentlich', biweekly: 'Zweiwöchentlich', monthly: 'Monatlich' },
        section: { dividends: 'Dividenden', operations: 'Transaktionen', commissions: 'Gebühren' },
        msg: {
          daily_digest: { name: 'Tägliche Übersicht', help: 'Dividenden, Transaktionen und Gebühren nach jedem Pull.' },
          performance_pulse: { name: 'Portfolio-Puls', help: 'MTD/YTD-Rendite vs. S&P 500 und Margin-APR.' },
          token_expiry: { name: 'Flex-Token-Ablauf', help: 'Warnung, wenn der Flex-Token bald abläuft.' },
          margin_eod: { name: 'Margin-Warnung (EOD)', help: 'Reg-T-Schätzung aus Flex zum Handelsschluss; warnt bei Schwellenübertritt.' },
          margin_usage: { name: 'Margin-Auslastung', help: 'Wie viel Margin du nutzt, wie nah am empfohlenen Limit und wie viel % des NLV Cash ist.' },
          staleness: { name: 'Pipeline veraltet', help: 'Warnt, wenn der letzte Pull zu alt ist.' },
          price_alerts: { name: 'Kursalarme', help: 'Schwellenübertritte je Regel (benötigt TWS/Live).' },
          margin_live: { name: 'Margin in Echtzeit', help: 'Live-Margin-Risiko von IBKR (benötigt TWS/Live).' },
          margin_fx: { name: 'Margin-Kosten je Währung', help: 'Prüfung der günstigsten Währung für die Margin-Schuld.' },
        },
      },
    },
    dashboardCards: {
      accountSummary: {
        title: 'Kontouebersicht',
        description: 'NAV, Zeitraum-TWR, Rendite seit Start und Nettoertrag 12 M.',
      },
      riskStrip: {
        title: 'Risikoleiste',
        description: 'Cushion, Excess Liquidity und Leverage (Reg-T-Schaetzung) plus Liquiditaet (Cash / NLV) mit Link zu Margin.',
      },
      tradingQuality: {
        title: 'Trading-Qualitaet',
        description: 'Profit Factor, Trefferquote, Erwartungswert, Payoff, Sharpe und Drawdown auf NAV. Standardmaessig eingeklappt.',
      },
      tradeCalendar: {
        title: 'Kalender',
        description: 'Monatliche Heatmap des realisierten P&L pro Tag.',
      },
      activityTiles: {
        title: 'Monatsaktivitaet',
        description: 'Gehandelte Ticker, offener Credit und monatlicher Profit Factor.',
      },
      allTimeReturn: {
        title: 'Rendite seit Start',
        description: 'NAV abzueglich Nettoeinzahlungen seit Start. Standardmaessig eingeklappt.',
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

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked storage: the setting just does not persist.
  }
}

function browserLanguage(): Language {
  if (typeof window === 'undefined') return 'es';
  const stored = readStorage(STORAGE_KEY);
  if (isLanguage(stored)) return stored;

  const preferred = window.navigator.languages?.[0] ?? window.navigator.language;
  const prefix = preferred?.slice(0, 2).toLowerCase();
  if (isLanguage(prefix)) return prefix;
  return 'es';
}

function browserPrivacyMode(): boolean {
  if (typeof window === 'undefined') return false;
  return readStorage(PRIVACY_MODE_STORAGE_KEY) === 'true';
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
    writeStorage(STORAGE_KEY, language);
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    writeStorage(PRIVACY_MODE_STORAGE_KEY, privacyMode ? 'true' : 'false');
  }, [privacyMode]);

  const value = useMemo<I18nValue>(() => {
    // Sync the formatter flag before children render with the new value.
    setPrivacyModeFlag(privacyMode);
    setFormatLocale(LOCALES[language]);
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
