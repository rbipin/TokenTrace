// frontend/src/pages/ProjectsPage.jsx
import { useEffect, useState } from "react";
import { getProjects, getProjectDetail } from "../api.js";
import ProjectList from "../components/ProjectList.jsx";
import HarnessCards from "../components/HarnessCards.jsx";
import ContextBreakdown from "../components/ContextBreakdown.jsx";

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);

  const refreshList = () => {
    getProjects({ period: "all" }).then((data) => {
      setProjects(data);
      if (!selected && data.length > 0) setSelected(data[0].project);
    }).catch(() => {});
  };

  useEffect(() => { refreshList(); }, []);

  useEffect(() => {
    if (!selected) return;
    getProjectDetail(selected, { period: "all" }).then(setDetail).catch(() => setDetail(null));
  }, [selected]);

  return (
    <div>
      <button onClick={refreshList}>Refresh</button>
      <div className="grid-2">
        <ProjectList projects={projects} selected={selected} onSelect={setSelected} />
        <div>
          {selected ? (
            <div className="card">
              <h4>{selected}</h4>
              <p>{detail ? detail.total_tokens.toLocaleString() : "—"} tokens</p>
              <HarnessCards summary={detail} />
            </div>
          ) : (
            <div className="card">Select a project.</div>
          )}
          <ContextBreakdown summary={detail} />
        </div>
      </div>
    </div>
  );
}
