# scanner.py

import os
import pyperclip
import sys

# --- CONFIGURATION ---

# Add or remove file extensions you want to scan.
# This list covers most common development files.
TARGET_EXTENSIONS = (
    '.py', '.pyw', '.java', '.c', '.h', '.cpp', '.hpp', '.cs', '.go', '.rs',
    '.js', '.ts', '.jsx', '.tsx', '.vue',
    '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.php', '.rb', '.swift', '.kt', '.kts',
    '.json', '.xml', '.yml', '.yaml', '.toml', '.ini', '.cfg',
    '.md', '.txt', '.rtf',
    '.sql', '.sh', '.bat', '.ps1',
    '.env'
)

# Add or remove specific filenames you want to capture (even if they don't have an extension).
EXACT_FILENAMES = (
    'Dockerfile', 'docker-compose.yml', 'Makefile', 'Jenkinsfile', 'requirements.txt',
    '.gitignore', '.dockerignore', '.editorconfig', 'package.json', 'README'
)

# --- SCRIPT LOGIC ---

def generate_project_snapshot():
    """
    Scans the execution directory, creates a structured project snapshot,
    and returns it as a single string.
    """
    try:
        # Get the directory where the script is located
        script_path = os.path.abspath(__file__)
        root_dir = os.path.dirname(script_path)
        script_filename = os.path.basename(script_path)

        output_lines = []
        files_to_read = []
        
        # --- 1. Build the Directory Tree ---
        tree_header = "PROJECT SCAN SNAPSHOT"
        output_lines.append(tree_header)
        output_lines.append("=" * len(tree_header))
        output_lines.append("\nDIRECTORY STRUCTURE\n-------------------\n")

        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
            # Exclude virtual environments and other common ignored directories
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__', 'node_modules', '.git', '.vscode', '.idea', 'venv', '.env')]
            
            # Calculate depth for indentation
            relative_path = os.path.relpath(dirpath, root_dir)
            if relative_path == ".":
                level = 0
                output_lines.append(f"{os.path.basename(root_dir)}/")
            else:
                level = len(relative_path.split(os.sep))
                output_lines.append(f"{'|   ' * level}|-- {os.path.basename(dirpath)}/")

            indent = '|   ' * (level + 1)
            
            # Combine and sort filenames and dirnames for consistent ordering
            display_items = sorted(filenames)

            for i, filename in enumerate(display_items):
                # Skip the scanner script itself
                if os.path.join(dirpath, filename) == script_path:
                    continue
                
                is_last_item = (i == len(display_items) - 1)
                prefix = "|-- "
                
                # Check if the file should be included
                is_target_file = filename.endswith(TARGET_EXTENSIONS) or filename in EXACT_FILENAMES
                if is_target_file:
                    files_to_read.append(os.path.join(dirpath, filename))
                    output_lines.append(f"{indent}{prefix}{filename}")


        # --- 2. Add File Contents ---
        output_lines.append("\n\n========================================\n")
        output_lines.append("FILE CONTENTS\n-------------------\n")

        # Sort files for consistent reading order
        files_to_read.sort()
        
        for file_path in files_to_read:
            relative_file_path = os.path.relpath(file_path, root_dir)
            output_lines.append(f"\n---------- CONTENT OF: {relative_file_path.replace(os.sep, '/')} ----------\n")
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    output_lines.append(content)
            except Exception as e:
                output_lines.append(f"!!! Could not read file: {e} !!!")
        
        output_lines.append("\n--- END OF SNAPSHOT ---")
        return "\n".join(output_lines)

    except Exception as e:
        return f"An error occurred during scanning: {e}"

def main():
    """
    Main function to generate snapshot and copy to clipboard.
    """
    print("Scanning project structure and files...")
    
    project_snapshot = generate_project_snapshot()
    
    try:
        pyperclip.copy(project_snapshot)
        print("\n✅ Project snapshot successfully generated and copied to clipboard!")
        print("You can now paste it wherever you need.")
    except pyperclip.PyperclipException as e:
        print("\n❌ Error: Could not copy to clipboard.")
        print("Please ensure you have a clipboard tool installed and configured.")
        print("On Linux, you might need to install 'xclip' or 'xsel'.")
        print(f"Details: {e}")
        # As a fallback, save to a file
        output_filename = "project_snapshot.txt"
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(project_snapshot)
        print(f"\n📋 Fallback: The snapshot has been saved to '{output_filename}' in the current directory.")

if __name__ == "__main__":
    main()