import os
import json
import asyncio
import threading
import websockets
IDE_LOCK_FILE = os.path.expanduser("~/.cmdai_code/ide.lock")
class IDEServer:
    def __init__(self):
        self.port = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._start_server, daemon=True)
        self.clients = set()
        self.active_context = {}
        
    def start(self):
        self.thread.start()
        
    def _start_server(self):
        asyncio.set_event_loop(self.loop)
        
        async def run():
            async with websockets.serve(self._handler, "localhost", 0) as server:
                self.server = server
                self.port = server.sockets[0].getsockname()[1]
                self._write_lock()
                await asyncio.Future()               
                
        try:
            self.loop.run_until_complete(run())
        finally:
            self._cleanup_lock()
            
    def _write_lock(self):
        os.makedirs(os.path.dirname(IDE_LOCK_FILE), exist_ok=True)
        with open(IDE_LOCK_FILE, "w") as f:
            json.dump({
                "port": self.port,
                "cwd": os.getcwd(),
                "pid": os.getpid()
            }, f)
            
    def _cleanup_lock(self):
        if os.path.exists(IDE_LOCK_FILE):
            os.remove(IDE_LOCK_FILE)
            
    async def _handler(self, websocket):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get("type") == "context_update":
                    self.active_context = data.get("payload", {})
        finally:
            self.clients.remove(websocket)
            
    def get_ide_context(self) -> str:
        if not self.active_context:
            return ""
        return f"IDE Active File: {self.active_context.get('active_file', 'None')}\nIDE Selection: {self.active_context.get('selection', 'None')}"
