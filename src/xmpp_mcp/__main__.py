"""Allow `python -m xmpp_mcp` and serve as the PyInstaller entry point."""

from xmpp_mcp.server import main

if __name__ == "__main__":
    main()
