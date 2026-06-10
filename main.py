"""
Entry point for Railway — runs from project root.
"""
import sys
import os

# Ensure the project root is in the path so 'src' is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from src.bot import run

if __name__ == "__main__":
    asyncio.run(run())
