"""
Zoey's Mac app: a menu-bar icon that runs her voice mode from this folder.

    ./make_app.sh          builds ~/Applications/Zoey.app (once)
    open -a Zoey           or launch it from Spotlight

The app is only a remote control. Zoey herself still runs from the files in
this folder, so you can keep editing her while she runs:

  personality.md     picked up at the start of her next conversation
  *.py and .env      she restarts herself as soon as she's idle

She starts in push-to-talk: click Talk (or press Enter in her menu), then
speak. With "Hey Zoey" set up in .env, she listens hands-free instead.
"""

import os
import queue
import signal
import subprocess
import threading
from pathlib import Path

import rumps

DIR = Path(__file__).resolve().parent
PYTHON = DIR / ".venv" / "bin" / "python"
LOG = Path.home() / "Library" / "Logs" / "Zoey" / "zoey.log"

# Changing any of these needs a restart (personality.md doesn't).
CODE_FILES = ["zoey.py", "tools.py", "memory.py", "voice.py", ".env"]

# What she prints -> (menu-bar title, status line). The last one seen wins.
MARKERS = {
    "[press Enter and talk]": ("Z", "Ready: click Talk"),
    "[sleeping":              ("Z", 'Asleep: say "Hey Zoey"'),
    "[listening...]":         ("Z 🎙", "Listening..."),
    "\nYou: ":                ("Z …", "Thinking..."),
    "Zoey: ":                 ("Z 💬", "Talking..."),
}
IDLE = {"Ready: click Talk", 'Asleep: say "Hey Zoey"'}


def code_version() -> list[float]:
    return [(DIR / f).stat().st_mtime if (DIR / f).exists() else 0 for f in CODE_FILES]


class ZoeyApp(rumps.App):
    def __init__(self):
        super().__init__("Zoey", title="Z", quit_button=None)
        self.status = rumps.MenuItem("Starting...")
        self.reload_item = rumps.MenuItem("Restart", callback=lambda _: self.restart())
        self.menu = [
            self.status,
            None,
            rumps.MenuItem("Talk", callback=self.talk, key="t"),
            None,
            rumps.MenuItem("Edit personality", callback=lambda _: self.open("-e", DIR / "personality.md")),
            rumps.MenuItem("Open Zoey folder", callback=lambda _: self.open(DIR)),
            rumps.MenuItem("Show log", callback=lambda _: self.open(LOG)),
            self.reload_item,
            None,
            rumps.MenuItem("Quit Zoey", callback=self.quit, key="q"),
        ]
        self.proc = None
        self.stopping = False             # True while we're stopping her on purpose
        self.updates = queue.Queue()      # status changes from the reader thread
        self.version = code_version()
        self.start()
        # UI changes must happen on the main thread, so a timer applies them.
        rumps.Timer(self.tick, 0.25).start()

    # -- Zoey's process ------------------------------------------------------
    def start(self) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        self.version = code_version()
        self.reload_item.title = "Restart"
        self.stopping = False
        self.proc = subprocess.Popen(
            [str(PYTHON), "-u", "zoey.py", "--voice"], cwd=DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "ZOEY_APP": "1"},
        )
        threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()

    def stop(self) -> None:
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.status.title = "Saving notes..."
            self.proc.send_signal(signal.SIGTERM)   # she writes her recap first
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self) -> None:
        self.stop()
        self.start()

    def read_output(self, proc) -> None:
        """Copy her output to the log and turn it into status updates."""
        with LOG.open("ab") as log:
            while chunk := os.read(proc.stdout.fileno(), 4096):
                log.write(chunk)
                log.flush()
                text = chunk.decode(errors="ignore")
                found = [(text.rfind(m), state) for m, state in MARKERS.items() if m in text]
                if found:
                    self.updates.put(max(found)[1])
        code = proc.wait()
        if not self.stopping:   # she quit or crashed on her own
            self.updates.put(("Z ⚠", f"Stopped (exit {code}): see Show log"))

    # -- menu ----------------------------------------------------------------
    def tick(self, _) -> None:
        while not self.updates.empty():
            self.title, self.status.title = self.updates.get()
        if code_version() != self.version:
            if self.status.title in IDLE or self.proc.poll() is not None:
                rumps.notification("Zoey", "", "Reloaded with your changes.")
                self.restart()
            else:
                self.reload_item.title = "Restart (changes waiting) ●"

    def talk(self, _) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(b"\n")
            self.proc.stdin.flush()

    @staticmethod
    def open(*args) -> None:
        subprocess.run(["open", *map(str, args)])

    def quit(self, _) -> None:
        self.stop()
        rumps.quit_application()


if __name__ == "__main__":
    ZoeyApp().run()
