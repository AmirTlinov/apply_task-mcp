import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Search, X, FileText } from "lucide-react";
import type { TaskListItem } from "@/types/task";
import { StatusBadge } from "@/components/common/StatusBadge";

export interface CommandPaletteCommand {
  id: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  shortcut?: string;
  keywords?: string[];
  onSelect: () => void;
}

interface CommandPaletteProps {
  isOpen: boolean;
  tasks: TaskListItem[];
  commands: CommandPaletteCommand[];
  onSelectTask: (taskId: string) => void;
  onClose: () => void;
}

function normalize(text: string): string {
  return text.trim().toLowerCase();
}

function matchesQuery(haystack: string, query: string): boolean {
  if (!query) return true;
  return haystack.includes(query);
}

type PaletteItem =
  | { kind: "command"; id: string; command: CommandPaletteCommand }
  | { kind: "task"; id: string; task: TaskListItem };

export function CommandPalette({
  isOpen,
  tasks,
  commands,
  onSelectTask,
  onClose,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const normalizedQuery = useMemo(() => normalize(query), [query]);

  const filteredCommands = useMemo(() => {
    const base = normalizedQuery;
    return commands.filter((c) => {
      const keywords = c.keywords?.join(" ") ?? "";
      const hay = normalize(`${c.label} ${c.description ?? ""} ${keywords}`);
      return matchesQuery(hay, base);
    });
  }, [commands, normalizedQuery]);

  const filteredTasks = useMemo(() => {
    const base = normalizedQuery;
    const sorted = tasks
      .slice()
      .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    const matched = sorted.filter((t) => {
      const hay = normalize(`${t.title} ${t.id} ${(t.tags ?? []).join(" ")}`);
      return matchesQuery(hay, base);
    });
    if (base) return matched.slice(0, 30);
    return matched.slice(0, 8);
  }, [tasks, normalizedQuery]);

  const items = useMemo<PaletteItem[]>(() => {
    const out: PaletteItem[] = [];
    for (const c of filteredCommands) out.push({ kind: "command", id: `cmd:${c.id}`, command: c });
    for (const t of filteredTasks) out.push({ kind: "task", id: `task:${t.id}`, task: t });
    return out;
  }, [filteredCommands, filteredTasks]);

  useEffect(() => {
    if (!isOpen) return;
    setQuery("");
    setActiveIndex(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    if (activeIndex < 0) setActiveIndex(0);
    if (activeIndex > items.length - 1) setActiveIndex(Math.max(0, items.length - 1));
  }, [activeIndex, isOpen, items.length]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown" || e.key.toLowerCase() === "j") {
        e.preventDefault();
        e.stopImmediatePropagation();
        setActiveIndex((i) => Math.min(items.length - 1, i + 1));
        return;
      }
      if (e.key === "ArrowUp" || e.key.toLowerCase() === "k") {
        e.preventDefault();
        e.stopImmediatePropagation();
        setActiveIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopImmediatePropagation();
        const item = items[activeIndex];
        if (!item) return;
        if (item.kind === "command") item.command.onSelect();
        if (item.kind === "task") onSelectTask(item.task.id);
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeIndex, isOpen, items, onClose, onSelectTask]);

  useEffect(() => {
    if (!isOpen) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, isOpen]);

  if (!isOpen) return null;

  const showCommands = filteredCommands.length > 0;
  const showTasks = filteredTasks.length > 0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        zIndex: 1200,
        padding: "10vh 16px 16px",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "640px",
          maxWidth: "96vw",
          backgroundColor: "var(--color-background)",
          borderRadius: "14px",
          boxShadow: "var(--shadow-xl)",
          border: "1px solid var(--color-border)",
          overflow: "hidden",
        }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            padding: "14px 16px",
            borderBottom: "1px solid var(--color-border)",
            backgroundColor: "var(--color-background-subtle)",
          }}
        >
          <Search style={{ width: "16px", height: "16px", color: "var(--color-foreground-subtle)" }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            placeholder="Type a command or search tasks…"
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              fontSize: "14px",
              color: "var(--color-foreground)",
            }}
          />
          <button
            onClick={onClose}
            style={{
              border: "none",
              background: "transparent",
              cursor: "pointer",
              padding: "6px",
              borderRadius: "8px",
              color: "var(--color-foreground-muted)",
            }}
            title="Close"
          >
            <X style={{ width: "16px", height: "16px" }} />
          </button>
        </div>

        <div
          ref={listRef}
          style={{
            maxHeight: "52vh",
            overflowY: "auto",
            padding: "8px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          {!showCommands && !showTasks && (
            <div
              style={{
                padding: "22px 12px",
                color: "var(--color-foreground-muted)",
                fontSize: "13px",
                textAlign: "center",
              }}
            >
              No matches
            </div>
          )}

          {showCommands && (
            <Section title="Commands">
              {filteredCommands.map((c, idx) => (
                <Row
                  key={c.id}
                  idx={idx}
                  isActive={idx === activeIndex}
                  icon={c.icon}
                  title={c.label}
                  description={c.description}
                  shortcut={c.shortcut}
                  onClick={() => {
                    c.onSelect();
                    onClose();
                  }}
                />
              ))}
            </Section>
          )}

          {showTasks && (
            <Section title={normalizedQuery ? "Tasks" : "Recent tasks"}>
              {filteredTasks.map((t, i) => {
                const idx = filteredCommands.length + i;
                return (
                  <TaskRow
                    key={t.id}
                    idx={idx}
                    isActive={idx === activeIndex}
                    task={t}
                    onClick={() => {
                      onSelectTask(t.id);
                      onClose();
                    }}
                  />
                );
              })}
            </Section>
          )}
        </div>

        <div
          style={{
            padding: "10px 14px",
            borderTop: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "10px",
            color: "var(--color-foreground-muted)",
            fontSize: "12px",
            backgroundColor: "var(--color-background-subtle)",
          }}
        >
          <span>↑/↓ or j/k · Enter to open · Esc to close</span>
          <span style={{ fontFamily: "var(--font-mono)" }}>{items.length} results</span>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div
        style={{
          fontSize: "11px",
          fontWeight: 700,
          color: "var(--color-foreground-subtle)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          padding: "10px 10px 6px",
        }}
      >
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>{children}</div>
    </div>
  );
}

function Row({
  idx,
  isActive,
  icon,
  title,
  description,
  shortcut,
  onClick,
}: {
  idx: number;
  isActive: boolean;
  icon?: ReactNode;
  title: string;
  description?: string;
  shortcut?: string;
  onClick: () => void;
}) {
  return (
    <div
      data-idx={idx}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "10px 10px",
        borderRadius: "10px",
        cursor: "pointer",
        backgroundColor: isActive ? "var(--color-primary-subtle)" : "transparent",
        border: `1px solid ${isActive ? "var(--color-primary)" : "transparent"}`,
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.backgroundColor = "var(--color-background-muted)";
      }}
      onMouseLeave={(e) => {
        if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
      }}
    >
      <div
        style={{
          width: "28px",
          height: "28px",
          borderRadius: "8px",
          backgroundColor: "var(--color-background-muted)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {icon ?? <FileText style={{ width: "14px", height: "14px", color: "var(--color-foreground-subtle)" }} />}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
          <div
            style={{
              fontSize: "13px",
              fontWeight: 600,
              color: "var(--color-foreground)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {title}
          </div>
          {shortcut && (
            <kbd
              style={{
                fontSize: "11px",
                fontFamily: "var(--font-mono)",
                color: "var(--color-foreground-subtle)",
                padding: "2px 6px",
                border: "1px solid var(--color-border)",
                borderRadius: "6px",
                backgroundColor: "var(--color-background)",
                flexShrink: 0,
              }}
            >
              {shortcut}
            </kbd>
          )}
        </div>
        {description && (
          <div
            style={{
              fontSize: "12px",
              color: "var(--color-foreground-muted)",
              marginTop: "2px",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {description}
          </div>
        )}
      </div>
    </div>
  );
}

function TaskRow({
  idx,
  isActive,
  task,
  onClick,
}: {
  idx: number;
  isActive: boolean;
  task: TaskListItem;
  onClick: () => void;
}) {
  return (
    <div
      data-idx={idx}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "10px 10px",
        borderRadius: "10px",
        cursor: "pointer",
        backgroundColor: isActive ? "var(--color-primary-subtle)" : "transparent",
        border: `1px solid ${isActive ? "var(--color-primary)" : "transparent"}`,
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.backgroundColor = "var(--color-background-muted)";
      }}
      onMouseLeave={(e) => {
        if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0, flex: 1 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-foreground-subtle)" }}>
          {task.id}
        </span>
        <span
          style={{
            fontSize: "13px",
            fontWeight: 600,
            color: "var(--color-foreground)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {task.title}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0 }}>
        <StatusBadge status={task.status} size="sm" />
        {typeof task.progress === "number" && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--color-foreground-subtle)" }}>
            {task.progress}%
          </span>
        )}
      </div>
    </div>
  );
}

