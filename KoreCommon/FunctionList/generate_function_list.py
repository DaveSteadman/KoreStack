import ast
import os
from pathlib import Path

def get_functions(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        return [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    except Exception:
        return []

def main(output_file):
    cwd = Path('.').absolute()
    with open(output_file, 'w', encoding='utf-8') as f:
        # Walk through the entire workspace starting from the directory where this script is run
        # or specifically the root of the project. Assuming we run from workspace root.
        # To make it robust, we'll use the parent of KoreCommon/FunctionList as the base.
        base_dir = Path(__file__).resolve().parents[2]
        
        for root, _, files in os.walk(base_dir):
            for file in files:
                if file.endswith('.py'):
                    full_path = Path(os.path.join(root, file)).absolute()
                    
                    # Skip the script itself and the output file
                    if file == 'generate_function_list.py' or file == 'FunctionList.md':
                        continue
                        
                    # Use relative path from the workspace root
                    try:
                        rel_path_str = full_path.relative_to(base_dir).as_posix()
                    except ValueError:
                        rel_path_str = str(full_path.relative_to(full_path.drive))

                    funcs = get_functions(full_path)
                    
                    f.write(f'### {rel_path_str}\n')
                    if funcs:
                        for func in funcs:
                            f.write(f'- {func}\n')
                    f.write('\n')

if __name__ == "__main__":
    # The script is in KoreCommon/FunctionList/
    # We want the output in KoreCommon/FunctionList/FunctionList.md
    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / 'FunctionList.md'
    main(output_path)
