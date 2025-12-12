import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listTasks, getStorage, updateTaskStatus as apiUpdateTaskStatus, deleteTask as apiDeleteTask } from "@/lib/tauri";
import type { TaskListItem, TaskStatus, Namespace, StorageInfo } from "@/types/task";
import { toast } from "@/components/common/Toast";

interface UseTasksResult {
  tasks: TaskListItem[];
  isLoading: boolean;
  error: string | null;
  projectName: string | null;
  projectPath: string | null;
  namespaces: Namespace[];
  refresh: () => Promise<void>;
  updateTaskStatus: (taskId: string, newStatus: TaskStatus) => void;
  deleteTask: (taskId: string) => void;
}

interface UseTasksParams {
  domain?: string;
  status?: string;
  namespace?: string | null;
  allNamespaces?: boolean;
}

export function useTasks(params?: UseTasksParams): UseTasksResult {
  const queryClient = useQueryClient();
  const queryKey = ["tasks", params?.domain, params?.status, params?.namespace, params?.allNamespaces];

  // Tasks Query
  const tasksQuery = useQuery({
    queryKey,
    queryFn: async () => {
      const response = await listTasks({
        domain: params?.domain,
        status: params?.status,
        compact: true,
        namespace: params?.namespace ?? undefined,
        allNamespaces: params?.allNamespaces ?? false,
      });
      if (!response.success) {
        throw new Error(response.error || "Failed to load tasks");
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return response.tasks.map((t: any) => {
        const taskId = t.id || t.task_id;
        // Use namespace for unique key (namespace is the storage folder)
        // Domain is the internal task categorization
        const namespace = t.namespace || "";
        const uniqueId = namespace ? `${namespace}/${taskId}` : taskId;
        return {
          id: uniqueId,
          // Keep original task_id for API calls
          task_id: taskId,
          title: t.title,
          status: t.status || "TODO",
          status_code: t.status_code,
          progress: t.progress || 0,
          subtask_count: t.subtask_count || t.subtasks?.length || 0,
          completed_count: t.completed_count || 0,
          // Keep both domain (task category) and namespace (storage location)
          domain: t.domain || "",
          namespace: namespace,
          tags: t.tags,
          updated_at: t.updated_at,
        };
      }) as TaskListItem[];
    },
  });

  // Storage Query
  const storageQuery = useQuery({
    queryKey: ["storage"],
    queryFn: async () => {
      const response = await getStorage();
      if (!response.success) {
        throw new Error(response.error || "Failed to load storage info");
      }
      // MCP returns { success, result: StorageInfo, context, suggestions }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const data = response.result as any;
      // Handle both direct StorageInfo and nested { result: StorageInfo }
      if (data && typeof data === "object" && "namespaces" in data) {
        return data as StorageInfo;
      }
      if (data && typeof data === "object" && "result" in data) {
        return data.result as StorageInfo;
      }
      throw new Error("Invalid storage response format");
    },
  });

  // Mutations
  const updateStatusMutation = useMutation({
    mutationFn: async ({ taskId, newStatus }: { taskId: string; newStatus: TaskStatus }) => {
      // taskId here is the UI id (namespace/TASK-XXX), need to find the actual task_id and namespace
      const tasks = queryClient.getQueryData<TaskListItem[]>(queryKey) || [];
      const task = tasks.find((t) => t.id === taskId);
      const actualTaskId = task?.task_id || taskId.split("/").pop() || taskId;
      const namespace = task?.namespace;
      const domain = task?.domain;

      const response = await apiUpdateTaskStatus(actualTaskId, newStatus, domain, namespace);
      if (!response.success) throw new Error(response.error);
      return response;
    },
    onMutate: async ({ taskId, newStatus }) => {
      await queryClient.cancelQueries({ queryKey });
      const previousTasks = queryClient.getQueryData<TaskListItem[]>(queryKey);

      if (previousTasks) {
        queryClient.setQueryData<TaskListItem[]>(queryKey, (old) =>
          old?.map((task) =>
            task.id === taskId
              ? {
                ...task,
                status: newStatus,
                updated_at: new Date().toISOString(),
              }
              : task
          )
        );
      }
      return { previousTasks };
    },
    onError: (err, _newTodo, context) => {
      if (context?.previousTasks) {
        queryClient.setQueryData(queryKey, context.previousTasks);
      }
      toast.error(err instanceof Error ? err.message : "Failed to update task status");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

	  const deleteMutation = useMutation({
	    mutationFn: async (taskId: string) => {
      // taskId here is the UI id (namespace/TASK-XXX), need to find the actual task_id and namespace
      const tasks = queryClient.getQueryData<TaskListItem[]>(queryKey) || [];
      const task = tasks.find((t) => t.id === taskId);
      const actualTaskId = task?.task_id || taskId.split("/").pop() || taskId;
      // Pass namespace as domain for cross-namespace operations
      const namespace = task?.namespace;

      const response = await apiDeleteTask(actualTaskId, task?.domain, namespace);
      if (!response.success) throw new Error(response.error);
	      return response;
	    },
	    onSuccess: () => {
	      toast.success("Task deleted");
	    },
	    onMutate: async (taskId) => {
      await queryClient.cancelQueries({ queryKey });
      const previousTasks = queryClient.getQueryData<TaskListItem[]>(queryKey);

      if (previousTasks) {
        queryClient.setQueryData<TaskListItem[]>(queryKey, (old) =>
          old?.filter((task) => task.id !== taskId)
        );
      }
      return { previousTasks };
    },
    onError: (err, _newTodo, context) => {
      if (context?.previousTasks) {
        queryClient.setQueryData(queryKey, context.previousTasks);
      }
      toast.error(err instanceof Error ? err.message : "Failed to delete task");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  return {
    tasks: tasksQuery.data || [],
    isLoading: tasksQuery.isLoading || storageQuery.isLoading,
    error: (tasksQuery.error as Error)?.message || (storageQuery.error as Error)?.message || null,
    projectName: storageQuery.data?.current_namespace || null,
    projectPath: storageQuery.data?.current_storage || null,
    namespaces: storageQuery.data?.namespaces || [],
    refresh: async () => {
      await Promise.all([tasksQuery.refetch(), storageQuery.refetch()]);
    },
    updateTaskStatus: (taskId, newStatus) => updateStatusMutation.mutate({ taskId, newStatus }),
    deleteTask: (taskId) => deleteMutation.mutate(taskId),
  };
}
