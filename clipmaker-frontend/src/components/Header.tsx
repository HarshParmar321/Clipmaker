import { Plus } from "lucide-react";
import { API_BASE } from "../api";

interface HeaderProps {
  healthy: boolean | null;
  workspace: boolean;
  onNewProject: () => void;
}

export function Header({ healthy, workspace, onNewProject }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="header-inner">
        <button type="button" className="brand" onClick={onNewProject} aria-label="Clipmaker home">
          <span className="brand-mark" aria-hidden="true">◆</span>
          <span className="brand-name">Clipmaker</span>
        </button>
        {!workspace ? (
          <nav className="header-links" aria-label="Main navigation">
            <a href="#create-project">New project</a>
            <a href="#how-it-works">How it works</a>
          </nav>
        ) : null}
        <div className="header-actions">
          <div className="health-pill" title={`Backend: ${API_BASE}`}>
            <span className={`health-dot ${healthy === true ? "online" : healthy === false ? "offline" : "checking"}`} />
            {healthy === true ? "System ready" : healthy === false ? "Backend offline" : "Connecting"}
          </div>
          {workspace ? (
            <button type="button" className="button secondary small" onClick={onNewProject}>
              <Plus size={16} /> New project
            </button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
