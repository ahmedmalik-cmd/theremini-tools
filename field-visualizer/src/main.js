import { FieldModel } from "./model.js";
import { startKinectPolling } from "./kinect.js";
import { attachMidiInput, hasMidiSupport, listMidiInputs, requestMidiAccess } from "./midi.js";
import { Field2DRenderer } from "./renderers/field2d.js";
import { Field3DRenderer } from "./renderers/field3d.js";

const connectButton = document.querySelector("#connect-button");
const midiStatus = document.querySelector("#midi-status");
const midiSupport = document.querySelector("#midi-support");
const inputCount = document.querySelector("#input-count");
const inputList = document.querySelector("#input-list");
const inputName = document.querySelector("#input-name");
const kinectStatus = document.querySelector("#kinect-status");
const trackedPeople = document.querySelector("#tracked-people");
const pitchValue = document.querySelector("#pitch-value");
const volumeValue = document.querySelector("#volume-value");
const disturbanceValue = document.querySelector("#disturbance-value");
const motionValue = document.querySelector("#motion-value");
const pitchSource = document.querySelector("#pitch-source");
const volumeSource = document.querySelector("#volume-source");
const intensitySource = document.querySelector("#intensity-source");
const ccActivity = document.querySelector("#cc-activity");
const messageLog = document.querySelector("#message-log");
const inputModeButtons = [...document.querySelectorAll("[data-input-mode]")];
const profileButtons = [...document.querySelectorAll("[data-lab-profile]")];
const labProfileName = document.querySelector("#lab-profile-name");
const labProfileNote = document.querySelector("#lab-profile-note");
const labSignalRows = [...document.querySelectorAll("[data-lab-signal]")];
const canvas = document.querySelector("#field-canvas");
const sceneButtons = [...document.querySelectorAll("[data-scene-mode]")];

const model = new FieldModel();
const renderers = {
  "2d": new Field2DRenderer(canvas, model),
  "3d": new Field3DRenderer(canvas, model),
};

let renderer = renderers["3d"];
let midiAccess = null;
let lastFrame = performance.now();
let kinectBridgeStatus = "Connecting to Kinect bridge...";
const LAB_PROFILES = {
  soloFace: {
    label: "Solo face lock",
    note: "One person isolated in front of the Kinect. Strong face focus, modest body depth, clean control.",
    signals: {
      bodyDepth: 0.38,
      personSeparation: 0.18,
      faceFocus: 0.88,
      foilCoupling: 0,
      shielding: 0.02,
      ultrasonicDistance: 0.72,
    },
  },
  duoSplit: {
    label: "Two-person split",
    note: "Two bodies separated in depth or x-position. Good for testing left-right or person-A/person-B modulation.",
    signals: {
      bodyDepth: 0.66,
      personSeparation: 0.92,
      faceFocus: 0.54,
      foilCoupling: 0,
      shielding: 0.04,
      ultrasonicDistance: 0.58,
    },
  },
  foilCoupled: {
    label: "Foil coupled antenna",
    note: "Foil near an antenna usually increases effective coupling and makes the field feel touchier and broader.",
    signals: {
      bodyDepth: 0.48,
      personSeparation: 0.34,
      faceFocus: 0.4,
      foilCoupling: 0.94,
      shielding: 0.08,
      ultrasonicDistance: 0.46,
    },
  },
  shieldedCage: {
    label: "Shielded / cage test",
    note: "More shielding tends to damp and destabilize interaction. Start loose and partial, not fully wrapped.",
    signals: {
      bodyDepth: 0.34,
      personSeparation: 0.2,
      faceFocus: 0.22,
      foilCoupling: 0.18,
      shielding: 0.86,
      ultrasonicDistance: 0.7,
    },
  },
  ultrasonicSweep: {
    label: "Ultrasonic sweep",
    note: "A distance sensor can become a secondary range envelope. Nearer readings push energy and volume up.",
    signals: {
      bodyDepth: 0.58,
      personSeparation: 0.4,
      faceFocus: 0.28,
      foilCoupling: 0.12,
      shielding: 0,
      ultrasonicDistance: 0.14,
    },
  },
};

function renderFrame(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;

  model.update(dt, renderer.viewport);
  renderer.draw(now / 1000);
  syncTelemetry();

  requestAnimationFrame(renderFrame);
}

function syncTelemetry() {
  pitchValue.textContent = model.pitchProxy.toFixed(3);
  volumeValue.textContent = model.volumeProxy.toFixed(3);
  disturbanceValue.textContent = model.disturbance.toFixed(3);
  motionValue.textContent = model.motion.toFixed(3);
  kinectStatus.textContent = kinectBridgeStatus;
  trackedPeople.textContent = String(model.kinectStatus.trackedCount);
  renderControllerActivity();
  renderMessageLog();
  refreshMappingMenus();
  syncLabUi();
}

function renderInputList(inputs) {
  inputCount.textContent = String(inputs.length);
  inputList.innerHTML = "";

  if (inputs.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No MIDI inputs detected";
    inputList.append(item);
    return;
  }

  for (const input of inputs) {
    const item = document.createElement("li");
    item.textContent = [
      input.name || "Unnamed input",
      input.manufacturer || "Unknown maker",
      input.state || "unknown state",
      input.connection || "unknown connection",
    ].join(" / ");
    inputList.append(item);
  }
}

function bindBestInput(access) {
  const inputs = listMidiInputs(access);
  renderInputList(inputs);

  const input = attachMidiInput(access, (message) => {
    model.ingest(message);
  });

  if (!input) {
    midiStatus.textContent = "No MIDI input found";
    inputName.textContent = "Unavailable";
    return;
  }

  midiStatus.textContent = "Connected";
  inputName.textContent = input.name || "Unnamed MIDI input";
}

function updateMidiSupportStatus() {
  midiSupport.textContent = hasMidiSupport() ? "Supported" : "Unavailable";
}

function setSceneMode(mode) {
  renderer = renderers[mode] || renderers["2d"];

  for (const button of sceneButtons) {
    button.dataset.active = String(button.dataset.sceneMode === mode);
  }
}

function setInputMode(mode) {
  model.setInputMode(mode);

  for (const button of inputModeButtons) {
    button.dataset.active = String(button.dataset.inputMode === mode);
  }

  const usingExperimentOnly = mode === "lab" || mode === "kinect";
  pitchSource.disabled = usingExperimentOnly;
  volumeSource.disabled = usingExperimentOnly;
  intensitySource.disabled = usingExperimentOnly;
}

function renderControllerActivity() {
  const controllers = model.getTopControllers(6);
  ccActivity.innerHTML = "";

  if (controllers.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No CC messages received yet";
    ccActivity.append(item);
    return;
  }

  for (const controller of controllers) {
    const item = document.createElement("li");
    item.textContent = `CC ${controller.controller} / value ${controller.raw} / activity ${controller.activity.toFixed(2)} / seen ${controller.seen}`;
    ccActivity.append(item);
  }
}

function renderMessageLog() {
  messageLog.innerHTML = "";

  if (model.messageLog.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No MIDI messages received yet";
    messageLog.append(item);
    return;
  }

  for (const message of model.messageLog) {
    const item = document.createElement("li");
    item.textContent = message.summary;
    messageLog.append(item);
  }
}

function collectMappingOptions() {
  const options = [
    { value: "pitchbend", label: "Pitch Bend" },
    { value: "note", label: "Note Number" },
    { value: "auto", label: "Auto (Most Active CC)" },
  ];

  for (const controller of model.getTopControllers(8)) {
    options.push({
      value: `cc:${controller.controller}`,
      label: `CC ${controller.controller}`,
    });
  }

  return options;
}

function refreshSelect(select, selectedValue) {
  const options = collectMappingOptions();
  const existing = new Set();
  select.innerHTML = "";

  for (const option of options) {
    if (existing.has(option.value)) {
      continue;
    }

    existing.add(option.value);
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    element.selected = option.value === selectedValue;
    select.append(element);
  }

  if (!existing.has(selectedValue)) {
    const fallback = document.createElement("option");
    fallback.value = selectedValue;
    fallback.textContent = selectedValue;
    fallback.selected = true;
    select.append(fallback);
  }
}

function refreshMappingMenus() {
  refreshSelect(pitchSource, model.mappings.pitchSource);
  refreshSelect(volumeSource, model.mappings.volumeSource);
  refreshSelect(intensitySource, model.mappings.intensitySource);
}

function applyMappingSelections() {
  model.setMappings({
    pitchSource: pitchSource.value,
    volumeSource: volumeSource.value,
    intensitySource: intensitySource.value,
  });
}

function getLabSignalElements(signalName) {
  const row = labSignalRows.find((element) => element.dataset.labSignal === signalName);
  const input = row?.querySelector("input");
  const output = row?.querySelector("output");
  return { row, input, output };
}

function updateLabSignal(signalName, value) {
  model.setLabSignals({ [signalName]: Number(value) });
  model.setLabProfile("custom");
}

function syncLabUi() {
  const visibleSignals =
    (model.inputMode === "kinect" || model.inputMode === "hybrid") && model.kinectSignals
      ? model.kinectSignals
      : model.labSignals;

  for (const [signalName, value] of Object.entries(visibleSignals)) {
    const { input, output } = getLabSignalElements(signalName);

    if (input && document.activeElement !== input) {
      input.value = String(value);
    }

    if (output) {
      output.value = value.toFixed(2);
      output.textContent = value.toFixed(2);
    }
  }

  const profile = LAB_PROFILES[model.labProfile];
  labProfileName.textContent = profile?.label || "Custom blend";
  labProfileNote.textContent =
    profile?.note ||
    "Manual blend of body depth, person separation, foil coupling, shielding, and ultrasonic distance.";

  for (const button of profileButtons) {
    button.dataset.active = String(button.dataset.labProfile === model.labProfile);
  }
}

function applyLabProfile(profileKey) {
  const profile = LAB_PROFILES[profileKey];

  if (!profile) {
    return;
  }

  model.setLabSignals(profile.signals);
  model.setLabProfile(profileKey);
  syncLabUi();
}

function installExternalInputApi() {
  window.thereminLab = {
    setSignals(nextSignals) {
      model.setLabSignals(nextSignals);
      model.setLabProfile("custom");
      syncLabUi();
    },
    setMode(mode) {
      setInputMode(mode);
    },
    applyProfile(profileKey) {
      applyLabProfile(profileKey);
    },
  };

  window.addEventListener("theremin-lab-input", (event) => {
    const nextSignals = event.detail || {};
    model.setLabSignals(nextSignals);
    model.setLabProfile("custom");
    syncLabUi();
  });
}

function installKinectBridge() {
  startKinectPolling({
    onFrame(frame) {
      model.setKinectFrame(frame);

      if (frame.connected) {
        kinectBridgeStatus = `Live / ${frame.pointCloud?.length ?? 0} pts`;
      } else if (frame.error) {
        kinectBridgeStatus = frame.error;
      } else {
        kinectBridgeStatus = "Bridge up, waiting for Kinect frames";
      }
    },
    onStatus(status) {
      if (!status.bridgeOk || !status.connected) {
        model.clearKinectFrame();
      }

      if (!model.kinectStatus.connected || !status.bridgeOk || !status.connected) {
        kinectBridgeStatus = status.message;
      }
    },
  });
}

connectButton.addEventListener("click", async () => {
  connectButton.disabled = true;
  midiStatus.textContent = "Requesting access...";

  try {
    midiAccess = await requestMidiAccess({
      onStateChange: () => {
        if (!midiAccess) {
          return;
        }

        midiStatus.textContent = "MIDI device state changed, rescanning...";
        bindBestInput(midiAccess);
      },
    });

    bindBestInput(midiAccess);
  } catch (error) {
    midiStatus.textContent = error.message;
    inputName.textContent = "Unavailable";
  } finally {
    connectButton.disabled = false;
  }
});

for (const button of sceneButtons) {
  button.addEventListener("click", () => {
    setSceneMode(button.dataset.sceneMode);
  });
}

for (const button of inputModeButtons) {
  button.addEventListener("click", () => {
    setInputMode(button.dataset.inputMode);
  });
}

for (const button of profileButtons) {
  button.addEventListener("click", () => {
    applyLabProfile(button.dataset.labProfile);
  });
}

for (const row of labSignalRows) {
  const signalName = row.dataset.labSignal;
  const input = row.querySelector("input");

  input?.addEventListener("input", () => {
    updateLabSignal(signalName, input.value);
  });
}

for (const select of [pitchSource, volumeSource, intensitySource]) {
  select.addEventListener("change", applyMappingSelections);
}

updateMidiSupportStatus();
renderInputList([]);
renderControllerActivity();
renderMessageLog();
refreshMappingMenus();
applyMappingSelections();
applyLabProfile("soloFace");
setInputMode("kinect");
setSceneMode("3d");
installExternalInputApi();
installKinectBridge();
requestAnimationFrame(renderFrame);
