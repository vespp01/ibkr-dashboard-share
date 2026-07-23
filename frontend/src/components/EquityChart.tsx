import { useRef, useEffect } from 'react';
import {
  createChart,
  ColorType,
  LineStyle,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type MouseEventParams,
} from 'lightweight-charts';

interface EquityPoint {
  date: string;
  portfolio: number;
  benchmark: number;
}

interface Props {
  points: EquityPoint[];
  portfolioLabel: string;
  benchmarkLabel: string;
  locale: string;
  height?: number;
}

export function EquityChart({
  points,
  portfolioLabel,
  benchmarkLabel,
  locale,
  height = 340,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Return early if no points
    if (points.length === 0) return;

    // Sort and deduplicate points by date (keep last value on duplicate dates)
    const sortedPoints = Array.from(points).sort((a, b) =>
      a.date.localeCompare(b.date)
    );

    // Deduplicate by keeping last occurrence of each date
    const deduped: EquityPoint[] = [];
    const seen = new Set<string>();
    for (let i = sortedPoints.length - 1; i >= 0; i--) {
      const point = sortedPoints[i];
      if (!seen.has(point.date)) {
        seen.add(point.date);
        deduped.unshift(point);
      }
    }

    // Create chart
    const chart: IChartApi = createChart(containerRef.current, {
      autoSize: true,
      height,
      layout: {
        background: {
          type: ColorType.Solid,
          color: 'transparent',
        },
        textColor: '#8b88a6',
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
      },
      grid: {
        vertLines: { color: 'rgba(45,41,64,0.4)' },
        horzLines: { color: 'rgba(45,41,64,0.4)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(168,85,247,0.5)',
          width: 1,
          style: LineStyle.Solid,
          labelBackgroundColor: '#a855f7',
        },
        horzLine: {
          color: 'rgba(168,85,247,0.35)',
          labelBackgroundColor: '#a855f7',
        },
      },
      rightPriceScale: { borderColor: '#2d2940' },
      timeScale: { borderColor: '#2d2940', fixLeftEdge: true, fixRightEdge: true },
    });

    // Add portfolio series (area, violet)
    const portfolioSeries: ISeriesApi<'Area'> = chart.addAreaSeries({
      lineColor: '#a855f7',
      topColor: 'rgba(168,85,247,0.30)',
      bottomColor: 'rgba(168,85,247,0.02)',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // Add benchmark series (line, cyan dashed)
    const benchmarkSeries: ISeriesApi<'Line'> = chart.addLineSeries({
      color: '#22d3ee',
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // Set data
    portfolioSeries.setData(deduped.map((p) => ({ time: p.date as Time, value: p.portfolio })));
    benchmarkSeries.setData(deduped.map((p) => ({ time: p.date as Time, value: p.benchmark })));

    // Fit content
    chart.timeScale().fitContent();

    // Crosshair tooltip handler
    const handleCrosshairMove = (param: MouseEventParams) => {
      if (!tooltipRef.current) return;

      // Hide tooltip if no data
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltipRef.current.style.display = 'none';
        return;
      }

      // Get series data
      const portfolioData = param.seriesData.get(portfolioSeries);
      const benchmarkData = param.seriesData.get(benchmarkSeries);

      // Format values
      const portfolioValue =
        portfolioData && 'value' in portfolioData && typeof portfolioData.value === 'number'
          ? portfolioData.value.toLocaleString(locale, { maximumFractionDigits: 1 })
          : '--';
      const benchmarkValue =
        benchmarkData && 'value' in benchmarkData && typeof benchmarkData.value === 'number'
          ? benchmarkData.value.toLocaleString(locale, { maximumFractionDigits: 1 })
          : '--';

      // Format date
      let dateLabel: string;
      if (typeof param.time === 'string') {
        dateLabel = param.time;
      } else if (typeof param.time === 'object' && param.time !== null) {
        const time = param.time as { year: number; month: number; day: number };
        const year = String(time.year);
        const month = String(time.month).padStart(2, '0');
        const day = String(time.day).padStart(2, '0');
        dateLabel = `${year}-${month}-${day}`;
      } else {
        dateLabel = String(param.time);
      }

      // Build tooltip HTML
      const tooltipHTML = `
        <div class="chart-tooltip-label">${dateLabel}</div>
        <div class="chart-tooltip-rows">
          <div class="chart-tooltip-row">
            <span class="chart-tooltip-dot" style="background: #a855f7;"></span>
            <span class="chart-tooltip-name">${portfolioLabel}</span>
            <span class="chart-tooltip-value">${portfolioValue}</span>
          </div>
          <div class="chart-tooltip-row">
            <span class="chart-tooltip-dot" style="background: #22d3ee;"></span>
            <span class="chart-tooltip-name">${benchmarkLabel}</span>
            <span class="chart-tooltip-value">${benchmarkValue}</span>
          </div>
        </div>
      `;

      tooltipRef.current.innerHTML = tooltipHTML;

      // Position tooltip
      const containerRect = containerRef.current?.getBoundingClientRect();
      if (containerRect) {
        let x = param.point.x + 12;
        let y = param.point.y + 12;

        // Keep inside container
        if (x + 100 > containerRect.width) {
          x = containerRect.width - 110;
        }
        if (y + 80 > containerRect.height) {
          y = containerRect.height - 90;
        }

        tooltipRef.current.style.left = `${x}px`;
        tooltipRef.current.style.top = `${y}px`;
      }

      tooltipRef.current.style.display = 'block';
    };

    // Subscribe
    chart.subscribeCrosshairMove(handleCrosshairMove);

    // Cleanup
    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      chart.remove();
    };
  }, [points, portfolioLabel, benchmarkLabel, locale, height]);

  return (
    <div style={{ position: 'relative' }}>
      <div
        ref={containerRef}
        style={{
          height,
          width: '100%',
        }}
      />
      <div
        ref={tooltipRef}
        className="chart-tooltip"
        style={{
          position: 'absolute',
          display: 'none',
          pointerEvents: 'none',
          zIndex: 20,
          top: 0,
          left: 0,
        }}
      />
    </div>
  );
}
