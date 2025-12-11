
import sys
import os
from pathlib import Path

# Add current directory to python path
sys.path.append(os.getcwd())

from core.desktop.devtools.application.task_manager import TaskManager
from core.desktop.devtools.interface.tasks_dir_resolver import get_tasks_dir_for_project, resolve_project_root

def test_manager():
    print("--- Initializing TaskManager ---")
    try:
        tasks_dir = Path("/home/amir/.tasks/amir")
        print(f"Tasks dir: {tasks_dir}")
        manager = TaskManager(tasks_dir=tasks_dir)
        
        print("\n--- Listing Tasks ---")
        tasks = manager.list_tasks()
        print(f"Found {len(tasks)} tasks.")
        
        if not tasks:
            print("No tasks found to test.")
            return

        target_task = tasks[0]
        print(f"Target Task: ID={target_task.id}, Domain='{target_task.domain}'")
        
        print("\n--- Test 1: Load with correct domain ---")
        loaded = manager.load_task(target_task.id, target_task.domain)
        if loaded:
            print(f"SUCCESS: Loaded {loaded.id}")
        else:
            print(f"FAILURE: Could not load {target_task.id} with domain '{target_task.domain}'")

        print("\n--- Test 2: Load with empty domain (fallback) ---")
        loaded_fallback = manager.load_task(target_task.id, "")
        if loaded_fallback:
             print(f"SUCCESS: Loaded {loaded_fallback.id} (fallback worked)")
        else:
             print(f"FAILURE: Could not load {target_task.id} with empty domain")

        print("\n--- Test 4: Load with WRONG domain ---")
        loaded_wrong = manager.load_task(target_task.id, "wrong/domain")
        if loaded_wrong:
            print(f"SUCCESS: Loaded {loaded_wrong.id} (robustness check)")
        else:
             print(f"FAILURE: Could not load {target_task.id} with wrong domain (Expected behavior?)")

        if target_task.domain:
            print("\n--- Test 5: Load with domain + trailing slash ---")
            try:
                loaded_slash = manager.load_task(target_task.id, target_task.domain + "/")
                if loaded_slash:
                    print(f"SUCCESS: Loaded with trailing slash")
                else:
                    print("FAILURE: Not found with trailing slash")
            except Exception as e:
                print(f"EXCEPTION with trailing slash: {e}")
            
    except Exception as e:
        print(f"EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_manager()
