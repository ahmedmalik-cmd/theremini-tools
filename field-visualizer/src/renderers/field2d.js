import { lerp } from "../model.js";

export class Field2DRenderer {
  constructor(canvas, model) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.model = model;
    this.particles = this.createParticles(180);
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  createParticles(count) {
    return Array.from({ length: count }, () => ({
      x: Math.random(),
      y: Math.random(),
      size: 1 + Math.random() * 2.5,
      drift: 0.25 + Math.random() * 0.8,
    }));
  }

  resize() {
    const bounds = this.canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.floor(bounds.width * ratio);
    this.canvas.height = Math.floor(bounds.height * ratio);
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.viewport = { width: bounds.width, height: bounds.height };
  }

  draw(timeSeconds) {
    const { context: ctx, viewport, model } = this;
    const width = viewport.width;
    const height = viewport.height;
    const centerX = model.centerX * width;
    const centerY = model.centerY * height;
    const disturbanceRadius = lerp(40, Math.min(width, height) * 0.28, model.disturbance);

    ctx.clearRect(0, 0, width, height);
    this.drawBackground(ctx, width, height, centerX, centerY, disturbanceRadius);
    this.drawFieldLines(ctx, width, height, centerX, centerY, disturbanceRadius, timeSeconds);
    this.drawHistory(ctx);
    this.drawParticles(ctx, width, height, centerX, centerY, disturbanceRadius, timeSeconds);
    this.drawCore(ctx, centerX, centerY, disturbanceRadius);
  }

  drawBackground(ctx, width, height, centerX, centerY, radius) {
    const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius * 2.8);
    gradient.addColorStop(0, "rgba(244, 191, 103, 0.16)");
    gradient.addColorStop(0.4, "rgba(111, 241, 220, 0.12)");
    gradient.addColorStop(1, "rgba(3, 19, 26, 0)");

    ctx.fillStyle = "rgba(5, 22, 28, 0.26)";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
  }

  drawFieldLines(ctx, width, height, centerX, centerY, radius, timeSeconds) {
    ctx.strokeStyle = "rgba(111, 241, 220, 0.16)";
    ctx.lineWidth = 1;

    const rows = 28;
    const cols = 38;

    for (let row = 0; row <= rows; row += 1) {
      ctx.beginPath();

      for (let col = 0; col <= cols; col += 1) {
        const x = (col / cols) * width;
        const y = (row / rows) * height;
        const dx = x - centerX;
        const dy = y - centerY;
        const distance = Math.hypot(dx, dy) || 1;
        const influence = Math.max(0, 1 - distance / (radius * 2.3));
        const wave = Math.sin(distance * 0.03 - timeSeconds * 3.1) * influence * radius * 0.08;
        const offsetX = (dx / distance) * influence * radius * 0.22;
        const offsetY = (dy / distance) * influence * radius * 0.22 + wave;
        const warpedX = x + offsetX;
        const warpedY = y + offsetY;

        if (col === 0) {
          ctx.moveTo(warpedX, warpedY);
        } else {
          ctx.lineTo(warpedX, warpedY);
        }
      }

      ctx.stroke();
    }
  }

  drawHistory(ctx) {
    for (const sample of this.model.history) {
      ctx.beginPath();
      ctx.fillStyle = `rgba(244, 191, 103, ${sample.life * 0.28})`;
      ctx.arc(sample.x, sample.y, 4 + (1 - sample.life) * 10, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  drawParticles(ctx, width, height, centerX, centerY, radius, timeSeconds) {
    for (const particle of this.particles) {
      const baseX = particle.x * width;
      const baseY = ((particle.y + timeSeconds * 0.012 * particle.drift) % 1) * height;
      const dx = baseX - centerX;
      const dy = baseY - centerY;
      const distance = Math.hypot(dx, dy) || 1;
      const influence = Math.max(0, 1 - distance / (radius * 2));
      const swirl = timeSeconds * (0.4 + particle.drift);
      const offsetX = (dx / distance) * influence * radius * 0.18 + Math.cos(swirl) * influence * 6;
      const offsetY = (dy / distance) * influence * radius * 0.18 + Math.sin(swirl) * influence * 6;

      ctx.beginPath();
      ctx.fillStyle = `rgba(111, 241, 220, ${0.2 + influence * 0.55})`;
      ctx.arc(baseX + offsetX, baseY + offsetY, particle.size + influence * 1.8, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  drawCore(ctx, centerX, centerY, radius) {
    const glow = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
    glow.addColorStop(0, "rgba(255, 244, 216, 0.9)");
    glow.addColorStop(0.18, "rgba(244, 191, 103, 0.7)");
    glow.addColorStop(0.48, "rgba(111, 241, 220, 0.25)");
    glow.addColorStop(1, "rgba(111, 241, 220, 0)");

    ctx.beginPath();
    ctx.fillStyle = glow;
    ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}
