import asyncio
import os
import sys

editor_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(editor_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)
if editor_dir not in sys.path:
    sys.path.insert(0, editor_dir)

from editor.ui.app import CMDAIEditorApp

async def run_test():
    app = CMDAIEditorApp(project_root)
    async with app.run_test() as pilot:
        print("PASS: CMDAIEditorApp mounted successfully!")
        await pilot.pause()
        print("PASS: Pilot paused successfully!")

if __name__ == "__main__":
    asyncio.run(run_test())
