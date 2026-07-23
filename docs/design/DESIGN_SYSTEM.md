# IBKR Dashboard — Design System

A cohesive design system for the **Dashboard Financiero IBKR**, a personal web app
that reads Interactive Brokers data via the FastAPI backend (`ibkr_backend/`) and
renders an interactive analytics dashboard.

The system is **framework-agnostic**. The current target frontend is **React + Vite**
(see `frontend/`); past prototypes were elsewhere. Tokens and rules below refer to
plain CSS variables and component archetypes — not to any specific UI library.

## Visual Identity

**Mood:** Modern fintech, TastyTrade-inspired. Dark, precise, information-dense,
with a **deep violet/purple** brand accent.

- **Always dark.** No light mode.
- **Violet-tinted dark neutrals** with saturated accent colors encoding meaning:
  - Violet (`#a855f7` / `#c084fc`) = neutral primary, section underlines, charts, links
  - Green (`#10b981`) = positive / gain / on-budget
  - Red (`#ef4444`) = negative / loss / over-budget / cost
  - Amber (`#f59e0b`) = warning / threshold / needs attention
  - Pink (`#ec4899`) and cyan (`#22d3ee`) = secondary accents / data highlights

## Design Tokens

All design tokens are defined in CSS custom properties (`--var-name`). The **single
source of truth** is `frontend/src/styles/tokens.css`; `design-tokens.css` in this
folder is a documentation mirror of it. This document explains intent.

### Surfaces

- `--bg-page` (`#13111c`): Main page background (deep violet-black)
- `--bg-sidebar` (`#0d0b15`): Sidebar background (one shade darker)
- `--bg-surface-1` (`#1c1928`): Start of card gradient
- `--bg-surface-2` (`#221f33`): End of card gradient
- `--gradient-card`: `linear-gradient(135deg, #1c1928, #221f33)` (135° is critical)

### Borders

- `--border-card` (`#2d2940`): Default 1px solid borders on cards
- `--border-card-soft` (`#3a3550`): Hover / inner border
- `--border-grid` (`#2d2940`): Chart gridlines
- `--border-axis` (`#4a4565`): Chart zero-lines
- `--border-warn` (`#f59e0b`): Dashed borders on missing-data callouts

### Typography

- **Font:** `Inter` (primary webfont) with a system sans-serif fallback stack
  (`-apple-system, BlinkMacSystemFont, "Segoe UI", ...`)
- **Numeric alignment:** Use `font-variant-numeric: tabular-nums` on all money/percentage columns

### Spacing

4px base unit. Scale: `4 / 8 / 12 / 16 / 20 / 24 / 32` px

- Cards: `padding: 16px 20px` (`var(--space-4) var(--space-5)`)
- Section headers: `margin-top: 2rem` (`var(--space-8)`)

### Radius

- `12px` on KPI cards and metric boxes
- `8px` on missing-data callouts
- `4px` on inline `<code>` chips

### Shadows

- `--shadow-card` (`0 2px 8px rgba(0, 0, 0, 0.25)`) on KPI cards (soft, dark, low-elevation)
- `--shadow-lift` (`0 8px 24px rgba(0, 0, 0, 0.35)`) on lifted panels
- `--shadow-purple` (`0 8px 32px rgba(168, 85, 247, 0.20)`) — the one allowed brand glow,
  used sparingly as a primary accent. No other glow, inner shadows, or layered effects.

## Components

Components are described as archetypes, not framework primitives. The CSS rules
below should be applied to whatever element renders the component (a `<div>`, a
React component, a Web Component, etc.).

### KPI Card

```css
.kpi-card {
  background: linear-gradient(135deg, #1c1928, #221f33);
  border: 1px solid #2d2940;
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}
```

- Label: `0.82rem`, `#8b88a6`
- Value: `1.55rem`, bold, `#fafafa`, tabular-nums (allow wrap on narrow viewports)
- Delta: `0.75rem`, color-coded (green/red/amber)

### Section Header

```css
.section-header {
  font-size: 1.25rem;
  font-weight: 700;
  color: #e0e0ea;
  border-bottom: 2px solid #a855f7; /* primary violet */
  margin-top: 2rem;
  padding-bottom: 5px;
}
```

- Emoji prefix for semantic meaning (📊, 📈, 💰, 💵, 🏷️, etc.)
- Violet underline is critical — don't skip it
- Appears before major sections

### Missing Data Box

```css
.missing-data-box {
  background: linear-gradient(135deg, #1c1928, #221f33);
  border: 1px dashed #f59e0b;
  border-radius: 8px;
  padding: 20px;
  color: #8b88a6;
}

.missing-data-box h4 {
  color: #f59e0b;
}
```

- Use when a section requires additional data sources (Activity Statement CSV, etc.)
- Dashed amber border is the "this section is pending data" pattern
- Include clear instructions for data source

### Data Table

```css
.dataframe {
  border: 1px solid #2d2940;
  border-radius: 8px;
}

.dataframe thead {
  background: #1c1928;
  color: #8b88a6;
}

.dataframe tbody td {
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid #1c1928;
}

.dataframe tbody tr:hover td {
  background: rgba(34, 31, 51, 0.5);
}
```

- Header row with secondary background
- Right-align numeric columns
- Light hover state on rows

## Content Conventions

### Language

- **Spanish is the default locale**, but the app must support at least Spanish,
  English, and German through the Settings page.
- Keep financial abbreviations and broker field names stable across languages
  where translation would reduce clarity: NAV, TWR, XIRR, MTM, FIFO, Sharpe
  Ratio, Maximum Drawdown, Reg-T.
- Locale changes should affect navigation, page headings, empty/error states,
  setup guidance, month/day labels, and number/date formatting.
- Imported broker data is not translated. Symbols, account currencies, strategy
  names from API payloads, and raw IBKR field labels remain as supplied.
- **Casing:** sentence case for headings, capitalized category labels in tables,
  field names match data where they are broker/API terms.

### Numbers

- **Money:** always prefix with currency code, never symbol — `CHF 12,345.67`
  in English locale, `CHF 12.345,67` in German locale, etc.
- **Separators:** follow the active UI locale.
- **Returns/percentages:** signed where directional — `+2.34%`, `-0.18%`.
- **Costs:** positive magnitude when the label already implies cost; otherwise
  keep source sign conventions visible and color-code consistently.

### Emoji

Used heavily as **section markers and status indicators**, never decoratively.

**Canonical vocabulary:**

| Emoji | Used for |
|---|---|
| 📊 | Overview, dashboards, KPI sections |
| 📈 | Positive/upward returns |
| 📉 | Risk metrics, drawdown |
| 💰 | NAV / portfolio value |
| 💵 | Dividends / income |
| 💸 | Capital contributions / withdrawals |
| 🏷️ | Costs / fees |
| ⚡ | Impact / waterfall |
| 🎯 | Ratios / targets |
| 🏦 | Instruments / holdings |
| 📋 | Summary tables |
| 📝 | Detail tables |
| 📥 / ⬇️ | Export / download |
| 📄 | Document / PDF |
| 💡 | Tip / hint |
| ✅ / ⚠️ / 🔴 / 🟢 / 🟡 / 🟠 | Status badges |

### Disclaimers

Always include the localized footer equivalent of: _"The displayed data is
historical and is not financial advice."_

## Charts

Charts run **transparent** so they sit directly on the page background. The exact
chart library is up to the frontend — recharts, visx, ECharts, Chart.js, all OK
— but the visual output must respect:

- **Plot/paper background:** `rgba(0,0,0,0)` (fully transparent)
- **Gridlines:** `#2d2940`
- **Zero-line:** `#4a4565` (lighter for emphasis)
- **Font color:** `#e0e0ea`
- **Primary bars/lines:** `#a855f7` (gross/neutral, violet)
- **Positive series:** `#10b981` (net return, gains)
- **Negative series:** `#ef4444` (costs, losses)
- **Warning:** `#f59e0b` (threshold lines)
- **Secondary accents:** `#ec4899` (pink), `#22d3ee` (cyan) for additional series
- **Area fills:** `rgba(168, 85, 247, 0.15)` for primary, `rgba(16, 185, 129, 0.20)` for positive, `rgba(245, 158, 11, 0.20)` for warning

### Animations

- **Defined in source:** None. Charts animate only on data change (default ~500ms ease).
- **For UI additions:** Use `transition: 150ms ease` on color/border changes only — no slides, bounces, or parallax.

## Hover & Interaction

- **Buttons:** Default cursor (no special hover state defined)
- **Cards:** Brighten accents by ~10% on hover: `color-mix(in oklch, <accent> 90%, white)`
- **Cards:** Add subtle inner border on hover (no scaling, no bounces)
- **Tables:** Rows get soft background highlight: `rgba(34, 31, 51, 0.5)`

## What NOT to Do

- ❌ No light mode
- ❌ Off-brand colors: stay within the token palette (violet primary + green/red/amber
  semantics + pink/cyan accents). Don't introduce blues, teals, or arbitrary hues.
- ❌ No display typefaces beyond `Inter` + the system fallback stack
- ❌ No illustrations, patterns, textures, or hero images
- ❌ No `backdrop-filter: blur` or glassmorphism
- ❌ No 135° gradients on anything except cards
- ❌ No layered drop shadows. The only glow is `--shadow-purple`, used sparingly.
- ❌ No bouncing animations
- ❌ No "cyberpunk" / sci-fi flourishes (corner brackets, scanlines, neon glows,
  taglines). The violet accent is a calm brand signal, not a neon terminal effect.
- ❌ Don't strip emoji — they're the brand

## References

- Source repo: this repository.
- Design tokens — source of truth: `frontend/src/styles/tokens.css` (this folder's
  `design-tokens.css` is a documentation mirror)
- Backend (data source): `ibkr_backend/` (FastAPI + SQLAlchemy 2.0)
- Frontend: `frontend/` (React + Vite, consumes the FastAPI endpoints)
