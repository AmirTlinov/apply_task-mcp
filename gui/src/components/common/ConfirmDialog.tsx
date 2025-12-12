import { useEffect, useRef } from "react";
import { X, AlertTriangle } from "lucide-react";

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  isLoading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  isOpen,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  isLoading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    confirmRef.current?.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
      if (e.key === "Enter" && !isLoading) {
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isLoading, onCancel, onConfirm]);

  if (!isOpen) return null;

  const dangerColor = "var(--color-status-fail)";
  const confirmBg = danger ? dangerColor : "var(--color-primary)";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1100,
        padding: "16px",
      }}
      onClick={onCancel}
    >
      <div
        style={{
          backgroundColor: "var(--color-background)",
          borderRadius: "12px",
          width: "420px",
          maxWidth: "96vw",
          boxShadow: "var(--shadow-xl)",
          overflow: "hidden",
        }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
      >
        <div
          style={{
            padding: "16px 18px",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "10px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
            {danger && (
              <AlertTriangle
                style={{
                  width: "16px",
                  height: "16px",
                  color: dangerColor,
                  flexShrink: 0,
                }}
              />
            )}
            <h3
              id="confirm-dialog-title"
              style={{
                fontSize: "15px",
                fontWeight: 600,
                margin: 0,
                color: "var(--color-foreground)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {title}
            </h3>
          </div>
          <button
            onClick={onCancel}
            style={{
              background: "none",
              border: "none",
              padding: "4px",
              borderRadius: "6px",
              cursor: "pointer",
              color: "var(--color-foreground-muted)",
            }}
          >
            <X style={{ width: "16px", height: "16px" }} />
          </button>
        </div>

        {description && (
          <div
            style={{
              padding: "14px 18px 0 18px",
              fontSize: "13px",
              color: "var(--color-foreground-muted)",
              lineHeight: 1.5,
            }}
          >
            {description}
          </div>
        )}

        <div
          style={{
            padding: "16px 18px",
            display: "flex",
            justifyContent: "flex-end",
            gap: "8px",
            flexWrap: "wrap",
          }}
        >
          <button
            onClick={onCancel}
            disabled={isLoading}
            style={{
              padding: "8px 12px",
              borderRadius: "8px",
              border: "1px solid var(--color-border)",
              backgroundColor: "transparent",
              fontSize: "13px",
              cursor: isLoading ? "not-allowed" : "pointer",
            }}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            disabled={isLoading}
            style={{
              padding: "8px 14px",
              borderRadius: "8px",
              border: "none",
              backgroundColor: confirmBg,
              color: "white",
              fontSize: "13px",
              fontWeight: 600,
              cursor: isLoading ? "not-allowed" : "pointer",
              opacity: isLoading ? 0.8 : 1,
            }}
          >
            {isLoading ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

