from .kritomatic_daemon import KritomaticDaemon
from .decorators import command
from .registry import get_command_registry, register_command

Krita.instance().addExtension(KritomaticDaemon(Krita.instance()))
