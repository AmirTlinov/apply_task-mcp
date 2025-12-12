import { useQuery } from "@tanstack/react-query";
import { getAIStatus } from "@/lib/tauri";

export interface AIStatusSnapshot {
  status: string;
  current?: { op?: string | null; task?: string | null; path?: string | null } | null;
  plan?: { task: string; steps: string[]; current: number; total: number; progress: string } | null;
  history?: Array<{ time: string; op: string; task?: string | null; path?: string | null; summary: string; ok: boolean; ms: number }>;
  signal?: { pending: string; message: string };
  stats?: { total_ops: number; errors: number };
}

export function useAIStatus() {
  return useQuery({
    queryKey: ["ai_status"],
    queryFn: async () => {
      const response = await getAIStatus();
      if (!response.success) {
        throw new Error(response.error || "Failed to load AI status");
      }
      return response.result as AIStatusSnapshot;
    },
    refetchInterval: 1500,
  });
}

