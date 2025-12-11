/**
 * Settings View - Application preferences and configuration
 * Uses zustand store for persistent settings
 */

import { useState, useCallback } from "react";
import {
  Settings,
  Palette,
  Bell,
  Keyboard,
  Database,
  Info,
  ChevronRight,
  Moon,
  Sun,
  Monitor,
  Check,
  ExternalLink,
  Download,
  Trash2,
  RefreshCw,
  X,
} from "lucide-react";
import { useSettingsStore, formatBytes, type ThemeMode } from "@/stores/settingsStore";

interface SettingsViewProps {
  isLoading?: boolean;
}

// App version (would come from package.json in production)
const APP_VERSION = "0.1.0";
const GITHUB_URL = "https://github.com/anthropics/apply-task";
const LICENSE_URL = "https://opensource.org/licenses/MIT";

interface SettingsSectionProps {
  title: string;
  description?: string;
  icon: typeof Settings;
  children: React.ReactNode;
}

function SettingsSection({ title, description, icon: Icon, children }: SettingsSectionProps) {
  return (
    <div
      style={{
        padding: "20px",
        backgroundColor: "var(--color-background)",
        borderRadius: "12px",
        border: "1px solid var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
        <div
          style={{
            width: "36px",
            height: "36px",
            borderRadius: "8px",
            backgroundColor: "var(--color-primary-subtle)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon style={{ width: "18px", height: "18px", color: "var(--color-primary)" }} />
        </div>
        <div>
          <h3 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-foreground)" }}>
            {title}
          </h3>
          {description && (
            <p style={{ fontSize: "12px", color: "var(--color-foreground-muted)", marginTop: "2px" }}>
              {description}
            </p>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}

interface ToggleProps {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

function Toggle({ label, description, checked, onChange }: ToggleProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 0",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <div>
        <div style={{ fontSize: "13px", fontWeight: 500, color: "var(--color-foreground)" }}>
          {label}
        </div>
        {description && (
          <div style={{ fontSize: "12px", color: "var(--color-foreground-muted)", marginTop: "2px" }}>
            {description}
          </div>
        )}
      </div>
      <button
        onClick={() => onChange(!checked)}
        style={{
          width: "40px",
          height: "22px",
          borderRadius: "999px",
          border: "none",
          backgroundColor: checked ? "var(--color-primary)" : "var(--color-background-muted)",
          cursor: "pointer",
          position: "relative",
          transition: "background-color 150ms ease",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: "2px",
            left: checked ? "20px" : "2px",
            width: "18px",
            height: "18px",
            borderRadius: "50%",
            backgroundColor: "white",
            boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            transition: "left 150ms ease",
          }}
        />
      </button>
    </div>
  );
}

interface SettingsRowProps {
  label: string;
  value?: string;
  icon?: typeof ChevronRight;
  onClick?: () => void;
  danger?: boolean;
}

function SettingsRow({ label, value, icon: Icon = ChevronRight, onClick, danger }: SettingsRowProps) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 0",
        borderBottom: "1px solid var(--color-border)",
        cursor: onClick ? "pointer" : "default",
      }}
    >
      <span
        style={{
          fontSize: "13px",
          fontWeight: 500,
          color: danger ? "var(--color-status-fail)" : "var(--color-foreground)",
        }}
      >
        {label}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        {value && (
          <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>{value}</span>
        )}
        {onClick && (
          <Icon
            style={{
              width: "16px",
              height: "16px",
              color: danger ? "var(--color-status-fail)" : "var(--color-foreground-subtle)",
            }}
          />
        )}
      </div>
    </div>
  );
}

// Keyboard shortcuts modal
interface ShortcutsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

function ShortcutsModal({ isOpen, onClose }: ShortcutsModalProps) {
  if (!isOpen) return null;

  const shortcuts = [
    { key: "⌘ K", action: "Open command palette" },
    { key: "⌘ N", action: "Create new task" },
    { key: "⌘ /", action: "Toggle AI chat" },
    { key: "j / k", action: "Navigate up/down" },
    { key: "Enter", action: "Open/confirm" },
    { key: "Space", action: "Toggle completion" },
    { key: "e", action: "Edit" },
    { key: "Esc", action: "Back/close" },
    { key: "g b", action: "Go to Board" },
    { key: "g l", action: "Go to List" },
    { key: "g t", action: "Go to Timeline" },
    { key: "g d", action: "Go to Dashboard" },
  ];

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: "var(--color-background)",
          borderRadius: "12px",
          padding: "24px",
          width: "400px",
          maxHeight: "80vh",
          overflowY: "auto",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "20px" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 600 }}>Keyboard Shortcuts</h3>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "4px",
            }}
          >
            <X style={{ width: "18px", height: "18px", color: "var(--color-foreground-muted)" }} />
          </button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {shortcuts.map(({ key, action }) => (
            <div
              key={key}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "8px 0",
                borderBottom: "1px solid var(--color-border)",
              }}
            >
              <span style={{ fontSize: "13px", color: "var(--color-foreground-muted)" }}>
                {action}
              </span>
              <kbd
                style={{
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  backgroundColor: "var(--color-background-muted)",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  color: "var(--color-foreground)",
                }}
              >
                {key}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SettingsView({ isLoading = false }: SettingsViewProps) {
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [isExporting, setIsExporting] = useState(false);

  // Get settings from store
  const {
    theme,
    compactMode,
    notifications,
    soundEffects,
    autoSave,
    vimMode,
    cacheSize,
    setTheme,
    setCompactMode,
    setNotifications,
    setSoundEffects,
    setAutoSave,
    setVimMode,
    clearCache,
    exportData,
  } = useSettingsStore();

  const handleClearCache = useCallback(async () => {
    setIsClearing(true);
    try {
      await clearCache();
    } finally {
      setIsClearing(false);
    }
  }, [clearCache]);

  const handleExportData = useCallback(async () => {
    setIsExporting(true);
    try {
      await exportData();
    } finally {
      setIsExporting(false);
    }
  }, [exportData]);

  const handleOpenLink = useCallback((url: string) => {
    window.open(url, "_blank", "noopener,noreferrer");
  }, []);

  if (isLoading) {
    return <SettingsSkeleton />;
  }

  const themeOptions: { mode: ThemeMode; icon: typeof Sun; label: string }[] = [
    { mode: "light", icon: Sun, label: "Light" },
    { mode: "dark", icon: Moon, label: "Dark" },
    { mode: "system", icon: Monitor, label: "System" },
  ];

  return (
    <>
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "24px",
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          maxWidth: "640px",
        }}
      >
        {/* Header */}
        <div style={{ marginBottom: "8px" }}>
          <h2
            style={{
              fontSize: "20px",
              fontWeight: 600,
              color: "var(--color-foreground)",
              marginBottom: "4px",
            }}
          >
            Settings
          </h2>
          <p style={{ fontSize: "14px", color: "var(--color-foreground-muted)" }}>
            Manage your preferences and app configuration
          </p>
        </div>

        {/* Appearance */}
        <SettingsSection
          title="Appearance"
          description="Customize how the app looks"
          icon={Palette}
        >
          <div style={{ marginBottom: "16px" }}>
            <div
              style={{
                fontSize: "12px",
                fontWeight: 500,
                color: "var(--color-foreground-muted)",
                marginBottom: "10px",
              }}
            >
              Theme
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              {themeOptions.map(({ mode, icon: Icon, label }) => (
                <button
                  key={mode}
                  onClick={() => setTheme(mode)}
                  style={{
                    flex: 1,
                    padding: "12px",
                    borderRadius: "8px",
                    border: `2px solid ${theme === mode ? "var(--color-primary)" : "var(--color-border)"}`,
                    backgroundColor: theme === mode ? "var(--color-primary-subtle)" : "transparent",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: "8px",
                    transition: "all 150ms ease",
                  }}
                >
                  <Icon
                    style={{
                      width: "20px",
                      height: "20px",
                      color: theme === mode ? "var(--color-primary)" : "var(--color-foreground-muted)",
                    }}
                  />
                  <span
                    style={{
                      fontSize: "12px",
                      fontWeight: 500,
                      color: theme === mode ? "var(--color-primary)" : "var(--color-foreground-muted)",
                    }}
                  >
                    {label}
                  </span>
                  {theme === mode && (
                    <Check style={{ width: "14px", height: "14px", color: "var(--color-primary)" }} />
                  )}
                </button>
              ))}
            </div>
          </div>

          <Toggle
            label="Compact mode"
            description="Reduce spacing and show more content"
            checked={compactMode}
            onChange={setCompactMode}
          />
        </SettingsSection>

        {/* Notifications */}
        <SettingsSection
          title="Notifications"
          description="Manage notification preferences"
          icon={Bell}
        >
          <Toggle
            label="Enable notifications"
            description="Get notified about task updates"
            checked={notifications}
            onChange={setNotifications}
          />
          <Toggle
            label="Sound effects"
            description="Play sounds for actions"
            checked={soundEffects}
            onChange={setSoundEffects}
          />
        </SettingsSection>

        {/* Keyboard */}
        <SettingsSection
          title="Keyboard Shortcuts"
          description="Customize keyboard navigation"
          icon={Keyboard}
        >
          <SettingsRow
            label="View all shortcuts"
            value="⌘ /"
            onClick={() => setShowShortcuts(true)}
          />
          <Toggle
            label="Vim mode"
            description="Enable vim-style navigation (h/j/k/l)"
            checked={vimMode}
            onChange={setVimMode}
          />
        </SettingsSection>

        {/* Data */}
        <SettingsSection title="Data & Storage" description="Manage your data" icon={Database}>
          <Toggle
            label="Auto-save"
            description="Automatically save changes"
            checked={autoSave}
            onChange={setAutoSave}
          />
          <SettingsRow
            label="Export data"
            icon={Download}
            value={isExporting ? "Exporting..." : undefined}
            onClick={handleExportData}
          />
          <SettingsRow
            label="Clear cache"
            icon={isClearing ? RefreshCw : Trash2}
            value={formatBytes(cacheSize)}
            onClick={handleClearCache}
          />
        </SettingsSection>

        {/* About */}
        <SettingsSection title="About" description="Application information" icon={Info}>
          <SettingsRow label="Version" value={APP_VERSION} />
          <SettingsRow
            label="View on GitHub"
            icon={ExternalLink}
            onClick={() => handleOpenLink(GITHUB_URL)}
          />
          <SettingsRow
            label="View license"
            icon={ExternalLink}
            onClick={() => handleOpenLink(LICENSE_URL)}
          />
          <SettingsRow
            label="Report an issue"
            icon={ExternalLink}
            onClick={() => handleOpenLink(`${GITHUB_URL}/issues/new`)}
          />
        </SettingsSection>
      </div>

      {/* Shortcuts Modal */}
      <ShortcutsModal isOpen={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </>
  );
}

function SettingsSkeleton() {
  return (
    <div
      style={{
        padding: "24px",
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        maxWidth: "640px",
      }}
    >
      <div>
        <div
          className="skeleton"
          style={{ height: "24px", width: "100px", marginBottom: "8px", borderRadius: "4px" }}
        />
        <div className="skeleton" style={{ height: "16px", width: "280px", borderRadius: "4px" }} />
      </div>
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="skeleton" style={{ height: "160px", borderRadius: "12px" }} />
      ))}
    </div>
  );
}
