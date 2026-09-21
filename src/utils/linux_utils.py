"""Linux-specific utilities for Wine/MT5"""

import os
import subprocess
from pathlib import Path

def check_mt5_running():
    """Check if MT5 is running under Wine"""
    try:
        result = subprocess.run(['pgrep', '-f', 'terminal.exe'], 
                              capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def get_mt5_path(wine_prefix=None):
    """Get MT5 executable path"""
    if wine_prefix is None:
        wine_prefix = os.path.expanduser("~/.wine")
    
    possible_paths = [
        f"{wine_prefix}/drive_c/Program Files/MetaTrader 5/terminal.exe",
        f"{wine_prefix}/drive_c/Program Files (x86)/MetaTrader 5/terminal.exe"
    ]
    
    for path in possible_paths:
        if Path(path).exists():
            return path
    return None
