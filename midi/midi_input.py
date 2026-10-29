import threading
import time
from dataclasses import dataclass
from typing import Optional

import mido


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


@dataclass
class ThereminState:
    connected: bool = False
    input_name: str = "None"
    pitch: float = 0.0
    volume: float = 0.0
    energy: float = 0.0
    disturbance: float = 0.0
    motion: float = 0.0
    signal: float = 0.0
    gate: bool = False
    note_velocity: float = 0.0
    message_count: int = 0
    last_message_type: str = "none"
    pitch_fallback: float = 0.0
    cc2_value: float = 0.0
    cc20_value: float = 0.0
    volume_controller: int = 2
    pitch_controller: int = 20
    volume_controller_value: float = 0.0
    pitch_controller_value: float = 0.0


class ThereminMidiInput:
    def __init__(self) -> None:
        self.state = ThereminState()
        self._input: Optional[mido.ports.BaseInput] = None
        self._lock = threading.Lock()
        self._running = False
        self._worker: Optional[threading.Thread] = None
        self._latest_pitch_bend = 0.0
        self._latest_note = 0.0
        self._last_activity = time.time()
        self._last_open_error = ""
        self._last_debug_log = 0.0
        self._last_note_velocity = 0.0
        self._note_gate_until = 0.0
        self._raw_debug_messages = 0
        self._cc_values: dict[int, int] = {}
        self._cc_msb_values: dict[int, int] = {}
        self._cc_lsb_values: dict[int, int] = {}
        self._controller_last_change: dict[int, float] = {}
        self._controller_activity: dict[int, float] = {}
        self._last_effective_pitch = 0.0
        self._last_effective_volume = 0.0
        self._pitch_cc = 20
        self._volume_cc = 5
        self._pitch_cc_candidates = (20, 16, 17, 18, 19, 21)
        self._volume_cc_candidates = (5, 2, 7, 11, 74)

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(target=self._poll_loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        if self._input is not None:
            self._input.close()
            self._input = None

    def get_state(self) -> ThereminState:
        with self._lock:
            return ThereminState(**self.state.__dict__)

    def _poll_loop(self) -> None:
        while self._running:
            if self._input is None:
                self._try_open_input()

            if self._input is not None:
                try:
                    for message in self._input.iter_pending():
                        self._handle_message(message)
                except Exception as error:
                    self._last_open_error = str(error)
                    with self._lock:
                        self.state.connected = False
                    try:
                        self._input.close()
                    except Exception:
                        pass
                    self._input = None

                self._decay_state()

            self._maybe_log_debug()
            time.sleep(0.01)

    def _try_open_input(self) -> None:
        names = mido.get_input_names()
        preferred = None

        for name in names:
            if "theremini" in name.lower() or "moog" in name.lower():
                preferred = name
                break

        if preferred is None and names:
            preferred = names[0]

        if preferred is None:
            return

        try:
            self._input = mido.open_input(preferred)
            with self._lock:
                self.state.connected = True
                self.state.input_name = preferred
            self._last_open_error = ""
            print(f"[midi] connected to {preferred}")
        except Exception:
            self._last_open_error = f"failed to open {preferred}"
            self._input = None

    def _handle_message(self, message: mido.Message) -> None:
        self._last_activity = time.time()
        self._raw_debug(message)
        with self._lock:
            self.state.message_count += 1
            self.state.last_message_type = message.type

        if message.type == "pitchwheel":
            self._latest_pitch_bend = clamp((message.pitch + 8192) / 16384.0, 0.0, 1.0)

        if message.type == "note_on":
            if getattr(message, "velocity", 0) <= 0:
                self._note_gate_until = 0.0
                with self._lock:
                    self.state.gate = False
                return

            self._latest_note = clamp(message.note / 127.0, 0.0, 1.0)
            velocity = clamp(message.velocity / 127.0, 0.0, 1.0)
            self._last_note_velocity = velocity
            self._note_gate_until = time.time() + 0.25
            with self._lock:
                self.state.note_velocity = velocity
                self.state.volume = max(self.state.volume, velocity * 0.9)
                self.state.energy = max(self.state.energy, velocity)
                self.state.gate = True

        if message.type == "note_off":
            self._note_gate_until = 0.0
            with self._lock:
                self.state.gate = False

        if message.type == "control_change":
            controller = message.control
            mapped_controller = controller - 32 if 32 <= controller < 64 else controller
            previous_value = self._controller_value(mapped_controller)
            self._cc_values[controller] = int(message.value)
            if 0 <= controller < 32:
                self._cc_msb_values[controller] = int(message.value)
            elif 32 <= controller < 64:
                self._cc_lsb_values[controller - 32] = int(message.value)

            current_value = self._controller_value(mapped_controller)
            changed_amount = abs(current_value - previous_value)
            if changed_amount > 0.0005:
                self._controller_last_change[mapped_controller] = time.time()
                self._controller_activity[mapped_controller] = self._controller_activity.get(mapped_controller, 0.0) + changed_amount

            self._pitch_cc = self._select_active_controller(self._pitch_cc_candidates, self._pitch_cc)
            self._volume_cc = self._select_active_controller(self._volume_cc_candidates, self._volume_cc)

            with self._lock:
                self.state.cc2_value = self._controller_value(2)
                self.state.cc20_value = self._controller_value(20)

            if mapped_controller == self._volume_cc:
                if changed_amount > 0.0005:
                    self._note_gate_until = time.time() + 0.18
                    with self._lock:
                        self.state.gate = True
            elif mapped_controller == self._pitch_cc:
                with self._lock:
                    self.state.pitch_fallback = current_value
                if changed_amount > 0.0005:
                    self._note_gate_until = time.time() + 0.18
                    with self._lock:
                        self.state.gate = True
            elif mapped_controller in (1, 7, 11, 74):
                with self._lock:
                    self.state.volume = max(self.state.volume, current_value)
                if changed_amount > 0.0005:
                    with self._lock:
                        self.state.gate = True
                    self._note_gate_until = time.time() + 0.18
            else:
                with self._lock:
                    self.state.energy = max(self.state.energy, current_value * 0.8)
                if changed_amount > 0.001:
                    with self._lock:
                        self.state.gate = True
                    self._note_gate_until = time.time() + 0.14

        if message.type in ("aftertouch", "polytouch", "channel_pressure"):
            value = getattr(message, "value", getattr(message, "pressure", 0))
            normalized = clamp(value / 127.0, 0.0, 1.0)
            with self._lock:
                self.state.volume = max(self.state.volume * 0.8, normalized)
                self.state.energy = max(self.state.energy, normalized)
                self.state.gate = normalized > 0.02 or self.state.gate
            if normalized > 0.02:
                self._note_gate_until = time.time() + 0.18

        with self._lock:
            controller_pitch = self._controller_value(self._pitch_cc)
            controller_volume = self._controller_value(self._volume_cc)
            effective_pitch = controller_pitch if controller_pitch > 0.0001 else (
                self._latest_pitch_bend if self._latest_pitch_bend > 0 else self._latest_note
            )
            effective_volume = controller_volume
            pitch_delta = abs(effective_pitch - self._last_effective_pitch)
            volume_delta = abs(effective_volume - self._last_effective_volume)
            self.state.pitch = effective_pitch
            self.state.volume = effective_volume
            self.state.pitch_controller = self._pitch_cc
            self.state.volume_controller = self._volume_cc
            self.state.pitch_controller_value = controller_pitch
            self.state.volume_controller_value = controller_volume
            self.state.motion = clamp(max(pitch_delta * 8.0, volume_delta * 6.0), 0.0, 1.0)
            self._last_effective_pitch = effective_pitch
            self._last_effective_volume = effective_volume
            self._recompute_signal_locked()

    def _decay_state(self) -> None:
        with self._lock:
            idle = time.time() - self._last_activity
            decay = 0.94 if idle < 0.4 else 0.88
            self.state.energy *= decay
            self.state.motion *= 0.9
            self.state.note_velocity *= 0.96
            recent_pitch_change = self._recent_controller_change(self._pitch_cc, 0.25)
            recent_volume_change = self._recent_controller_change(self._volume_cc, 0.25)
            if time.time() > self._note_gate_until and not recent_pitch_change and not recent_volume_change and self.state.energy < 0.06:
                self.state.gate = False
            self._recompute_signal_locked()

    def _recompute_signal_locked(self) -> None:
        recent_pitch_activity = 1.0 if self._recent_controller_change(self._pitch_cc, 0.22) else 0.0
        recent_volume_activity = 1.0 if self._recent_controller_change(self._volume_cc, 0.22) else 0.0
        self.state.disturbance = clamp(
            self.state.pitch * 0.18 +
            self.state.volume * 0.34 +
            self.state.energy * 0.28 +
            self.state.motion * 0.20,
            0.0,
            1.0,
        )
        self.state.signal = clamp(
            max(
                recent_pitch_activity * 0.85,
                recent_volume_activity,
                self.state.energy * 0.92,
                self.state.note_velocity * 0.85,
                self.state.motion * 0.65,
            ),
            0.0,
            1.0,
        )

    def _controller_value(self, controller: int) -> float:
        msb = self._cc_msb_values.get(controller)
        if msb is None:
            return 0.0

        lsb = self._cc_lsb_values.get(controller)
        if lsb is None:
            return clamp(msb / 127.0, 0.0, 1.0)

        return clamp((((msb << 7) | lsb) / 16383.0), 0.0, 1.0)

    def _recent_controller_change(self, controller: int, within_seconds: float) -> bool:
        last_change = self._controller_last_change.get(controller, 0.0)
        return (time.time() - last_change) <= within_seconds

    def _select_active_controller(self, candidates: tuple[int, ...], fallback: int) -> int:
        now = time.time()
        best = fallback
        best_score = -1.0
        for controller in candidates:
            value = self._controller_value(controller)
            last_change = self._controller_last_change.get(controller, 0.0)
            recent_bonus = 2.0 if (now - last_change) <= 1.0 else 0.0
            activity = self._controller_activity.get(controller, 0.0)
            has_value_bonus = 0.5 if value > 0.0 else 0.0
            score = recent_bonus + activity + has_value_bonus
            if score > best_score:
                best = controller
                best_score = score

        return best

    def _raw_debug(self, message: mido.Message) -> None:
        if self._raw_debug_messages >= 24:
            return

        self._raw_debug_messages += 1
        print(f"[midi][raw] {message}")

    def _maybe_log_debug(self) -> None:
        now = time.time()
        if now - self._last_debug_log < 2.0:
            return

        self._last_debug_log = now
        with self._lock:
            state = ThereminState(**self.state.__dict__)

        if state.connected:
            print(
                f"[midi] {state.input_name} pitch={state.pitch:.2f} volume={state.volume:.2f} "
                f"energy={state.energy:.2f} signal={state.signal:.2f} gate={int(state.gate)} "
                f"pitchCC={state.pitch_controller} volumeCC={state.volume_controller} "
                f"disturbance={state.disturbance:.2f} last={state.last_message_type}"
            )
        elif self._last_open_error:
            print(f"[midi] {self._last_open_error}")
