import { Search, Plus, RefreshCw, Command, ChevronDown, FolderOpen } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import type { Namespace } from "@/types/task";
import type { AIStatusSnapshot } from "@/hooks/useAIStatus";
import { sendAISignal } from "@/lib/tauri";
import { toast } from "@/components/common/Toast";

interface HeaderProps {
  title: string;
  subtitle?: string;
  taskCount?: number;
  onSearch?: (query: string) => void;
  onNewTask?: () => void;
  onRefresh?: () => void;
  onCommandPalette?: () => void;
  isLoading?: boolean;
  namespaces?: Namespace[];
  selectedNamespace?: string | null;
  onNamespaceChange?: (namespace: string | null) => void;
  aiStatus?: AIStatusSnapshot;
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
  namespaces = [],
  selectedNamespace,
  onNamespaceChange,
  aiStatus,
}: HeaderProps) {
  const [searchValue, setSearchValue] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [showNamespaceDropdown, setShowNamespaceDropdown] = useState(false);
  const [showAiDropdown, setShowAiDropdown] = useState(false);
  const [signalMessage, setSignalMessage] = useState("");
  const [isSendingSignal, setIsSendingSignal] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const aiRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowNamespaceDropdown(false);
      }
      if (aiRef.current && !aiRef.current.contains(e.target as Node)) {
        setShowAiDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Get display name for selected namespace
  const getNamespaceDisplayName = () => {
    if (!selectedNamespace) return "All Projects";
    const ns = namespaces.find((n) => n.namespace === selectedNamespace);
    return ns?.namespace || selectedNamespace;
  };

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

  const handleAiSignal = async (
    signal: "pause" | "resume" | "stop" | "skip" | "message",
    message?: string
  ) => {
    if (isSendingSignal) return;
    setIsSendingSignal(true);
    try {
      await sendAISignal(signal, message);
      if (signal === "message") {
        setSignalMessage("");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to send AI signal");
    } finally {
      setIsSendingSignal(false);
    }
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
      {/* Left: Title & Project Selector */}
      <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "16px" }}>
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

        {/* Project/Namespace Selector */}
        {namespaces.length > 0 && onNamespaceChange && (
          <div ref={dropdownRef} style={{ position: "relative" }}>
            <button
              onClick={() => setShowNamespaceDropdown(!showNamespaceDropdown)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                padding: "6px 12px",
                borderRadius: "8px",
                border: "1px solid var(--color-border)",
                backgroundColor: showNamespaceDropdown
                  ? "var(--color-background-muted)"
                  : "var(--color-background)",
                cursor: "pointer",
                transition: "all 150ms ease",
              }}
            >
              <FolderOpen
                style={{
                  width: "14px",
                  height: "14px",
                  color: "var(--color-foreground-muted)",
                }}
              />
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: 500,
                  color: "var(--color-foreground)",
                  maxWidth: "150px",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {getNamespaceDisplayName()}
              </span>
              <ChevronDown
                style={{
                  width: "14px",
                  height: "14px",
                  color: "var(--color-foreground-muted)",
                  transform: showNamespaceDropdown ? "rotate(180deg)" : "none",
                  transition: "transform 150ms ease",
                }}
              />
            </button>

            {/* Dropdown Menu */}
            {showNamespaceDropdown && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  left: 0,
                  minWidth: "200px",
                  maxHeight: "300px",
                  overflowY: "auto",
                  backgroundColor: "var(--color-background)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "10px",
                  boxShadow: "var(--shadow-lg)",
                  zIndex: 100,
                  padding: "4px",
                }}
              >
                {/* All Projects Option */}
                <button
                  onClick={() => {
                    onNamespaceChange(null);
                    setShowNamespaceDropdown(false);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    width: "100%",
                    padding: "10px 12px",
                    border: "none",
                    borderRadius: "6px",
                    backgroundColor: selectedNamespace === null
                      ? "var(--color-primary-subtle)"
                      : "transparent",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "background-color 100ms ease",
                  }}
                  onMouseEnter={(e) => {
                    if (selectedNamespace !== null) {
                      e.currentTarget.style.backgroundColor = "var(--color-background-hover)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (selectedNamespace !== null) {
                      e.currentTarget.style.backgroundColor = "transparent";
                    }
                  }}
                >
                  <FolderOpen
                    style={{
                      width: "16px",
                      height: "16px",
                      color: selectedNamespace === null
                        ? "var(--color-primary)"
                        : "var(--color-foreground-muted)",
                    }}
                  />
                  <span
                    style={{
                      fontSize: "13px",
                      fontWeight: selectedNamespace === null ? 600 : 400,
                      color: selectedNamespace === null
                        ? "var(--color-primary)"
                        : "var(--color-foreground)",
                    }}
                  >
                    All Projects
                  </span>
                </button>

                {/* Divider */}
                <div
                  style={{
                    height: "1px",
                    backgroundColor: "var(--color-border)",
                    margin: "4px 8px",
                  }}
                />

                {/* Individual Namespaces */}
                {namespaces.map((ns) => (
                  <button
                    key={ns.namespace}
                    onClick={() => {
                      onNamespaceChange(ns.namespace);
                      setShowNamespaceDropdown(false);
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      width: "100%",
                      padding: "10px 12px",
                      border: "none",
                      borderRadius: "6px",
                      backgroundColor: selectedNamespace === ns.namespace
                        ? "var(--color-primary-subtle)"
                        : "transparent",
                      cursor: "pointer",
                      textAlign: "left",
                      transition: "background-color 100ms ease",
                    }}
                    onMouseEnter={(e) => {
                      if (selectedNamespace !== ns.namespace) {
                        e.currentTarget.style.backgroundColor = "var(--color-background-hover)";
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (selectedNamespace !== ns.namespace) {
                        e.currentTarget.style.backgroundColor = "transparent";
                      }
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <FolderOpen
                        style={{
                          width: "16px",
                          height: "16px",
                          color: selectedNamespace === ns.namespace
                            ? "var(--color-primary)"
                            : "var(--color-foreground-muted)",
                        }}
                      />
                      <span
                        style={{
                          fontSize: "13px",
                          fontWeight: selectedNamespace === ns.namespace ? 600 : 400,
                          color: selectedNamespace === ns.namespace
                            ? "var(--color-primary)"
                            : "var(--color-foreground)",
                          maxWidth: "130px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {ns.namespace}
                      </span>
                    </div>
                    <span
                      style={{
                        fontSize: "11px",
                        color: "var(--color-foreground-subtle)",
                        backgroundColor: "var(--color-background-muted)",
                        padding: "2px 6px",
                        borderRadius: "4px",
                      }}
                    >
                      {ns.task_count}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Task Count */}
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
	            onClick={() => {
	              if (onCommandPalette) onCommandPalette();
	            }}
	            style={{
	              position: "absolute",
	              right: "12px",
	              top: "50%",
	              transform: "translateY(-50%)",
	              display: "flex",
	              alignItems: "center",
	              gap: "4px",
	              pointerEvents: onCommandPalette ? "auto" : "none",
	              cursor: onCommandPalette ? "pointer" : "default",
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
        {aiStatus && (
          <div ref={aiRef} style={{ position: "relative" }}>
            <button
              onClick={() => setShowAiDropdown((v) => !v)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                height: "40px",
                padding: "0 10px",
                borderRadius: "10px",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-background)",
                fontSize: "12px",
                fontWeight: 600,
                color: aiStatus.status === "paused"
                  ? "var(--color-status-warn)"
                  : aiStatus.status === "error"
                    ? "var(--color-status-fail)"
                    : "var(--color-foreground)",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
              title="AI status"
            >
              <span>AI</span>
              <span style={{ fontWeight: 500, color: "var(--color-foreground-muted)" }}>
                {aiStatus.status}
              </span>
              {aiStatus.current?.op && (
                <span style={{ fontWeight: 500 }}>
                  · {aiStatus.current.op}
                </span>
              )}
              {aiStatus.plan && (
                <span style={{ fontWeight: 500, color: "var(--color-foreground-muted)" }}>
                  ({aiStatus.plan.progress})
                </span>
              )}
              <ChevronDown
                style={{
                  width: "12px",
                  height: "12px",
                  color: "var(--color-foreground-subtle)",
                  transform: showAiDropdown ? "rotate(180deg)" : "none",
                  transition: "transform 150ms ease",
                }}
              />
            </button>

            {showAiDropdown && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  right: 0,
                  width: "320px",
                  maxHeight: "360px",
                  overflowY: "auto",
                  backgroundColor: "var(--color-background)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "10px",
                  boxShadow: "var(--shadow-lg)",
                  zIndex: 120,
                  padding: "10px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                }}
              >
	                <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
	                  Status: {aiStatus.status}{aiStatus.current?.task ? ` · ${aiStatus.current.task}` : ""}
	                </div>

	                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
	                  {aiStatus.status === "paused" ? (
	                    <button
	                      type="button"
	                      onClick={() => handleAiSignal("resume")}
	                      disabled={isSendingSignal}
	                      style={{
	                        padding: "6px 10px",
	                        borderRadius: "6px",
	                        border: "1px solid var(--color-border)",
	                        backgroundColor: "var(--color-status-ok-subtle)",
	                        fontSize: "12px",
	                        cursor: isSendingSignal ? "not-allowed" : "pointer",
	                        opacity: isSendingSignal ? 0.6 : 1,
	                      }}
	                    >
	                      Resume
	                    </button>
	                  ) : (
	                    <button
	                      type="button"
	                      onClick={() => handleAiSignal("pause")}
	                      disabled={isSendingSignal}
	                      style={{
	                        padding: "6px 10px",
	                        borderRadius: "6px",
	                        border: "1px solid var(--color-border)",
	                        backgroundColor: "var(--color-status-warn-subtle)",
	                        fontSize: "12px",
	                        cursor: isSendingSignal ? "not-allowed" : "pointer",
	                        opacity: isSendingSignal ? 0.6 : 1,
	                      }}
	                    >
	                      Pause
	                    </button>
	                  )}
	                  <button
	                    type="button"
	                    onClick={() => handleAiSignal("skip")}
	                    disabled={isSendingSignal}
	                    style={{
	                      padding: "6px 10px",
	                      borderRadius: "6px",
	                      border: "1px solid var(--color-border)",
	                      backgroundColor: "transparent",
	                      fontSize: "12px",
	                      cursor: isSendingSignal ? "not-allowed" : "pointer",
	                      opacity: isSendingSignal ? 0.6 : 1,
	                    }}
	                  >
	                    Skip
	                  </button>
	                  <button
	                    type="button"
	                    onClick={() => handleAiSignal("stop")}
	                    disabled={isSendingSignal}
	                    style={{
	                      padding: "6px 10px",
	                      borderRadius: "6px",
	                      border: "1px solid var(--color-border)",
	                      backgroundColor: "transparent",
	                      fontSize: "12px",
	                      cursor: isSendingSignal ? "not-allowed" : "pointer",
	                      opacity: isSendingSignal ? 0.6 : 1,
	                    }}
	                  >
	                    Stop
	                  </button>
	                </div>

	                <div style={{ display: "flex", gap: "6px" }}>
	                  <input
	                    type="text"
	                    value={signalMessage}
	                    onChange={(e) => setSignalMessage(e.target.value)}
	                    placeholder="Message to AI..."
	                    disabled={isSendingSignal}
	                    style={{
	                      flex: 1,
	                      padding: "6px 8px",
	                      borderRadius: "6px",
	                      border: "1px solid var(--color-border)",
	                      backgroundColor: "var(--color-background)",
	                      fontSize: "12px",
	                      color: "var(--color-foreground)",
	                      outline: "none",
	                    }}
	                  />
	                  <button
	                    type="button"
	                    onClick={() => handleAiSignal("message", signalMessage.trim())}
	                    disabled={!signalMessage.trim() || isSendingSignal}
	                    style={{
	                      padding: "6px 10px",
	                      borderRadius: "6px",
	                      border: "1px solid var(--color-border)",
	                      backgroundColor: "var(--color-primary-subtle)",
	                      fontSize: "12px",
	                      cursor: !signalMessage.trim() || isSendingSignal ? "not-allowed" : "pointer",
	                      opacity: !signalMessage.trim() || isSendingSignal ? 0.6 : 1,
	                    }}
	                  >
	                    Send
	                  </button>
	                </div>

	                {aiStatus.signal?.pending && (
	                  <div style={{ fontSize: "11px", color: "var(--color-foreground-muted)" }}>
	                    Pending signal: {aiStatus.signal.pending}
	                    {aiStatus.signal.message ? ` · ${aiStatus.signal.message}` : ""}
	                  </div>
	                )}

	                {aiStatus.plan && (
	                  <div>
                    <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: "6px" }}>
                      Plan ({aiStatus.plan.progress})
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                      {aiStatus.plan.steps.map((step, idx) => (
                        <div
                          key={`${idx}-${step}`}
                          style={{
                            fontSize: "12px",
                            padding: "4px 6px",
                            borderRadius: "6px",
                            backgroundColor: idx < aiStatus.plan!.current
                              ? "var(--color-status-ok-subtle)"
                              : idx === aiStatus.plan!.current
                                ? "var(--color-primary-subtle)"
                                : "transparent",
                          }}
                        >
                          {idx + 1}. {step}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {aiStatus.history && aiStatus.history.length > 0 && (
                  <div>
                    <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: "6px" }}>
                      Recent AI ops
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                      {aiStatus.history.map((h) => (
                        <div key={`${h.time}-${h.op}-${h.summary}`} style={{ fontSize: "12px", color: "var(--color-foreground-muted)" }}>
                          [{h.time}] {h.op} {h.task ? `· ${h.task}` : ""} — {h.summary}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

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
