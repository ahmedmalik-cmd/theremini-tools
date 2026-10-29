from __future__ import annotations

import argparse
import time

import mido


def find_input_name() -> str | None:
    names = mido.get_input_names()
    for name in names:
        lowered = name.lower()
        if "theremini" in lowered or "moog" in lowered:
            return name
    return names[0] if names else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Live monitor for Theremini MIDI CC movement.")
    parser.add_argument("--seconds", type=float, default=20.0, help="How long to listen.")
    args = parser.parse_args()

    name = find_input_name()
    if not name:
        print("[monitor] no MIDI inputs found")
        return

    print(f"[monitor] listening on: {name}")
    previous: dict[int, int] = {}
    start = time.time()
    with mido.open_input(name) as port:
        while time.time() - start < args.seconds:
            saw_message = False
            for message in port.iter_pending():
                saw_message = True
                if message.type != "control_change":
                    print(f"[monitor] {message}")
                    continue

                control = int(message.control)
                value = int(message.value)
                delta = None if control not in previous else value - previous[control]
                previous[control] = value

                partner_text = ""
                if control in (2, 20, 34, 52):
                    partner = control - 32 if control >= 32 else control + 32
                    if partner in previous:
                        msb_control = control - 32 if control >= 32 else control
                        msb_value = previous.get(msb_control, 0)
                        lsb_value = previous.get(msb_control + 32, 0)
                        combined = ((msb_value << 7) | lsb_value) / 16383.0
                        partner_text = f" | paired14={combined:.4f}"

                print(f"[monitor] cc{control}={value} delta={delta}{partner_text}")

            if not saw_message:
                time.sleep(0.01)


if __name__ == "__main__":
    main()
