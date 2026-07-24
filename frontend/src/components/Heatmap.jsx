import { useEffect, useState } from "react";
import { getHeatmap } from "../api.js";

const DAYS_BACK = 180;
const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function toISODate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Builds a Sun->Sat, week-per-column calendar covering the last DAYS_BACK
// days, gap-filling any date missing from the API response with 0 tokens,
// and padding the first/last partial weeks with alignment-only placeholders.
function buildWeeks(records, daysBack) {
  const tokensByDate = new Map(records.map((r) => [r.date, r.tokens]));

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const rangeStart = new Date(today);
  rangeStart.setDate(rangeStart.getDate() - (daysBack - 1));

  // Pad backwards to the previous Sunday and forwards to the next Saturday
  // so every week column is a full 7 cells.
  const gridStart = new Date(rangeStart);
  gridStart.setDate(gridStart.getDate() - gridStart.getDay());

  const gridEnd = new Date(today);
  gridEnd.setDate(gridEnd.getDate() + (6 - gridEnd.getDay()));

  const cells = [];
  const cursor = new Date(gridStart);
  while (cursor <= gridEnd) {
    const inRange = cursor >= rangeStart && cursor <= today;
    if (inRange) {
      const iso = toISODate(cursor);
      cells.push({ date: iso, tokens: tokensByDate.get(iso) ?? 0, placeholder: false });
    } else {
      cells.push({ date: toISODate(cursor), tokens: 0, placeholder: true });
    }
    cursor.setDate(cursor.getDate() + 1);
  }

  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    weeks.push(cells.slice(i, i + 7));
  }
  return weeks;
}

// One label per week column: the month name is placed on the column
// containing the 1st of that month (GitHub-style), so it stays aligned
// with the day grid below it.
function buildMonthLabels(weeks) {
  return weeks.map((week) => {
    const firstOfMonth = week.find((c) => !c.placeholder && Number(c.date.slice(8, 10)) === 1);
    if (!firstOfMonth) return null;
    const monthIndex = Number(firstOfMonth.date.slice(5, 7)) - 1;
    return MONTH_NAMES[monthIndex];
  });
}

export default function Heatmap() {
  const [days, setDays] = useState([]);

  useEffect(() => {
    getHeatmap(DAYS_BACK).then(setDays).catch(() => setDays([]));
  }, []);

  const max = days.reduce((m, d) => Math.max(m, d.tokens), 0);
  const weeks = buildWeeks(days, DAYS_BACK);
  const monthLabels = buildMonthLabels(weeks);

  return (
    <div className="card">
      <h4>Activity (last {DAYS_BACK} days)</h4>
      <div className="heatmap-wrapper">
        <div className="heatmap-months" aria-hidden="true">
          {monthLabels.map((label, i) => (
            <div key={i} className="heatmap-month-label">
              {label || ""}
            </div>
          ))}
        </div>
        <div className="heatmap-grid" role="group" aria-label={`Daily token activity for the last ${DAYS_BACK} days`}>
          {weeks.map((week) =>
            week.map((d) => {
              if (d.placeholder) {
                return (
                  <div
                    key={d.date}
                    className="heatmap-cell placeholder"
                    aria-hidden="true"
                  />
                );
              }
              const intensity = max ? d.tokens / max : 0;
              const hasData = d.tokens > 0;
              const label = `${d.date}: ${d.tokens.toLocaleString()} tokens`;
              return (
                <div
                  key={d.date}
                  role="img"
                  aria-label={label}
                  title={label}
                  tabIndex={0}
                  className={`heatmap-cell ${hasData ? "has-data" : "empty"}`}
                  style={hasData ? { opacity: 0.25 + intensity * 0.75 } : undefined}
                />
              );
            })
          )}
        </div>
      </div>
      <div className="heatmap-legend">Less → More</div>
    </div>
  );
}
