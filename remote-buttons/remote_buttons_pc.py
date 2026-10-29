from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports


BAUD = 115200
BUTTONS = ("SETUP", "EFFECT", "ROOT", "SCALE")


class RemoteButtonsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Theremini Remote Buttons")
        self.geometry("620x480")
        self.minsize(560, 420)

        self.port_name = tk.StringVar()
        self.status = tk.StringVar(value="Connect the Arduino, choose its COM port, then press Connect.")
        self.duration_ms = tk.IntVar(value=250)
        self.serial_port: serial.Serial | None = None

        self._build()
        self.refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        top = ttk.Frame(self, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_name)
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="Connect", command=self.connect).grid(row=0, column=3)
        ttk.Label(top, textvariable=self.status).grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))

        seq = ttk.LabelFrame(self, text="Calibration sequences", padding=12)
        seq.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        seq.columnconfigure(0, weight=1)
        seq.columnconfigure(1, weight=1)
        ttk.Button(seq, text="Enter Calibration: SETUP hold, then EFFECT", command=lambda: self.send("CAL")).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(seq, text="Shortcut: SETUP + ROOT", command=lambda: self.send("CALROOT")).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(seq, text="Advance Calibration Step: SETUP", command=lambda: self.press("SETUP")).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(seq, text="Rest All Servos", command=lambda: self.send("REST")).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))

        manual = ttk.LabelFrame(self, text="Manual buttons", padding=12)
        manual.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        for col in range(4):
            manual.columnconfigure(col, weight=1)

        ttk.Label(manual, text="Tap length ms").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(manual, from_=50, to=10000, increment=50, textvariable=self.duration_ms, width=8).grid(row=0, column=1, sticky="w")

        for index, button in enumerate(BUTTONS):
            frame = ttk.Frame(manual)
            frame.grid(row=1, column=index, sticky="ew", padx=4, pady=(12, 0))
            frame.columnconfigure(0, weight=1)
            ttk.Button(frame, text=f"Tap {button}", command=lambda b=button: self.press(b)).grid(row=0, column=0, sticky="ew")
            ttk.Button(frame, text=f"Hold {button}", command=lambda b=button: self.send(f"H {b}")).grid(row=1, column=0, sticky="ew", pady=(4, 0))
            ttk.Button(frame, text=f"Release {button}", command=lambda b=button: self.send(f"R {b}")).grid(row=2, column=0, sticky="ew", pady=(4, 0))

        log_frame = ttk.Frame(self, padding=(12, 0, 12, 12))
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", height=8)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.configure(state="disabled")

    def refresh_ports(self) -> None:
        ports = list(list_ports.comports())
        labels = [f"{port.device} - {port.description}" for port in ports]
        seen = {label.split(" - ", 1)[0].upper() for label in labels}
        for index in range(1, 17):
            name = f"COM{index}"
            if name not in seen:
                labels.append(name)
        self.port_combo.configure(values=labels)
        if labels and not self.port_name.get():
            self.port_name.set(labels[0])
        self.status.set(f"Found {len(ports)} live serial port(s). Manual COM entry is allowed.")

    def connect(self) -> None:
        self.close_serial()
        selected = self.port_name.get()
        if not selected:
            self.status.set("No serial port selected.")
            return
        device = selected.split(" - ", 1)[0]
        try:
            self.serial_port = serial.Serial(device, BAUD, timeout=0.2)
            time.sleep(2.0)
            self.status.set(f"Connected to {device}.")
            self._log(f"Connected to {device}")
            self.read_pending()
        except Exception as error:
            self.serial_port = None
            self.status.set(f"Connect failed: {error}")
            self._log(f"Connect failed: {error}")

    def press(self, button: str) -> None:
        self.send(f"P {button} {self.duration_ms.get()}")

    def send(self, command: str) -> None:
        if self.serial_port is None or not self.serial_port.is_open:
            self.connect()
        if self.serial_port is None:
            return
        try:
            line = f"{command.strip()}\n"
            self.serial_port.write(line.encode("ascii"))
            self.serial_port.flush()
            self._log(f"> {command}")
            self.after(120, self.read_pending)
        except Exception as error:
            self.status.set(f"Send failed: {error}")
            self._log(f"Send failed: {error}")

    def read_pending(self) -> None:
        if self.serial_port is None or not self.serial_port.is_open:
            return
        try:
            while self.serial_port.in_waiting:
                line = self.serial_port.readline().decode("utf-8", errors="replace").strip()
                if line:
                    self._log(f"< {line}")
        except Exception as error:
            self._log(f"Read failed: {error}")

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def close_serial(self) -> None:
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None

    def close(self) -> None:
        self.close_serial()
        self.destroy()


def main() -> None:
    app = RemoteButtonsApp()
    app.mainloop()


if __name__ == "__main__":
    main()
