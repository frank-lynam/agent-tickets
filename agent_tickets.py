#!/usr/bin/env python3
"""agent-tickets is now hearthboard (hearthboard.py). This file keeps the old name working:
services and scripts that run agent_tickets.py, or import it, get hearthboard. Data, tokens
and backups are unchanged. `python3 hearthboard.py service add` moves the service to the new name."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hearthboard import *  # noqa: E402,F401,F403
from hearthboard import cli  # noqa: E402

if __name__ == "__main__":
    cli()
