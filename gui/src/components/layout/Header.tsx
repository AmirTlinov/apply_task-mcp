import { Search, Plus, RefreshCw, Command } from "lucide-react";
import { useState, useEffect, useRef } from "react";

interface HeaderProps {
  title: string;
  subtitle?: string;
  taskCount?: number;
  onSearch?: (query: string) => void;
  onNewTask?: () => void;
  onRefresh?: () => void;
  onCommandPalette?: () => void;
  isLoading?: boolean;
}

export function Header({
  title,
  subtitle,
  taskCount,
  onSearch,
  onNewTask,
  onRefresh,
  onCommandPalette,
  isLoading = false,
}: HeaderProps) {
  const [searchValue, setSearchValue] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcut: Cmd/Ctrl + K
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        if (onCommandPalette) {
          onCommandPalette();
        } else {
          inputRef.current?.focus();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCommandPalette]);

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearchValue(value);
    onSearch?.(value);
  };

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 24px",
        borderBottom: "1px solid var(--color-border)",
        backgroundColor: "var(--color-background)",
        gap: "20px",
        minHeight: "60px",
        flexShrink: 0,
      }}
    >
      {/* Left: Title & Count */}
      <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "12px" }}>
        <div>
          <h1
            style={{
              fontSize: "17px",
              fontWeight: 600,
              color: "var(--color-foreground)",
              margin: 0,
              letterSpacing: "-0.01em",
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p
              style={{
                fontSize: "12px",
                color: "var(--color-foreground-muted)",
                margin: "2px 0 0 0",
              }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {typeof taskCount === "number" && (
          <span
            style={{
              fontSize: "12px",
              fontWeight: 500,
              color: "var(--color-foreground-muted)",
              backgroundColor: "var(--color-background-muted)",
              padding: "4px 10px",
              borderRadius: "999px",
            }}
          >
            {taskCount} task{taskCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Center: Search / Command Palette Trigger */}
      {(onSearch || onCommandPalette) && (
        <div
          style={{
            flex: "1 1 auto",
            maxWidth: "480px",
            position: "relative",
          }}
        >
          <Search
            style={{
              position: "absolute",
              left: "14px",
              top: "50%",
              transform: "translateY(-50%)",
              width: "16px",
              height: "16px",
              color: isFocused ? "var(--color-primary)" : "var(--color-foreground-subtle)",
              pointerEvents: "none",
              transition: "color 150ms ease",
            }}
          />
          <input
            ref={inputRef}
            type="text"
            placeholder="Search tasks..."
            value={searchValue}
            onChange={handleSearchChange}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onClick={() => {
              if (onCommandPalette && !searchValue) {
                onCommandPalette();
              }
            }}
            style={{
              width: "100%",
              height: "40px",
              padding: "0 90px 0 42px",
              fontSize: "14px",
              border: `1px solid ${isFocused ? "var(--color-primary)" : "var(--color-border)"}`,
              borderRadius: "10px",
              backgroundColor: "var(--color-background-subtle)",
              color: "var(--color-foreground)",
              outline: "none",
              transition: "border-color 150ms ease, box-shadow 150ms ease",
              boxShadow: isFocused ? "0 0 0 3px var(--color-primary-subtle)" : "none",
            }}
          />
          {/* Keyboard shortcut hint */}
          <div
            style={{
              position: "absolute",
              right: "12px",
              top: "50%",
              transform: "translateY(-50%)",
              display: "flex",
              alignItems: "center",
              gap: "4px",
              pointerEvents: "none",
            }}
          >
            <kbd
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "2px",
                fontSize: "11px",
                fontFamily: "var(--font-mono)",
                color: "var(--color-foreground-subtle)",
                padding: "4px 8px",
                backgroundColor: "var(--color-background)",
                border: "1px solid var(--color-border)",
                borderRadius: "6px",
                boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
              }}
            >
              <Command style={{ width: "11px", height: "11px" }} />
              <span>K</span>
            </kbd>
          </div>
        </div>
      )}

      {/* Right: Actions */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexShrink: 0,
        }}
      >
        {onRefresh && (
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="btn"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "40px",
              height: "40px",
              borderRadius: "10px",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-background)",
              cursor: isLoading ? "not-allowed" : "pointer",
              opacity: isLoading ? 0.6 : 1,
              transition: "all 150ms ease",
            }}
            title="Refresh (R)"
          >
            <RefreshCw
              style={{
                width: "16px",
                height: "16px",
                color: "var(--color-foreground-muted)",
                animation: isLoading ? "spin 1s linear infinite" : "none",
              }}
            />
          </button>
        )}
        {onNewTask && (
          <button
            onClick={onNewTask}
            className="btn btn-primary"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              height: "40px",
              padding: "0 18px",
              borderRadius: "10px",
              border: "none",
              background: "linear-gradient(135deg, var(--color-primary) 0%, #2563eb 100%)",
              color: "white",
              fontSize: "14px",
              fontWeight: 500,
              cursor: "pointer",
              boxShadow: "0 2px 8px -2px rgba(59, 130, 246, 0.4)",
              transition: "all 150ms ease",
            }}
          >
            <Plus style={{ width: "16px", height: "16px" }} />
            <span>New Task</span>
          </button>
        )}
      </div>
    </header>
  );
}
