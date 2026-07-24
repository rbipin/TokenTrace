import { useEffect, useState } from "react";
import { getSummary } from "../api.js";
import StatsCard from "../components/StatsCard.jsx";
import SyncLogCard from "../components/SyncLogCard.jsx";
import Heatmap from "../components/Heatmap.jsx";
import TrendChart from "../components/TrendChart.jsx";

export default function TokensPage() {
  const [summary, setSummary] = useState(null);

  const refresh = () => getSummary({ period: "all" }).then(setSummary).catch(() => {});

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div>
      <button onClick={refresh}>Refresh</button>
      <div className="grid-2">
        <StatsCard summary={summary} />
        <SyncLogCard />
      </div>
      <Heatmap />
      <TrendChart />
    </div>
  );
}
