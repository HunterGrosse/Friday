"""
Zoey's Mac app: a Dock icon (and menu-bar "Z", when there's room) that runs
her voice mode from this folder.

    ./make_app.sh          builds ~/Applications/Zoey.app (once)
    open -a Zoey           or launch it from Spotlight

  Click her Dock icon     talk (turns her on if she's off)
  Right-click it          Talk, Turn off/on, Edit personality, Show log, Restart, Quit
  Badge on the icon       🎙 listening, … thinking, 💬 talking, "off"

The app is only a remote control. Zoey herself still runs from the files in
this folder, so you can keep editing her while she runs:

  personality.md     picked up at the start of her next conversation
  *.py and .env      she restarts herself as soon as she's idle
"""

import os
import queue
import signal
import subprocess
import threading
from pathlib import Path

import objc
import rumps
from AppKit import (
    NSApplication, NSAttributedString, NSBezierPath, NSColor, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSImage, NSMakeRect,
    NSMenu, NSMenuItem, NSObject, NSTerminateNow,
)

DIR = Path(__file__).resolve().parent
PYTHON = DIR / ".venv" / "bin" / "python"
LOG = Path.home() / "Library" / "Logs" / "Zoey" / "zoey.log"

# Changing any of these needs a restart (personality.md doesn't).
CODE_FILES = ["zoey.py", "tools.py", "memory.py", "voice.py", ".env"]

# What she prints -> (status, Dock badge). The last one seen in a chunk wins.
MARKERS = {
    "[press Enter and talk]": ("Ready: click to talk", ""),
    "[sleeping":              ('Asleep: say "Hey Zoey"', ""),
    "[listening...]":         ("Listening...", "🎙"),
    "\nYou: ":                ("Thinking...", "…"),
    "Zoey: ":                 ("Talking...", "💬"),
}
IDLE = {"Ready: click to talk", 'Asleep: say "Hey Zoey"'}


def code_version() -> list[float]:
    return [(DIR / f).stat().st_mtime if (DIR / f).exists() else 0 for f in CODE_FILES]


def zoey_icon() -> NSImage:
    """A pink rounded square with a white Z."""
    size = 512
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.92, 0.30, 0.55, 1).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(40, 40, 432, 432), 96, 96).fill()
    text = NSAttributedString.alloc().initWithString_attributes_("Z", {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(300),
        NSForegroundColorAttributeName: NSColor.whiteColor(),
    })
    w, h = text.size()
    text.drawAtPoint_(((size - w) / 2, (size - h) / 2))
    image.unlockFocus()
    return image


class ZoeyApp(rumps.App):
    def __init__(self):
        super().__init__("Zoey", title="Z", quit_button=None)
        self.status = rumps.MenuItem("Starting...")
        self.toggle_item = rumps.MenuItem("Turn off", callback=self.toggle)
        self.reload_item = rumps.MenuItem("Restart", callback=lambda _: self.restart())
        self.menu = [
            self.status,
            None,
            rumps.MenuItem("Talk", callback=self.talk),
            self.toggle_item,
            None,
            rumps.MenuItem("Edit personality", callback=self.edit_personality),
            rumps.MenuItem("Open Zoey folder", callback=lambda _: self.open(DIR)),
            rumps.MenuItem("Show log", callback=self.show_log),
            self.reload_item,
            None,
            rumps.MenuItem("Quit Zoey", callback=self.quit),
        ]
        self.proc = None
        self.on = False
        self.stopping = False             # True while we're stopping her on purpose
        self.updates = queue.Queue()      # status changes from the reader thread
        self.version = code_version()
        # She starts OFF: opening the app just puts her in the Dock. Clicking
        # her (or Turn on) wakes her up, and that's when she says hi.
        self.toggle_item.title = "Turn on"
        self.set_status("Off: click to turn on", "off")
        # UI changes must happen on the main thread, so timers apply them.
        rumps.Timer(self.tick, 0.25).start()
        rumps.Timer(self.install_dock, 0.5).start()
        # Logging out or `kill` sends SIGTERM: stop her properly (recap first).
        signal.signal(signal.SIGTERM, lambda *_: self.quit(None))

    # -- Zoey's process ------------------------------------------------------
    def start(self, greet: bool = True) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        self.version = code_version()
        self.reload_item.title = "Restart"
        self.stopping = False
        self.on = True
        self.toggle_item.title = "Turn off"
        self.set_status("Starting...", "")
        self.proc = subprocess.Popen(
            [str(PYTHON), "-u", "zoey.py", "--voice"], cwd=DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "ZOEY_APP": "1", **({} if greet else {"ZOEY_NO_GREETING": "1"})},
        )
        threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()

    def stop(self) -> None:
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.set_status("Saving notes...", "")
            self.proc.send_signal(signal.SIGTERM)   # she writes her recap first
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self) -> None:
        if not self.on:
            return self.start()   # Restart while off just turns her on
        self.stop()
        self.start(greet=False)   # a reload shouldn't make her pipe up again

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
            self.updates.put((f"Stopped (exit {code}): see Show log", "!"))

    # -- controls --------------------------------------------------------------
    def set_status(self, status: str, badge: str) -> None:
        self.status.title = status
        self.title = f"Z {badge}".strip()
        NSApplication.sharedApplication().dockTile().setBadgeLabel_(badge)

    def tick(self, _) -> None:
        while not self.updates.empty():
            self.set_status(*self.updates.get())
        if self.on and code_version() != self.version:
            if self.status.title in IDLE or self.proc.poll() is not None:
                rumps.notification("Zoey", "", "Reloaded with your changes.")
                self.restart()
            else:
                self.reload_item.title = "Restart (changes waiting) ●"

    def talk(self, _=None) -> None:
        if not self.on or self.proc.poll() is not None:
            self.start()        # clicking her while she's off turns her on
            return
        self.proc.stdin.write(b"\n")
        self.proc.stdin.flush()

    def toggle(self, _=None) -> None:
        if self.on:
            self.stop()
            self.on = False
            self.toggle_item.title = "Turn on"
            self.set_status("Off: click to turn on", "off")
        else:
            self.start()

    def edit_personality(self, _=None) -> None:
        self.open("-e", DIR / "personality.md")

    def show_log(self, _=None) -> None:
        self.open(LOG)

    @staticmethod
    def open(*args) -> None:
        subprocess.run(["open", *map(str, args)])

    def quit(self, _=None) -> None:
        self.stop()
        rumps.quit_application()

    # -- Dock icon -------------------------------------------------------------
    def install_dock(self, timer) -> None:
        # Runs once, after rumps has finished starting up.
        timer.stop()
        nsapp = NSApplication.sharedApplication()
        nsapp.setApplicationIconImage_(zoey_icon())
        self.dock = DockControls.alloc().initWithApp_(self)
        nsapp.setDelegate_(self.dock)


class DockControls(NSObject):
    """Answers the Dock: a click means talk, a right-click shows the menu."""

    def initWithApp_(self, app):
        self = objc.super(DockControls, self).init()
        self.app = app
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _nsapp, _flag):
        self.app.talk()
        return False

    def applicationDockMenu_(self, _nsapp):
        menu = NSMenu.alloc().init()
        items = [("Talk", "talk:"),
                 ("Turn off" if self.app.on else "Turn on", "toggle:"),
                 None,
                 ("Edit personality", "personality:"),
                 ("Show log", "log:"),
                 ("Restart", "restart:")]
        for entry in items:
            if entry is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry[0], entry[1], "")
            item.setTarget_(self)
            menu.addItem_(item)
        return menu   # macOS adds Quit on its own

    def talk_(self, _):
        self.app.talk()

    def toggle_(self, _):
        self.app.toggle()

    def personality_(self, _):
        self.app.edit_personality()

    def log_(self, _):
        self.app.show_log()

    def restart_(self, _):
        self.app.restart()

    def applicationShouldTerminate_(self, _nsapp):
        self.app.stop()          # Quit from the Dock: she still writes her recap
        return NSTerminateNow


if __name__ == "__main__":
    ZoeyApp().run()
