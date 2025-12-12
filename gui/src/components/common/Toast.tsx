/**
 * Toast notification component with auto-dismiss
 */

import { useState } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { create } from "zustand";

export type ToastType = "success" | "error" | "info" | "warning";

interface Toast {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
}

interface ToastStore {
  toasts: Toast[];
  addToast: (message: string, type?: ToastType, duration?: number) => void;
  removeToast: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  addToast: (message, type = "info", duration = 3000) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    set((state) => ({
      toasts: [...state.toasts, { id, message, type, duration }],
    }));

    if (duration > 0) {
      setTimeout(() => {
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        }));
      }, duration);
    }
  },
  removeToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }));
  },
}));

// Helper function to show toasts
export const toast = {
  success: (message: string, duration?: number) =>
    useToastStore.getState().addToast(message, "success", duration),
  error: (message: string, duration?: number) =>
    useToastStore.getState().addToast(message, "error", duration),
  info: (message: string, duration?: number) =>
    useToastStore.getState().addToast(message, "info", duration),
  warning: (message: string, duration?: number) =>
    useToastStore.getState().addToast(message, "warning", duration),
};

const typeConfig: Record<ToastType, { icon: typeof Info; color: string; bg: string }> = {
  success: {
    icon: CheckCircle2,
    color: "var(--color-status-ok)",
    bg: "var(--color-status-ok-subtle)",
  },
  error: {
    icon: AlertTriangle,
    color: "var(--color-status-fail)",
    bg: "var(--color-status-fail-subtle)",
  },
  warning: {
    icon: AlertTriangle,
    color: "var(--color-status-warn)",
    bg: "var(--color-status-warn-subtle)",
  },
  info: {
    icon: Info,
    color: "var(--color-primary)",
    bg: "var(--color-primary-subtle)",
  },
};

function ToastItem({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const [isExiting, setIsExiting] = useState(false);
  const config = typeConfig[toast.type];
  const Icon = config.icon;

  const handleClose = () => {
    setIsExiting(true);
    setTimeout(onClose, 200);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "12px 16px",
        backgroundColor: "var(--color-background)",
        borderRadius: "10px",
        boxShadow: "var(--shadow-lg)",
        border: "1px solid var(--color-border)",
        minWidth: "280px",
        maxWidth: "400px",
        animation: isExiting
          ? "toast-exit 200ms ease-out forwards"
          : "toast-enter 200ms ease-out",
      }}
    >
      <div
        style={{
          width: "28px",
          height: "28px",
          borderRadius: "6px",
          backgroundColor: config.bg,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon style={{ width: "16px", height: "16px", color: config.color }} />
      </div>
      <span
        style={{
          flex: 1,
          fontSize: "13px",
          fontWeight: 500,
          color: "var(--color-foreground)",
          lineHeight: 1.4,
        }}
      >
        {toast.message}
      </span>
      <button
        onClick={handleClose}
        style={{
          padding: "4px",
          borderRadius: "4px",
          border: "none",
          backgroundColor: "transparent",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <X style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />
      </button>
    </div>
  );
}

export function ToastContainer() {
  const { toasts, removeToast } = useToastStore();

  if (toasts.length === 0) return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: "24px",
        right: "24px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      {toasts.map((t) => (
        <div key={t.id} style={{ pointerEvents: "auto" }}>
          <ToastItem toast={t} onClose={() => removeToast(t.id)} />
        </div>
      ))}
    </div>
  );
}
