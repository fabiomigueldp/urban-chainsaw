#!/usr/bin/env python3
"""
Wrapper to run main.py with maximum permissions.
"""

import os
import sys
import stat
import subprocess

def setup_permissions():
    """Sets up maximum permissions for all necessary files."""
    try:
        # Set maximum permissions for the application directory
        os.chmod('/app', 0o777)
        
        # Create configuration files with maximum permissions if they don't exist
        config_files = [
            '/app/finviz_config.json',
            '/app/webhook_config.json',
            '/app/system_config.json'
        ]
        
        for config_file in config_files:
            if not os.path.exists(config_file):
                with open(config_file, 'w') as f:
                    f.write('{}')
            os.chmod(config_file, 0o666)
        
        # Set permissions for data directories
        for directory in ['/app/logs', '/app/data', '/app/database']:
            if os.path.exists(directory):
                os.chmod(directory, 0o777)
                # Set permissions for all files in the directory
                for root, dirs, files in os.walk(directory):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), 0o777)
                    for f in files:
                        os.chmod(os.path.join(root, f), 0o666)
        
        # Set permissions for Python files
        for file in os.listdir('/app'):
            if file.endswith(('.py', '.json', '.log', '.txt')):
                file_path = os.path.join('/app', file)
                if os.path.isfile(file_path):
                    os.chmod(file_path, 0o666)
        
        print("✅ Permissões máximas configuradas com sucesso!")
        
    except Exception as e:
        print(f"⚠️  Warning: Could not set some permissions: {e}")
        # Continue even with permission errors

def main():
    """Main function that sets up permissions and runs the application."""
    print("🔧 Setting up maximum permissions...")
    setup_permissions()
    
    print("🚀 Starting application with maximum permissions...")
    
    # Run the main application
    try:
        # Import and run main normally
        import uvicorn
        from main import app
        
        # Run uvicorn with necessary configurations
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=80,
            access_log=True,
            log_level="info"
        )
        
    except Exception as e:
        print(f"❌ Error running the application: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
