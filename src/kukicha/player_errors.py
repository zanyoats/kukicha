from __future__ import annotations

class PlayerConfigError(ValueError):
    """Raised when player configuration cannot be loaded."""

class PlayerNotFoundError(ValueError):
    """Raised when a requested player entity does not exist."""

class PlayerConflictError(ValueError):
    """Raised when a player action cannot be started because it is already running."""
