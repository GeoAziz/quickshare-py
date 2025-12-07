"""Compatibility shim so `import ui` resolves in tests and editors.

This re-exports the main `scripts.ui` module symbols so Pylance (and other
static checkers) can find `ui` when tests do `from ui import ...`.
"""

# Try to import from the package-style path first, then fallback to scripts.ui
try:
    from scripts.ui import *  # noqa: F401,F403
except Exception:
    # If running in an environment where scripts isn't a package on sys.path,
    # attempt a direct import by file location.
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), 'scripts', 'ui.py')
    spec = importlib.util.spec_from_file_location('scripts.ui', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore
    globals().update({k: getattr(module, k) for k in dir(module) if not k.startswith('_')})
