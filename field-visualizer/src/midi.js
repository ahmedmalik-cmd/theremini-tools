const MIDI_NOTE_ON = 0x90;
const MIDI_NOTE_OFF = 0x80;
const MIDI_CC = 0xb0;
const MIDI_PITCH_BEND = 0xe0;

export function hasMidiSupport() {
  return Boolean(navigator.requestMIDIAccess);
}

export async function requestMidiAccess({ onStateChange, timeoutMs = 8000 } = {}) {
  if (!navigator.requestMIDIAccess) {
    throw new Error("Web MIDI is not supported in this browser.");
  }

  const access = await Promise.race([
    navigator.requestMIDIAccess({ sysex: false }),
    new Promise((_, reject) => {
      window.setTimeout(() => {
        reject(new Error("MIDI request timed out. Check browser MIDI permission and reconnect the Theremini."));
      }, timeoutMs);
    }),
  ]);

  if (onStateChange) {
    access.onstatechange = onStateChange;
  }

  return access;
}

export function listMidiInputs(access) {
  return [...access.inputs.values()];
}

export function selectMidiInput(access) {
  const inputs = listMidiInputs(access);
  return inputs.find((input) => /theremini|moog/i.test(input.name || "")) || inputs[0] || null;
}

export function attachMidiInput(access, onMessage) {
  const selectedInput = selectMidiInput(access);

  if (!selectedInput) {
    return null;
  }

  selectedInput.onmidimessage = (event) => {
    const [status, data1 = 0, data2 = 0] = event.data;
    const command = status & 0xf0;
    const channel = status & 0x0f;
    onMessage(parseMidiMessage(command, channel, data1, data2));
  };

  return selectedInput;
}

function parseMidiMessage(command, channel, data1, data2) {
  if (command === MIDI_NOTE_ON && data2 > 0) {
    return {
      type: "noteon",
      channel,
      note: data1,
      velocity: data2 / 127,
      rawValue: data2,
    };
  }

  if (command === MIDI_NOTE_OFF || (command === MIDI_NOTE_ON && data2 === 0)) {
    return {
      type: "noteoff",
      channel,
      note: data1,
      velocity: 0,
      rawValue: data2,
    };
  }

  if (command === MIDI_CC) {
    return {
      type: "cc",
      channel,
      controller: data1,
      value: data2 / 127,
      rawValue: data2,
    };
  }

  if (command === MIDI_PITCH_BEND) {
    const raw = ((data2 << 7) | data1) - 8192;

    return {
      type: "pitchbend",
      channel,
      value: raw / 8192,
      rawValue: raw,
    };
  }

  return {
    type: "unknown",
    channel,
    data1,
    data2,
  };
}
