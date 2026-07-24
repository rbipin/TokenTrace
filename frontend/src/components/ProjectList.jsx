export default function ProjectList({ projects, selected, onSelect }) {
  const max = Math.max(1, ...projects.map((p) => p.tokens));
  return (
    <div className="card">
      <h4>Projects</h4>
      <ul className="project-list">
        {projects.map((p) => (
          <li
            key={p.project}
            className={p.project === selected ? "selected" : ""}
            onClick={() => onSelect(p.project)}
          >
            <span>{p.project}</span>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${(p.tokens / max) * 100}%` }} /></div>
            <small>{p.tokens.toLocaleString()}</small>
          </li>
        ))}
        {projects.length === 0 && <p>No project data yet.</p>}
      </ul>
    </div>
  );
}
