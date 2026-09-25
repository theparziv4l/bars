"""Core business logic package.

Re-exports are lazy (PEP 562): importing any ``app.core.*`` submodule
executes this file, so eager imports here would drag the orchestrator (and
its model imports) into every ``app.core`` import — an order-dependent
circular import once models import from ``app.core.utils``.
"""

__all__ = [
    "TaskOrchestrator",
    "OutputParser",
    "TemplateEngine",
    "NotificationManager",
]

_LAZY_EXPORTS = {
    "TaskOrchestrator": "app.core.orchestrator",
    "OutputParser": "app.core.parser",
    "TemplateEngine": "app.core.templating",
    "NotificationManager": "app.core.notifications",
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
