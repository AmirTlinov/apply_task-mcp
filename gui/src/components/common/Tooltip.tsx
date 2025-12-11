/**
 * Simple tooltip component
 */

import { useState, useRef, useEffect } from "react";

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  shortcut?: string;
  position?: "top" | "bottom" | "left" | "right";
  delay?: number;
}

export function Tooltip({
  content,
  children,
  shortcut,
  position = "top",
  delay = 300,
}: TooltipProps) {
  const [isVisible, setIsVisible] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  const showTooltip = () => {
    timeoutRef.current = setTimeout(() => {
      setIsVisible(true);
    }, delay);
  };

  const hideTooltip = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    setIsVisible(false);
  };

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const getPositionStyles = (): React.CSSProperties => {
    const base: React.CSSProperties = {
      position: "absolute",
      zIndex: 1000,
      whiteSpace: "nowrap",
    };

    switch (position) {
      case "top":
        return { ...base, bottom: "100%", left: "50%", transform: "translateX(-50%)", marginBottom: "8px" };
      case "bottom":
        return { ...base, top: "100%", left: "50%", transform: "translateX(-50%)", marginTop: "8px" };
      case "left":
        return { ...base, right: "100%", top: "50%", transform: "translateY(-50%)", marginRight: "8px" };
      case "right":
        return { ...base, left: "100%", top: "50%", transform: "translateY(-50%)", marginLeft: "8px" };
    }
  };

  return (
    <div
      ref={triggerRef}
      onMouseEnter={showTooltip}
      onMouseLeave={hideTooltip}
      onFocus={showTooltip}
      onBlur={hideTooltip}
      style={{ position: "relative", display: "inline-flex" }}
    >
      {children}
      {isVisible && (
        <div
          className="tooltip"
          role="tooltip"
          style={{
            ...getPositionStyles(),
            padding: "6px 10px",
            backgroundColor: "var(--color-foreground)",
            color: "var(--color-background)",
            fontSize: "12px",
            fontWeight: 500,
            borderRadius: "6px",
            boxShadow: "var(--shadow-lg)",
            display: "flex",
            alignItems: "center",
            gap: "8px",
          }}
        >
          <span>{content}</span>
          {shortcut && (
            <kbd
              style={{
                fontSize: "10px",
                fontFamily: "var(--font-mono)",
                padding: "2px 5px",
                backgroundColor: "rgba(255, 255, 255, 0.15)",
                borderRadius: "4px",
                opacity: 0.8,
              }}
            >
              {shortcut}
            </kbd>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Keyboard shortcut hint badge (standalone)
 */
export function KeyboardHint({ shortcut }: { shortcut: string }) {
  return (
    <kbd
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "2px",
        fontSize: "11px",
        fontFamily: "var(--font-mono)",
        color: "var(--color-foreground-subtle)",
        padding: "3px 6px",
        backgroundColor: "var(--color-background)",
        border: "1px solid var(--color-border)",
        borderRadius: "4px",
        letterSpacing: "0.02em",
      }}
    >
      {shortcut}
    </kbd>
  );
}
