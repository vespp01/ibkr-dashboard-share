interface Props {
  label: string;
  value: string;
  delta?: string;
  deltaKind?: 'pos' | 'neg' | 'warn' | 'neutral';
  primary?: boolean;
  loading?: boolean;
}

export function KPICard({ label, value, delta, deltaKind = 'neutral', primary, loading }: Props) {
  const deltaClass = deltaKind === 'neutral' ? '' : deltaKind;
  const cardClass = `kpi-card${primary ? ' primary' : ''}`;

  if (loading) {
    return (
      <div className={cardClass}>
        <span className="label">{label}</span>
        <span className="skeleton skeleton-value" />
        {delta !== undefined ? <span className="skeleton skeleton-delta" /> : null}
      </div>
    );
  }

  return (
    <div className={cardClass}>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {delta ? <span className={`delta ${deltaClass}`}>{delta}</span> : null}
    </div>
  );
}
