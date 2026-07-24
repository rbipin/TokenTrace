import { useEffect, useState } from "react";
import { getTrend } from "../api.js";

const COLORS = ["#5b8def", "#f2994a", "#9b59b6", "#27ae60", "#e74c3c", "#f1c40f"];

export default function TrendChart() {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    getTrend(30).then(setRows).catch(() => setRows([]));
  }, []);

  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const sources = [...new Set(rows.map((r) => r.source))];
  const byDate = Object.fromEntries(dates.map((d) => [d, {}]));
  rows.forEach((r) => { byDate[r.date][r.source] = r.tokens; });
  const dayTotals = dates.map((d) => sources.reduce((sum, s) => sum + (byDate[d][s] || 0), 0));
  const maxTotal = Math.max(1, ...dayTotals);

  const sourceTotals = Object.fromEntries(sources.map((s) => [s, 0]));
  rows.forEach((r) => { sourceTotals[r.source] += r.tokens; });
  const grandTotal = Object.values(sourceTotals).reduce((a, b) => a + b, 0) || 1;

  const barWidth = 8;
  const gap = 4;
  const chartHeight = 120;

  return (
    <div className="card">
      <h4>Usage trend (last 30 days)</h4>
      <svg
        width={dates.length * (barWidth + gap)}
        height={chartHeight}
        role="group"
        aria-label="Usage trend, last 30 days, stacked by source"
      >
        {dates.map((date, i) => {
          let yOffset = chartHeight;
          return sources.map((source, si) => {
            const tokens = byDate[date][source] || 0;
            const h = (tokens / maxTotal) * chartHeight;
            yOffset -= h;
            const label = `${date} — ${source}: ${tokens.toLocaleString()}`;
            return (
              <rect
                key={`${date}-${source}`}
                x={i * (barWidth + gap)}
                y={yOffset}
                width={barWidth}
                height={h}
                fill={COLORS[si % COLORS.length]}
                role="img"
                aria-label={label}
                tabIndex={0}
              >
                <title>{label}</title>
              </rect>
            );
          });
        })}
      </svg>
      <div className="legend">
        {sources.map((s, i) => (
          <span key={s} className="legend-item">
            <i style={{ background: COLORS[i % COLORS.length] }} />
            {s}: {((sourceTotals[s] / grandTotal) * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </div>
  );
}
