from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

import mido


DEFAULT_PITCH_CC = 20
DEFAULT_VOLUME_CC = 2


@dataclass
class MidiEvent:
    timestamp: float
    text: str
    controller: int | None = None
    value: int | None = None


def clamp_midi(value: int) -> int:
    return max(0, min(127, int(value)))


def preferred_name(names: list[str], fallback_index: int = 0) -> str:
    for name in names:
        lowered = name.lower()
        if "theremini" in lowered or "moog" in lowered:
            return name
    return names[fallback_index] if names else ""


class MidiWatcher:
    def __init__(self, event_queue: queue.Queue[MidiEvent]) -> None:
        self.event_queue = event_queue
        self.input_name = ""
        self._port: mido.ports.BaseInput | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.controllers: dict[int, int] = {}
        self.controller_seen_at: dict[int, float] = {}

    def start(self, name: str) -> None:
        self.stop()
        self.input_name = name
        if not name:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None

    def snapshot(self) -> list[tuple[int, int, float]]:
        now = time.time()
        with self._lock:
            return sorted(
                (cc, value, now - self.controller_seen_at.get(cc, now))
                for cc, value in self.controllers.items()
            )

    def _loop(self) -> None:
        try:
            self._port = mido.open_input(self.input_name)
            self.event_queue.put(MidiEvent(time.time(), f"Listening on {self.input_name}"))
        except Exception as error:
            self.event_queue.put(MidiEvent(time.time(), f"Input open failed: {error}"))
            return

        while self._running and self._port is not None:
            try:
                for message in self._port.iter_pending():
                    self._handle_message(message)
            except Exception as error:
                self.event_queue.put(MidiEvent(time.time(), f"Input read failed: {error}"))
                break
            time.sleep(0.01)

    def _handle_message(self, message: mido.Message) -> None:
        now = time.time()
        if message.type == "control_change":
            controller = int(message.control)
            value = int(message.value)
            with self._lock:
                self.controllers[controller] = value
                self.controller_seen_at[controller] = now
            self.event_queue.put(MidiEvent(now, f"In  CC {controller:03d} = {value:03d}", controller, value))
            return

        self.event_queue.put(MidiEvent(now, f"In  {message}"))


class ThereminiCalibrator(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Theremini MIDI Calibration Console")
        self.geometry("980x640")
        self.minsize(820, 560)

        self.event_queue: queue.Queue[MidiEvent] = queue.Queue()
        self.watcher = MidiWatcher(self.event_queue)
        self.output_port: mido.ports.BaseOutput | None = None
        self.output_name = tk.StringVar()
        self.input_name = tk.StringVar()
        self.channel = tk.IntVar(value=1)
        self.pitch_cc = tk.IntVar(value=DEFAULT_PITCH_CC)
        self.volume_cc = tk.IntVar(value=DEFAULT_VOLUME_CC)
        self.custom_cc = tk.IntVar(value=0)
        self.custom_value = tk.IntVar(value=0)
        self.status = tk.StringVar(value="Choose ports, then use the sliders to send MIDI CC.")
        self.monitor_rows: dict[int, str] = {}

        self._build()
        self.refresh_ports()
        self.after(50, self._pump_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Input").grid(row=0, column=0, sticky="w")
        self.input_combo = ttk.Combobox(top, textvariable=self.input_name, state="readonly")
        self.input_combo.grid(row=0, column=1, sticky="ew", padx=(8, 16))

        ttk.Label(top, text="Output").grid(row=0, column=2, sticky="w")
        self.output_combo = ttk.Combobox(top, textvariable=self.output_name, state="readonly")
        self.output_combo.grid(row=0, column=3, sticky="ew", padx=(8, 16))

        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(top, text="Connect", command=self.connect_ports).grid(row=0, column=5)

        ttk.Label(top, text="Channel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(top, from_=1, to=16, textvariable=self.channel, width=6).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(top, textvariable=self.status).grid(row=1, column=2, columnspan=4, sticky="w", pady=(10, 0))

        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        left = ttk.Frame(panes, padding=10)
        right = ttk.Frame(panes, padding=10)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(left, text="Live Incoming MIDI").grid(row=0, column=0, sticky="w")
        self.monitor = ttk.Treeview(left, columns=("value", "age"), show="headings", height=10)
        self.monitor.heading("value", text="Value")
        self.monitor.heading("age", text="Age")
        self.monitor.column("value", width=100, anchor="center")
        self.monitor.column("age", width=100, anchor="center")
        self.monitor.grid(row=1, column=0, sticky="nsew", pady=(8, 12))

        self.log = tk.Text(left, height=10, wrap="word")
        self.log.grid(row=2, column=0, sticky="nsew")
        self.log.configure(state="disabled")

        self._cc_slider(right, "Pitch antenna / field range", self.pitch_cc, DEFAULT_PITCH_CC, 0)
        self._cc_slider(right, "Volume antenna / loudness range", self.volume_cc, DEFAULT_VOLUME_CC, 1)

        tools = ttk.LabelFrame(right, text="One-shot CC sender", padding=10)
        tools.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="CC").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(tools, from_=0, to=127, textvariable=self.custom_cc, width=6).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(tools, text="Value").grid(row=0, column=2, sticky="w")
        ttk.Scale(tools, from_=0, to=127, variable=self.custom_value, orient=tk.HORIZONTAL).grid(row=0, column=3, sticky="ew", padx=8)
        ttk.Button(tools, text="Send", command=lambda: self.send_cc(self.custom_cc.get(), self.custom_value.get())).grid(row=0, column=4)

        presets = ttk.LabelFrame(right, text="Useful sends", padding=10)
        presets.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        for i in range(4):
            presets.columnconfigure(i, weight=1)
        ttk.Button(presets, text="Pitch low", command=lambda: self.send_cc(self.pitch_cc.get(), 0)).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Pitch mid", command=lambda: self.send_cc(self.pitch_cc.get(), 64)).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Pitch high", command=lambda: self.send_cc(self.pitch_cc.get(), 127)).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Silence volume", command=lambda: self.send_cc(self.volume_cc.get(), 0)).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Volume half", command=lambda: self.send_cc(self.volume_cc.get(), 64)).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Volume full", command=lambda: self.send_cc(self.volume_cc.get(), 127)).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="All notes off", command=self.all_notes_off).grid(row=1, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(presets, text="Reset CCs", command=self.reset_defaults).grid(row=1, column=3, sticky="ew", padx=3, pady=3)

        note = ttk.Label(
            right,
            text=(
                "This does not guarantee access to the front-panel calibration wizard. "
                "It gives you a safe MIDI control surface for remote CC setup, range parking, "
                "and discovering any CCs the firmware accepts."
            ),
            wraplength=420,
            foreground="#555555",
        )
        note.grid(row=4, column=0, sticky="ew", pady=(14, 0))

    def _cc_slider(self, parent: ttk.Frame, title: str, cc_var: tk.IntVar, default_cc: int, row: int) -> None:
        box = ttk.LabelFrame(parent, text=title, padding=10)
        box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(3, weight=1)
        value = tk.IntVar(value=64)
        ttk.Label(box, text="CC").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(box, from_=0, to=127, textvariable=cc_var, width=6).grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(box, text="Value").grid(row=0, column=2, sticky="w")
        slider = ttk.Scale(box, from_=0, to=127, variable=value, orient=tk.HORIZONTAL)
        slider.grid(row=0, column=3, sticky="ew", padx=(8, 8))
        ttk.Button(box, text="Send", command=lambda: self.send_cc(cc_var.get(), value.get())).grid(row=0, column=4)
        ttk.Button(box, text=f"CC {default_cc}", command=lambda: cc_var.set(default_cc)).grid(row=0, column=5, padx=(8, 0))

    def refresh_ports(self) -> None:
        inputs = mido.get_input_names()
        outputs = mido.get_output_names()
        self.input_combo.configure(values=inputs)
        self.output_combo.configure(values=outputs)
        if inputs:
            self.input_name.set(preferred_name(inputs))
        if outputs:
            self.output_name.set(preferred_name(outputs))
        self.status.set(f"Found {len(inputs)} MIDI input(s), {len(outputs)} output(s).")

    def connect_ports(self) -> None:
        self.watcher.start(self.input_name.get())
        if self.output_port is not None:
            self.output_port.close()
            self.output_port = None
        name = self.output_name.get()
        if not name:
            self.status.set("No MIDI output selected.")
            return
        try:
            self.output_port = mido.open_output(name)
            self.status.set(f"Connected output: {name}")
            self._log(f"Output connected: {name}")
        except Exception as error:
            self.status.set(f"Output open failed: {error}")
            self._log(f"Output open failed: {error}")

    def send_cc(self, controller: int, value: int | float) -> None:
        if self.output_port is None:
            self.connect_ports()
        if self.output_port is None:
            return

        controller = clamp_midi(controller)
        value = clamp_midi(round(float(value)))
        channel = max(0, min(15, int(self.channel.get()) - 1))
        message = mido.Message("control_change", channel=channel, control=controller, value=value)
        try:
            self.output_port.send(message)
            self._log(f"Out CC {controller:03d} = {value:03d} ch {channel + 1}")
        except Exception as error:
            self._log(f"Send failed: {error}")
            self.status.set(f"Send failed: {error}")

    def all_notes_off(self) -> None:
        for cc in (120, 121, 123):
            self.send_cc(cc, 0)

    def reset_defaults(self) -> None:
        self.pitch_cc.set(DEFAULT_PITCH_CC)
        self.volume_cc.set(DEFAULT_VOLUME_CC)
        self.custom_cc.set(0)
        self.custom_value.set(0)

    def _pump_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._log(event.text)

        for cc, value, age in self.watcher.snapshot():
            row_id = self.monitor_rows.get(cc)
            values = (f"CC {cc:03d}: {value:03d}", f"{age:.1f}s")
            if row_id is None:
                self.monitor_rows[cc] = self.monitor.insert("", "end", values=values)
            else:
                self.monitor.item(row_id, values=values)
        self.after(100, self._pump_events)

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _close(self) -> None:
        self.watcher.stop()
        if self.output_port is not None:
            self.output_port.close()
        self.destroy()


def main() -> None:
    app = ThereminiCalibrator()
    app.mainloop()


if __name__ == "__main__":
    main()
