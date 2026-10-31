import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(APP_ROOT, "src")
if os.path.isdir(SRC_PATH) and SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from cmdai.cli import main

if __name__ == "__main__":
    main()
