import { useEffect, useState } from "react";
import { getHeatmap } from "../api.js";

export default function Heatmap() {
  const [days, setDays] = useState([]);

  useEffect(() => {
    getHeatmap(180).then(setDays).catch(() => setDays([]));
  }, []);

  const max = days.reduce((m, d) => Math.max(m, d.tokens), 0);

  return (
    <div className="card">
      <h4>Activity (last 180 days)</h4>
      <div className="heatmap-grid">
        {days.map((d) => {
          const intensity = max ? d.tokens / max : 0;
          return (
            <div
              key={d.date}
              title={`${d.date}: ${d.tokens.toLocaleString()} tokens`}
              className="heatmap-cell"
              style={{ opacity: 0.15 + intensity * 0.85 }}
            />
          );
        })}
      </div>
      <div className="heatmap-legend">Less → More</div>
    </div>
  );
}
