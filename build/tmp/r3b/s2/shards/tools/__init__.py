"""FLAME engine package.

Deliberately empty of imports: ``tools.flame_engine`` is the module the console
script addresses, and importing it here would make every ``import tools`` pay the
engine's load cost (and would break nothing loudly enough to notice if it failed).

This file exists so ``flame-engine = "tools.flame_engine:main"`` resolves after
``pip install .``. Without it ``tools`` is a script directory, not a package, and
the entry point is dead.
"""
