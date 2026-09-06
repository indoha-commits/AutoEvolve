from __future__ import annotations
import json
from agents.coding_orchestrator import execute_task, apply_task
from core.ops_store import list_tasks, list_incidents

def multiline():
    print("Finish with a line containing only .")
    out=[]
    while True:
        x=input("... ")
        if x==".":
            break
        out.append(x)
    return "\n".join(out).strip()

def main():
    print("""
Company Coding V0.5

/newtask <workspace>     create isolated coding task
/tasks                  recent structured tasks
/incidents              recent incidents
/apply <task-id>        apply a validated task patch to the source checkout
/quit
""")
    while True:
        try:
            cmd=input("code> ").strip()
        except (EOFError,KeyboardInterrupt):
            break
        if not cmd: continue
        if cmd=="/quit": break
        if cmd=="/tasks":
            for t in list_tasks(20):
                print(f'{t["id"]}  {t["status"]}  {t["workspace"]}  {t.get("risk")}')
            continue
        if cmd=="/incidents":
            for i in list_incidents(20):
                print(f'{i["id"]}  {i["service"]}  {i["status"]}')
            continue
        if cmd.startswith("/apply "):
            print(json.dumps(apply_task(cmd.split(" ",1)[1]), indent=2))
            continue
        if cmd.startswith("/newtask "):
            workspace=cmd.split(" ",1)[1].strip()
            req=multiline()
            result=execute_task(workspace,req)
            print(json.dumps({
                "id":result["id"],"status":result["status"],
                "risk":result.get("risk"),
                "changed_files":result.get("changed_files",[]),
                "validation_error_count":result.get("validation_error_count"),
                "patch_path":result.get("patch_path"),
            },indent=2))
            continue
        print("Unknown command")

if __name__=="__main__":
    main()
