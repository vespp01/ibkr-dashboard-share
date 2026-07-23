# Metodología de cálculo — NAV, TWR, XIRR, benchmark y opciones

> Trazabilidad exigida por `VISION.md` (principio 1: *sin métricas inventadas*;
> principio 5: *honestidad en comparativas*). Cada cifra documentada aquí
> traza a un campo Flex de IBKR o a una fórmula transparente. Las referencias
> apuntan a módulo + función (las líneas exactas cambian; el código es la
> fuente de verdad si este doc diverge).

## Mapa rápido: cifra → endpoint → código → origen

| Cifra (frontend) | Endpoint | Código | Origen del dato |
|---|---|---|---|
| NAV diario | `/portfolio/overview`, serie de `/performance/twr` | `loaders/pipeline.py::_load_nav` | Flex `EquitySummaryByReportDateInBase` (campo `total`) |
| Aportes/retiradas (external cashflow) | dentro de la serie TWR | `loaders/pipeline.py::_external_cashflows_by_settle_date` | Flex `CashTransaction` tipo `Deposits/Withdrawals` e `Internal Transfers` |
| TWR acumulado / anualizado | `/performance/twr` | `analytics/twr.py::twr_series` | Modified Dietz diario encadenado sobre `nav_daily` |
| XIRR (money-weighted) | `/performance/xirr` | `analytics/xirr.py::xirr_summary` | pyxirr sobre cashflows + NAV terminal |
| Serie S&P 500 | `/performance/benchmark` | `benchmarks/sp500.py::fetch_sp500_in_currency` | Yahoo Finance `^GSPC` (índice de **precio**) |
| Comparativa cartera vs S&P | `/performance/vs-benchmark` | `analytics/risk.py::vs_benchmark` | TWR propio + benchmark muestreado en las mismas fechas |
| Sharpe/Sortino/MaxDD/vol/Calmar | `/performance/risk` | `analytics/risk.py::risk_metrics` | Flex `ChangeInNAV` (campo `twr` diario pre-calculado por IBKR) |
| Alpha/Beta | `/performance/alpha` | `analytics/risk.py::alpha_vs_benchmark` | regresión retornos diarios cartera vs benchmark |
| Concentración HHI del libro OPT | `/options/concentration` | `analytics/options.py::options_concentration` | Flex `OpenPositions` / `positions_eod` del último snapshot OPT |

---

## 1. NAV diario (`nav_daily`)

**Fuente Flex**: sección `EquitySummaryByReportDateInBase` — un snapshot EOD por
día y cuenta, en divisa base (CHF). Parser: `flex/parser.py::_parse_nav`
(acepta también la variante legacy `EquitySummaryInBase` de configuraciones
antiguas). Loader: `loaders/pipeline.py::_load_nav`, upsert-replace con clave
`(snapshot_date, account_id)` — re-ingestas no duplican (VISION principio 4).

Campos persistidos (todos directos del XML, sin transformación):

- `total` → `nav_total`; `cash`, `stock`, `options`, `bonds`, `funds` → ídem.
- `dividendsReceivable` (fallback `dividendAccruals` si el primero falta o es
  vacío) → `dividends_receivable`.
- `accruedDividends` → `accrued_dividends` **sin fallback** al campo anterior:
  un `accruedDividends=""` produce NULL, nunca hereda el mismo valor en las dos
  columnas (eso duplicaría el accrual en cualquier waterfall posterior).
- `interestAccruals` (o legacy `interestReceivable`) → `interest_receivable`.

### External cashflow (regla settle-date + FX)

`nav_daily.external_cashflow` se deriva de `CashTransaction` con
`type ∈ {"Deposits/Withdrawals", "Internal Transfers"}`. La derivación canónica
es **una sola función** — `_external_cashflows_by_settle_date` en
`loaders/pipeline.py` — compartida por las dos vías de escritura:

- `_refresh_external_cashflows`: por statement, en cada ingesta.
- `reconcile_external_cashflows`: re-deriva el histórico completo desde la
  tabla `cash_transactions` y pone a 0 los residuos (fechas que tuvieron flujo
  pero ya no tienen transacción que lo respalde).

Reglas de la derivación:

1. **El flujo se contabiliza en su `settle_date`**, no en `event_datetime`:
   el NAV de IBKR (`EquitySummaryByReportDateInBase`) registra el depósito a
   liquidación. Fechar por evento desalinea flujo y salto de NAV y produce
   retornos Dietz espurios (observado: −85 % vs +50 % real).
2. Filas sin `settle_date` o sin `fxRateToBase` se omiten (espejo del
   `SUM(amount * fx)` SQL, que descarta productos NULL).
3. Conversión a divisa base: `amount × fxRateToBase` — ambos campos Flex, sin
   tipo de cambio inventado.
4. Filas resumen de IBKR (`account_id == "-"`) se descartan.

Un depósito cuyo settle cae en un día **sin** snapshot NAV crea una fila
`nav_daily` "solo-cashflow" (`nav_total` NULL). Cómo la trata el TWR: §2.

---

## 2. TWR — Modified Dietz diario encadenado (`analytics/twr.py`)

Para cada día *t* con NAV de apertura, NAV de cierre y cashflow externo CF
(depósitos positivos, retiradas negativas):

```
r_t = (NAV_close − NAV_open − CF) / (NAV_open + 0.5 · CF)
TWR del periodo = ∏(1 + r_t) − 1
```

`NAV_open` es el `nav_total` del día anterior **con NAV**. La fórmula coincide
con el Time-Weighted Return de PortfolioAnalyst de IBKR.

- **Días sin NAV (filas solo-cashflow)**: el CF se acumula en `pending_cf` y se
  arrastra al siguiente día que sí tiene NAV. Así el salto de NAV causado por
  un depósito liquidado fuera de snapshot no se contabiliza como rendimiento
  (fix 2026-06-10; antes la fila fantasma rompía la cadena).
- **Warm-up skip**: las filas iniciales con NAV < `max(100, 0.5 % del NAV
  máximo del periodo)` se omiten (`warmup_skipped` en la respuesta). Un saldo
  promocional de apertura seguido del primer depósito real explota Modified
  Dietz con retornos de "miles de por ciento" sin sentido.
- **Anualización**: `(∏(1 + r_t))^(365 / n_days) − 1`, solo si `n_days > 0` y
  el acumulado es positivo.
- **Quality gate** (`_series_quality`): cobertura < 50 % de los días naturales
  del rango o un hueco > 10 días entre snapshots → `is_reliable: false`. En ese
  caso `twr_pct` y `annualised_pct` se publican como `null` y los valores crudos
  quedan en `raw_twr_pct` / `raw_annualised_pct` — la cifra dudosa nunca se
  presenta como fiable.

### Conversión de divisa (`analytics/fx.py`)

Si `display_currency` ≠ CHF (base de la cuenta), cada NAV y CF se convierte al
spot **del día del snapshot** antes de aplicar Dietz — esto aísla el efecto de
apreciación/depreciación del CHF del retorno.

- Fuente: Frankfurter.app (datos BCE, gratuito, sin auth). Fines de semana y
  festivos resuelven al día hábil BCE anterior.
- Cache en memoria **solo de tasas reales** (los históricos son inmutables, no
  caduca). Si la API falla, esa llamada degrada a 1.0 **sin cachear el fallo**:
  la siguiente llamada reintenta (fix B3 2026-06-10; antes un fallo transitorio
  fijaba 1.0 para esas fechas durante toda la vida del proceso, produciendo
  series mixtas convertidas/sin convertir).

---

## 3. XIRR — retorno money-weighted (`analytics/xirr.py`)

Resuelto con la librería `pyxirr`. Equivale al Money-Weighted Return de
PortfolioAnalyst.

- **Flujos**: `cash_transactions` con los mismos tipos que el TWR
  (`Deposits/Withdrawals`, `Internal Transfers`), en divisa base
  (`amount × fxRateToBase`; si falta `fxRateToBase` el flujo entra **sin
  convertir** — `coalesce(fx, 1)`), fechados por `settle_date` (fallback
  `event_datetime` si falta). Nótese que esta regla es más laxa que la del
  `external_cashflow` del TWR (§1), que omite esas filas — ver §8.5.
- **Convención de signos** (necesaria para que pyxirr converja): depósitos
  negativos (capital que entra a la inversión), retiradas positivas, NAV
  terminal positivo. Como `CashTransaction.amount` guarda depósitos en positivo
  (vista pasivo-del-broker), se niega el signo al construir la lista.
- **NAV terminal**: último `nav_daily.nav_total` no nulo ≤ `period.end` — las
  filas solo-cashflow se excluyen explícitamente (`nav_total IS NOT NULL`).
- **Síntesis de inversión inicial en sub-periodos**: si `period.start` está
  acotado, se inserta un flujo negativo igual al último NAV anterior a
  `period.start` fechado en `period.start`. Sin esto, pyxirr atribuye toda la
  riqueza acumulada al inicio del periodo a los pocos depósitos hechos dentro
  de él, produciendo tasas absurdas (+1.000.000 %). El XIRR all-time
  (`period.start` = None) no necesita la síntesis. Si no existe NAV anterior al
  inicio, la respuesta lo declara en `note` (resultado probablemente
  infraestimado) en vez de fingir precisión.

---

## 4. Benchmark S&P 500 (`benchmarks/sp500.py` + `/performance/benchmark`)

- **Fuente**: Yahoo Finance (API v8 chart, JSON gratuito sin auth), símbolo
  `^GSPC` — el índice S&P 500 **de precio**. **No es total return**: no
  reinvierte dividendos. Implicaciones de honestidad en §6.
- **Conversión USD→CHF** (o EUR/GBP/JPY): serie diaria `USDCHF=X` de Yahoo
  (semántica: CHF por 1 USD), `close_base = close_USD × fx`. En días en que la
  bolsa abre pero el mercado FX no publica, el FX se rellena hacia delante
  (forward-fill) con el último valor conocido.
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

Modelo de honestidad (VISION principio 5):

1. **Mismas fechas exactas**: el benchmark se muestrea solo en las fechas donde
   la cartera tiene punto TWR, con regla *as-of* (último cierre del benchmark ≤
   esa fecha). Donde el NAV es disperso (pre-2026 = snapshots anuales), AMBAS
   líneas son rectas entre los mismos puntos — nunca una curva suave de cartera
   contra un índice diario dentado.
2. **Rebase común**: ambas series se reescalan a 100 en la primera fecha común.
3. **Guard de fiabilidad**: si la calidad del TWR subyacente es `is_reliable:
   false` (cobertura NAV insuficiente, ver §2), la comparación de nivel **no se
   publica** — la respuesta lleva `is_reliable: false` con la razón y los datos
   de resolución, en vez de un número engañoso.
4. **Dos capas, cada una honesta sobre su ventana**:
   - `level_comparison`: retorno total cartera vs benchmark sobre la ventana
     común completa (ratio de extremos de las series rebasadas).
   - `risk_comparison`: beta, Jensen alpha, tracking error e information ratio,
     calculados **solo** sobre el segmento de resolución diaria
     (`ChangeInNavDaily`, ≥ 30 días alineados) — nunca sobre tramos dispersos.
5. **Flags de resolución** en la respuesta: `points`, `max_gap_days`,
   `is_sparse` (hueco máximo > 7 días), `daily_segment_from` (inicio del tramo
   denso fiable) y una nota legible para el usuario.

`/performance/alpha` aplica el mismo principio: beta/Jensen alpha solo con
≥ 30 días alineados. Con 5–29 días, beta y Jensen quedan `null` pero se reporta
la diferencia simple de retornos (`simple_alpha_pct`) con `is_reliable: true`.
Solo con < 5 días alineados la respuesta es `is_reliable: false` (sin cifras).

---

## 6. Métricas de riesgo `/performance/risk` (`analytics/risk.py::risk_metrics`)

- **Fuente**: `ChangeInNavDaily.twr` — el TWR diario que **IBKR pre-calcula**
  (sección Flex `ChangeInNAV`), no una derivación propia. Es la serie densa y
  limpia; en esta cuenta solo existe desde enero 2026.
- Sharpe, Sortino, volatilidad anualizada (√252), max drawdown (con fechas
  pico/valle), Calmar. Mínimo 10 días de muestra; por debajo, todo `null` con
  `is_reliable: false`.
- **Flags de truncamiento**: si la ventana pedida excede la cobertura de
  `ChangeInNAV` (p. ej. "all-time" desde 2023), la respuesta lleva
  `is_truncated: true` + la ventana realmente usada — sin truncamiento
  silencioso.
- `risk_free_rate_pct` es parámetro explícito (default 0 → Sharpe se reduce a
  retorno/volatilidad); referencia CHF: SARON 1M ≈ 0,95 % a inicios de 2026.

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
2. **Dos fuentes de FX distintas.** La cartera convierte con Frankfurter (BCE);
   el benchmark con las series `=X` de Yahoo. Fixings distintos → pequeñas
   discrepancias diarias posibles entre ambas líneas, no atribuibles a
   rendimiento.
3. **Degradación FX a 1.0.** Si Frankfurter no responde, esa respuesta concreta
   mezcla valores convertidos y sin convertir (no se cachea el fallo; el
   siguiente request reintenta). Afecta solo a vistas con `currency` ≠ CHF.
4. **NAV histórico disperso pre-2026.** Snapshots aproximadamente anuales hasta
   2025: los tramos antiguos del TWR son rectas entre pocos puntos y las
   métricas de riesgo no existen ahí. Los flags `quality`, `is_sparse` e
   `is_truncated` lo declaran en cada respuesta.
5. **Cashflows con datos incompletos — TWR y XIRR difieren.** Transacciones sin
   `settle_date` o sin `fxRateToBase` no entran en `external_cashflow` (regla
   deliberada, §1) y por tanto tampoco en el TWR. El XIRR en cambio **sí las
   incluye**: usa `event_datetime` como fallback de fecha y `coalesce(fx, 1)`
   (el flujo entra sin convertir). Al reconciliar TWR contra XIRR, ese descuadre
   es esperado. La reconciliación (`reconcile_external_cashflows`) es la red de
   seguridad si IBKR dejara de poblar esos campos.
6. **Warm-up excluido.** El TWR no cubre el periodo inicial de saldo
   promocional (§2); `warmup_skipped` indica cuántas filas se saltaron.
7. **Dependencia de APIs externas sin SLA** (Yahoo, Frankfurter). Los fallos no
   se rellenan con datos inventados: con la caché `benchmark_daily` vacía,
   `/performance/benchmark` devuelve 502; con caché poblada se sirve lo
   persistido — que puede ser una serie **parcial** si el tramo no cacheado no
   se pudo fetchear (misma señalización de fiabilidad que una ventana corta de
   Yahoo). Las rutas de comparación declaran fiabilidad vía `is_reliable`.
