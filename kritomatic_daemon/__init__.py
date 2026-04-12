from .kritomatic_daemon import KritomaticDaemon

Krita.instance().addExtension(KritomaticDaemon(Krita.instance()))
