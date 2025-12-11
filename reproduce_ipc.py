
import sys
import os
import json
import subprocess
from pathlib import Path

def test_ipc():
    # Path to local python interpreter and script
    python_exe = sys.executable
    script_path = "core.desktop.devtools.interface.mcp_server"
    
    # Environment with PYTHONPATH to include current dir
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    # Ensure invalid/empty global tasks dir doesn't interfere? 
    # Actually we want to mimic normal run, so keep env as is.
    
    # Start subprocess
    print("Starting MCP server subprocess...")
    proc = subprocess.Popen(
        [python_exe, "-m", script_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        cwd=os.getcwd()  # Mimic running from project root
    )

    def send_request(req_id, method, params):
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": method,
                "arguments": params
            }
        }
        req_str = json.dumps(req)
        print(f"-> Sending: {req_str}")
        proc.stdin.write(req_str + "\n")
        proc.stdin.flush()
        
        # Read response
        resp_str = proc.stdout.readline()
        print(f"<- Received: {resp_str.strip()}")
        return json.loads(resp_str) if resp_str else None

    try:
        # 1. Initialize (MCP handshake usually needed? 
        # mcp_server.py checks initialized state? Protocol says yes.
        # But run_stdio_server just loop reads. Let's see if it needs init.
        # Bridge sends 'initialize' first.
        
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        print(f"-> Sending Init: {json.dumps(init_req)}")
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
        print(f"<- Received Init Resp: {proc.stdout.readline().strip()}")
        
        # 2. Notification initialized
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }) + "\n")
        proc.stdin.flush()

        # 3. List tasks (to get ID and Domain)
        print("\n--- Listing Tasks ---")
        list_resp = send_request(2, "tasks_list", {})
        
        tasks_content = list_resp.get("result", {}).get("content", [])
        tasks = []
        if tasks_content:
            text = tasks_content[0].get("text", "[]")
            tasks = json.loads(text).get("tasks", [])
            print(f"Found {len(tasks)} tasks.")
        
        if not tasks:
            print("No tasks found. Cannot proceed with show test.")
            return

        target = tasks[0]
        t_id = target.get("id")
        t_domain = target.get("domain")
        print(f"Target: {t_id}, Domain: '{t_domain}'")

        # 4. Show Task (Correct Domain)
        print("\n--- Show Task (With Domain) ---")
        show_resp = send_request(3, "tasks_show", {"task": t_id, "domain": t_domain})
        print(f"Result: {show_resp.get('error') or 'Success'}")

        # 5. Show Task (Empty Domain - Fallback)
        print("\n--- Show Task (Empty Domain) ---")
        show_resp_empty = send_request(4, "tasks_show", {"task": t_id, "domain": ""})
        print(f"Result: {show_resp_empty.get('error') or 'Success'}")

        # 6. Show Task (Wrong Domain)
        print("\n--- Show Task (Wrong Domain) ---")
        show_resp_wrong = send_request(5, "tasks_show", {"task": t_id, "domain": "invalid/path"})
        print(f"Result: {show_resp_wrong.get('error') or 'Success'}")

    finally:
        proc.terminate()
        try:
             outs, errs = proc.communicate(timeout=2)
             if errs:
                 print(f"Stderr: {errs}")
        except:
             pass

if __name__ == "__main__":
    test_ipc()
