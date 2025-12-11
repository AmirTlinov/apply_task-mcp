# Tauri GUI Debug Fixes (2025-12-08)

## Issues Found and Fixed

### 1. Projects not displaying
**Root cause:** Storage response parsing mismatch

MCP returns:
```json
{
  "success": true,
  "result": {
    "namespaces": [...],
    "current_namespace": "..."
  }
}
```

Frontend expected `response.result` to be `StorageInfo` directly, but it was wrapped in another `result` object.

**Fix:** `gui/src/features/tasks/hooks/useTasks.ts`
- Added handling for both direct `StorageInfo` and nested `{result: StorageInfo}` structures

### 2. Task not found when expanding
**Root cause:** Missing `domain` field in compact task list

`task_to_dict(compact=True)` didn't include the `domain` field, so when clicking a task, the frontend couldn't determine which namespace to query.

**Fix:** `core/desktop/devtools/interface/serializers.py`
- Added `domain` field to compact serialization

### 3. Duplicate React keys
**Root cause:** Tasks from different namespaces had same IDs (TASK-001, TASK-002, etc.)

When aggregating tasks from all namespaces, multiple tasks could have the same ID, causing React key conflicts and incorrect task selection.

**Fix:** `gui/src/features/tasks/hooks/useTasks.ts` + `gui/src/types/task.ts`
- Create unique ID by combining `domain/task_id`
- Added `task_id` field to preserve original ID for API calls

### 4. Wrong task ID sent to API
**Root cause:** TaskDetailModal used unique ID instead of original task_id

**Fix:** `gui/src/App.tsx`
- Use `task_id` from task object for API calls, not the unique React key

## Files Modified

1. `gui/src/features/tasks/hooks/useTasks.ts` - Storage parsing, unique IDs
2. `gui/src/types/task.ts` - Added `task_id` field
3. `gui/src/App.tsx` - Fixed TaskDetailModal props
4. `core/desktop/devtools/interface/serializers.py` - Added domain to compact output

## Testing

- TypeScript compilation: ✅ Pass
- ESLint: ✅ Pass  
- Python MCP tests: ✅ Pass
- Manual testing: Tasks load with domains, detail modal opens correctly
