import { useI18n } from '../lib/i18n';

interface Props {
  /** Endpoint path, shown so a failure can be traced without opening the console. */
  endpoint: string;
  onRetry: () => void;
}

/** Error branch for a panel whose query failed: names the endpoint and offers a retry. */
export function QueryError({ endpoint, onRetry }: Props) {
  const { t } = useI18n();
  return (
    <div className="trades-empty error-state" role="alert">
      {t('common.loadError')} <code>{endpoint}</code>{' '}
      <button type="button" className="warn-cta" onClick={onRetry}>{t('common.retry')}</button>
    </div>
  );
}
