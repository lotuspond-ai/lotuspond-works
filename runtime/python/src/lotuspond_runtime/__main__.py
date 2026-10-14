"""Explicit module entrypoint; package imports remain inert."""

from .cli import main

raise SystemExit(main())
