import { useEffect, useState } from "react";
import { getSyncStatus } from "../api.js";

export default function SyncLogCard() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    getSyncStatus()
      .then(setStatus)
      .catch(() => setStatus({ last_collected_at: null, stores: [] }));
  }, []);

  if (status === null) return <div className="card">Loading sync status…</div>;

  return (
    <div className="card">
      <h4>Sync log</h4>
      <p>Last collected: {status.last_collected_at || "Never"}</p>
      {status.stores.length === 0 ? (
        <p>No remote stores configured.</p>
      ) : (
        <ul>
          {status.stores.map((s) => (
            <li key={s.name}>{s.name}: last synced {s.last_synced_at || "never"}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
