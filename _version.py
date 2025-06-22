"""Version information for Trading Signal Processor."""

__version__ = "1.1.0"
__title__ = "Trading Signal Processor"
__description__ = "Microservice for filtering trading signals by Finviz ranking and forwarding to webhooks"
__author__ = "Fabio Miguel"
__license__ = "MIT"
__url__ = "https://github.com/fabiomigueldp/Trading-Signal-Processor"

# Version history
VERSION_HISTORY = {
    "1.1.0": {
        "date": "2025-06-06",
        "description": "Enhanced robust signal processing with improved webhook delivery control",
        "breaking_changes": False,
        "highlights": [
            "Robust signal input validation and processing",
            "Enhanced webhook delivery control with retry mechanisms",
            "Improved rate limiting for webhook destinations",
            "Better error handling and recovery",
            "Enhanced logging and monitoring capabilities"
        ]
    },
    "1.0.0": {
        "date": "2025-06-04",
        "description": "Initial release - Production-ready trading signal processor with Finviz integration",
        "breaking_changes": False,
        "highlights": [
            "High-performance signal filtering (100+ signals/second)",
            "Finviz Elite integration with automatic rate limiting",
            "Real-time WebSocket-powered admin panel",
            "Dynamic configuration management",
            "Token-based security for config updates",
            "Comprehensive monitoring and logging",
            "Docker containerization for production deployment"
        ]
    }
}

# Feature flags for current version
FEATURES = {
    "rate_limiting": True,
    "admin_panel": True,
    "websocket_updates": True,
    "dynamic_config": True,
    "token_auth": True,
    "health_checks": True,
    "retry_logic": True,
    "comprehensive_logging": True,
    "finviz_elite_support": True
}

# API version
API_VERSION = "v1"