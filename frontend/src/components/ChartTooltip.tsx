import { useI18n } from '../lib/i18n';

interface TooltipPayload {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

interface Props {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string;
  formatter?: (value: number | string, name: string) => string;
}

export function ChartTooltip({ active, payload, label, formatter }: Props) {
  const { locale } = useI18n();
  if (!active || !payload || payload.length === 0) return null;

  return (
    <div className="chart-tooltip">
      {label ? <div className="chart-tooltip-label">{label}</div> : null}
      <div className="chart-tooltip-rows">
        {payload.map((item, idx) => {
          const value =
            item.value === undefined || item.value === null
              ? '--'
              : formatter
                ? formatter(item.value, String(item.name ?? ''))
                : typeof item.value === 'number'
                  ? item.value.toLocaleString(locale, { maximumFractionDigits: 2 })
                  : String(item.value);
          return (
            <div key={`${item.dataKey ?? idx}`} className="chart-tooltip-row">
              <span className="chart-tooltip-dot" style={{ background: item.color }} />
              <span className="chart-tooltip-name">{item.name}</span>
              <span className="chart-tooltip-value">{value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
