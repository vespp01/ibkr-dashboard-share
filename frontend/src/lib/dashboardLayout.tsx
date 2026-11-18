import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type DashboardCardId =
  | 'accountSummary'
  | 'riskStrip'
  | 'tradingQuality'
  | 'tradeCalendar'
  | 'activityTiles'
  | 'allTimeReturn'
  | 'realizedMonthly'
  | 'totalReturnMonthly';

export interface DashboardCardDefinition {
  id: DashboardCardId;
  titleKey: string;
  descriptionKey: string;
  recommended: boolean;
}

const STORAGE_KEY = 'ibkr-dashboard-card-visibility';

export const DASHBOARD_CARDS: DashboardCardDefinition[] = [
  {
    id: 'accountSummary',
    titleKey: 'dashboardCards.accountSummary.title',
    descriptionKey: 'dashboardCards.accountSummary.description',
    recommended: true,
  },
  {
    id: 'riskStrip',
    titleKey: 'dashboardCards.riskStrip.title',
    descriptionKey: 'dashboardCards.riskStrip.description',
    recommended: true,
  },
  {
    id: 'tradingQuality',
    titleKey: 'dashboardCards.tradingQuality.title',
    descriptionKey: 'dashboardCards.tradingQuality.description',
    recommended: true,
  },
  {
    id: 'tradeCalendar',
    titleKey: 'dashboardCards.tradeCalendar.title',
    descriptionKey: 'dashboardCards.tradeCalendar.description',
    recommended: true,
  },
  {
    id: 'activityTiles',
    titleKey: 'dashboardCards.activityTiles.title',
    descriptionKey: 'dashboardCards.activityTiles.description',
    recommended: true,
  },
  {
    id: 'allTimeReturn',
    titleKey: 'dashboardCards.allTimeReturn.title',
    descriptionKey: 'dashboardCards.allTimeReturn.description',
    recommended: true,
  },
  {
    id: 'realizedMonthly',
    titleKey: 'dashboardCards.realizedMonthly.title',
    descriptionKey: 'dashboardCards.realizedMonthly.description',
    recommended: true,
  },
  {
    id: 'totalReturnMonthly',
    titleKey: 'dashboardCards.totalReturnMonthly.title',
    descriptionKey: 'dashboardCards.totalReturnMonthly.description',
    recommended: false,
  },
];

type VisibilityMap = Record<DashboardCardId, boolean>;

interface DashboardLayoutValue {
  visibility: VisibilityMap;
  isVisible: (id: DashboardCardId) => boolean;
  setCardVisible: (id: DashboardCardId, visible: boolean) => void;
  resetRecommended: () => void;
  showAll: () => void;
  hideOptional: () => void;
}

const DashboardLayoutContext = createContext<DashboardLayoutValue | null>(null);

function recommendedVisibility(): VisibilityMap {
  return Object.fromEntries(DASHBOARD_CARDS.map((card) => [card.id, card.recommended])) as VisibilityMap;
}

function allVisible(): VisibilityMap {
  return Object.fromEntries(DASHBOARD_CARDS.map((card) => [card.id, true])) as VisibilityMap;
}

function loadVisibility(): VisibilityMap {
  if (typeof window === 'undefined') return recommendedVisibility();
  const defaults = recommendedVisibility();
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return defaults;
  try {
    const parsed = JSON.parse(raw) as Partial<Record<DashboardCardId, boolean>>;
    return Object.fromEntries(
      DASHBOARD_CARDS.map((card) => [card.id, typeof parsed[card.id] === 'boolean' ? parsed[card.id] : defaults[card.id]]),
    ) as VisibilityMap;
  } catch {
    return defaults;
  }
}

export function DashboardLayoutProvider({ children }: { children: ReactNode }) {
  const [visibility, setVisibility] = useState<VisibilityMap>(() => loadVisibility());

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(visibility));
  }, [visibility]);

  const value = useMemo<DashboardLayoutValue>(() => ({
    visibility,
    isVisible: (id) => visibility[id] !== false,
    setCardVisible: (id, visible) => setVisibility((current) => ({ ...current, [id]: visible })),
    resetRecommended: () => setVisibility(recommendedVisibility()),
    showAll: () => setVisibility(allVisible()),
    hideOptional: () => setVisibility(recommendedVisibility()),
  }), [visibility]);

  return <DashboardLayoutContext.Provider value={value}>{children}</DashboardLayoutContext.Provider>;
}

export function useDashboardLayout(): DashboardLayoutValue {
  const value = useContext(DashboardLayoutContext);
  if (!value) throw new Error('useDashboardLayout must be used inside DashboardLayoutProvider');
  return value;
}
