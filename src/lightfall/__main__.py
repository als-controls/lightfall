"""Entry point for running LUCID as a module (python -m lightfall).

Note: Remote display configuration (for VNC/X11 forwarding) is handled
in lightfall.main._configure_remote_display() which runs before Qt imports.
"""

from lightfall.main import main

if __name__ == "__main__":
    main()
