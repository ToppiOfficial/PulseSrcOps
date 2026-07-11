#! python3

import zipfile, os, re

script_dir = os.path.join("io_scene_valvesource")
toml_path = os.path.join(script_dir, "blender_manifest.toml")

with open(toml_path) as toml_file:
    content = toml_file.read()
    version_match = re.search(r'^version\s*=\s*[\'"]?([0-9.]+)[\'"]?', content, re.MULTILINE)
    
    if not version_match:
        print("Error: version not found in blender_manifest.toml")
        exit(1)
    
    version_str = version_match.group(1)

zip_name = f"pulsesrcops_{version_str}.zip"
print(f"Creating {zip_name}...")

zip_file = zipfile.ZipFile(os.path.join("..", zip_name), 'w', zipfile.ZIP_BZIP2)

for path, dirnames, filenames in os.walk(script_dir):
    if path.endswith("__pycache__"):
        continue
    
    for f in filenames:
        file_path = os.path.join(path, f)
        
        if file_path.endswith(".whl"):
            continue
        
        relative_path = os.path.relpath(file_path, ".")
        zip_file.write(file_path, relative_path)

zip_file.close()
zip_size = os.path.getsize(os.path.join("..", zip_name)) / (1024 * 1024)
print(f"{zip_name} ({zip_size:.2f} MB)")