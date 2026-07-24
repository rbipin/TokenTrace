import { useEffect, useState } from "react";
import { getMeta } from "./api.js";
import TokensPage from "./pages/TokensPage.jsx";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import "./App.css";

export default function App() {
  const [page, setPage] = useState("tokens");
  const [theme, setTheme] = useState("dark");
  const [mostRecent, setMostRecent] = useState(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    getMeta().then((data) => setMostRecent(data.most_recent_data_at)).catch(() => {});
  }, [page]);

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="nav-group">General</div>
        <button
          className={page === "tokens" ? "nav-item active" : "nav-item"}
          onClick={() => setPage("tokens")}
        >
          Tokens
        </button>
        <button
          className={page === "projects" ? "nav-item active" : "nav-item"}
          onClick={() => setPage("projects")}
        >
          By Project
        </button>
        <button className="theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </nav>
      <main className="content">
        <header className="page-header">
          <span>Most recent data: {mostRecent || "—"}</span>
        </header>
        {page === "tokens" ? <TokensPage /> : <ProjectsPage />}
      </main>
    </div>
  );
}
