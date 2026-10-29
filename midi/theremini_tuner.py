"""
Theremini MIDI Tuner
====================
A standalone GUI for sending MIDI Control Change, Program Change, and
Note On/Off messages to a Moog Theremini connected over USB MIDI. Useful
when the Theremini is physically housed somewhere you can't reach its
buttons (e.g., inside a pedestal) and you need to:

  - Send CC values to tweak any parameter the Theremini listens for
  - Trigger Program Change to switch between presets / scales / modes
  - Test the synth voice by sending Note On/Off
  - Try the "Theremin Mode" toggle and quantization parameters

The Theremini receives MIDI on Channel 1 by default. Many parameters are
exposed as CCs but the exact mapping varies by firmware — Moog's official
MIDI implementation chart lists them, but the tool here is intentionally
generic so you can experiment without rebuilding it.

Dependencies (you already have these from the main project):
    pip install mido python-rtmidi

Run:
    python theremini_tuner.py
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import Optional

import mido

# ── Theremini quick-action labels.  CC numbers are *guesses* based on
# common Moog conventions; verify against the Theremini's own MIDI
# implementation chart and edit this table if needed. The tool will still
# function correctly even if a particular CC does nothing on your unit —
# nothing breaks, you just see no audible change.
QUICK_CC_PRESETS = [
    ("Volume",          7,   100),
    ("Modulation",      1,   64),
    ("Expression",      11,  100),
    ("Portamento Time", 5,   40),
    ("Portamento On",   65,  127),
    ("Portamento Off",  65,  0),
    ("Sustain On",      64,  127),
    ("Sustain Off",     64,  0),
    ("All Sound Off",   120, 0),
    ("Reset Controls",  121, 0),
    ("All Notes Off",   123, 0),
]

# Speculative — Moog hasn't published a fully consistent chart, edit freely.
# If a button does nothing, try a nearby CC number.
THEREMINI_FUNCTIONS = [
    ("Theremin Mode ON",      102, 127),
    ("Theremin Mode OFF",     102, 0),
    ("Quantize OFF",          103, 0),
    ("Quantize Chromatic",    103, 64),
    ("Quantize Major",        103, 96),
    ("Scale Root C",          104, 0),
    ("Scale Root A",          104, 9),
    ("Octave Down",           105, 0),
    ("Octave Up",             105, 127),
    ("Wave Sine",             106, 0),
    ("Wave Sawtooth",         106, 64),
    ("Wave Square",           106, 127),
]


class ThereminiTuner:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Theremini MIDI Tuner")
        self.root.geometry("780x740")
        self.root.minsize(680, 640)

        self.port: Optional[mido.ports.BaseOutput] = None
        self.port_name: Optional[str] = None
        self.live_cc: tk.BooleanVar = tk.BooleanVar(value=False)
        self.channel: tk.IntVar = tk.IntVar(value=1)
        self.cc_num: tk.IntVar = tk.IntVar(value=7)
        self.cc_val: tk.IntVar = tk.IntVar(value=64)
        self.pc_num: tk.IntVar = tk.IntVar(value=0)
        self.note_num: tk.IntVar = tk.IntVar(value=60)
        self.note_vel: tk.IntVar = tk.IntVar(value=100)

        self._build_ui()
        self.refresh_ports()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = dict(padx=8, pady=4)

        top = ttk.LabelFrame(self.root, text="MIDI Port")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.port_combo = ttk.Combobox(top, state="readonly", width=50)
        self.port_combo.pack(side="left", **pad)
        ttk.Button(top, text="Refresh", command=self.refresh_ports).pack(side="left", **pad)
        ttk.Button(top, text="Connect", command=self.connect_port).pack(side="left", **pad)
        ttk.Button(top, text="Disconnect", command=self.disconnect_port).pack(side="left", **pad)
        self.status_lbl = ttk.Label(top, text="(disconnected)", foreground="gray")
        self.status_lbl.pack(side="left", **pad)

        chan = ttk.LabelFrame(self.root, text="MIDI Channel (Theremini default = 1)")
        chan.pack(fill="x", padx=10, pady=4)
        ttk.Scale(chan, from_=1, to=16, orient="horizontal",
                  variable=self.channel,
                  command=lambda v: self.channel.set(int(float(v)))).pack(fill="x", padx=8, pady=4)
        ttk.Label(chan, textvariable=self.channel).pack()

        # ── Control Change ────────────────────────────────────────────
        cc = ttk.LabelFrame(self.root, text="Control Change (CC)")
        cc.pack(fill="x", padx=10, pady=4)

        row1 = ttk.Frame(cc); row1.pack(fill="x", padx=8, pady=2)
        ttk.Label(row1, text="CC #:").pack(side="left")
        ttk.Spinbox(row1, from_=0, to=127, textvariable=self.cc_num, width=6).pack(side="left", padx=4)
        ttk.Label(row1, text="Value:").pack(side="left", padx=(20, 0))
        ttk.Spinbox(row1, from_=0, to=127, textvariable=self.cc_val, width=6).pack(side="left", padx=4)
        ttk.Checkbutton(row1, text="Send live as I drag",
                        variable=self.live_cc).pack(side="left", padx=(20, 4))
        ttk.Button(row1, text="Send CC", command=self.send_cc_now).pack(side="right", padx=4)

        self.cc_slider = ttk.Scale(cc, from_=0, to=127, orient="horizontal",
                                    variable=self.cc_val, command=self._on_cc_slider)
        self.cc_slider.pack(fill="x", padx=8, pady=(2, 6))

        # Quick CC presets
        qc = ttk.LabelFrame(self.root, text="Quick CC Presets (click to fire)")
        qc.pack(fill="x", padx=10, pady=4)
        grid_into(qc, QUICK_CC_PRESETS, self._fire_cc, cols=4)

        # Speculative Theremini functions
        tf = ttk.LabelFrame(self.root, text="Theremini Function Buttons (firmware-dependent — edit in source if needed)")
        tf.pack(fill="x", padx=10, pady=4)
        grid_into(tf, THEREMINI_FUNCTIONS, self._fire_cc, cols=4)

        # ── Program Change ─────────────────────────────────────────────
        pc = ttk.LabelFrame(self.root, text="Program Change (preset switch)")
        pc.pack(fill="x", padx=10, pady=4)
        pc_row = ttk.Frame(pc); pc_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(pc_row, text="Program (0–127):").pack(side="left")
        ttk.Spinbox(pc_row, from_=0, to=127, textvariable=self.pc_num, width=6).pack(side="left", padx=4)
        ttk.Button(pc_row, text="Send PC", command=self.send_pc_now).pack(side="left", padx=8)
        for p in (0, 1, 2, 3, 4, 5, 6, 7):
            ttk.Button(pc_row, text=f"PC {p}", width=6,
                       command=lambda pp=p: (self.pc_num.set(pp), self.send_pc_now())).pack(side="left", padx=2)

        # ── Note On/Off ────────────────────────────────────────────────
        nt = ttk.LabelFrame(self.root, text="Note On/Off (test synth voice)")
        nt.pack(fill="x", padx=10, pady=4)
        nt_row = ttk.Frame(nt); nt_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(nt_row, text="Note:").pack(side="left")
        ttk.Spinbox(nt_row, from_=0, to=127, textvariable=self.note_num, width=6).pack(side="left", padx=4)
        ttk.Label(nt_row, text="(60 = middle C)").pack(side="left", padx=4)
        ttk.Label(nt_row, text="Velocity:").pack(side="left", padx=(16, 0))
        ttk.Spinbox(nt_row, from_=0, to=127, textvariable=self.note_vel, width=6).pack(side="left", padx=4)
        ttk.Button(nt_row, text="Note On",  command=self.send_note_on).pack(side="left", padx=8)
        ttk.Button(nt_row, text="Note Off", command=self.send_note_off).pack(side="left", padx=2)

        # ── Log ────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self.root, text="Sent Messages")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log = scrolledtext.ScrolledText(log_frame, height=10, font=("Consolas", 10))
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.configure(state="disabled")

    # ── Port management ───────────────────────────────────────────────
    def refresh_ports(self):
        names = mido.get_output_names()
        self.port_combo["values"] = names
        if names:
            # Auto-pick first port containing "theremini"
            preselect = next((n for n in names if "theremini" in n.lower()), names[0])
            self.port_combo.set(preselect)
        self._log(f"Refreshed — {len(names)} MIDI output port(s) found.")

    def connect_port(self):
        if self.port:
            self.disconnect_port()
        name = self.port_combo.get()
        if not name:
            messagebox.showerror("Theremini Tuner", "No MIDI output selected.")
            return
        try:
            self.port = mido.open_output(name)
            self.port_name = name
            self.status_lbl.configure(text=f"Connected: {name}", foreground="#0a7d29")
            self._log(f"Connected to {name!r}")
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            self._log(f"Connection failed: {e}")

    def disconnect_port(self):
        if self.port:
            try: self.port.close()
            except Exception: pass
            self._log(f"Disconnected from {self.port_name!r}")
        self.port = None
        self.port_name = None
        self.status_lbl.configure(text="(disconnected)", foreground="gray")

    # ── Senders ───────────────────────────────────────────────────────
    def _send(self, msg: mido.Message):
        if not self.port:
            self._log(f"× no port open — {msg}")
            return
        try:
            self.port.send(msg)
            self._log(f"→ {msg}")
        except Exception as e:
            self._log(f"× send failed: {e}")

    def send_cc_now(self):
        msg = mido.Message("control_change",
                           channel=self.channel.get() - 1,
                           control=self.cc_num.get(),
                           value=self.cc_val.get())
        self._send(msg)

    def _on_cc_slider(self, _value):
        if self.live_cc.get():
            self.send_cc_now()

    def _fire_cc(self, label: str, cc: int, val: int):
        self.cc_num.set(cc)
        self.cc_val.set(val)
        self.send_cc_now()
        self._log(f"   preset '{label}'  CC{cc}={val}")

    def send_pc_now(self):
        msg = mido.Message("program_change",
                           channel=self.channel.get() - 1,
                           program=self.pc_num.get())
        self._send(msg)

    def send_note_on(self):
        msg = mido.Message("note_on",
                           channel=self.channel.get() - 1,
                           note=self.note_num.get(),
                           velocity=self.note_vel.get())
        self._send(msg)

    def send_note_off(self):
        msg = mido.Message("note_off",
                           channel=self.channel.get() - 1,
                           note=self.note_num.get(),
                           velocity=0)
        self._send(msg)

    # ── Log helper ─────────────────────────────────────────────────────
    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def grid_into(parent, items, on_click, cols=4):
    """Lay out a list of (label, cc, val) tuples as buttons in a grid."""
    for i, (label, cc, val) in enumerate(items):
        r, c = divmod(i, cols)
        b = ttk.Button(parent, text=label,
                       command=lambda l=label, cc=cc, v=val: on_click(l, cc, v))
        b.grid(row=r, column=c, padx=4, pady=4, sticky="ew")
    for c in range(cols):
        parent.columnconfigure(c, weight=1)


if __name__ == "__main__":
    root = tk.Tk()
    app = ThereminiTuner(root)
    try:
        root.mainloop()
    finally:
        app.disconnect_port()
