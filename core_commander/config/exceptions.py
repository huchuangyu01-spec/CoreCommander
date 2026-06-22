# -*- coding: utf-8 -*-

class CoreCommanderException(Exception):
    """Base exception class for Core Commander."""
    pass

class PrivilegeElevationError(CoreCommanderException):
    """Raised when administrative privileges are required but missing or fail to elevate."""
    pass

class ConfigurationError(CoreCommanderException):
    """Raised when configuration validation or settings parse fails."""
    pass

class TopologyQueryError(CoreCommanderException):
    """Raised when CPU topology information cannot be queried from system APIs."""
    pass

class ProcessAccessDeniedError(CoreCommanderException):
    """Raised when a process affinity or priority adjustment is blocked by system permissions."""
    pass

class ServiceOperationError(CoreCommanderException):
    """Raised when underlying system services (Memory/Power) fail their operations."""
    pass
