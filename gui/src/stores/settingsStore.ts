/**
 * Settings Store - Persistent application settings with zustand
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemeMode = "light" | "dark" | "system";

interface SettingsState {
  // Appearance
  theme: ThemeMode;
  compactMode: boolean;

  // Notifications
  notifications: boolean;
  soundEffects: boolean;

  // Data
  autoSave: boolean;
  vimMode: boolean;

  // Computed/cached
  cacheSize: number; // in bytes

  // Actions
  setTheme: (theme: ThemeMode) => void;
  setCompactMode: (enabled: boolean) => void;
  setNotifications: (enabled: boolean) => void;
  setSoundEffects: (enabled: boolean) => void;
  setAutoSave: (enabled: boolean) => void;
  setVimMode: (enabled: boolean) => void;
  setCacheSize: (size: number) => void;
  clearCache: () => Promise<void>;
  exportData: () => Promise<void>;
  resetSettings: () => void;
}

const DEFAULT_SETTINGS = {
  theme: "light" as ThemeMode,
  compactMode: false,
  notifications: true,
  soundEffects: true,
  autoSave: true,
  vimMode: false,
  cacheSize: 0,
};

// Apply theme to document
function applyTheme(theme: ThemeMode): void {
  const root = document.documentElement;

  if (theme === "system") {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.setAttribute("data-theme", prefersDark ? "dark" : "light");
  } else {
    root.setAttribute("data-theme", theme);
  }
}

// Calculate localStorage cache size
function calculateCacheSize(): number {
  let total = 0;
  for (const key in localStorage) {
    if (Object.prototype.hasOwnProperty.call(localStorage, key)) {
      total += localStorage[key].length * 2; // UTF-16 = 2 bytes per char
    }
  }
  return total;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      ...DEFAULT_SETTINGS,

      setTheme: (theme) => {
        applyTheme(theme);
        set({ theme });
      },

      setCompactMode: (compactMode) => {
        document.documentElement.setAttribute(
          "data-compact",
          compactMode ? "true" : "false"
        );
        set({ compactMode });
      },

      setNotifications: (notifications) => set({ notifications }),
      setSoundEffects: (soundEffects) => set({ soundEffects }),
      setAutoSave: (autoSave) => set({ autoSave }),
      setVimMode: (vimMode) => set({ vimMode }),
      setCacheSize: (cacheSize) => set({ cacheSize }),

      clearCache: async () => {
        // Clear all localStorage except settings
        const settingsKey = "apply-task-settings";
        const settings = localStorage.getItem(settingsKey);

        // Clear caches (in a real app, would also clear IndexedDB, etc.)
        const keysToRemove: string[] = [];
        for (const key in localStorage) {
          if (key !== settingsKey && Object.prototype.hasOwnProperty.call(localStorage, key)) {
            keysToRemove.push(key);
          }
        }
        keysToRemove.forEach((key) => localStorage.removeItem(key));

        // Restore settings
        if (settings) {
          localStorage.setItem(settingsKey, settings);
        }

        set({ cacheSize: calculateCacheSize() });
      },

      exportData: async () => {
        // Collect all app data
        const data = {
          settings: get(),
          exportedAt: new Date().toISOString(),
          version: "0.1.0",
        };

        // Create and download JSON file
        const blob = new Blob([JSON.stringify(data, null, 2)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `apply-task-export-${new Date().toISOString().split("T")[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      },

      resetSettings: () => {
        set(DEFAULT_SETTINGS);
        applyTheme(DEFAULT_SETTINGS.theme);
      },
    }),
    {
      name: "apply-task-settings",
    }
  )
);

// Apply settings after hydration from localStorage
if (typeof window !== "undefined") {
  // Use a small delay to ensure store is hydrated
  setTimeout(() => {
    const state = useSettingsStore.getState();
    applyTheme(state.theme);
    if (state.compactMode) {
      document.documentElement.setAttribute("data-compact", "true");
    }
    state.setCacheSize(calculateCacheSize());
  }, 0);
}

// Subscribe to system theme changes
if (typeof window !== "undefined") {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const { theme } = useSettingsStore.getState();
    if (theme === "system") {
      applyTheme("system");
    }
  });
}

// Helper to format bytes
export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
