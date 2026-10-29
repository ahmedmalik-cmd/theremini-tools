"""
theremin_spatial.py

Extracts every derivable signal from the Theremini and renders a
2D spatial estimate of hand positions using triangulation from the
two antenna distance proxies.

Derived signals:
  pitch_raw      CC20 0-127
  volume_raw     CC2  0-127
  pitch_dist     estimated distance from rod antenna (normalized)
  volume_dist    estimated distance from loop antenna (normalized)
  pitch_vel      rate of change of pitch signal (Hz/s equivalent)
  volume_vel     rate of change of volume signal
  pitch_accel    second derivative of pitch
  volume_accel   second derivative of volume
  anomaly        geometric mean (pitch * volume)^0.5
  gesture_mag    magnitude of velocity vector
  gesture_angle  direction of gesture in joint space
  2D position    triangulated hand estimate (see notes above)
"""

import os
import mido
import threading
import time
import math
import random
import pygame
import numpy as np
from collections import deque

MIDI_PORT  = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
CC_VOLUME  = 2
CC_PITCH   = 20
W, H       = 1280, 800
FPS        = 60
HIST       = 256   # samples kept for derivatives + display

# physical geometry estimate (pixels, roughly to scale on screen)
# rod antenna is on the right side of the device body
# loop antenna is on the left
ROD_XY  = (760, 480)   # pitch antenna position on screen
LOOP_XY = (380, 480)   # volume antenna position on screen
MAX_DIST = 340          # pixel radius = "arms length" from each antenna

# ── signal processing ─────────────────────────────────────────────────────────
class SignalBuffer:
    """Ring buffer with velocity + acceleration via finite differences."""
    def __init__(self, maxlen=HIST):
        self.vals   = deque([0.0] * maxlen, maxlen=maxlen)
        self.times  = deque([time.perf_counter()] * maxlen, maxlen=maxlen)
        self.vel    = 0.0
        self.accel  = 0.0
        self._prev_vel = 0.0

    def push(self, v):
        now = time.perf_counter()
        dt  = now - self.times[-1]
        if dt > 0.0001:
            new_vel     = (v - self.vals[-1]) / dt
            self.accel  = (new_vel - self.vel) / dt
            self.vel    = new_vel * 0.4 + self.vel * 0.6   # smooth
        self.vals.append(v)
        self.times.append(now)

    def numpy(self):
        return np.array(self.vals)

    def smoothed(self, n=6):
        arr = list(self.vals)
        return sum(arr[-n:]) / n

class ThereminInput:
    def __init__(self):
        self.pitch_buf  = SignalBuffer()
        self.volume_buf = SignalBuffer()
        self._running   = False

    @property
    def pitch(self):  return self.pitch_buf.vals[-1]
    @property
    def volume(self): return self.volume_buf.vals[-1]
    @property
    def pitch_vel(self):   return self.pitch_buf.vel
    @property
    def volume_vel(self):  return self.volume_buf.vel
    @property
    def pitch_accel(self): return self.pitch_buf.accel
    @property
    def volume_accel(self):return self.volume_buf.accel
    @property
    def anomaly(self):
        p, v = self.pitch, self.volume
        return (p * v) ** 0.5 if p > 0 and v > 0 else 0.0
    @property
    def gesture_mag(self):
        return math.hypot(self.pitch_vel, self.volume_vel)
    @property
    def gesture_angle(self):
        return math.atan2(self.volume_vel, self.pitch_vel)

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self): self._running = False

    def _loop(self):
        try:
            with mido.open_input(MIDI_PORT) as port:
                while self._running:
                    for msg in port.iter_pending():
                        if msg.type == 'control_change':
                            if msg.control == CC_PITCH:
                                self.pitch_buf.push(msg.value / 127.0)
                            elif msg.control == CC_VOLUME:
                                self.volume_buf.push(msg.value / 127.0)
                    time.sleep(0.001)
        except Exception as e:
            print(f"[midi] {e}")

# ── spatial triangulation ─────────────────────────────────────────────────────
def estimate_hand_position(pitch, volume):
    """
    Given two distance proxies (0=close, 1=far from each antenna),
    estimate the most probable 2D hand position.

    The Theremini's field falls off roughly as inverse square of distance.
    We invert this to get a distance estimate from each antenna,
    then find the intersection of two circles.

    Returns (x, y) in screen space, or None if no valid intersection.
    """
    if pitch < 0.01 and volume < 0.01:
        return None

    # invert the approximate response curve to get distance
    # pitch increases as hand approaches rod → dist_rod = 1 - pitch (approx)
    # volume increases as hand approaches loop → dist_loop = 1 - volume
    dist_rod  = (1.0 - pitch)  * MAX_DIST
    dist_loop = (1.0 - volume) * MAX_DIST

    rx, ry = ROD_XY
    lx, ly = LOOP_XY
    d = math.hypot(rx - lx, ry - ly)   # distance between antennas

    if d < 1:
        return None

    # two-circle intersection
    a = (dist_rod**2 - dist_loop**2 + d**2) / (2 * d)
    h_sq = dist_rod**2 - a**2

    if h_sq < 0:
        # no intersection — clamp to midpoint on line between antennas
        t = dist_rod / (dist_rod + dist_loop)
        return (int(lx + t * (rx - lx)), int(ly + t * (ry - ly)))

    h = math.sqrt(h_sq)
    dx, dy = (rx - lx) / d, (ry - ly) / d

    # two possible intersection points — pick the one in front of the device (above)
    mx = lx + a * dx
    my = ly + a * dy

    x1 = mx + h * dy
    y1 = my - h * dx
    x2 = mx - h * dy
    y2 = my + h * dx

    # prefer the point that is above (y smaller) the antenna line
    return (int(x1), int(y1)) if y1 <= y2 else (int(x2), int(y2))


# ── rendering helpers ─────────────────────────────────────────────────────────
def lerp(a, b, t): return a + (b - a) * t
def lerpC(a, b, t): return tuple(int(lerp(a[i], b[i], t)) for i in range(3))
def clamp(v, lo, hi): return max(lo, min(hi, v))

def draw_field_iso(surf, cx, cy, value, color, radius, n_rings=8):
    """Isometric field rings — denser and brighter near center when active."""
    for i in range(n_rings, 0, -1):
        t    = i / n_rings
        r    = int(radius * t)
        # intensity: field compresses inward as value rises
        edge = clamp(1.0 - t + value * t * 0.8, 0, 1)
        alpha = int(edge * 55)
        col  = lerpC((10,10,15), color, edge * value)
        s    = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*col, alpha), (r, r), r, 1)
        surf.blit(s, (cx-r, cy-r))

def draw_hand_object(surf, x, y, pitch, volume, gesture_mag, gesture_angle, font):
    """
    Render a hand-like presence object at the estimated position.
    Size encodes proximity, color encodes which field dominates,
    trailing lines encode gesture direction.
    """
    if x is None:
        return

    anomaly = (pitch * volume) ** 0.5
    size    = int(12 + anomaly * 40)

    # dominant field color blend
    col = lerpC((80, 140, 255), (255, 80, 160), volume / (pitch + volume + 0.001))

    # gesture trail direction
    speed = clamp(gesture_mag * 0.4, 0, 1)
    if speed > 0.05:
        tx = x - int(math.cos(gesture_angle) * speed * 80)
        ty = y - int(math.sin(gesture_angle) * speed * 80)
        pygame.draw.line(surf, (*col, 60), (x, y), (tx, ty), max(1, int(speed * 4)))

    # outer glow
    for ring in range(4, 0, -1):
        r = size + ring * 6
        a = int(anomaly * 35 * (5 - ring))
        s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*col, a), (r, r), r)
        surf.blit(s, (x-r, y-r))

    # body — two overlapping ellipses suggesting a palm
    palm_w = size
    palm_h = int(size * 0.75)
    body_surf = pygame.Surface((palm_w*2+4, palm_h*2+4), pygame.SRCALPHA)
    pygame.draw.ellipse(body_surf, (*col, 200), (2, 2, palm_w*2, palm_h*2))
    surf.blit(body_surf, (x - palm_w - 2, y - palm_h - 2))

    # finger stubs — 5 short lines radiating outward and slightly upward
    for i in range(5):
        angle = math.pi * (0.15 + i * 0.175) + math.pi   # fan upward
        fx = x + int(math.cos(angle) * (size + 8))
        fy = y + int(math.sin(angle) * (size + 8) * 0.7)
        pygame.draw.line(surf, (*col, 160), (x, y), (fx, fy), 2)
        pygame.draw.circle(surf, (*col, 180), (fx, fy), 3)

    # core dot
    pygame.draw.circle(surf, (240, 240, 240), (x, y), 4)

def draw_signal_history(surf, buf, x, y, w, h, color, label, font, norm_range=None):
    """Mini sparkline for a signal buffer."""
    pygame.draw.rect(surf, (18, 18, 20), (x, y, w, h))
    pygame.draw.rect(surf, (35, 35, 40), (x, y, w, h), 1)
    arr = buf.numpy()
    if norm_range:
        lo, hi = norm_range
        arr = np.clip((arr - lo) / (hi - lo + 1e-9), 0, 1)
    pts = []
    step = w / len(arr)
    for i, v in enumerate(arr):
        px = int(x + i * step)
        py = int(y + h - v * h)
        pts.append((px, py))
    if len(pts) > 1:
        pygame.draw.lines(surf, color, False, pts, 1)
    lbl = font.render(label, True, (70, 70, 80))
    surf.blit(lbl, (x + 3, y + 2))
    val_txt = font.render(f"{arr[-1]:.3f}", True, color)
    surf.blit(val_txt, (x + w - val_txt.get_width() - 4, y + 2))

def draw_phase_portrait(surf, pitch_buf, volume_buf, cx, cy, size, font):
    """
    Phase portrait: pitch velocity vs volume velocity.
    Reveals gesture structure — circles = periodic, lines = single axis.
    """
    pygame.draw.rect(surf, (12, 12, 16),
                     (cx - size//2, cy - size//2, size, size))
    pygame.draw.rect(surf, (35, 35, 40),
                     (cx - size//2, cy - size//2, size, size), 1)

    p_arr = pitch_buf.numpy()
    v_arr = volume_buf.numpy()
    # velocity via diff
    pv = np.diff(p_arr)
    vv = np.diff(v_arr)
    scale = size / 2 / (np.max(np.abs(pv)) + 1e-6) * 0.4
    v_scale = size / 2 / (np.max(np.abs(vv)) + 1e-6) * 0.4

    pts = []
    for i in range(len(pv)):
        px = int(cx + pv[i] * scale)
        py = int(cy - vv[i] * v_scale)
        px = clamp(px, cx - size//2 + 2, cx + size//2 - 2)
        py = clamp(py, cy - size//2 + 2, cy + size//2 - 2)
        pts.append((px, py, i / len(pv)))

    for i in range(1, len(pts)):
        age = pts[i][2]
        col = lerpC((20, 30, 60), (120, 180, 255), age)
        pygame.draw.line(surf, col, pts[i-1][:2], pts[i][:2], 1)

    # axes
    pygame.draw.line(surf, (30, 30, 40),
                     (cx, cy - size//2), (cx, cy + size//2))
    pygame.draw.line(surf, (30, 30, 40),
                     (cx - size//2, cy), (cx + size//2, cy))

    lbl = font.render("phase portrait  dP/dt vs dV/dt", True, (50, 50, 60))
    surf.blit(lbl, (cx - size//2, cy + size//2 + 4))


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    theremin = ThereminInput()
    theremin.start()

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Theremini / Spatial Field")
    clock  = pygame.time.Clock()
    font   = pygame.font.SysFont("monospace", 11)
    font_m = pygame.font.SysFont("monospace", 12)
    font_l = pygame.font.SysFont("monospace", 14)

    # particle cloud
    N = 400
    class Particle:
        def __init__(self):
            self.reset()
        def reset(self):
            cx = (ROD_XY[0] + LOOP_XY[0]) // 2
            cy = (ROD_XY[1] + LOOP_XY[1]) // 2
            spread = MAX_DIST * 1.1
            self.x = random.gauss(cx, spread * 0.4)
            self.y = random.gauss(cy, spread * 0.3)
            self.vx = random.uniform(-0.3, 0.3)
            self.vy = random.uniform(-0.3, 0.3)
            self.life = random.uniform(0.2, 0.8)
            self.size = random.uniform(1, 2.5)
            self.phase = random.uniform(0, math.tau)

        def update(self, pitch, volume, dt, t):
            # drift toward whichever antenna is more active
            rx, ry = ROD_XY
            lx, ly = LOOP_XY
            dx_r = (rx - self.x) * pitch  * 0.0003
            dy_r = (ry - self.y) * pitch  * 0.0003
            dx_l = (lx - self.x) * volume * 0.0003
            dy_l = (ly - self.y) * volume * 0.0003
            self.vx += dx_r + dx_l + random.gauss(0, 0.05)
            self.vy += dy_r + dy_l + random.gauss(0, 0.05)
            self.vx *= 0.97
            self.vy *= 0.97
            self.x += self.vx
            self.y += self.vy
            self.phase += dt * (0.5 + (pitch + volume))
            anomaly = (pitch * volume) ** 0.5
            self.life = 0.2 + anomaly * 0.6 + math.sin(self.phase) * 0.1

        def draw(self, surf, pitch, volume):
            t_p = clamp(
                1.0 - math.hypot(self.x - ROD_XY[0],  self.y - ROD_XY[1])  / MAX_DIST, 0, 1)
            t_v = clamp(
                1.0 - math.hypot(self.x - LOOP_XY[0], self.y - LOOP_XY[1]) / MAX_DIST, 0, 1)
            blend = t_v / (t_p + t_v + 1e-6)
            col   = lerpC((60, 130, 255), (255, 70, 160), blend)
            alpha = int(clamp(self.life * 180, 0, 200))
            s = int(clamp(self.size * (0.5 + (pitch+volume)*0.5), 1, 4))
            surf_a = pygame.Surface((s*2+2, s*2+2), pygame.SRCALPHA)
            pygame.draw.circle(surf_a, (*col, alpha), (s+1, s+1), s)
            surf.blit(surf_a, (int(self.x)-s-1, int(self.y)-s-1))

    particles = [Particle() for _ in range(N)]

    # position trail
    TRAIL = 400
    pos_trail = deque(maxlen=TRAIL)

    t = 0.0
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        t += dt

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: running = False
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE: running = False

        pitch   = theremin.pitch
        volume  = theremin.volume
        anomaly = theremin.anomaly
        p_vel   = theremin.pitch_vel
        v_vel   = theremin.volume_vel
        g_mag   = theremin.gesture_mag
        g_ang   = theremin.gesture_angle

        screen.fill((8, 8, 10))

        # ── draw device outline ────────────────────────────────────────────
        device_rect = pygame.Rect(280, 510, 580, 55)
        pygame.draw.rect(screen, (22, 22, 26), device_rect, border_radius=10)
        pygame.draw.rect(screen, (40, 40, 50), device_rect, 1, border_radius=10)
        dev_lbl = font.render("Moog Theremini", True, (45, 45, 55))
        screen.blit(dev_lbl, (W//2 - dev_lbl.get_width()//2, 530))

        # rod antenna
        pygame.draw.rect(screen, (50, 60, 80),
                         (ROD_XY[0]-3, ROD_XY[1]-120, 6, 120))
        pygame.draw.circle(screen, (80, 120, 180), ROD_XY, 8)
        r_lbl = font.render("rod", True, (60, 80, 120))
        screen.blit(r_lbl, (ROD_XY[0] - r_lbl.get_width()//2, ROD_XY[1] + 10))

        # loop antenna
        pygame.draw.ellipse(screen, (50, 40, 60),
                            (LOOP_XY[0]-30, LOOP_XY[1]-70, 60, 70), 3)
        pygame.draw.circle(screen, (160, 60, 120), LOOP_XY, 8)
        l_lbl = font.render("loop", True, (120, 60, 100))
        screen.blit(l_lbl, (LOOP_XY[0] - l_lbl.get_width()//2, LOOP_XY[1] + 10))

        # ── field iso rings ────────────────────────────────────────────────
        draw_field_iso(screen, ROD_XY[0],  ROD_XY[1],  pitch,  (80,160,255), MAX_DIST)
        draw_field_iso(screen, LOOP_XY[0], LOOP_XY[1], volume, (255,80,160), MAX_DIST)

        # ── particles ─────────────────────────────────────────────────────
        for p in particles:
            p.update(pitch, volume, dt, t)
            p.draw(screen, pitch, volume)

        # ── triangulated hand position ─────────────────────────────────────
        hand_pos = estimate_hand_position(pitch, volume)
        if hand_pos:
            pos_trail.append((*hand_pos, pitch, volume))

        # draw trail
        for i, (tx, ty, tp, tv) in enumerate(pos_trail):
            age   = i / len(pos_trail)
            a     = int(age * 100)
            col   = lerpC((30,60,130), (200,60,140), tv/(tp+tv+1e-6))
            s_    = pygame.Surface((4,4), pygame.SRCALPHA)
            pygame.draw.circle(s_, (*col, a), (2,2), 2)
            screen.blit(s_, (tx-2, ty-2))

        # draw hand object
        if hand_pos:
            draw_hand_object(screen, hand_pos[0], hand_pos[1],
                             pitch, volume, g_mag, g_ang, font)

        # ── right panel: data readouts ─────────────────────────────────────
        px0 = W - 300
        row_h = 62
        panels = [
            (theremin.pitch_buf,  (60,130,255), "pitch     CC20"),
            (theremin.volume_buf, (255,70,150), "volume    CC2"),
        ]
        for i, (buf, col, lbl) in enumerate(panels):
            draw_signal_history(screen, buf, px0, 20 + i*row_h,
                                280, 50, col, lbl, font)

        # velocity sparklines
        vel_buf_p = SignalBuffer.__new__(SignalBuffer)
        vel_buf_p.vals = deque([0.0]*HIST, HIST)
        vel_arr = np.diff(theremin.pitch_buf.numpy()) * 60
        for v in vel_arr[-HIST+1:]:
            vel_buf_p.vals.append(v)

        draw_signal_history(screen, vel_buf_p,
                            px0, 20 + 2*row_h, 280, 50,
                            (100,200,180), "pitch vel  d/dt",
                            font, norm_range=(-1, 1))

        vol_vel_buf = SignalBuffer.__new__(SignalBuffer)
        vol_vel_buf.vals = deque([0.0]*HIST, HIST)
        vol_arr = np.diff(theremin.volume_buf.numpy()) * 60
        for v in vol_arr[-HIST+1:]:
            vol_vel_buf.vals.append(v)

        draw_signal_history(screen, vol_vel_buf,
                            px0, 20 + 3*row_h, 280, 50,
                            (255,160,80), "volume vel d/dt",
                            font, norm_range=(-1, 1))

        # ── phase portrait ─────────────────────────────────────────────────
        draw_phase_portrait(screen,
                            theremin.pitch_buf, theremin.volume_buf,
                            px0 + 140, 20 + 4*row_h + 80,
                            220, font)

        # ── bottom data strip ──────────────────────────────────────────────
        strip_y = H - 80
        pygame.draw.line(screen, (22,22,26), (0, strip_y-8), (W, strip_y-8))

        fields = [
            ("pitch",        f"{pitch:.3f}",      (80,140,255)),
            ("volume",       f"{volume:.3f}",     (255,80,160)),
            ("anomaly",      f"{anomaly:.3f}",    (200,160,60)),
            ("pitch vel",    f"{p_vel:+.2f}",     (100,200,180)),
            ("volume vel",   f"{v_vel:+.2f}",     (200,140,80)),
            ("gesture mag",  f"{g_mag:.3f}",      (180,180,100)),
            ("gesture ang",  f"{math.degrees(g_ang):+.1f}°", (140,100,200)),
        ]
        col_w = (W - 20) // len(fields)
        for i, (lbl, val, col) in enumerate(fields):
            x = 10 + i * col_w
            l = font.render(lbl, True, (55,55,65))
            v = font_m.render(val, True, col)
            screen.blit(l, (x, strip_y))
            screen.blit(v, (x, strip_y + 16))

        # anomaly bar
        bw = int((W - 20) * anomaly)
        bar_col = lerpC((30,50,120), (220,80,60), anomaly)
        pygame.draw.rect(screen, (16,16,20), (10, H-16, W-20, 8))
        if bw: pygame.draw.rect(screen, bar_col, (10, H-16, bw, 8))

        # title
        ttl = font_l.render("THEREMINI  /  SPATIAL FIELD  /  ALL SIGNALS", True, (35,35,45))
        screen.blit(ttl, (W//2 - ttl.get_width()//2, 8))

        fps = font.render(f"{clock.get_fps():.0f}fps  esc", True, (30,30,35))
        screen.blit(fps, (W - fps.get_width() - 8, 8))

        pygame.display.flip()

    theremin.stop()
    pygame.quit()

if __name__ == '__main__':
    main()
