"""On-screen status HUD for the harvest agent.

A small borderless always-on-top window that mirrors the agent's live status from C:\\ib\\hud.json
(written atomically by the agent's _status()). Shows what it's doing, what it's targeting, and the
running harvest count. Runs in session 1 (the interactive desktop) via the 'ibhud' scheduled task
so it floats over the game.

Opsec: borderless => no title bar / no taskbar entry. Draggable. Esc or right-click quits.
"""
import json
import os
import time
import tkinter as tk

HUD = r"C:\ib\hud.json"
STOP = r"C:\ib\STOP"
POLL_MS = 400
STALE_S = 30.0           # harvest+wait cycles can go ~15s between status writes; only call it idle
                         # after this long so the HUD doesn't flicker to "idle" while it's working

# state -> accent colour
COLORS = {
    "combat": "#ff4d4d", "flee": "#ff4d4d",
    "harvest": "#46d369", "settl": "#46d369", "deplete": "#46d369",
    "node": "#4db8ff", "travel": "#4db8ff", "to nearest": "#4db8ff",
    "idle": "#888888", "stop": "#ffb347", "finish": "#c9a0ff",
}


def accent(status: str) -> str:
    s = (status or "").lower()
    for key, col in COLORS.items():
        if key in s:
            return col
    return "#cccccc"


class HUD_App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        root.configure(bg="#0d0d12")
        # place top-right
        sw = root.winfo_screenwidth()
        self.W, self.Hh = 320, 150
        root.geometry(f"{self.W}x{self.Hh}+{sw - self.W - 16}+16")

        bar = tk.Frame(root, bg="#0d0d12")
        bar.pack(fill="both", expand=True, padx=10, pady=8)

        self.dot = tk.Label(bar, text="●", fg="#46d369", bg="#0d0d12", font=("Consolas", 11))
        self.dot.grid(row=0, column=0, sticky="w")
        self.title = tk.Label(bar, text="ib · harvest", fg="#e6e6e6", bg="#0d0d12",
                              font=("Consolas", 11, "bold"))
        self.title.grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.l_state = tk.Label(bar, text="—", fg="#cccccc", bg="#0d0d12",
                                font=("Consolas", 13, "bold"), anchor="w")
        self.l_state.grid(row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.l_node = tk.Label(bar, text="", fg="#9fd6ff", bg="#0d0d12",
                               font=("Consolas", 10), anchor="w")
        self.l_node.grid(row=2, column=0, columnspan=2, sticky="we")
        self.l_tgt = tk.Label(bar, text="", fg="#9a9aa6", bg="#0d0d12",
                              font=("Consolas", 9), anchor="w")
        self.l_tgt.grid(row=3, column=0, columnspan=2, sticky="we")
        self.l_count = tk.Label(bar, text="harvested: 0", fg="#46d369", bg="#0d0d12",
                                font=("Consolas", 12, "bold"), anchor="w")
        self.l_count.grid(row=4, column=0, columnspan=2, sticky="we", pady=(6, 0))
        bar.columnconfigure(1, weight=1)

        # drag to move
        for w in (root, bar, self.title, self.l_state):
            w.bind("<Button-1>", self._press)
            w.bind("<B1-Motion>", self._drag)
        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<Button-3>", lambda e: root.destroy())
        self._blink = True
        self.tick()

    def _press(self, e):
        self._ox, self._oy = e.x, e.y

    def _drag(self, e):
        self.root.geometry(f"+{self.root.winfo_pointerx() - self._ox}+{self.root.winfo_pointery() - self._oy}")

    def tick(self):
        try:                                  # RE-ASSERT topmost every tick — EQ2 going fullscreen
            self.root.attributes("-topmost", True)   # / grabbing foreground can bury a one-shot
            self.root.lift()                          # topmost; keep clawing back above it
        except tk.TclError:
            pass
        d, age = self._read()
        self._blink = not self._blink
        if d is None or age > STALE_S:
            self.dot.configure(fg="#666")
            self.l_state.configure(text="idle / stopped", fg="#888")
            self.l_node.configure(text="")
            self.l_tgt.configure(text="(no live status)")
            if os.path.exists(STOP):
                self.l_state.configure(text="STOP requested", fg="#ffb347")
        else:
            status = str(d.get("status") or d.get("err") or d.get("mode") or "running")
            col = accent(status)
            self.dot.configure(fg=col if self._blink else "#333")
            self.l_state.configure(text=status, fg=col)
            ev = d.get("events") or []
            last = (d.get("named_nodes") or [{}])[-1].get("name") if d.get("named_nodes") else None
            self.l_node.configure(text=(f"node: {last}" if last else
                                        (f"target node" if d.get("target") else "")))
            t = d.get("target")
            extra = f"  lap {d.get('lap', '?')}/{d.get('anchors', '?')}wp" if d.get("mode") == "gather_loop" else ""
            self.l_tgt.configure(text=(f"@ {t[0]:.0f}, {t[1]:.0f}{extra}" if t else extra.strip()))
            n = d.get("harvests_total", 0)
            fl = d.get("fled")
            self.l_count.configure(text=f"harvested: {n}" + (f"   fled: {fl}" if fl else ""))
        self.root.after(POLL_MS, self.tick)

    def _read(self):
        try:
            age = time.time() - os.path.getmtime(HUD)
            with open(HUD) as f:
                return json.load(f), age
        except Exception:
            return None, 1e9


def main():
    root = tk.Tk()
    HUD_App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
