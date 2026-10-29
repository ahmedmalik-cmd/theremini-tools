const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const lerp = (start, end, amount) => start + (end - start) * amount;

export { clamp, lerp };

export class FieldModel {
  constructor() {
    this.pitchProxy = 0;
    this.volumeProxy = 0;
    this.disturbance = 0;
    this.energy = 0;
    this.motion = 0;
    this.turbulence = 0;
    this.centerX = 0.5;
    this.centerY = 0.5;
    this.centerZ = 0;
    this.history = [];
    this.lastNote = null;
    this.latestPitchBend = 0;
    this.latestMessage = null;
    this.messageCounter = 0;
    this.controllerState = new Map();
    this.messageLog = [];
    this.mappings = {
      pitchSource: "pitchbend",
      volumeSource: "auto",
      intensitySource: "auto",
    };
    this.inputMode = "midi";
    this.labSignals = {
      bodyDepth: 0.5,
      personSeparation: 0.2,
      faceFocus: 0.3,
      foilCoupling: 0,
      shielding: 0,
      ultrasonicDistance: 1,
    };
    this.kinectSignals = null;
    this.kinectFrame = null;
    this.kinectStatus = {
      connected: false,
      pointCount: 0,
      trackedCount: 0,
      updatedAt: 0,
    };
    this.labProfile = "custom";
  }

  setMappings(nextMappings) {
    this.mappings = {
      ...this.mappings,
      ...nextMappings,
    };
  }

  setInputMode(mode) {
    this.inputMode = mode;
  }

  setLabSignals(nextSignals) {
    this.labSignals = {
      ...this.labSignals,
      ...nextSignals,
    };
  }

  setLabProfile(profile) {
    this.labProfile = profile;
  }

  setKinectFrame(frame) {
    this.kinectFrame = frame;
    this.kinectSignals = {
      ...this.labSignals,
      ...(frame?.controls ?? {}),
    };
    this.kinectStatus = {
      connected: Boolean(frame?.connected),
      pointCount: frame?.pointCloud?.length ?? 0,
      trackedCount: frame?.players?.length ?? 0,
      updatedAt: frame?.timestamp ?? Date.now(),
    };
  }

  clearKinectFrame() {
    this.kinectFrame = null;
    this.kinectSignals = null;
    this.kinectStatus = {
      connected: false,
      pointCount: 0,
      trackedCount: 0,
      updatedAt: 0,
    };
  }

  ingest(message) {
    this.latestMessage = message;
    this.messageCounter += 1;
    this.recordMessage(message);

    if (message.type === "pitchbend") {
      this.latestPitchBend = clamp((message.value + 1) * 0.5, 0, 1);
      this.energy = Math.max(this.energy, Math.abs(message.value));
    }

    if (message.type === "noteon") {
      this.lastNote = message.note;
      this.energy = Math.max(this.energy, message.velocity);
    }

    if (message.type === "noteoff") {
      this.energy *= 0.7;
    }

    if (message.type === "cc") {
      this.updateControllerState(message);
      this.energy = Math.max(this.energy, message.value * 0.8);
    }
  }

  update(dt, viewport) {
    const experimentSignals = this.getActiveExperimentSignals();
    const midiPitch = this.resolveMapping(this.mappings.pitchSource, this.pitchProxy);
    const midiVolume = this.resolveMapping(this.mappings.volumeSource, this.volumeProxy);
    const midiEnergy = this.resolveIntensity();
    const experimentState = this.resolveLabState(experimentSignals);
    const mix = this.inputMode === "lab" || this.inputMode === "kinect" ? 1 : this.inputMode === "hybrid" ? 0.55 : 0;
    const targetPitch = lerp(midiPitch, experimentState.pitch, mix);
    const targetVolume = lerp(midiVolume, experimentState.volume, mix);
    const targetEnergy = lerp(midiEnergy, experimentState.energy, mix);

    const pitchDelta = Math.abs(targetPitch - this.pitchProxy);
    const volumeDelta = Math.abs(targetVolume - this.volumeProxy);

    this.pitchProxy = lerp(this.pitchProxy, targetPitch, 1 - Math.exp(-dt * 7));
    this.volumeProxy = lerp(this.volumeProxy, targetVolume, 1 - Math.exp(-dt * 5));
    this.motion = lerp(this.motion, clamp((pitchDelta + volumeDelta) * 6, 0, 1), 1 - Math.exp(-dt * 9));
    this.energy = lerp(this.energy, targetEnergy, 1 - Math.exp(-dt * 4));
    this.turbulence = lerp(
      this.turbulence,
      clamp(this.motion * 0.7 + this.energy * 0.45, 0, 1),
      1 - Math.exp(-dt * 4),
    );

    const targetDisturbance = clamp(
      0.34 * this.pitchProxy +
        0.26 * this.volumeProxy +
        0.32 * this.energy +
        0.34 * this.motion +
        0.24 * this.turbulence,
      0,
      1,
    );

    this.disturbance = lerp(this.disturbance, targetDisturbance, 1 - Math.exp(-dt * 5));

    const midiTargetX = lerp(0.14, 0.86, this.pitchProxy);
    const midiTargetY = lerp(0.84, 0.14, this.volumeProxy || this.disturbance);
    const midiTargetZ = lerp(-1, 1, this.energy);
    const targetX = lerp(midiTargetX, experimentState.centerX, mix);
    const targetY = lerp(midiTargetY, experimentState.centerY, mix);
    const targetZ = lerp(midiTargetZ, experimentState.centerZ, mix);

    this.centerX = lerp(this.centerX, targetX, 1 - Math.exp(-dt * 4));
    this.centerY = lerp(this.centerY, targetY, 1 - Math.exp(-dt * 4));
    this.centerZ = lerp(this.centerZ, targetZ, 1 - Math.exp(-dt * 4));

    this.history.push({
      x: this.centerX * viewport.width,
      y: this.centerY * viewport.height,
      z: this.centerZ,
      life: 1,
    });

    if (this.history.length > 140) {
      this.history.shift();
    }

    for (const sample of this.history) {
      sample.life = Math.max(0, sample.life - dt * 0.48);
    }

    this.history = this.history.filter((sample) => sample.life > 0.01);
  }

  updateControllerState(message) {
    const key = `cc:${message.controller}`;
    const previous = this.controllerState.get(key);
    const previousValue = previous?.normalized ?? message.value;
    const delta = Math.abs(message.value - previousValue);

    this.controllerState.set(key, {
      controller: message.controller,
      normalized: message.value,
      raw: message.rawValue,
      seen: (previous?.seen ?? 0) + 1,
      activity: clamp((previous?.activity ?? 0) * 0.9 + delta * 4 + 0.05, 0, 1),
    });
  }

  resolveMapping(source, fallback) {
    if (source === "pitchbend") {
      return this.latestPitchBend;
    }

    if (source === "note") {
      return this.lastNote === null ? fallback : clamp(this.lastNote / 127, 0, 1);
    }

    if (source?.startsWith("cc:")) {
      return this.controllerState.get(source)?.normalized ?? fallback;
    }

    if (source === "auto") {
      return this.getTopControllers(1)[0]?.normalized ?? fallback;
    }

    return fallback;
  }

  resolveIntensity() {
    if (this.mappings.intensitySource !== "auto") {
      return this.resolveMapping(this.mappings.intensitySource, this.energy);
    }

    const topControllers = this.getTopControllers(2);
    const controllerEnergy = topControllers.reduce((sum, controller) => sum + controller.normalized * 0.5, 0);
    return clamp(Math.max(this.energy * 0.7, controllerEnergy), 0, 1);
  }

  getActiveExperimentSignals() {
    if ((this.inputMode === "kinect" || this.inputMode === "hybrid") && this.kinectSignals) {
      return this.kinectSignals;
    }

    return this.labSignals;
  }

  resolveLabState(signals = this.labSignals) {
    const bodyDepth = signals.bodyDepth;
    const personSeparation = signals.personSeparation;
    const faceFocus = signals.faceFocus;
    const foilCoupling = signals.foilCoupling;
    const shielding = signals.shielding;
    const ultrasonicDistance = signals.ultrasonicDistance;
    const ultrasonicNearness = 1 - ultrasonicDistance;
    const shieldingLoss = clamp(1 - shielding * 0.88, 0.05, 1);
    const foilBoost = 1 + foilCoupling * 0.9;
    const faceDominance = clamp(faceFocus * 1.1 + personSeparation * 0.2, 0, 1);
    const bodyDominance = clamp(bodyDepth * 0.85 + ultrasonicNearness * 0.25, 0, 1);
    const pitch = clamp(
      (0.2 + personSeparation * 0.55 + faceDominance * 0.35 + foilCoupling * 0.18) * shieldingLoss,
      0,
      1,
    );
    const volume = clamp(
      (0.16 + bodyDominance * foilBoost * 0.68 + ultrasonicNearness * 0.3) * shieldingLoss,
      0,
      1,
    );
    const energy = clamp(
      (0.08 +
        bodyDepth * 0.28 +
        faceFocus * 0.24 +
        personSeparation * 0.18 +
        foilCoupling * 0.34 +
        shielding * 0.12 +
        ultrasonicNearness * 0.26) *
        clamp(0.22 + shieldingLoss, 0, 1),
      0,
      1,
    );

    return {
      pitch,
      volume,
      energy,
      centerX: clamp(0.2 + personSeparation * 0.62 + foilCoupling * 0.1 - shielding * 0.08, 0.08, 0.92),
      centerY: clamp(0.82 - bodyDepth * 0.48 - faceFocus * 0.12 + shielding * 0.1, 0.08, 0.92),
      centerZ: lerp(-0.85, 0.95, clamp(energy * 0.75 + foilCoupling * 0.15, 0, 1)),
    };
  }

  getTopControllers(limit = 6) {
    return [...this.controllerState.values()]
      .sort((left, right) => {
        if (right.activity !== left.activity) {
          return right.activity - left.activity;
        }

        return right.seen - left.seen;
      })
      .slice(0, limit);
  }

  recordMessage(message) {
    this.messageLog.unshift({
      id: this.messageCounter,
      summary: summarizeMessage(message),
    });

    if (this.messageLog.length > 16) {
      this.messageLog.length = 16;
    }
  }
}

function summarizeMessage(message) {
  if (message.type === "cc") {
    return `CC ${message.controller} / value ${message.rawValue} / ch ${message.channel + 1}`;
  }

  if (message.type === "pitchbend") {
    return `Pitch Bend / ${message.value.toFixed(3)} / ch ${message.channel + 1}`;
  }

  if (message.type === "noteon") {
    return `Note On ${message.note} / vel ${Math.round(message.velocity * 127)} / ch ${message.channel + 1}`;
  }

  if (message.type === "noteoff") {
    return `Note Off ${message.note} / ch ${message.channel + 1}`;
  }

  return `Unknown ${message.data1 ?? 0} ${message.data2 ?? 0}`;
}
