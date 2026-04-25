"""Bundled scenario YAMLs — copied to ./scenarios/ on first run.

The package is intentionally empty of code; ``importlib.resources``
walks it as a data package and the CLI's bootstrap routine extracts
each ``*.yaml`` to disk so the operator can hand-edit them without
unpacking the exe.
"""
