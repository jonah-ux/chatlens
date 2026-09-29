"""Stable import surface for the installed console script."""
from .cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
