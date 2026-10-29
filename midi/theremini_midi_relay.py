from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from midi_input import ThereminMidiInput


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write Theremini MIDI state to JSON for Unity.")
    parser.add_argument("--output", required=True, help="Output JSON file path.")
    parser.add_argument("--interval", type=float, default=0.05, help="Write interval in seconds.")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    midi = ThereminMidiInput()
    midi.start()
    print(f"[theremini-relay] writing {output_path}", flush=True)

    try:
        while True:
            state = midi.get_state()
            payload = {
                "timestamp": time.time(),
                "connected": bool(state.connected),
                "input_name": state.input_name,
                "pitch": float(state.pitch),
                "volume": float(state.volume),
                "energy": float(state.energy),
                "disturbance": float(state.disturbance),
                "motion": float(state.motion),
                "signal": float(state.signal),
                "gate": bool(state.gate),
                "note_velocity": float(state.note_velocity),
                "message_count": int(state.message_count),
                "last_message_type": state.last_message_type,
                "pitch_fallback": float(state.pitch_fallback),
                "cc2_value": float(state.cc2_value),
                "cc20_value": float(state.cc20_value),
                "pitch_controller": int(state.pitch_controller),
                "volume_controller": int(state.volume_controller),
                "pitch_controller_value": float(state.pitch_controller_value),
                "volume_controller_value": float(state.volume_controller_value),
            }
            write_state(output_path, payload)
            time.sleep(max(0.01, float(args.interval)))
    except KeyboardInterrupt:
        pass
    finally:
        midi.stop()


if __name__ == "__main__":
    main()
