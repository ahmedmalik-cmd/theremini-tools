import os
import mido
import threading
import time
import math
import random
import pygame

MIDI_PORT = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
CC_VOLUME = 2
CC_PITCH  = 20
W, H      = 1100, 750
FPS       = 60

# ── MIDI reader ───────────────────────────────────────────────────────────────
class ThereminInput:
    def __init__(self):
        self.pitch  = 0.0
        self.volume = 0.0
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _loop(self):
        try:
            with mido.open_input(MIDI_PORT) as port:
                while self._running:
                    for msg in port.iter_pending():
                        if msg.type == 'control_change':
                            if msg.control == CC_PITCH:
                                self.pitch = msg.value / 127.0
                            elif msg.control == CC_VOLUME:
                                self.volume = msg.value / 127.0
                    time.sleep(0.001)
        except Exception as e:
            print(f"[midi] {e}")

# ── point cloud ───────────────────────────────────────────────────────────────
class FieldParticle:
    def __init__(self, cx, cy, radius):
        self.cx = cx
        self.cy = cy
        self.radius = radius
        self.reset()

    def reset(self):
        # distribute in a disc
        angle = random.uniform(0, math.tau)
        r     = random.uniform(0, self.radius) ** 0.6  # bias toward edge
        self.base_x = self.cx + math.cos(angle) * r * self.radius / self.radius
        self.base_y = self.cy + math.sin(angle) * r
        self.x = float(self.base_x)
        self.y = float(self.base_y)
        self.angle = angle
        self.dist  = r
        self.phase = random.uniform(0, math.tau)
        self.speed = random.uniform(0.005, 0.02)
        self.size  = random.uniform(1.2, 3.5)
        self.life  = random.uniform(0.4, 1.0)

    def update(self, value, dt, t):
        # field intensity at this particle's distance
        norm_dist = self.dist / self.radius
        intensity = value * max(0, 1.0 - norm_dist * 0.8)

        # drift tangentially, compressed by value
        drift = self.speed * (0.3 + value * 1.4)
        self.angle += drift * dt * 60
        self.phase += dt * (0.5 + value * 2.0)

        # radial breathing
        breath = math.sin(self.phase) * 8 * intensity
        r = self.dist + breath

        self.x = self.cx + math.cos(self.angle) * r
        self.y = self.cy + math.sin(self.angle) * r

        # life flicker influenced by field
        self.life = 0.3 + intensity * 0.7 + math.sin(self.phase * 1.3) * 0.1 * intensity
        self.life = max(0.05, min(1.0, self.life))

    def draw(self, surf, color_fn, value):
        alpha = int(self.life * 200)
        col   = color_fn(self.dist / self.radius, value, self.life)
        s = max(1, int(self.size * (0.5 + value * 0.8)))
        a_surf = pygame.Surface((s*2+2, s*2+2), pygame.SRCALPHA)
        pygame.draw.circle(a_surf, (*col, alpha), (s+1, s+1), s)
        surf.blit(a_surf, (int(self.x) - s - 1, int(self.y) - s - 1))


def pitch_color(norm_dist, value, life):
    # blue-cyan-white gradient, hotter toward center when active
    t = value * (1.0 - norm_dist * 0.6)
    r = int(30  + t * 120)
    g = int(100 + t * 100)
    b = int(200 + t * 55)
    return (min(255,r), min(255,g), min(255,b))

def volume_color(norm_dist, value, life):
    # magenta-rose-white
    t = value * (1.0 - norm_dist * 0.6)
    r = int(180 + t * 75)
    g = int(40  + t * 80)
    b = int(120 + t * 100)
    return (min(255,r), min(255,g), min(255,b))

def combined_color(norm_dist, value, life):
    # the overlap zone — amber/gold where both fields interact
    t = value * (1.0 - norm_dist * 0.5)
    r = int(200 + t * 55)
    g = int(140 + t * 80)
    b = int(20  + t * 60)
    return (min(255,r), min(255,g), min(255,b))


# ── hand ghost ────────────────────────────────────────────────────────────────
def draw_hand_presence(surf, cx, cy, value, color, label, font):
    if value < 0.01:
        return
    # pulsing outer glow rings
    for ring in range(4, 0, -1):
        r = int(18 + ring * 14 * value)
        alpha = int((5 - ring) * 15 * value)
        s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*color, alpha), (r, r), r)
        surf.blit(s, (cx - r, cy - r))

    # solid core
    core_r = int(6 + value * 10)
    pygame.draw.circle(surf, color, (cx, cy), core_r)

    # label
    txt = font.render(f"{label}  {value:.2f}", True, (*color, 180))
    surf.blit(txt, (cx - txt.get_width()//2, cy + core_r + 6))


# ── field overlap indicator ───────────────────────────────────────────────────
def draw_overlap_zone(surf, cx, cy, pitch, volume, font):
    anomaly = (pitch * volume) ** 0.5
    if anomaly < 0.02:
        return
    r = int(20 + anomaly * 60)
    col = (
        int(150 + anomaly * 105),
        int(100 + anomaly * 80),
        int(20  + anomaly * 40)
    )
    s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
    pygame.draw.circle(s, (*col, int(anomaly * 120)), (r, r), r)
    surf.blit(s, (cx - r, cy - r))
    pygame.draw.circle(surf, col, (cx, cy), 4)
    txt = font.render(f"field  {anomaly:.3f}", True, col)
    surf.blit(txt, (cx - txt.get_width()//2, cy + r + 8))


# ── layout constants ──────────────────────────────────────────────────────────
FIELD_R   = 220   # radius of each antenna field
P_CX      = 300   # pitch field center x
V_CX      = 760   # volume field center x
FIELD_CY  = 360   # shared y center

# hand presence anchors — placed at field edges facing each other
PITCH_HAND_X  = P_CX + int(FIELD_R * 0.55)
VOLUME_HAND_X = V_CX - int(FIELD_R * 0.55)
HAND_Y        = FIELD_CY

# overlap center (midpoint between the two fields)
OVERLAP_CX = (P_CX + V_CX) // 2
OVERLAP_CY = FIELD_CY


def main():
    theremin = ThereminInput()
    theremin.start()

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Theremini / Field Map")
    clock  = pygame.time.Clock()
    font   = pygame.font.SysFont("monospace", 11)
    font_lg = pygame.font.SysFont("monospace", 13)

    # generate particles for each field
    N = 320
    pitch_particles  = [FieldParticle(P_CX,  FIELD_CY, FIELD_R) for _ in range(N)]
    volume_particles = [FieldParticle(V_CX,  FIELD_CY, FIELD_R) for _ in range(N)]

    # persistent trail of the joint state
    TRAIL = 300
    trail_pts = []  # (x, y, pitch, volume)

    t = 0.0
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        t += dt

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                running = False

        pitch  = theremin.pitch
        volume = theremin.volume
        anomaly = (pitch * volume) ** 0.5

        screen.fill((8, 8, 10))

        # ── field boundary circles ─────────────────────────────────────────
        for cx, val, col in [(P_CX, pitch, (40,80,160)), (V_CX, volume, (140,40,100))]:
            for ring in [1.0, 0.75, 0.5, 0.25]:
                r = int(FIELD_R * ring)
                alpha = int(12 + val * 20)
                s = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
                pygame.draw.circle(s, (*col, alpha), (r, r), r, 1)
                screen.blit(s, (cx-r, FIELD_CY-r))

        # ── update + draw particles ────────────────────────────────────────
        for p in pitch_particles:
            p.update(pitch, dt, t)
            p.draw(screen, pitch_color, pitch)

        for p in volume_particles:
            p.update(volume, dt, t)
            p.draw(screen, volume_color, volume)

        # ── overlap zone ───────────────────────────────────────────────────
        draw_overlap_zone(screen, OVERLAP_CX, OVERLAP_CY, pitch, volume, font)

        # ── joint-state trail ──────────────────────────────────────────────
        # map pitch → x across the full canvas, volume → y
        tx = int(80 + pitch  * (W - 160))
        ty = int(H - 60 - volume * (H * 0.25))
        trail_pts.append((tx, ty, pitch, volume))
        if len(trail_pts) > TRAIL:
            trail_pts.pop(0)

        for i in range(1, len(trail_pts)):
            px, py, pp, pv = trail_pts[i-1]
            nx, ny, np_, nv = trail_pts[i]
            age = i / len(trail_pts)
            a   = int(age * 120)
            r_  = int(50 + age * 180)
            g_  = int(40 + age * 80)
            b_  = int(150 + age * 60)
            s = pygame.Surface((4, 4), pygame.SRCALPHA)
            pygame.draw.circle(s, (r_, g_, b_, a), (2, 2), 2)
            screen.blit(s, (nx-2, ny-2))

        # ── hand presence ghosts ───────────────────────────────────────────
        draw_hand_presence(screen, PITCH_HAND_X, HAND_Y,
                           pitch,  (80, 160, 255), "right hand / pitch", font)
        draw_hand_presence(screen, VOLUME_HAND_X, HAND_Y,
                           volume, (255, 80,  160), "left hand / volume", font)

        # ── status bar ─────────────────────────────────────────────────────
        bar_y = H - 32
        pygame.draw.line(screen, (25, 25, 25), (0, bar_y - 8), (W, bar_y - 8))

        # pitch bar
        bw = int((W//2 - 60) * pitch)
        pygame.draw.rect(screen, (20, 20, 20), (40, bar_y, W//2 - 60, 14))
        if bw: pygame.draw.rect(screen, (60, 130, 220), (40, bar_y, bw, 14))
        txt = font.render(f"pitch  {int(pitch*127):3d}   {pitch:.3f}", True, (80, 120, 180))
        screen.blit(txt, (44, bar_y + 1))

        # volume bar
        vx  = W//2 + 20
        vbw = int((W//2 - 60) * volume)
        pygame.draw.rect(screen, (20, 20, 20), (vx, bar_y, W//2 - 60, 14))
        if vbw: pygame.draw.rect(screen, (200, 60, 130), (vx, bar_y, vbw, 14))
        txt2 = font.render(f"volume {int(volume*127):3d}   {volume:.3f}", True, (180, 80, 130))
        screen.blit(txt2, (vx + 4, bar_y + 1))

        # anomaly
        ac = (
            int(60  + anomaly * 195),
            int(100 + anomaly * 100),
            int(180 + anomaly * 60)
        )
        atxt = font_lg.render(f"anomaly  {anomaly:.3f}", True, (min(255,ac[0]),min(255,ac[1]),min(255,ac[2])))
        screen.blit(atxt, (W//2 - atxt.get_width()//2, 16))

        fps_txt = font.render(f"{clock.get_fps():.0f}fps  esc", True, (35, 35, 35))
        screen.blit(fps_txt, (W - fps_txt.get_width() - 8, H - 18))

        pygame.display.flip()

    theremin.stop()
    pygame.quit()

if __name__ == '__main__':
    main()
