import os

def print_tree(root, stop_at, indent=""):
    try:
        # Get all files/folders and sort them
        items = sorted(os.listdir(root))
    except PermissionError:
        return

    for i, item in enumerate(items):
        path = os.path.join(root, item)
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        
        print(f"{indent}{connector}{item}")
        
        # Logic: If it's a directory and NOT in our 'stop' list, keep going
        if os.path.isdir(path) and item not in stop_at:
            extension = "    " if is_last else "│   "
            print_tree(path, stop_at, indent + extension)

# These folders will show up in the tree, but the script won't look inside them
stop_folders = {".terraform", "node_modules", "k3s", "env", "venv", ".git", "__pycache__", ".venv", "pytests", "frontend",  ".claude", ".github", "main", "vm_creator", "shared_core", "utils", "tests", "scripts", "docs", "examples", "data", "config", "assets", "static", "templates", ".vscode", ".idea", "shadow_engine", "contestant_engine", "loadgen", "telemetry", "extra_services", ".pytest_cache"}
# stop_folders = {".terraform", "node_modules", "k3s", "env", "venv", ".git", "__pycache__", ".venv", "pytests", ".claude", ".github"}

if __name__ == "__main__":
    print(f"Directory tree for: {os.path.abspath('.')}")
    print(".")
    print_tree(".", stop_folders)