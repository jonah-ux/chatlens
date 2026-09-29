"""Allow ``python -m chatlens`` to use the same console entry point."""
from .cli import main

raise SystemExit(main())
