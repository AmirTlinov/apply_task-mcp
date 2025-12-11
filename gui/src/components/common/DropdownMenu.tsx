/**
 * Reusable Dropdown Menu component using Radix UI
 */

import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { type ReactNode } from "react";

interface MenuItem {
  label: string;
  icon?: ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}

interface MenuSeparator {
  type: "separator";
}

type MenuItemOrSeparator = MenuItem | MenuSeparator;

interface DropdownMenuProps {
  trigger: ReactNode;
  items: MenuItemOrSeparator[];
  align?: "start" | "center" | "end";
}

export function DropdownMenu({ trigger, items, align = "end" }: DropdownMenuProps) {
  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        {trigger}
      </DropdownMenuPrimitive.Trigger>

      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align={align}
          sideOffset={4}
          style={{
            minWidth: "160px",
            backgroundColor: "var(--color-background)",
            borderRadius: "8px",
            border: "1px solid var(--color-border)",
            boxShadow: "0 4px 12px rgba(0,0,0,0.1), 0 2px 4px rgba(0,0,0,0.05)",
            padding: "4px",
            zIndex: 1000,
            animation: "fadeIn 150ms ease",
          }}
        >
          {items.map((item, index) => {
            if ("type" in item && item.type === "separator") {
              return (
                <DropdownMenuPrimitive.Separator
                  key={`sep-${index}`}
                  style={{
                    height: "1px",
                    backgroundColor: "var(--color-border)",
                    margin: "4px 0",
                  }}
                />
              );
            }

            const menuItem = item as MenuItem;
            return (
              <DropdownMenuPrimitive.Item
                key={menuItem.label}
                disabled={menuItem.disabled}
                onSelect={menuItem.onClick}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  padding: "8px 12px",
                  borderRadius: "4px",
                  fontSize: "13px",
                  cursor: menuItem.disabled ? "not-allowed" : "pointer",
                  outline: "none",
                  color: menuItem.danger
                    ? "var(--color-status-fail)"
                    : menuItem.disabled
                    ? "var(--color-foreground-subtle)"
                    : "var(--color-foreground)",
                  opacity: menuItem.disabled ? 0.5 : 1,
                  transition: "background-color 100ms ease",
                }}
                onMouseEnter={(e) => {
                  if (!menuItem.disabled) {
                    e.currentTarget.style.backgroundColor = menuItem.danger
                      ? "rgba(239, 68, 68, 0.1)"
                      : "var(--color-background-muted)";
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                {menuItem.icon && (
                  <span style={{ display: "flex", width: "14px", height: "14px" }}>
                    {menuItem.icon}
                  </span>
                )}
                {menuItem.label}
              </DropdownMenuPrimitive.Item>
            );
          })}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}

// CSS animation (add to global styles or use inline keyframes)
const styles = `
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
`;

// Inject styles
if (typeof document !== "undefined") {
  const styleEl = document.createElement("style");
  styleEl.textContent = styles;
  document.head.appendChild(styleEl);
}
