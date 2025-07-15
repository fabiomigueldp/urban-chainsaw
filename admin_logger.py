"""Admin action logging decorator for automatic audit trail."""

import time
import logging
from functools import wraps
from typing import Optional, Dict, Any
from fastapi import Request

_logger = logging.getLogger("admin_logger")

def log_admin_action(action_type: str, action_name: str, target_resource: Optional[str] = None):
    """
    Decorator to automatically log administrative actions.
    
    Args:
        action_type: Type of action (config_update, engine_control, etc.)
        action_name: Specific name of the action
        target_resource: Optional resource being targeted (URL ID, config key, etc.)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            request = None
            admin_token = None
            
            # Extract request and token from arguments
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            # Extract token from payload
            if 'payload' in kwargs and isinstance(kwargs['payload'], dict):
                admin_token = kwargs['payload'].get('token')
            
            # Extract request data
            ip_address = request.client.host if request else None
            user_agent = request.headers.get('user-agent') if request else None
            
            # Resolve target resource if it's a placeholder
            resolved_target = target_resource
            if target_resource and '{' in target_resource:
                # Handle dynamic target resource (e.g., "{url_id}")
                for key, value in kwargs.items():
                    placeholder = f"{{{key}}}"
                    if placeholder in target_resource:
                        resolved_target = target_resource.replace(placeholder, str(value))
                        break
            
            try:
                # Execute original function
                result = await func(*args, **kwargs)
                
                # Calculate execution time
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                # Log success
                if admin_token:
                    from database.DBManager import db_manager
                    await db_manager.log_admin_action(
                        action_type=action_type,
                        action_name=action_name,
                        admin_token=admin_token,  # Direct token without hashing
                        ip_address=ip_address,
                        user_agent=user_agent,
                        target_resource=resolved_target,
                        success=True,
                        execution_time_ms=execution_time_ms
                    )
                    
                    _logger.info(f"Admin action logged: {action_type}/{action_name} - Success")
                
                return result
                
            except Exception as e:
                # Calculate execution time even on error
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                # Log error
                if admin_token:
                    from database.DBManager import db_manager
                    await db_manager.log_admin_action(
                        action_type=action_type,
                        action_name=action_name,
                        admin_token=admin_token,  # Direct token without hashing
                        ip_address=ip_address,
                        user_agent=user_agent,
                        target_resource=resolved_target,
                        success=False,
                        error_message=str(e),
                        execution_time_ms=execution_time_ms
                    )
                    
                    _logger.error(f"Admin action failed: {action_type}/{action_name} - Error: {str(e)}")
                
                raise
                
        return wrapper
    return decorator

def log_admin_action_manual(
    action_type: str,
    action_name: str,
    admin_token: str,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    target_resource: Optional[str] = None,
    error_message: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
):
    """
    Manual admin action logging for cases where decorator is not suitable.
    
    This is an async function that should be awaited.
    """
    async def _log():
        try:
            from database.DBManager import db_manager
            await db_manager.log_admin_action(
                action_type=action_type,
                action_name=action_name,
                admin_token=admin_token,
                ip_address=ip_address,
                user_agent=user_agent,
                details=details,
                target_resource=target_resource,
                success=success,
                error_message=error_message,
                execution_time_ms=execution_time_ms
            )
            
            status = "Success" if success else "Failed"
            _logger.info(f"Manual admin action logged: {action_type}/{action_name} - {status}")
            
        except Exception as e:
            _logger.error(f"Failed to log admin action manually: {e}")
    
    return _log()
