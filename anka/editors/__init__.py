"""Editor modules. Importing this package self-registers every module via decorators.

The mod-editor window only needs ``EditorRegistry.all()`` after this import; it never
references concrete editor classes, keeping the architecture open for extension.
"""
from .base import EditorModule, EditorRegistry, EditorServices

# Importing the modules triggers @EditorRegistry.register side-effects.
from .countries import CountriesEditor  # noqa: F401,E402
from .focuses import FocusesEditor  # noqa: F401,E402
from .decisions import DecisionsEditor  # noqa: F401,E402
from .events import EventsEditor  # noqa: F401,E402
from .characters import CharactersEditor  # noqa: F401,E402
from . import _stubs  # noqa: F401,E402

__all__ = ["EditorModule", "EditorRegistry", "EditorServices"]
