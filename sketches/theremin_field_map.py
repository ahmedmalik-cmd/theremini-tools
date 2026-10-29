import os
import mido
import threading
import time
import math
import pygame
import numpy as np

# ── config ────────────────────────────────────────────────────────────────────
MIDI_PORT  = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
CC_VOLUME  = 2
CC_PITCH   = 20
W, H       = 1000, 700
FPS        = 60
TRAIL_LEN  = 180   # frames of history

# ── MIDI reader ───────────────────────────────────────────────────────────────
class ThereminInput:
    def __init__(self):
        self.pitch = 0.0
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

# ── helpers ───────────────────────────────────────────────────────────────────
def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def draw_field_grid(surf, cx, cy, radius, value, color_low, color_high, label, font_sm):
    """Draw a radial field heatmap as concentric rings."""
    steps = 12
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        ring_t = 1.0 - (i / steps)
        # field intensity falls off with distance — value compresses the falloff
        intensity = max(0.0, min(1.0, ring_t * (0.3 + value * 0.7)))
        col = lerp_color(color_low, color_high, intensity)
        alpha_surf = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        pygame.draw.circle(alpha_surf, (*col, int(intensity * 80)), (r, r), r)
        surf.blit(alpha_surf, (cx - r, cy - r))

    # outer ring border
    pygame.draw.circle(surf, (60, 60, 60), (cx, cy), radius, 1)

    # value arc
    arc_rect = pygame.Rect(cx - radius, cy - radius, radius*2, radius*2)
    end_angle = -math.pi/2 + value * 2 * math.pi
    if value > 0.001:
        pygame.draw.arc(surf, color_high, arc_rect, -math.pi/2, end_angle, 3)

    # center dot
    dot_r = int(4 + value * 12)
    pygame.draw.circle(surf, color_high, (cx, cy), dot_r)

    # label
    txt = font_sm.render(label, True, (160, 160, 160))
    surf.blit(txt, (cx - txt.get_width()//2, cy + radius + 8))

def draw_crossfield(surf, pitch, volume, cx, cy, size, font_sm):
    """
    2D cross-field plot: pitch on X axis, volume on Y axis.
    Shows the joint hand-position state as a point in the field.
    """
    # grid
    for i in range(5):
        t = i / 4
        x = int(cx - size/2 + t * size)
        y = int(cy - size/2 + t * size)
        pygame.draw.line(surf, (35, 35, 35), (x, cy - size//2), (x, cy + size//2))
        pygame.draw.line(surf, (35, 35, 35), (cx - size//2, y), (cx + size//2, y))

    # border
    pygame.draw.rect(surf, (55, 55, 55),
                     (cx - size//2, cy - size//2, size, size), 1)

    # axis labels
    lx = font_sm.render("pitch →", True, (90, 130, 180))
    ly = font_sm.render("vol ↑", True, (180, 100, 130))
    surf.blit(lx, (cx + size//2 - lx.get_width() - 4, cy + size//2 + 6))
    surf.blit(ly, (cx - size//2 - ly.get_width() - 6, cy - size//2))

    # cursor
    px = int(cx - size//2 + pitch  * size)
    py = int(cy + size//2 - volume * size)

    # crosshair lines
    pygame.draw.line(surf, (40, 60, 40), (px, cy - size//2), (px, cy + size//2))
    pygame.draw.line(surf, (40, 60, 40), (cx - size//2, py), (cx + size//2, py))

    # cursor circle — color encodes anomaly (geometric mean)
    anomaly = (pitch * volume) ** 0.5
    col = lerp_color((60, 80, 180), (220, 80, 60), anomaly)
    pygame.draw.circle(surf, col, (px, py), 10)
    pygame.draw.circle(surf, (220, 220, 220), (px, py), 10, 1)

    return (px, py, col)

def draw_signal_bars(surf, pitch, volume, x, y, w, h, font_sm):
    """Horizontal signal bars with value readout."""
    pad = 4
    bh  = (h - pad * 3) // 2

    # pitch bar
    pw = int(pitch * w)
    col_p = lerp_color((30, 50, 100), (80, 160, 255), pitch)
    pygame.draw.rect(surf, (25, 25, 25), (x, y, w, bh))
    if pw > 0:
        pygame.draw.rect(surf, col_p, (x, y, pw, bh))
    pygame.draw.rect(surf, (55, 55, 55), (x, y, w, bh), 1)
    lbl = font_sm.render(f"pitch  CC20   {int(pitch*127):3d} / 127   {pitch:.3f}", True, (160,160,160))
    surf.blit(lbl, (x + 6, y + bh//2 - lbl.get_height()//2))

    # volume bar
    vy = y + bh + pad
    vw = int(volume * w)
    col_v = lerp_color((100, 30, 50), (255, 80, 130), volume)
    pygame.draw.rect(surf, (25, 25, 25), (x, vy, w, bh))
    if vw > 0:
        pygame.draw.rect(surf, col_v, (x, vy, vw, bh))
    pygame.draw.rect(surf, (55, 55, 55), (x, vy, w, bh), 1)
    lbl2 = font_sm.render(f"volume CC2    {int(volume*127):3d} / 127   {volume:.3f}", True, (160,160,160))
    surf.blit(lbl2, (x + 6, vy + bh//2 - lbl2.get_height()//2))

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    theremin = ThereminInput()
    theremin.start()

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Theremini Field Map")
    clock  = pygame.time.Clock()

    font_lg = pygame.font.SysFont("monospace", 13)
    font_sm = pygame.font.SysFont("monospace", 11)

    trail = []          # list of (x, y, col) for the crossfield trail
    field_surf = pygame.Surface((W, H), pygame.SRCALPHA)

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                running = False

        pitch  = theremin.pitch
        volume = theremin.volume
        anomaly = (pitch * volume) ** 0.5

        screen.fill((12, 12, 12))

        # ── title ─────────────────────────────────────────────────────────────
        title = font_lg.render("THEREMINI  /  ELECTROMAGNETIC FIELD MAP", True, (70, 70, 70))
        screen.blit(title, (W//2 - title.get_width()//2, 14))

        # ── field rings ───────────────────────────────────────────────────────
        # pitch field — left side
        draw_field_grid(screen, 200, 300, 140, pitch,
                        (10, 10, 30), (80, 160, 255),
                        "pitch antenna (rod / right hand)", font_sm)

        # volume field — right side  
        draw_field_grid(screen, 560, 300, 140, volume,
                        (30, 10, 10), (255, 80, 130),
                        "volume antenna (loop / left hand)", font_sm)

        # ── cross-field plot ──────────────────────────────────────────────────
        cx, cy = 380, 300
        pt = draw_crossfield(screen, pitch, volume, cx, cy, 200, font_sm)
        trail.append(pt)
        if len(trail) > TRAIL_LEN:
            trail.pop(0)

        # draw trail
        for i, (tx, ty, tc) in enumerate(trail[:-1]):
            alpha = int(180 * (i / TRAIL_LEN))
            s = pygame.Surface((6, 6), pygame.SRCALPHA)
            pygame.draw.circle(s, (*tc, alpha), (3, 3), 2)
            screen.blit(s, (tx - 3, ty - 3))

        # ── anomaly score ─────────────────────────────────────────────────────
        anom_col = lerp_color((60, 80, 180), (220, 80, 60), anomaly)
        anom_txt = font_lg.render(f"anomaly  {anomaly:.3f}", True, anom_col)
        screen.blit(anom_txt, (cx - anom_txt.get_width()//2, cy + 120))

        # ── signal bars ───────────────────────────────────────────────────────
        draw_signal_bars(screen, pitch, volume, 40, 530, W - 80, 120, font_sm)

        # ── fps / status ──────────────────────────────────────────────────────
        fps_txt = font_sm.render(f"{clock.get_fps():.0f} fps   esc to quit", True, (40, 40, 40))
        screen.blit(fps_txt, (W - fps_txt.get_width() - 10, H - 20))

        pygame.display.flip()
        clock.tick(FPS)

    theremin.stop()
    pygame.quit()

if __name__ == '__main__':
    main()
