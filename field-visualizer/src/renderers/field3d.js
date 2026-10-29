import { lerp } from "../model.js";

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const SLICE_CONFIG = [
  {
    id: 0,
    name: "near",
    displacement: 0.48,
    orbit: 0.02,
    pulse: 0.08,
    memoryLag: 0.18,
    alphaBoost: 0.24,
    radiusBoost: 1.7,
  },
  {
    id: 1,
    name: "mid",
    displacement: 0.34,
    orbit: 0.1,
    pulse: 0.05,
    memoryLag: 0.34,
    alphaBoost: 0.16,
    radiusBoost: 1.15,
  },
  {
    id: 2,
    name: "far",
    displacement: 0.2,
    orbit: 0.16,
    pulse: 0.03,
    memoryLag: 0.52,
    alphaBoost: 0.08,
    radiusBoost: 0.85,
  },
];

function rotateX(point, angle) {
  const sin = Math.sin(angle);
  const cos = Math.cos(angle);

  return {
    x: point.x,
    y: point.y * cos - point.z * sin,
    z: point.y * sin + point.z * cos,
  };
}

function rotateY(point, angle) {
  const sin = Math.sin(angle);
  const cos = Math.cos(angle);

  return {
    x: point.x * cos + point.z * sin,
    y: point.y,
    z: -point.x * sin + point.z * cos,
  };
}

function rotateZ(point, angle) {
  const sin = Math.sin(angle);
  const cos = Math.cos(angle);

  return {
    x: point.x * cos - point.y * sin,
    y: point.x * sin + point.y * cos,
    z: point.z,
  };
}

function createPaletteTint(sliceId, hueMix, ageMix, pitch, volume, energy) {
  if (sliceId === 0) {
    return {
      red: Math.round(lerp(247, 255, hueMix * 0.45 + energy * 0.28)),
      green: Math.round(lerp(215, 178, hueMix * 0.55 + ageMix * 0.25)),
      blue: Math.round(lerp(154, 102, volume * 0.42 + ageMix * 0.3)),
    };
  }

  if (sliceId === 1) {
    return {
      red: Math.round(lerp(126, 174, pitch * 0.4 + ageMix * 0.2)),
      green: Math.round(lerp(240, 214, hueMix * 0.4 + volume * 0.25)),
      blue: Math.round(lerp(218, 167, energy * 0.34 + ageMix * 0.28)),
    };
  }

  return {
    red: Math.round(lerp(94, 162, hueMix * 0.55 + ageMix * 0.3)),
    green: Math.round(lerp(182, 224, pitch * 0.22 + volume * 0.2)),
    blue: Math.round(lerp(255, 236, ageMix * 0.42 + energy * 0.18)),
  };
}

function createKinectTint(playerIndex, depthMix, energy) {
  if (playerIndex === 1) {
    return {
      red: 255,
      green: Math.round(lerp(180, 232, depthMix * 0.5 + energy * 0.2)),
      blue: Math.round(lerp(116, 168, depthMix * 0.35)),
    };
  }

  if (playerIndex === 2) {
    return {
      red: Math.round(lerp(120, 164, energy * 0.2)),
      green: 232,
      blue: Math.round(lerp(208, 244, depthMix * 0.3)),
    };
  }

  if (playerIndex > 2) {
    return {
      red: Math.round(lerp(130, 220, depthMix * 0.4)),
      green: Math.round(lerp(180, 214, energy * 0.2)),
      blue: 255,
    };
  }

  return {
    red: Math.round(lerp(74, 124, depthMix * 0.6)),
    green: Math.round(lerp(154, 210, depthMix * 0.5 + energy * 0.1)),
    blue: Math.round(lerp(240, 255, depthMix * 0.25)),
  };
}

function resolveAnchorPoint(source) {
  if ("x" in source && "y" in source && "z" in source) {
    return source;
  }

  return {
    x: source.baseX,
    y: source.baseY,
    z: source.baseZ,
  };
}

export class Field3DRenderer {
  constructor(canvas, model) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.model = model;
    this.viewport = { width: 0, height: 0 };
    this.mouseRotation = { x: 0.32, y: -0.6 };
    this.points = this.createPointCloud(1800);
    this.snapshots = [];
    this.snapshotInterval = 3.4;
    this.snapshotLimit = 3;
    this.lastSnapshotTime = Number.NEGATIVE_INFINITY;
    this.resize();

    window.addEventListener("resize", () => this.resize());
    window.addEventListener("pointermove", (event) => {
      const x = event.clientX / window.innerWidth;
      const y = event.clientY / window.innerHeight;
      this.mouseRotation.y = lerp(-0.95, 0.95, x);
      this.mouseRotation.x = lerp(0.95, -0.15, y);
    });
  }

  createPointCloud(count) {
    return Array.from({ length: count }, (_, index) => {
      const layerBias = index / Math.max(1, count - 1);
      const radius = Math.pow(Math.random(), 0.72) * lerp(0.9, 1.7, 1 - layerBias * 0.35);
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);

      const x = radius * Math.sin(phi) * Math.cos(theta);
      const y = radius * Math.cos(phi);
      const z = radius * Math.sin(phi) * Math.sin(theta);
      const normalizedDepth = clamp((z + 1.8) / 3.6, 0, 1);
      const sliceId = normalizedDepth < 0.36 ? 0 : normalizedDepth < 0.68 ? 1 : 2;

      return {
        id: index,
        baseX: x,
        baseY: y,
        baseZ: z,
        hueMix: radius / 1.7,
        sliceId,
        seed: Math.random() * Math.PI * 2,
      };
    });
  }

  resize() {
    const bounds = this.canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.floor(bounds.width * ratio);
    this.canvas.height = Math.floor(bounds.height * ratio);
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.viewport = { width: bounds.width, height: bounds.height };
  }

  project(point) {
    const depth = 4.9;
    const scale = depth / (depth - point.z);

    return {
      x: this.viewport.width * 0.5 + point.x * scale * this.viewport.width * 0.16,
      y: this.viewport.height * 0.5 - point.y * scale * this.viewport.height * 0.16,
      scale,
      z: point.z,
    };
  }

  captureSnapshot(timeSeconds, center, controls) {
    const capturedPoints = this.points
      .filter((point) => point.id % 3 === 0)
      .map((point) => ({
        ...point,
        captured: this.sculptPoint(point, center, controls, timeSeconds, {
          memoryAge: 0,
          memoryMix: 0,
          memoryStrength: 0,
        }),
      }));

    this.snapshots.unshift({
      createdAt: timeSeconds,
      points: capturedPoints,
    });

    if (this.snapshots.length > this.snapshotLimit) {
      this.snapshots.length = this.snapshotLimit;
    }
  }

  sculptPoint(point, center, controls, timeSeconds, memoryState = null) {
    const slice = SLICE_CONFIG[point.sliceId];
    const anchor = resolveAnchorPoint(memoryState?.anchor ?? point);
    const memoryAge = memoryState?.memoryAge ?? 0;
    const memoryMix = memoryState?.memoryMix ?? 0;
    const memoryStrength = memoryState?.memoryStrength ?? 0;
    const dx = anchor.x - center.x;
    const dy = anchor.y - center.y;
    const dz = anchor.z - center.z;
    const distance = Math.hypot(dx, dy, dz) || 1;
    const influenceRadius = lerp(1.2, 2.5, 0.4 + controls.volume * 0.2 + controls.disturbance * 0.4);
    const influence = Math.max(0, 1 - distance / influenceRadius);
    const repel = controls.force * influence * slice.displacement * (1 - memoryMix * 0.35);
    const pulse = Math.sin(
      distance * (4.2 + controls.pitch * 6.2) -
        timeSeconds * (1.3 + controls.motion * 2.8) -
        memoryAge * 4,
    );
    const phase = point.seed + timeSeconds * (0.1 + slice.id * 0.03) + memoryAge * 0.7;
    const drift = {
      x: Math.cos(phase + point.baseZ * 2.4) * slice.pulse * (0.2 + controls.turbulence * 0.8),
      y: Math.sin(phase * 1.12 + point.baseX * 1.6) * slice.pulse * (0.2 + controls.turbulence * 0.8),
      z: Math.cos(phase * 0.92 + point.baseY * 1.9) * slice.pulse * (0.2 + controls.turbulence * 0.8),
    };
    const inverseDistance = 1 / distance;
    const radial = {
      x: dx * inverseDistance,
      y: dy * inverseDistance,
      z: dz * inverseDistance,
    };
    const tangential = {
      x: -radial.z,
      y: radial.y * 0.18,
      z: radial.x,
    };
    const laggedCenterLift = memoryStrength * slice.memoryLag;

    return {
      x:
        anchor.x +
        radial.x * repel +
        tangential.x * controls.force * influence * slice.orbit +
        pulse * influence * slice.pulse +
        drift.x +
        (center.x - anchor.x) * laggedCenterLift * 0.06,
      y:
        anchor.y +
        radial.y * repel * 0.82 +
        tangential.y * controls.force * influence * slice.orbit +
        pulse * influence * slice.pulse * 0.8 +
        drift.y +
        (center.y - anchor.y) * laggedCenterLift * 0.04,
      z:
        anchor.z +
        radial.z * repel +
        tangential.z * controls.force * influence * slice.orbit +
        pulse * influence * slice.pulse +
        drift.z +
        (center.z - anchor.z) * laggedCenterLift * 0.08,
      influence,
    };
  }

  buildRenderPoints(timeSeconds, center, controls) {
    const renderPoints = [];

    for (const point of this.points) {
      renderPoints.push({
        point,
        sliceId: point.sliceId,
        ageMix: 0,
        position: this.sculptPoint(point, center, controls, timeSeconds),
        memoryWeight: 1,
      });
    }

    for (const snapshot of this.snapshots) {
      const ageSeconds = Math.max(0, timeSeconds - snapshot.createdAt);
      const ageMix = clamp(ageSeconds / (this.snapshotInterval * (this.snapshotLimit + 0.45)), 0, 1);
      const brightness = 1 - ageMix;

      if (brightness <= 0.02) {
        continue;
      }

      for (const point of snapshot.points) {
        renderPoints.push({
          point,
          sliceId: point.sliceId,
          ageMix,
          memoryWeight: brightness,
          position: this.sculptPoint(point, center, controls, timeSeconds, {
            anchor: point.captured,
            memoryAge: ageSeconds,
            memoryMix: ageMix,
            memoryStrength: brightness,
          }),
        });
      }
    }

    return renderPoints;
  }

  draw(timeSeconds) {
    const ctx = this.context;
    const { width, height } = this.viewport;
    const kinectFrame = this.model.kinectFrame;

    if (kinectFrame?.connected && kinectFrame.pointCloud?.length) {
      this.drawKinectCloud(ctx, width, height, timeSeconds, kinectFrame);
      return;
    }

    const disturbance = this.model.disturbance;
    const pitch = this.model.pitchProxy;
    const volume = this.model.volumeProxy;
    const motion = this.model.motion;
    const turbulence = this.model.turbulence;
    const center = {
      x: lerp(-1.2, 1.2, this.model.centerX),
      y: lerp(-1.05, 1.05, 1 - this.model.centerY),
      z: this.model.centerZ * 0.9,
    };
    const controls = {
      disturbance,
      pitch,
      volume,
      motion,
      turbulence,
      force: lerp(0.08, 0.92, clamp(volume * 0.55 + disturbance * 0.45, 0, 1)),
    };

    if (timeSeconds - this.lastSnapshotTime >= this.snapshotInterval) {
      this.captureSnapshot(timeSeconds, center, controls);
      this.lastSnapshotTime = timeSeconds;
    }

    const rotationX = this.mouseRotation.x * 0.3 + 0.12;
    const rotationY = this.mouseRotation.y * 0.38 + timeSeconds * 0.09;
    const rotationZ = Math.sin(timeSeconds * 0.18) * 0.06;
    const projected = [];
    const renderPoints = this.buildRenderPoints(timeSeconds, center, controls);

    ctx.clearRect(0, 0, width, height);
    this.drawBackground(ctx, width, height, disturbance, volume);
    this.drawDepthRings(ctx, width, height, disturbance, pitch, volume);

    for (const entry of renderPoints) {
      let sculpted = entry.position;
      sculpted = rotateX(sculpted, rotationX);
      sculpted = rotateY(sculpted, rotationY);
      sculpted = rotateZ(sculpted, rotationZ);

      const screen = this.project(sculpted);
      const slice = SLICE_CONFIG[entry.sliceId];
      const tint = createPaletteTint(entry.sliceId, entry.point.hueMix, entry.ageMix, pitch, volume, disturbance);

      projected.push({
        ...screen,
        sliceId: entry.sliceId,
        alpha: clamp(
          0.1 +
            slice.alphaBoost +
            entry.position.influence * 0.34 +
            entry.memoryWeight * 0.2 +
            (screen.scale - 0.85) * 0.15,
          0.05,
          0.96,
        ),
        radius: clamp(
          (0.8 +
            slice.radiusBoost +
            entry.position.influence * 2.8 +
            entry.memoryWeight * 1.2 +
            motion * 1.4) *
            screen.scale *
            lerp(0.86, 1.02, 1 - entry.ageMix),
          0.5,
          8.8,
        ),
        tint,
      });
    }

    projected.sort((left, right) => left.z - right.z);

    this.drawSnapshotGlow(ctx, width, height, volume, disturbance);
    this.drawTrail(ctx, width, height, rotationX, rotationY, rotationZ);
    this.drawForceCenter(ctx, center, rotationX, rotationY, rotationZ, width, height, controls.force);

    for (const point of projected) {
      ctx.beginPath();
      ctx.fillStyle = `rgba(${point.tint.red}, ${point.tint.green}, ${point.tint.blue}, ${point.alpha})`;
      ctx.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
      ctx.fill();
    }

    this.drawShell(ctx, width, height, disturbance, volume, pitch);
  }

  drawKinectCloud(ctx, width, height, timeSeconds, frame) {
    const disturbance = this.model.disturbance;
    const volume = this.model.volumeProxy;
    const pitch = this.model.pitchProxy;
    const rotationX = this.mouseRotation.x * 0.24 + 0.08;
    const rotationY = this.mouseRotation.y * 0.34 + timeSeconds * 0.05;
    const rotationZ = Math.sin(timeSeconds * 0.16) * 0.04;
    const projected = [];

    ctx.clearRect(0, 0, width, height);
    this.drawBackground(ctx, width, height, disturbance, volume);
    this.drawDepthRings(ctx, width, height, disturbance, pitch, volume);
    this.drawSnapshotGlow(ctx, width, height, volume, disturbance);
    this.drawTrail(ctx, width, height, rotationX, rotationY, rotationZ);

    for (const point of frame.pointCloud) {
      const [x, y, z, playerIndex] = point;
      let normalized = {
        x,
        y,
        z: 2.3 - z,
      };

      normalized = rotateX(normalized, rotationX);
      normalized = rotateY(normalized, rotationY);
      normalized = rotateZ(normalized, rotationZ);

      const screen = this.project(normalized);
      const depthMix = clamp((z - 0.6) / 3.2, 0, 1);
      const tint = createKinectTint(playerIndex, depthMix, disturbance);

      projected.push({
        ...screen,
        alpha: clamp(0.18 + (1 - depthMix) * 0.44 + disturbance * 0.08, 0.16, 0.94),
        radius: clamp(screen.scale * lerp(1.1, 3.9, 1 - depthMix), 0.8, 5.8),
        tint,
      });
    }

    projected.sort((left, right) => left.z - right.z);

    for (const point of projected) {
      ctx.beginPath();
      ctx.fillStyle = `rgba(${point.tint.red}, ${point.tint.green}, ${point.tint.blue}, ${point.alpha})`;
      ctx.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
      ctx.fill();
    }

    for (const player of frame.players ?? []) {
      const anchor = {
        x: player.x,
        y: player.y,
        z: 2.3 - player.z,
      };

      this.drawForceCenter(ctx, anchor, rotationX, rotationY, rotationZ, width, height, 0.45 + disturbance * 0.35);
    }

    this.drawShell(ctx, width, height, disturbance, volume, pitch);
  }

  drawBackground(ctx, width, height, disturbance, volume) {
    const gradient = ctx.createRadialGradient(
      width * 0.52,
      height * 0.46,
      0,
      width * 0.52,
      height * 0.46,
      Math.max(width, height) * 0.7,
    );
    gradient.addColorStop(0, "rgba(35, 26, 18, 0.4)");
    gradient.addColorStop(0.38, "rgba(17, 29, 33, 0.32)");
    gradient.addColorStop(1, "rgba(3, 12, 18, 0.05)");

    ctx.fillStyle = "rgba(7, 14, 19, 0.34)";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    const haze = ctx.createLinearGradient(0, 0, 0, height);
    haze.addColorStop(0, `rgba(248, 211, 143, ${0.04 + volume * 0.06})`);
    haze.addColorStop(0.5, "rgba(71, 182, 205, 0.03)");
    haze.addColorStop(1, "rgba(132, 210, 229, 0.08)");
    ctx.fillStyle = haze;
    ctx.fillRect(0, 0, width, height);

    const halo = ctx.createRadialGradient(
      width * 0.5,
      height * 0.52,
      0,
      width * 0.5,
      height * 0.52,
      Math.min(width, height) * lerp(0.18, 0.38, disturbance),
    );
    halo.addColorStop(0, "rgba(248, 235, 205, 0.12)");
    halo.addColorStop(0.48, "rgba(248, 194, 112, 0.08)");
    halo.addColorStop(1, "rgba(85, 210, 231, 0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, width, height);
  }

  drawDepthRings(ctx, width, height, disturbance, pitch, volume) {
    const centerX = width * 0.5;
    const centerY = height * 0.5;
    const radii = [
      Math.min(width, height) * lerp(0.14, 0.18, volume),
      Math.min(width, height) * lerp(0.23, 0.29, disturbance),
      Math.min(width, height) * lerp(0.33, 0.42, pitch),
    ];

    for (let index = 0; index < radii.length; index += 1) {
      ctx.beginPath();
      ctx.strokeStyle =
        index === 0
          ? `rgba(249, 196, 113, ${0.08 + volume * 0.14})`
          : index === 1
            ? `rgba(129, 221, 219, ${0.06 + disturbance * 0.12})`
            : `rgba(110, 164, 255, ${0.05 + pitch * 0.08})`;
      ctx.lineWidth = 1 + index * 0.5;
      ctx.ellipse(centerX, centerY, radii[index], radii[index] * lerp(0.7, 1.1, 0.5 + index * 0.18), 0, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  drawSnapshotGlow(ctx, width, height, volume, disturbance) {
    const radius = Math.min(width, height) * lerp(0.16, 0.26, disturbance + volume * 0.3);
    const aura = ctx.createRadialGradient(width * 0.5, height * 0.5, 0, width * 0.5, height * 0.5, radius);
    aura.addColorStop(0, "rgba(255, 248, 229, 0.18)");
    aura.addColorStop(0.4, `rgba(249, 196, 113, ${0.07 + volume * 0.1})`);
    aura.addColorStop(1, "rgba(100, 221, 233, 0)");
    ctx.fillStyle = aura;
    ctx.fillRect(0, 0, width, height);
  }

  drawTrail(ctx, width, height, rotationX, rotationY, rotationZ) {
    const history = this.model.history;

    if (history.length < 2) {
      return;
    }

    ctx.beginPath();

    for (let index = 0; index < history.length; index += 1) {
      const sample = history[index];
      let point = {
        x: lerp(-1.2, 1.2, sample.x / width),
        y: lerp(-1.05, 1.05, 1 - sample.y / height),
        z: sample.z * 0.9,
      };

      point = rotateX(point, rotationX);
      point = rotateY(point, rotationY);
      point = rotateZ(point, rotationZ);

      const screen = this.project(point);

      if (index === 0) {
        ctx.moveTo(screen.x, screen.y);
      } else {
        ctx.lineTo(screen.x, screen.y);
      }
    }

    ctx.strokeStyle = "rgba(249, 196, 113, 0.32)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  drawForceCenter(ctx, center, rotationX, rotationY, rotationZ, width, height, force) {
    let point = rotateX(center, rotationX);
    point = rotateY(point, rotationY);
    point = rotateZ(point, rotationZ);
    const screen = this.project(point);
    const radius = lerp(4, 12, force) * screen.scale;

    const glow = ctx.createRadialGradient(screen.x, screen.y, 0, screen.x, screen.y, radius * 3.8);
    glow.addColorStop(0, "rgba(255, 247, 226, 0.9)");
    glow.addColorStop(0.28, `rgba(249, 196, 113, ${0.26 + force * 0.24})`);
    glow.addColorStop(1, "rgba(249, 196, 113, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(screen.x - radius * 4, screen.y - radius * 4, radius * 8, radius * 8);

    ctx.beginPath();
    ctx.fillStyle = "rgba(255, 246, 233, 0.9)";
    ctx.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.strokeStyle = `rgba(129, 221, 219, ${0.22 + force * 0.18})`;
    ctx.lineWidth = 1.5;
    ctx.ellipse(
      screen.x,
      screen.y,
      radius * lerp(1.6, 3.2, force),
      radius * lerp(1.1, 2.2, force),
      Math.sin(performance.now() * 0.00035) * 0.8,
      0,
      Math.PI * 2,
    );
    ctx.stroke();
  }

  drawShell(ctx, width, height, disturbance, volume, pitch) {
    ctx.beginPath();
    ctx.strokeStyle = `rgba(115, 218, 229, ${lerp(0.08, 0.28, disturbance + volume * 0.18)})`;
    ctx.lineWidth = lerp(1.2, 2.8, disturbance + volume * 0.2);
    ctx.ellipse(
      width * 0.5,
      height * 0.5,
      width * lerp(0.13, 0.24, disturbance + pitch * 0.18),
      height * lerp(0.16, 0.31, disturbance + volume * 0.2),
      Math.sin(performance.now() * 0.00016) * 0.72 + pitch * 0.8,
      0,
      Math.PI * 2,
    );
    ctx.stroke();
  }
}
