"""FastAPI delivery surface.

Imports are lazy: ``fastapi`` is an optional dependency. Users installing
the MCP-only package don't need it. Pulling the web app in unconditionally
would force every install to drag fastapi/uvicorn, which is the wrong
default while the MCP server is still the primary entry point.
"""

from __future__ import annotations

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    if name in {"app", "create_app"}:
        from .app import app, create_app

        return {"app": app, "create_app": create_app}[name]
    raise AttributeError(name)
