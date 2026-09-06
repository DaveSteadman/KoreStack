# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Generates the repository-wide Python function inventory in FunctionList.md.
#
# Public API:
#   - list_function_names() -- extracts synchronous and asynchronous function names from one file.
#   - main()                -- walks the repository and writes the Markdown inventory.
#
# The repository root is derived from this script's location, so the generator is safe to run from
# any working directory. Virtual environments are deliberately excluded from the inventory.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import ast
import os
from pathlib import Path

# ====================================================================================================
# MARK: AST EXTRACTION (PUBLIC)
# ====================================================================================================
def list_function_names(file_path):
    """Return the synchronous and asynchronous function names in one source file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        return [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        ]
    except Exception:
        return []

# ====================================================================================================
# MARK: INVENTORY GENERATION (PUBLIC)
# ====================================================================================================
def main(output_file):
    # The generator can be launched from any working directory.  Its location
    # is stable: <project-root>/KoreCommon/FunctionList/generate_function_list.py.
    base_dir = Path(__file__).resolve().parents[2]
    with open(output_file, 'w', encoding='utf-8') as f:
        for root, directories, files in os.walk(base_dir):
            # Do not index the active Python environment as project source.
            directories[:] = [
                directory
                for directory in directories
                if directory.casefold() != '.venv'
            ]
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

                    funcs = list_function_names(full_path)
                    
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
