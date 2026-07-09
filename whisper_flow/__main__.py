"""Entry point: `python -m whisper_flow ...`"""

try:
    from .cli import main
except ImportError:  # PyInstaller executes this file without package context.
    from whisper_flow.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
