# Alertas de margen y revisión de divisa

## Objetivo

El backend avisa por Telegram cuando el riesgo de margen empeora. También envía una revisión
semanal si existen saldos de caja negativos. Ningún flujo ejecuta órdenes ni conversiones.

## Alerta de seguridad

La fuente live es el resumen de cuenta de TWS o IB Gateway mediante `ib_async`. La respuesta y el
mensaje eliminan `account_id`. Se evalúan:

- `Cushion` y `ExcessLiquidity` actuales;
- `MaintMarginReq` y `EquityWithLoanValue`;
- `LookAheadExcessLiquidity` y el cushion look-ahead derivado;
- el peor estado entre los valores actuales y look-ahead.

Bandas por defecto:

| Estado | Cushion | Naturaleza |
|---|---:|---|
| vigilancia | ≤ 30 % | política local configurable |
| alto | ≤ 20 % | política local configurable |
| crítico | ≤ 10 % | banda oficial de advertencia de IBKR |
| incumplimiento | exceso de liquidez ≤ 0 | riesgo inmediato de liquidación |

El job consulta cada tres minutos. Envía solo al entrar en una banda peor. Envía un único mensaje
de recuperación al volver a `safe`. Si Telegram falla, no consume la transición y la reintenta.

Tras el pull diario, el mismo clasificador evalúa el cálculo Reg-T conservador. Ese mensaje siempre
dice `estimación Flex EOD`; nunca se presenta como margen live o Portfolio Margin real.

El mensaje periódico «Uso de margen» (`margin_usage`, semanal los lunes por defecto) incluye una línea
de liquidez: el % del patrimonio en caja (`nav_daily.cash / nav_total`) y el importe neto en la divisa
base. La cifra sale siempre del snapshot Flex EOD; con la fuente live, la línea añade `Flex EOD <fecha>`.
Cálculo: [`docs/methodology.md` §9](../methodology.md).

## Revisión de la divisa de financiación

El job semanal se activa solo cuando el último Cash Report contiene deuda y el snapshot no tiene más
de cuatro días. Compara CHF, EUR, GBP, USD y JPY por defecto.

1. Descarga la tabla oficial de tipos de préstamo IBKR Pro.
2. Aplica sus tramos progresivos al principal equivalente en cada divisa.
3. Descarga tipos de referencia diarios del BCE.
4. Convierte cada serie a CHF por unidad de divisa.
5. Reproduce el cambio de los últimos 365 días y lo combina con el tipo actual.

La salida separa dos ganadores:

- menor coste de interés IBKR actual;
- menor coste en el replay histórico de interés más FX.

Solo muestra un `robust_candidate` si ambos coinciden. En los demás casos muestra `sin ganador
claro`. El replay describe el pasado. No predice el cambio futuro y no incluye el spread real de una
conversión, slippage, impuestos ni cambios intradía del tipo de IBKR.

En la siguiente iteración, el replay seguirá como contexto, pero no participará en una recomendación.
Cualquier candidato deberá superar propósito de la deuda, natural hedge, breakeven FX, coste
cubierto y escenarios adversos. Si no existe ventaja robusta, se mantiene `no_clear_winner`.

## Fallo cerrado y privacidad

No se envía la revisión si falta una divisa, si el HTML de IBKR cambia, si el BCE está obsoleto o si
el snapshot Flex es antiguo. Los mensajes pueden incluir importes de deuda, pero nunca credenciales
ni identificadores de cuenta. Los endpoints son read-only:

- `GET /portfolio/margin/live`
- `GET /portfolio/margin/financing`

## Operación

```bash
# Vista previa sin Telegram ni cambio de watermark
python -m ibkr_backend.cli margin-alert --source live --test
python -m ibkr_backend.cli margin-alert --source eod --test

# Análisis de divisa sin enviar
python -m ibkr_backend.cli margin-financing

# Envío manual; --force ignora el watermark semanal
python -m ibkr_backend.cli margin-financing --send --force
```

Para live, TWS o IB Gateway debe aceptar API de solo lectura desde el backend. No publiques los
puertos 7496/7497/4001/4002. Entre equipos usa un túnel SSH o una red privada con reglas limitadas al
host y puerto necesarios.

## Fuentes oficiales

- [IBKR: account summary tags](https://interactivebrokers.github.io/tws-api/account_summary.html)
- [IBKR: margin warning](https://www.interactivebrokers.com/campus/glossary-terms/margin-warning/)
- [IBKR: margin rates](https://www.interactivebrokers.com/en/trading/margin-rates.php)
- [ECB: reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
