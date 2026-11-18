import { useMemo } from 'react';
import type { UpcomingDividends } from '../../lib/api';
import { formatCurrency, parseLocalDate } from '../../lib/format';
import { useI18n } from '../../lib/i18n';
import { fill, type DividendsCopy } from './copy';

const WINDOW_DAYS = 60;
const DAY_MS = 86_400_000;

interface Props {
  copy: DividendsCopy;
  data: UpcomingDividends | undefined;
  loading: boolean;
  error: boolean;
}

export function UpcomingPayments({ copy, data, loading, error }: Props) {
  const { locale } = useI18n();
  const dayFmt = useMemo(() => new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }), [locale]);

  const model = useMemo(() => {
    if (!data?.as_of) return null;
    // Count down from today, not from the data date: with stale data a
    // payment "in 5 days" from as_of may be due in 2, or already paid.
    const now = new Date();
    const anchor = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const events = data.events.map((event) => {
      const days = Math.round((parseLocalDate(event.pay_date).getTime() - anchor.getTime()) / DAY_MS);
      return { event, days };
    });
    return {
      anchor,
      inWindow: events.filter((e) => e.days >= 0 && e.days <= WINDOW_DAYS),
      later: events.filter((e) => e.days > WINDOW_DAYS).length,
      pastDue: events.filter((e) => e.days < 0).length,
    };
  }, [data]);

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton short" aria-busy="true" />;
  } else if (error || !data) {
    body = <div className="trades-empty error-state">{copy.upcomingError}</div>;
  } else if (!model || model.inWindow.length === 0) {
    body = <div className="trades-empty">{copy.upcomingEmpty}</div>;
  } else {
    body = (
      <>
        <div className="dv-timeline" aria-hidden="true">
          <span className="dv-timeline-track" />
          {model.inWindow.map(({ event, days }, i) => (
            <span
              key={`${event.conid}-${event.pay_date}`}
              className="dv-timeline-dot"
              style={{ left: `${(days / WINDOW_DAYS) * 100}%`, animationDelay: `${i * 60}ms` }}
              title={`${event.symbol ?? ''} · ${dayFmt.format(parseLocalDate(event.pay_date))}`}
            />
          ))}
        </div>
        <ul className="dv-upcoming-list">
          {model.inWindow.map(({ event, days }, i) => {
            const payDate = parseLocalDate(event.pay_date);
            const foreign = event.currency !== data.base_currency;
            return (
              <li key={`${event.conid}-${event.pay_date}`} style={{ '--i': i } as React.CSSProperties}>
                <div className="dv-up-date">
                  <strong>{dayFmt.format(payDate)}</strong>
                  <span>{days === 0 ? copy.today : fill(copy.inDays, { n: days })}</span>
                </div>
                <div className="dv-up-main">
                  <strong>{event.symbol ?? '--'}</strong>
                  <span>{event.ex_date ? `${copy.ex} ${dayFmt.format(parseLocalDate(event.ex_date))}` : ''}</span>
                </div>
                <div className="dv-up-amount num">
                  <strong>{event.net_base !== null ? formatCurrency(event.net_base, data.base_currency, locale) : copy.noFx}</strong>
                  {foreign ? <span>{formatCurrency(event.net_amount, event.currency, locale)}</span> : null}
                </div>
              </li>
            );
          })}
        </ul>
        {model.later > 0 ? <p className="dv-note">{fill(copy.upcomingLater, { n: model.later })}</p> : null}
      </>
    );
  }

  return (
    <div className="table-panel dv-panel">
      <div className="panel-title">{copy.upcomingTitle}</div>
      {body}
      {data?.as_of ? <p className="dv-note">{fill(copy.upcomingEstimate, { date: data.as_of })}</p> : null}
      {data?.as_of && model && model.pastDue > 0 ? <p className="dv-note">{fill(copy.upcomingPastDue, { n: model.pastDue, date: data.as_of })}</p> : null}
      {data && data.stale_open_events > 0 ? <p className="dv-note">{fill(copy.upcomingStale, { n: data.stale_open_events })}</p> : null}
    </div>
  );
}
