import { useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { PanelLeft } from "lucide-react";

interface MainLayoutProps {
  children: ReactNode;
  currentView: string;
  onViewChange: (view: string) => void;
  projectName?: string;
}

export function MainLayout({
  children,
  currentView,
  onViewChange,
  projectName,
}: MainLayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        width: "100vw",
        overflow: "hidden",
        backgroundColor: "var(--color-background)",
      }}
    >
      {/* Sidebar */}
      <Sidebar
        currentView={currentView}
        onViewChange={onViewChange}
        projectName={projectName}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
      />

      {/* Expand button when collapsed */}
      {sidebarCollapsed && (
        <button
          onClick={() => setSidebarCollapsed(false)}
          style={{
            position: "fixed",
            bottom: "16px",
            left: "16px",
            zIndex: 50,
            padding: "8px",
            borderRadius: "8px",
            backgroundColor: "var(--color-background-subtle)",
            border: "1px solid var(--color-border)",
            boxShadow: "var(--shadow-sm)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          title="Expand sidebar"
        >
          <PanelLeft
            style={{
              width: "16px",
              height: "16px",
              color: "var(--color-foreground-muted)",
            }}
          />
        </button>
      )}

      {/* Main Content Area */}
      <main
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {children}
      </main>
    </div>
  );
}

export { Sidebar } from "./Sidebar";
export { Header } from "./Header";
