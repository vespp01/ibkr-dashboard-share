# Metodología de cálculo — NAV, TWR, XIRR, benchmark y opciones

> Trazabilidad exigida por dos principios del proyecto (*sin métricas inventadas*;
> *honestidad en comparativas*). Cada cifra documentada aquí
> traza a un campo Flex de IBKR o a una fórmula transparente. Las referencias
> apuntan a módulo + función (las líneas exactas cambian; el código es la
> fuente de verdad si este doc diverge).

## Mapa rápido: cifra → endpoint → código → origen

| Cifra (frontend) | Endpoint | Código | Origen del dato |
|---|---|---|---|
| NAV diario | `/portfolio/overview`, serie de `/performance/twr` | `loaders/pipeline.py::_load_nav` | Flex `EquitySummaryByReportDateInBase` (campo `total`) |
| Aportes/retiradas (flujo externo) | `/performance/xirr`, `/portfolio/all-time-return`, columna de reconciliación del TWR | `analytics/flows.py::cin_flows` | Flex `ChangeInNAV`: `depositsWithdrawals + assetTransfers`, en la fecha CIN |
| TWR acumulado / anualizado | `/performance/twr` (`?summary=1` sin serie) | `analytics/twr.py::twr_series` | Encadenado del `twr` diario de IBKR (`ChangeInNAV`); divisas vía `fx_rates` |
| XIRR (money-weighted) | `/performance/xirr` | `analytics/xirr.py::xirr_summary` | pyxirr sobre flujos CIN + NAV terminal; `null` bajo 365 días |
| Serie S&P 500 | `/performance/benchmark` | `benchmarks/sp500.py::fetch_sp500_in_currency` | Yahoo Finance `^GSPC` (índice de **precio**) |
| Comparativa cartera vs S&P | `/performance/vs-benchmark` | `analytics/risk.py::vs_benchmark` | misma serie CIN + S&P emparejados en fechas comunes, rebasados a 100 |
| Sharpe/Sortino/MaxDD/vol/Calmar | `/performance/risk` | `analytics/risk.py::risk_metrics` | Flex `ChangeInNAV` (campo `twr` diario pre-calculado por IBKR) |
| Alpha/Beta | `/performance/alpha` | `analytics/risk.py::alpha_vs_benchmark` | regresión sobre retornos emparejados, una sola divisa |
| Concentración HHI del libro OPT | `/options/concentration` | `analytics/options.py::options_concentration` | Flex `OpenPositions` / `positions_eod` del último snapshot OPT |
| Liquidez (% del NAV en caja) | `/portfolio/margin` (`cash_pct_nlv`) | `analytics/margin.py::margin_summary` | Flex `EquitySummaryByReportDateInBase` (`cash` / `total`) |
| Caja por divisa (Cartera) | `/portfolio/margin` (`cash_by_currency`) | `analytics/margin.py::margin_summary` | Flex `CashReport` (`endingCash`) × `ConversionRates` (`fx_rates`) |

---

## 1. NAV diario (`nav_daily`)

**Fuente Flex**: sección `EquitySummaryByReportDateInBase` — un snapshot EOD por
día y cuenta, en divisa base (CHF). Parser: `flex/parser.py::_parse_nav`
(acepta también la variante legacy `EquitySummaryInBase` de configuraciones
antiguas). Loader: `loaders/pipeline.py::_load_nav`, upsert-replace con clave
`(snapshot_date, account_id)` — re-ingestas no duplican (idempotencia).

Campos persistidos (todos directos del XML, sin transformación):

- `total` → `nav_total`; `cash`, `stock`, `options`, `bonds`, `funds` → ídem.
- `dividendsReceivable` (fallback `dividendAccruals` si el primero falta o es
  vacío) → `dividends_receivable`.
- `accruedDividends` → `accrued_dividends` **sin fallback** al campo anterior:
  un `accruedDividends=""` produce NULL, nunca hereda el mismo valor en las dos
  columnas (eso duplicaría el accrual en cualquier waterfall posterior).
- `interestAccruals` (o legacy `interestReceivable`) → `interest_receivable`.

### Flujos externos (`analytics/flows.py`)

La **única** definición de flujo externo para TWR (reconciliación), XIRR,
retorno all-time y waterfall es la de Flex `ChangeInNAV`, en divisa base y en
la **fecha CIN** (la misma fecha en que IBKR registra el salto de NAV):

```
flujo_t = depositsWithdrawals_t + assetTransfers_t
```

- `assetTransfers` son aportes **en especie** (ACATS / FOP / traspasos internos
  de títulos). Suben el NAV sin ser rendimiento: tratarlos como beneficio
  inflaba el XIRR (13,5 % vs 7,5 %) y el retorno absoluto (23,7 % vs 13,6 %).
  El parser los guarda en `change_in_nav_daily.asset_transfers` (junto con
  `sales_tax`); datos antiguos: CLI `backfill-cin-columns` desde `raw_xml`.
- `cash_transactions` **no** se usa para flujos: se fecha por `settle_date`
  (desfasada 1 día respecto al NAV en 12 de 51 depósitos, lo que fabricaba
  ganancias/pérdidas de ±30 % en días sueltos) y no recoge las transferencias
  en especie. La suma `Σ CIN depositsWithdrawals / Σ cash D/W` cuadra a 1,000
  (control previo a `drop-csv-flow-rows`).
- `nav_daily.external_cashflow` (derivado de `Deposits/Withdrawals` por
  `settle_date`) queda como columna **heredada**: ningún cálculo la lee.
  `Internal Transfers` (tipo muerto, 0 filas) se eliminó.
- El loader ya no crea filas `nav_daily` "solo-cashflow" (`nav_total` NULL):
  solo refleja el flujo sobre filas NAV existentes. Los residuos antiguos se
  borran con `drop-navless-nav-rows` (p. ej. 2023-11-18).

---

## 2. TWR — IBKR `ChangeInNAV.twr` encadenado (`analytics/twr.py`)

**Método canónico (decisión D2, 2026-10)**: el retorno diario en divisa base
(CHF) es el campo `twr` de la fila `ChangeInNAV` de ese día — IBKR lo calcula
con flujos aislados — y el TWR del periodo es su encadenado:

```
TWR = ∏(1 + twr_t / 100) − 1
```

La serie es **densa** (una fila por día hábil desde 2023-11-20, 751 filas;
cobertura de calendario ≈ 0,7 por fines de semana). No existe tramo "disperso":
las notas antiguas sobre NAV anual pre-2026 eran falsas.

- **Ventanas acotadas**: la fila fechada *t* es el retorno **del día t**. Una
  ventana `[start, end]` encadena las filas dentro del rango; el día 1 entra
  (MTD incluye el día 1). `base_date` es la última fila CIN estrictamente
  anterior a `start` (sin ella: el día previo a la primera fila) y es el ancla
  del benchmark.
- **Divisa de visualización X** (`analytics/fx.py::fx_lookup`): se convierten los
  retornos CHF con la serie `fx_rates` de IBKR (mismas fechas y valoración que
  el NAV; `fx` = unidades de X por 1 CHF):

  ```
  r_X,t = (1 + r_CHF,t) · fx_t / fx_{t−1} − 1
  ```

  Fuente de respaldo **marcada**: si `fx_rates` no tiene serie para la divisa
  se usa Frankfurter (BCE) con `fx_degraded: true` / `fx_source: "frankfurter"`;
  si tampoco hay tasa, las cifras convertidas son `null` (nunca 1.0 silencioso).
  Cada respuesta lleva `fx_source` (`identity`, `ibkr_fx_rates`, `frankfurter`).
- **Anualización**: `(∏(1 + r_t))^(365 / n_days) − 1` **solo** con
  `n_days ≥ 365` (GIPS prohíbe anualizar periodos < 1 año); si no,
  `annualised_pct = null` y `annualised_note` lo explica. `n_days` son días
  naturales desde `base_date` hasta la última fila.
- **Quality gate** (`_series_quality`): cobertura < 50 % de los días naturales
  del rango o un hueco > 10 días → `is_reliable: false`; `twr_pct` y
  `annualised_pct` se publican `null` y el valor crudo queda en `raw_twr_pct`.
- **Modified Dietz = solo reconciliación** (`reconciliation`, columna
  `dietz_cumulative_pct` en la serie CHF): `r = (fin − inicio − CF) / (inicio +
  0,5·CF)` con `CF = depositsWithdrawals + assetTransfers` en la fecha CIN,
  encadenado sobre las mismas filas. `diff_pp` frente al canónico es un control
  (≈ 0,09 pp en el histórico completo); el canónico manda.
- **`?summary=1`**: omite la serie diaria (< 2 KB en lugar de ≈ 100 KB); las
  cifras de cabecera son idénticas. Rentabilidad, Resumen y el pulso de
  Telegram usan el modo resumen sobre la misma serie.
- Test de regresión: `|app − ∏ CIN| < 0,1 pp` por año natural.

---

## 3. XIRR — retorno money-weighted (`analytics/xirr.py`)

Resuelto con la librería `pyxirr`.

- **Flujos**: `flujo_t = depositsWithdrawals + assetTransfers` por fecha CIN
  (`analytics/flows.py`), en divisa base — sin `fxRateToBase` NULL ni fechas
  de liquidación.
- **Convención de signos** (necesaria para que pyxirr converja): depósitos
  negativos (capital que entra a la inversión), retiradas positivas, NAV
  terminal positivo.
- **NAV terminal**: último `nav_daily.nav_total` no nulo ≤ `period.end`.
- **Síntesis de inversión inicial en sub-periodos**: si `period.start` está
  acotado, se inserta un flujo negativo igual al último NAV anterior a
  `period.start` fechado en `period.start`. Sin NAV previo, `note` lo declara.
- **No se anualiza bajo 365 días**: con ventana < 365 días `xirr_pct = null`,
  `annualised: false` y `window_days` informa de la longitud.
- `/portfolio/all-time-return` devuelve el
  mismo `xirr_pct` en lugar del antiguo `cagr_pct`, que suponía todos los
  aportes hechos en la fecha de inicio.

---

## 4. Benchmark S&P 500 (`benchmarks/sp500.py` + `/performance/benchmark`)

- **Fuente**: Yahoo Finance (API v8 chart, JSON gratuito sin auth), símbolo
  `^GSPC` — el índice S&P 500 **de precio**. **No es total return**: no
  reinvierte dividendos. Implicaciones de honestidad en §6.
- **Conversión USD→CHF** (o EUR/GBP/JPY): con la tabla `fx_rates` de IBKR (la
  misma que convierte la cartera; fechas y valoración del NAV), `close_base =
  close_USD × fx(USD→CHF) / fx(X→CHF)`; el FX se rellena hacia delante
  (as-of) en días sin tipo. Solo si `fx_rates` no tiene serie para la divisa se
  usa `USDCHF=X` de Yahoo, y las filas llevan `fx_source: "yahoo"` (las barras
  Yahoo vienen fechadas en UTC y en invierno se desplazan un día: β 0,69 vs 0,73).
- **Normalización**: índice = 100 en el primer día de la ventana;
  `return_pct` = retorno acumulado desde ese día. Directamente superponible a
  una serie TWR porcentual.
- **Ventana all-time**: la ruta usa el rango real de `nav_daily`
  (min/max `snapshot_date`) en vez de un genérico de 1 año, para que la línea
  del S&P cubra exactamente el mismo periodo que la cartera.
- **Persistencia/caché**: ver código en `ibkr_backend/benchmarks/` — la capa de
  caché/persistencia de la serie está evolucionando; este doc documenta fuente
  y semántica, no el mecanismo de fetch.
- Si Yahoo falla con la caché `benchmark_daily` vacía, `/performance/benchmark`
  devuelve 502 explícito; con caché poblada se sirve la serie persistida (que
  puede cubrir solo parte de la ventana). Las rutas de comparación señalizan la
  fiabilidad (`is_reliable`) — ver §8.7.

---

## 5. Comparativa honesta `/performance/vs-benchmark` (`analytics/risk.py::vs_benchmark`)

Modelo de honestidad:

1. **Una serie, unas fechas**: cartera (CIN, en la divisa pedida vía `fx_rates`)
   y S&P (convertido con la misma `fx_rates`) se emparejan en las fechas donde
   **ambos** tienen valor (`_pair_returns`). Los retornos se **componen** entre
   fechas comunes: un festivo de EE. UU. compone el día de la cartera en la
   siguiente fecha común y un festivo suizo compone el movimiento del S&P —
   ningún día de la cartera se descarta (751 días → 720 pares en el histórico).
2. **Rebase común**: ambas líneas valen 100 en `base_date` (último NAV antes
   del periodo); el S&P usa su último cierre ≤ `base_date` (se pide desde
   `base_date − 7 d`), de modo que el día 1 cuenta aunque el periodo empiece en
   festivo.
3. **Guard de fiabilidad**: calidad del TWR `is_reliable: false` → la
   comparación no se publica.
4. **Una cifra de retorno y un exceso**: `level_comparison` sale de los mismos
   pares que `risk_comparison` y los puntos del gráfico; su
   `portfolio_return_pct` coincide con `/performance/twr?summary=1`.
5. **Una divisa**: beta, alpha de Jensen, tracking error e IR se calculan con
   retornos de cartera convertidos a la divisa del benchmark.
   `/performance/risk` corre en CHF (`currency` en la respuesta).
6. Etiquetas: `benchmark_type: "price index (^GSPC), not total return"`,
   `fx_source` / `benchmark_fx_source`.

`/performance/alpha`: beta/Jensen solo con ≥ 30 pares; con 5–29, solo
diferencia simple. Con < 5 pares: `is_reliable: false`.

---

## 6. Métricas de riesgo `/performance/risk` (`analytics/risk.py::risk_metrics`)

- **Fuente**: `ChangeInNavDaily.twr` (la misma serie que el TWR canónico).
- **Anualización en tiempo de calendario**: observaciones/año = `n / (días
  naturales / 365)` (CIN tiene 261–262 filas/año, no 252). Volatilidad y
  semidesviación con `√obs/año`.
- **Menos de 365 días**: `annualised_return_pct` y Calmar son `null`
  (`annualised: false`); Sharpe/Sortino usan la media aritmética diaria
  (`sharpe_method: "arithmetic"`) en lugar de extrapolar. Desde 1 año se usa
  el retorno anualizado geométrico (`"geometric"`).
- **Tasa libre de riesgo**: tabla CHF por año **documentada y configurable**,
  por defecto **0 %** (sin descarga externa). Variable de entorno
  `RISK_FREE_CHF_PCT_BY_YEAR="2023:1.7,2024:1.2"`; el parámetro
  `risk_free_rate_pct` de la ruta la sustituye. La respuesta indica
  `risk_free_source`. Con 0 %, Sharpe = retorno/volatilidad.
- Max drawdown (con fechas pico/valle). Mínimo 10 días; por debajo, todo
  `null` con `is_reliable: false`.
- **Truncamiento**: `is_truncated` solo si la primera fila diaria empieza más
  de 5 días naturales después del inicio pedido (all-time = primer NAV con
  valor) o la ventana termina > 7 días después de la última fila.

---

## 7. Concentración HHI de opciones (`/options/concentration`)

La pestaña Opciones muestra la concentración del libro abierto de opciones por
subyacente con Herfindahl-Hirschman Index (HHI). Es una métrica descriptiva de
concentración, no una recomendación de riesgo ni de tamaño.

Fuente de datos:

- Último snapshot EOD de `positions_eod` con `asset_class = "OPT"`.
- Subyacente parseado con los campos OCC persistidos en `Instrument`
  (`option_underlying`, `option_expiry`, `option_right`, `option_strike`),
  con fallback al parser compartido (`analytics/options_parsing.py`) para
  datos antiguos o incompletos.
- Valor de posición en divisa base:
  `position_value × fxRateToBase`. Si `position_value` falta, fallback defensivo
  `mark_price × quantity × multiplier × fxRateToBase`.

Fórmula:

```
exposure_i = abs(position_value_base_i)
share_u = sum(exposure_i for underlying u) / sum(exposure_i for all OPT positions)
HHI = sum(share_u^2)
effective_names = 1 / HHI
```

Interpretación:

- `HHI = 1.00` significa que toda la exposición OPT marcada a mercado está en
  un solo subyacente.
- `effective_names` aproxima cuántos subyacentes igualmente ponderados darían
  la misma concentración. Ejemplo: `effective_names = 2` equivale a dos nombres
  de peso similar.

Limitaciones:

- El denominador es **valor de mercado absoluto**, no nocional de assignment
  (`strike × multiplier × contracts`) ni margen IBKR. Por eso una short put muy
  OTM puede pesar poco en HHI aunque su assignment notional sea grande.
- La métrica usa el último snapshot OPT disponible; no es intradía y no incluye
  precios live.
- HHI no distingue delta, gamma, vencimiento ni correlación entre subyacentes.
  Para riesgo operativo se debe leer junto con DTE buckets, assignment risk y
  las posiciones abiertas.

### Campos OCC persistidos en `Instrument`

Al cargar Flex, los contratos `OPT` se normalizan una vez y se guardan en la
tabla `instruments`:

- `option_underlying`
- `option_expiry`
- `option_right`
- `option_strike`

La fuente sigue siendo Flex: símbolo OSI (`Trade.symbol`, `OpenPosition.symbol`,
`OptionEAE.symbol`) y, si hace falta, la descripción humana de IBKR. Persistir
estos campos evita múltiples parsers por superficie y permite que Operaciones,
Opciones y futuras consultas SQL usen el mismo contrato normalizado. Las
columnas son nullable y se añaden con una migración idempotente en `init_db()`
para no requerir rebootstrap de la base SQLite viva.

---

## 8. Limitaciones honestas (léelas antes de sacar conclusiones)

1. **Benchmark sin dividendos vs cartera con dividendos.** `^GSPC` es índice de
   *precio*; el TWR de la cartera sí captura los dividendos cobrados (entran en
   el NAV). La comparativa **favorece sistemáticamente a la cartera** en
   aproximadamente el dividend yield del S&P 500 (del orden de 1–2 puntos
   porcentuales anuales). Un "gano al S&P por 1 punto" puede ser empate o
   pérdida contra el índice total-return.
2. **Una sola fuente de FX.** Cartera y benchmark convierten con `fx_rates`
   de IBKR. Solo si falta la serie de una divisa se recurre a Frankfurter
   (cartera) o Yahoo (benchmark), siempre con `fx_degraded` / `fx_source`.
3. **Sin tipo de cambio, sin cifra.** Si no hay tasa para una divisa, los
   retornos convertidos son `null`; nunca se sustituyen por 1.0.
4. **Histórico denso, no disperso.** `ChangeInNAV` cubre cada día hábil desde
   2023-11-20; los flags `quality` e `is_truncated` siguen declarando huecos
   reales si apareciesen.
5. **Benchmark sin dividendos y tasa libre de riesgo 0 %.** `^GSPC` es de
   precio (decisión D4: se mantiene) y Sharpe/IR no restan SARON salvo que se
   configure la tabla anual (§6).
6. **Dietz ≠ canónico.** La columna de reconciliación (Dietz sobre filas CIN)
   difiere del `twr` de IBKR en décimas de punto; manda el de IBKR.
7. **Dependencia de APIs externas sin SLA** (Yahoo, Frankfurter). Los fallos no
   se rellenan con datos inventados: con la caché `benchmark_daily` vacía,
   `/performance/benchmark` devuelve 502; con caché poblada se sirve lo
   persistido — que puede ser una serie **parcial** si el tramo no cacheado no
   se pudo fetchear (misma señalización de fiabilidad que una ventana corta de
   Yahoo). Las rutas de comparación declaran fiabilidad vía `is_reliable`.

---

## 9. Liquidez y caja por divisa (`/portfolio/margin`)

Añadido 2026-11-18. Dos cifras del mismo snapshot EOD; ninguna es una estimación Reg-T.

**Liquidez** (`cash_pct_nlv`) = `nav_daily.cash / nav_daily.nav_total × 100`, del último
snapshot NAV. `cash` es la caja del NAV de IBKR: ya resta la deuda de cada divisa. Por eso
la cifra puede ser negativa si la deuda supera a la caja.

**Caja por divisa** (`cash_by_currency`): una fila por divisa del `CashReport` del mismo día.

- `ending_cash`: saldo en la divisa (`endingCash`). Negativo = deuda de margen (`is_debt`).
- `ending_cash_base`: `ending_cash × tasa`, con la tasa de `fx_rates` (Flex `ConversionRates`)
  vigente el día del snapshot, vía `FxTable`. Sin tasa de IBKR el valor es `null`; nunca 1,0
  ni una fuente externa.
- `pct_of_nav`: `ending_cash_base / nav_total × 100`, la misma base que el `% NAV` de las
  posiciones.

La suma de `ending_cash_base` coincide con `nav_daily.cash` salvo redondeo.

Dónde se ve:

| Superficie | Qué muestra |
|---|---|
| Resumen, franja de riesgo | chip «Liquidez» (`cash_pct_nlv`) |
| Margen | KPI «Liquidez (cash / NLV)» con el importe de caja |
| Cartera, «Posiciones actuales» | filas `CASH <divisa>` al final: saldo en la divisa, valor en base, `% NAV` |
| Telegram, «Uso de margen» (semanal) | línea «Liquidez: X% del patrimonio en caja (N <base> netos)» |

