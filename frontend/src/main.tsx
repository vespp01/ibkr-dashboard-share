import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from './App';
import { DashboardLayoutProvider } from './lib/dashboardLayout';
import { I18nProvider } from './lib/i18n';
import './styles/global.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('Missing #root in index.html');

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <DashboardLayoutProvider>
          <App />
        </DashboardLayoutProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
