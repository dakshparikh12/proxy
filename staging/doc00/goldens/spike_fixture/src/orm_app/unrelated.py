"""A module that does NOT depend on models — must be absent from the blast radius."""
import os


def cwd() -> str:
    return os.getcwd()
