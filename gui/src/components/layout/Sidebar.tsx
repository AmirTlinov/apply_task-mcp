import {
  LayoutList,
  LayoutGrid,
  Clock,
  BarChart3,
  Settings,
  FolderOpen,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";

interface NavItem {
  id: string;
  label: string;
  icon: React.ComponentType<{ style?: React.CSSProperties; className?: string }>;
  shortcut?: string;
}

const navItems: NavItem[] = [
  { id: "list", label: "Tasks", icon: LayoutList, shortcut: "g l" },
  { id: "board", label: "Board", icon: LayoutGrid, shortcut: "g b" },
  { id: "timeline", label: "Timeline", icon: Clock, shortcut: "g t" },
  { id: "dashboard", label: "Dashboard", icon: BarChart3, shortcut: "g d" },
];

interface SidebarProps {
  currentView: string;
  onViewChange: (view: string) => void;
  projectName?: string;
  collapsed?: boolean;
  onToggle?: () => void;
}

export function Sidebar({
  currentView,
  onViewChange,
  projectName,
  collapsed = false,
  onToggle,
}: SidebarProps) {
  return (
    <aside
      style={{
        height: "100%",
        width: collapsed ? "64px" : "240px",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--color-border)",
        backgroundColor: "var(--color-background-subtle)",
        transition: "width 200ms cubic-bezier(0.32, 0.72, 0, 1)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: collapsed ? "16px 12px" : "16px",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          gap: "12px",
          minHeight: "64px",
        }}
      >
        {/* Logo */}
        <div
          style={{
            width: "36px",
            height: "36px",
            borderRadius: "10px",
            background: "linear-gradient(135deg, var(--color-primary) 0%, #2563eb 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            boxShadow: "0 2px 8px -2px rgba(59, 130, 246, 0.4)",
          }}
        >
          <span
            style={{
              color: "white",
              fontWeight: 700,
              fontSize: "13px",
              letterSpacing: "-0.02em",
            }}
          >
            AT
          </span>
        </div>

        {!collapsed && (
          <>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "15px",
                  color: "var(--color-foreground)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  letterSpacing: "-0.01em",
                }}
              >
                Apply Task
              </div>
              {projectName && (
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--color-foreground-muted)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    marginTop: "1px",
                  }}
                >
                  {projectName}
                </div>
              )}
            </div>
            {onToggle && (
              <button
                onClick={onToggle}
                className="btn"
                style={{
                  padding: "8px",
                  borderRadius: "8px",
                  border: "none",
                  backgroundColor: "transparent",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--color-foreground-muted)",
                }}
                title="Collapse sidebar"
              >
                <PanelLeftClose
                  style={{
                    width: "18px",
                    height: "18px",
                  }}
                />
              </button>
            )}
          </>
        )}

        {collapsed && onToggle && (
          <button
            onClick={onToggle}
            className="btn"
            style={{
              position: "absolute",
              right: "-12px",
              top: "50%",
              transform: "translateY(-50%)",
              padding: "6px",
              borderRadius: "6px",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-background)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "var(--shadow-sm)",
            }}
            title="Expand sidebar"
          >
            <PanelLeftOpen
              style={{
                width: "14px",
                height: "14px",
                color: "var(--color-foreground-muted)",
              }}
            />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav
        style={{
          flex: 1,
          padding: "12px 8px",
          overflowY: "auto",
        }}
      >
        {navItems.map((item) => (
          <NavButton
            key={item.id}
            item={item}
            isActive={currentView === item.id}
            collapsed={collapsed}
            onClick={() => onViewChange(item.id)}
          />
        ))}
      </nav>

      {/* Footer */}
      <div
        style={{
          padding: "8px",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        <NavButton
          item={{ id: "projects", label: "Projects", icon: FolderOpen }}
          isActive={currentView === "projects"}
          collapsed={collapsed}
          onClick={() => onViewChange("projects")}
        />
        <NavButton
          item={{ id: "settings", label: "Settings", icon: Settings }}
          isActive={currentView === "settings"}
          collapsed={collapsed}
          onClick={() => onViewChange("settings")}
        />
      </div>
    </aside>
  );
}

interface NavButtonProps {
  item: NavItem;
  isActive: boolean;
  collapsed: boolean;
  onClick: () => void;
}

function NavButton({ item, isActive, collapsed, onClick }: NavButtonProps) {
  const Icon = item.icon;

  return (
    <button
      onClick={onClick}
      className="sidebar-item"
      aria-current={isActive ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      style={{
        width: "100%",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: collapsed ? "10px" : "10px 12px",
        marginBottom: "2px",
        borderRadius: "8px",
        fontSize: "14px",
        fontWeight: isActive ? 500 : 400,
        color: isActive ? "var(--color-primary)" : "var(--color-foreground-muted)",
        backgroundColor: isActive ? "var(--color-primary-subtle)" : "transparent",
        border: "none",
        cursor: "pointer",
        transition: "all 150ms ease",
        justifyContent: collapsed ? "center" : "flex-start",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={(e) => {
        if (!isActive) {
          e.currentTarget.style.backgroundColor = "var(--color-background-hover)";
          e.currentTarget.style.color = "var(--color-foreground)";
        }
      }}
      onMouseLeave={(e) => {
        if (!isActive) {
          e.currentTarget.style.backgroundColor = "transparent";
          e.currentTarget.style.color = "var(--color-foreground-muted)";
        }
      }}
    >
      {/* Active indicator bar */}
      {isActive && (
        <span
          style={{
            position: "absolute",
            left: 0,
            top: "50%",
            transform: "translateY(-50%)",
            width: "3px",
            height: "20px",
            backgroundColor: "var(--color-primary)",
            borderRadius: "0 2px 2px 0",
          }}
        />
      )}

      <Icon
        style={{
          width: "18px",
          height: "18px",
          flexShrink: 0,
          transition: "transform 150ms ease",
        }}
      />

      {!collapsed && (
        <>
          <span
            style={{
              flex: 1,
              textAlign: "left",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {item.label}
          </span>
          {item.shortcut && (
            <kbd
              style={{
                fontSize: "10px",
                fontFamily: "var(--font-mono)",
                color: "var(--color-foreground-subtle)",
                padding: "3px 6px",
                backgroundColor: "var(--color-background)",
                borderRadius: "4px",
                border: "1px solid var(--color-border)",
                letterSpacing: "0.02em",
                opacity: 0.8,
              }}
            >
              {item.shortcut}
            </kbd>
          )}
        </>
      )}
    </button>
  );
}
