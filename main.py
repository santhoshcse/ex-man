import os
import json
from pathlib import Path

# The file extensions Windows considers runnable
VALID_EXTENSIONS = ('.exe', '.bat', '.cmd')


def discover_from_custom_paths(custom_directories):
    """Recursively crawls user-defined custom directories for executables."""
    executables = []
    
    for directory in custom_directories:
        dir_path = Path(directory)
        if not dir_path.is_dir():
            print(f"Skipping invalid directory: {directory}")
            continue
            
        # rglob walks through all nested subdirectories
        for entry in dir_path.rglob('*'):
            try:
                if entry.is_file() and entry.suffix.lower() in VALID_EXTENSIONS:
                    executables.append({
                        "name": entry.stem,
                        "path": str(entry.resolve()),
                        "type": entry.suffix.lower(),
                        "source": f"Custom Path: {directory}"
                    })
            except PermissionError:
                continue
                
    return executables

if __name__ == "__main__":
    # 1. Define custom directories you want to map
    my_custom_paths = [
        r"D:\Apps\node-v24.19.0-win-x64", 
        r"D:\Apps\Python\Python313\Scripts",
        # r"C:\ProgramData\chocolatey",
        # r"",
    ]
    
    # 2. Run discovery engines   
    print("Scanning custom paths...")
    custom_apps = discover_from_custom_paths(my_custom_paths)
    
    # 3. Merge results into a single clean list
    all_discovered_apps = custom_apps
    
    # 4. Format outputs cleanly as JSON
    print(f"\nSuccessfully discovered {len(all_discovered_apps)} executable binaries.")
    # print(json.dumps(all_discovered_apps[:3], indent=4)) # Preview the first 3 items
    print(json.dumps([app["path"] for app in all_discovered_apps], indent=2))
