"""Entry point for running LUCID as a module (python -m lucid).

Note: Remote display configuration (for VNC/X11 forwarding) is handled
in lucid.main._configure_remote_display() which runs before Qt imports.
"""

from lucid.main import main

if __name__ == "__main__":
    main()
