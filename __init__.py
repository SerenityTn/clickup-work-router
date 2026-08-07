"""Hermes plugin entrypoint.

The v1 plugin contributes no agent tools or hooks. Its enabled state gates the
shared dashboard/Desktop backend API at gateway startup.
"""


def register(ctx):
    """Register no agent-facing capabilities; the dashboard API is separate."""
    del ctx
