"""Editor modules. Importing this package self-registers every module via decorators.

The mod-editor window only needs ``EditorRegistry.all()`` after this import; it never
references concrete editor classes, keeping the architecture open for extension.
"""
from .base import EditorModule, EditorRegistry, EditorServices

# Importing the modules triggers @EditorRegistry.register side-effects.
from .general import GeneralEditor  # noqa: F401,E402
from .countries import CountriesEditor  # noqa: F401,E402
from .focuses import FocusesEditor  # noqa: F401,E402
from .decisions import DecisionsEditor  # noqa: F401,E402
from .events import EventsEditor  # noqa: F401,E402
from .characters import CharactersEditor  # noqa: F401,E402
from .traits import TraitsEditor  # noqa: F401,E402
from .ideas import IdeasEditor  # noqa: F401,E402
from .oob import OobEditor  # noqa: F401,E402
from .map import MapEditor  # noqa: F401,E402
from .on_actions import OnActionsEditor  # noqa: F401,E402
from .interface import InterfaceEditor  # noqa: F401,E402
from .dynamic_modifiers import DynamicModifiersEditor  # noqa: F401,E402
from .ideologies import IdeologiesEditor  # noqa: F401,E402
from . import _stubs  # noqa: F401,E402

__all__ = ["EditorModule", "EditorRegistry", "EditorServices"]
