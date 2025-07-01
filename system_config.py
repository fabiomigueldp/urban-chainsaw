"""
System Configuration Management
Handles persistence and loading of system-wide configuration settings
Used by the admin interface for dynamic configuration updates
"""

import json
import logging
import time
from typing import Dict, Any, Optional
from pathlib import Path

_logger = logging.getLogger(__name__)

# Default configuration file path
SYSTEM_CONFIG_FILE = "system_config.json"

def load_system_config() -> Dict[str, Any]:
    """
    Load system configuration from JSON file.
    Returns default values if file doesn't exist or is invalid.
    """
    try:
        config_path = Path(SYSTEM_CONFIG_FILE)
        if not config_path.exists():
            _logger.info(f"{SYSTEM_CONFIG_FILE} not found, creating with defaults")
            default_config = get_default_system_config()
            persist_system_config(default_config)
            return default_config
            
        with open(SYSTEM_CONFIG_FILE, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        # Ensure all required keys exist with defaults
        default_config = get_default_system_config()
        for key, default_value in default_config.items():
            if key not in config_data:
                config_data[key] = default_value
                
        _logger.debug(f"Successfully loaded system config from {SYSTEM_CONFIG_FILE}")
        return config_data
        
    except json.JSONDecodeError as e:
        _logger.error(f"Invalid JSON in {SYSTEM_CONFIG_FILE}: {e}")
        _logger.info("Using default configuration")
        return get_default_system_config()
    except Exception as e:
        _logger.error(f"Error loading system config: {e}")
        _logger.info("Using default configuration")
        return get_default_system_config()

def persist_system_config(config_data: Dict[str, Any]) -> None:
    """
    Persist system configuration to JSON file.
    """
    try:
        # Add timestamp
        config_data["last_updated"] = time.time()
        
        # Ensure directory exists
        config_path = Path(SYSTEM_CONFIG_FILE)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(SYSTEM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
            
        _logger.info(f"System config persisted to {SYSTEM_CONFIG_FILE}")
        
    except Exception as e:
        _logger.error(f"Error persisting system config to {SYSTEM_CONFIG_FILE}: {e}")
        raise

def get_default_system_config() -> Dict[str, Any]:
    """
    Get default system configuration values.
    """
    return {
        "sell_all_cleanup_enabled": True,
        "sell_all_cleanup_lifetime_hours": 72,
        "last_updated": time.time()
    }

def update_system_config_field(field_name: str, value: Any) -> Dict[str, Any]:
    """
    Update a specific field in system configuration.
    Returns the updated configuration.
    """
    config = load_system_config()
    config[field_name] = value
    persist_system_config(config)
    return config

def get_sell_all_cleanup_config() -> Dict[str, Any]:
    """
    Get sell all cleanup configuration.
    """
    config = load_system_config()
    return {
        "enabled": config.get("sell_all_cleanup_enabled", True),
        "lifetime_hours": config.get("sell_all_cleanup_lifetime_hours", 72)
    }

def update_sell_all_cleanup_config(enabled: bool, lifetime_hours: int) -> Dict[str, Any]:
    """
    Update sell all cleanup configuration.
    Returns the updated configuration.
    """
    if lifetime_hours <= 0:
        raise ValueError("Lifetime hours must be positive")
        
    config = load_system_config()
    config["sell_all_cleanup_enabled"] = enabled
    config["sell_all_cleanup_lifetime_hours"] = lifetime_hours
    persist_system_config(config)
    
    _logger.info(f"Sell All cleanup config updated: enabled={enabled}, lifetime_hours={lifetime_hours}")
    
    return {
        "enabled": enabled,
        "lifetime_hours": lifetime_hours
    }
