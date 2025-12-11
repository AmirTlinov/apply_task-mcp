/**
 * Skeleton loading components for perceived performance
 */

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  borderRadius?: string | number;
  className?: string;
}

export function Skeleton({
  width,
  height = 16,
  borderRadius = 6,
  className = "",
}: SkeletonProps) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{
        width: width ?? "100%",
        height,
        borderRadius,
      }}
    />
  );
}

/**
 * TaskCard skeleton for loading state
 */
export function TaskCardSkeleton() {
  return (
    <div
      style={{
        padding: "16px",
        border: "1px solid var(--color-border)",
        borderRadius: "12px",
        backgroundColor: "var(--color-background)",
      }}
    >
      {/* Header: ID, Status, Updated */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "10px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Skeleton width={60} height={22} borderRadius={6} />
          <Skeleton width={50} height={22} borderRadius={999} />
        </div>
        <Skeleton width={50} height={14} />
      </div>

      {/* Title */}
      <Skeleton height={20} className="mb-2" />
      <Skeleton width="70%" height={20} />

      {/* Tags */}
      <div
        style={{
          display: "flex",
          gap: "6px",
          margin: "14px 0",
        }}
      >
        <Skeleton width={50} height={22} borderRadius={6} />
        <Skeleton width={60} height={22} borderRadius={6} />
      </div>

      {/* Footer */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingTop: "4px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <Skeleton width={140} height={5} borderRadius={999} />
          <Skeleton width={40} height={16} />
        </div>
        <Skeleton width={60} height={14} />
      </div>
    </div>
  );
}

/**
 * List of skeleton cards
 */
export function TaskListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: "16px",
      }}
    >
      {Array.from({ length: count }).map((_, i) => (
        <TaskCardSkeleton key={i} />
      ))}
    </div>
  );
}

/**
 * Sidebar skeleton
 */
export function SidebarSkeleton() {
  return (
    <div
      style={{
        padding: "12px 8px",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
      }}
    >
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} height={40} borderRadius={8} />
      ))}
    </div>
  );
}

/**
 * Header skeleton
 */
export function HeaderSkeleton() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 24px",
        height: "64px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
        <Skeleton width={100} height={28} />
        <Skeleton width={60} height={20} borderRadius={999} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <Skeleton width={240} height={36} borderRadius={8} />
        <Skeleton width={100} height={36} borderRadius={8} />
      </div>
    </div>
  );
}
