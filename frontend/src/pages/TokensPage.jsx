import { useEffect, useState } from "react";
import { getSummary } from "../api.js";
import StatsCard from "../components/StatsCard.jsx";
import SyncLogCard from "../components/SyncLogCard.jsx";
import Heatmap from "../components/Heatmap.jsx";
import TrendChart from "../components/TrendChart.jsx";
import HarnessCards from "../components/HarnessCards.jsx";
import ContextBreakdown from "../components/ContextBreakdown.jsx";
import ModelTable from "../components/ModelTable.jsx";

const RANGES = ["day", "week", "month", "all", "custom"];
const RANGE_LABELS = { day: "Day", week: "Week", month: "Month", all: "Total", custom: "Custom" };

export default function TokensPage() {
  const [summary, setSummary] = useState(null);
  const [range, setRange] = useState("all");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = () => {
    const params = range === "custom"
      ? { period: "custom", start: customStart, end: customEnd }
      : { period: range };
    setRefreshKey((k) => k + 1);
    return getSummary(params).then(setSummary).catch(() => {});
  };

  useEffect(() => {
    if (range !== "custom" || (customStart && customEnd)) refresh();
  }, [range, customStart, customEnd]);

  return (
    <div>
      <button onClick={refresh}>Refresh</button>
      <div className="grid-2">
        <StatsCard summary={summary} />
        <SyncLogCard refreshKey={refreshKey} />
      </div>
      <Heatmap refreshKey={refreshKey} />
      <TrendChart refreshKey={refreshKey} />
      <div className="card">
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button
              key={r}
              className={r === range ? "active" : ""}
              aria-current={r === range ? "true" : undefined}
              onClick={() => setRange(r)}
            >
              {RANGE_LABELS[r]}
            </button>
          ))}
        </div>
        {range === "custom" && (
          <div className="custom-range">
            <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
            <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
          </div>
        )}
        <h3>{summary ? summary.total_tokens.toLocaleString() : "—"} tokens</h3>
        <HarnessCards summary={summary} />
      </div>
      <ContextBreakdown summary={summary} />
      <ModelTable summary={summary} />
    </div>
  );
}
