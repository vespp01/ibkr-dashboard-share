import { useCountUp } from '../../lib/motion';
import { useI18n } from '../../lib/i18n';

interface Props {
  label: string;
  /** Numeric value that counts up. Undefined renders the placeholder. */
  value: number | undefined;
  format: (value: number | undefined) => string;
  delta?: string;
  deltaKind?: 'pos' | 'neg' | 'warn' | 'neutral';
  hint?: string;
  loading?: boolean;
  primary?: boolean;
}

/** KPI card whose number counts up on first resolve and on every value change. */
export function HeroCard({ label, value, format, delta, deltaKind = 'neutral', hint, loading, primary }: Props) {
  const { privacyMode } = useI18n();
  const shown = useCountUp(value, !privacyMode);
  const cardClass = `kpi-card dv-hero${primary ? ' primary' : ''}`;

  if (loading) {
    return (
      <div className={cardClass} aria-busy="true">
        <span className="label">{label}</span>
        <span className="skeleton skeleton-value" />
        <span className="skeleton skeleton-delta" />
      </div>
    );
  }

  return (
    <div className={cardClass}>
      <span className="label">{label}</span>
      <span className="value">{format(typeof shown === 'number' ? shown : value)}</span>
      {delta ? <span className={`delta ${deltaKind === 'neutral' ? '' : deltaKind}`}>{delta}</span> : null}
      {hint ? <span className="dv-hero-hint">{hint}</span> : null}
    </div>
  );
}
