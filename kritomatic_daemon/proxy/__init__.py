"""Supervisor for the comfy-api-proxy child process.

See manager.py for the lifecycle. This package holds no state; the PID
file and log file on disk are the authoritative record, so the module
is safe to drop during reload_daemon.
"""