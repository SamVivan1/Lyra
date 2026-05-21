#!/usr/bin/env python3
"""Compatibility wrapper for the Lyra interactive CLI."""

from src.lyra.cli.interactive import InteractiveDownloader

if __name__ == "__main__":
    InteractiveDownloader().run()
