import os
import json

def get_kanban_path(root_dir: str) -> str:
    return os.path.join(root_dir, ".cc_kanban.json")

def load_tasks(root_dir: str) -> list[dict]:
    path = get_kanban_path(root_dir)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_tasks(root_dir: str, tasks: list[dict]) -> None:
    path = get_kanban_path(root_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=4)
    except Exception:
        pass

def generate_id(tasks: list[dict]) -> str:
    existing_ids = [t.get("id", "") for t in tasks]
    nums = []
    for eid in existing_ids:
        if eid.startswith("TSK-"):
            try:
                nums.append(int(eid[4:]))
            except ValueError:
                pass
    next_num = max(nums) + 1 if nums else 1
    return f"TSK-{next_num}"
