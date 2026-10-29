"""
theremin_vision_fusion.py

Fuses:
  - Moog Theremini MIDI (electromagnetic field / distance)
  - MediaPipe Hands (21 3D landmarks, gesture recognition, finger state)
  - MediaPipe Face Mesh (blink, mouth, eyebrow, landmark jitter)
  - DroidCam or any OpenCV source

Each system contributes what the other cannot see:
  Camera  → hand shape, gesture, finger state, face expression
  Theremin → electromagnetic presence, field distortion, distance
"""

import os
import cv2
import mido
import threading
import time
import math
import numpy as np
import pygame
from collections import deque

try:
    import mediapipe as mp
    MP_AVAILABLE = True
except ImportError:
    MP_AVAILABLE = False
    print("[warn] mediapipe not found — install with: pip install mediapipe")

# ── config ────────────────────────────────────────────────────────────────────
MIDI_PORT   = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
CC_VOLUME   = 2
CC_PITCH    = 20
CAM_SOURCE  = 0   # DroidCam — or change to 0 for webcam
W, H        = 1280, 780
FPS         = 60
HIST        = 180

# ── MIDI ──────────────────────────────────────────────────────────────────────
class ThereminInput:
    def __init__(self):
        self.pitch  = 0.0
        self.volume = 0.0
        self.pitch_hist  = deque([0.0]*HIST, HIST)
        self.volume_hist = deque([0.0]*HIST, HIST)
        self.pitch_vel   = 0.0
        self.volume_vel  = 0.0
        self._prev_pitch = 0.0
        self._prev_vol   = 0.0
        self._prev_t     = time.perf_counter()
        self._running    = False

    @property
    def anomaly(self):
        p, v = self.pitch, self.volume
        return (p * v) ** 0.5 if p > 0 and v > 0 else 0.0

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
                            now = time.perf_counter()
                            dt  = max(now - self._prev_t, 1e-4)
                            if msg.control == CC_PITCH:
                                v = msg.value / 127.0
                                self.pitch_vel = (v - self._prev_pitch) / dt * 0.3 + self.pitch_vel * 0.7
                                self.pitch = v
                                self._prev_pitch = v
                                self.pitch_hist.append(v)
                            elif msg.control == CC_VOLUME:
                                v = msg.value / 127.0
                                self.volume_vel = (v - self._prev_vol) / dt * 0.3 + self.volume_vel * 0.7
                                self.volume = v
                                self._prev_vol = v
                                self.volume_hist.append(v)
                            self._prev_t = now
                    time.sleep(0.001)
        except Exception as e:
            print(f"[midi] {e}")

# ── gesture classification ────────────────────────────────────────────────────
def classify_gesture(landmarks):
    """
    Classify basic hand gesture from 21 MediaPipe landmarks.
    Returns a string label.
    Landmarks: 0=wrist, 4=thumb tip, 8=index tip, 12=middle tip,
               16=ring tip, 20=pinky tip
    MCP joints: 5,9,13,17
    PIP joints: 6,10,14,18
    """
    if not landmarks:
        return "none"

    def lm(i): return landmarks[i]

    # finger extended = tip y < pip y (in normalized coords, y increases downward)
    # thumb extended = tip x further from wrist than mcp
    wrist = lm(0)

    def extended(tip_i, pip_i):
        return lm(tip_i).y < lm(pip_i).y

    thumb_open = abs(lm(4).x - lm(2).x) > 0.06
    index  = extended(8,  6)
    middle = extended(12, 10)
    ring   = extended(16, 14)
    pinky  = extended(20, 18)

    fingers = [index, middle, ring, pinky]
    n_open  = sum(fingers)

    if n_open == 0 and not thumb_open:
        return "fist"
    if n_open == 4 and thumb_open:
        return "open"
    if index and not middle and not ring and not pinky:
        return "point"
    if index and middle and not ring and not pinky:
        return "two fingers"
    if index and middle and ring and not pinky:
        return "three fingers"
    if not index and not middle and not ring and pinky:
        return "pinky"
    if thumb_open and not index and not middle and not ring and not pinky:
        return "thumb"
    if thumb_open and pinky and not index and not middle and not ring:
        return "hang loose"
    return "partial"

def finger_curl_scores(landmarks):
    """Return per-finger curl 0.0 (extended) to 1.0 (curled)."""
    if not landmarks:
        return [0.0] * 5
    def lm(i): return landmarks[i]
    scores = []
    # thumb
    dx = lm(4).x - lm(2).x
    scores.append(max(0, min(1, 1.0 - abs(dx) * 8)))
    # four fingers
    for tip, pip, mcp in [(8,6,5),(12,10,9),(16,14,13),(20,18,17)]:
        dy = lm(tip).y - lm(pip).y
        scores.append(max(0, min(1, dy * 8 + 0.5)))
    return scores

# ── face metrics (from face_perception.py approach) ──────────────────────────
def face_metrics(face_landmarks):
    """Extract blink, mouth, eyebrow, jitter from face mesh landmarks."""
    if not face_landmarks:
        return {"blink": 0.0, "mouth": 0.0, "brow": 0.0, "jitter": 0.0}

    lm = face_landmarks.landmark

    def dist(a, b):
        return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)

    # eye aspect ratio (both eyes averaged)
    # left eye: 159,145 vertical / 133,163 horizontal
    # right eye: 386,374 vertical / 362,263 horizontal
    ear_l = dist(159, 145) / (dist(133, 163) + 1e-6)
    ear_r = dist(386, 374) / (dist(362, 263) + 1e-6)
    blink = 1.0 - min(1.0, (ear_l + ear_r) / 2 * 4)

    # mouth openness
    mouth_v = dist(13, 14)
    mouth_h = dist(78, 308)
    mouth = min(1.0, mouth_v / (mouth_h + 1e-6) * 3)

    # eyebrow height (relative to eye)
    brow_l = lm[70].y - lm[159].y   # negative = raised
    brow_r = lm[300].y - lm[386].y
    brow = max(0.0, min(1.0, -(brow_l + brow_r) / 2 * 20 + 0.5))

    # landmark jitter (mean movement across a few key points)
    # can't compute without history here — return 0 as placeholder
    jitter = 0.0

    return {"blink": blink, "mouth": mouth, "brow": brow, "jitter": jitter}

# ── Kalman filter for hand position ──────────────────────────────────────────
class Kalman2D:
    """Simple 2D position Kalman filter for smoothed trajectory."""
    def __init__(self):
        self.x = np.zeros((4, 1))  # [px, py, vx, vy]
        self.P = np.eye(4) * 1000
        self.F = np.array([[1,0,1,0],[0,1,0,1],[0,0,1,0],[0,0,0,1]], float)
        self.H = np.array([[1,0,0,0],[0,1,0,0]], float)
        self.R = np.eye(2) * 20
        self.Q = np.eye(4) * 0.1
        self.initialized = False

    def update(self, px, py):
        z = np.array([[float(px)],[float(py)]])
        if not self.initialized:
            self.x[0,0] = px; self.x[1,0] = py
            self.initialized = True
            return px, py
        # predict
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        # update
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ self.H) @ self.P
        return float(self.x[0,0]), float(self.x[1,0])

    @property
    def velocity(self):
        return float(self.x[2,0]), float(self.x[3,0])

# ── drawing helpers ───────────────────────────────────────────────────────────
def lerpC(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def draw_hand_landmarks_on_frame(frame, hand_landmarks, gesture, curls, theremin_val):
    """Draw landmarks + connections + gesture on the camera frame."""
    h, w = frame.shape[:2]
    if not hand_landmarks:
        return

    connections = [
        (0,1),(1,2),(2,3),(3,4),
        (0,5),(5,6),(6,7),(7,8),
        (5,9),(9,10),(10,11),(11,12),
        (9,13),(13,14),(14,15),(15,16),
        (13,17),(17,18),(18,19),(19,20),
        (0,17)
    ]

    lms = [(int(lm.x*w), int(lm.y*h)) for lm in hand_landmarks.landmark]

    # draw connections — color by curl of that finger
    finger_colors = [
        (120, 200, 255),  # thumb
        (100, 255, 180),  # index
        (200, 200, 100),  # middle
        (255, 140, 100),  # ring
        (220, 100, 220),  # pinky
    ]
    finger_map = {0:0,1:0,2:0,3:0,4:0,5:1,6:1,7:1,8:1,9:2,10:2,11:2,12:2,
                  13:3,14:3,15:3,16:3,17:4,18:4,19:4,20:4}

    for a, b in connections:
        fi = finger_map.get(a, 0)
        curl = curls[fi] if curls else 0
        base_col = finger_colors[fi]
        col = tuple(int(c * (1 - curl * 0.5)) for c in base_col)
        cv2.line(frame, lms[a], lms[b], col, 2)

    # draw landmark points
    for i, (px, py) in enumerate(lms):
        cv2.circle(frame, (px, py), 3, (240, 240, 240), -1)

    # wrist label
    wx, wy = lms[0]
    col_t = (100, 200, 255) if theremin_val > 0.3 else (160, 160, 160)
    cv2.putText(frame, gesture, (wx-20, wy+20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, col_t, 1)

    # theremin field aura around wrist — radius encodes proximity
    aura_r = int(20 + theremin_val * 60)
    overlay = frame.copy()
    cv2.circle(overlay, (wx, wy), aura_r,
               (int(50+theremin_val*200), 80, int(200-theremin_val*100)), -1)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
    cv2.circle(frame, (wx, wy), aura_r,
               (int(50+theremin_val*200), 80, int(200-theremin_val*100)), 1)

def draw_sparkline(surf, data, x, y, w, h, col, label, font):
    arr = list(data)
    if not arr: return
    mn, mx = min(arr), max(arr)
    rng = mx - mn + 1e-6
    pygame.draw.rect(surf, (14,14,18), (x, y, w, h))
    pygame.draw.rect(surf, (30,30,38), (x, y, w, h), 1)
    pts = []
    for i, v in enumerate(arr):
        px_ = int(x + i * w / len(arr))
        py_ = int(y + h - (v - mn) / rng * h)
        pts.append((px_, py_))
    if len(pts) > 1:
        pygame.draw.lines(surf, col, False, pts, 1)
    lbl = font.render(label, True, (50,50,60))
    surf.blit(lbl, (x+3, y+2))
    val = font.render(f"{arr[-1]:.3f}", True, col)
    surf.blit(val, (x + w - val.get_width() - 3, y+2))

def draw_finger_state(surf, curls, gesture, x, y, font):
    names = ["T","I","M","R","P"]
    cols  = [(120,200,255),(100,255,180),(200,200,100),(255,140,100),(220,100,220)]
    for i, (name, curl, col) in enumerate(zip(names, curls, cols)):
        bx = x + i * 28
        # bar going up = extended, down = curled
        bh = int(40 * (1 - curl))
        pygame.draw.rect(surf, (20,20,24), (bx, y, 20, 44))
        pygame.draw.rect(surf, col, (bx, y + 44 - bh, 20, bh))
        pygame.draw.rect(surf, (40,40,50), (bx, y, 20, 44), 1)
        lbl = font.render(name, True, col)
        surf.blit(lbl, (bx + 10 - lbl.get_width()//2, y + 48))
    g_txt = font.render(gesture, True, (160,160,180))
    surf.blit(g_txt, (x, y + 62))


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    theremin = ThereminInput()
    theremin.start()

    # camera
    cap = cv2.VideoCapture(CAM_SOURCE)
    if not cap.isOpened():
        print(f"[cam] could not open {CAM_SOURCE}, trying webcam 0")
        cap = cv2.VideoCapture(0)

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Theremin + Vision Fusion")
    clock  = pygame.time.Clock()
    font   = pygame.font.SysFont("monospace", 11)
    font_m = pygame.font.SysFont("monospace", 13)

    # MediaPipe
    if MP_AVAILABLE:
        mp_hands = mp.solutions.hands
        mp_face  = mp.solutions.face_mesh
        hands    = mp_hands.Hands(max_num_hands=2,
                                  min_detection_confidence=0.6,
                                  min_tracking_confidence=0.5)
        face_mesh = mp_face.FaceMesh(max_num_faces=1,
                                     min_detection_confidence=0.6,
                                     min_tracking_confidence=0.5)
    else:
        hands = face_mesh = None

    kalman = Kalman2D()
    traj   = deque(maxlen=300)  # smoothed trajectory

    # state
    gesture     = "none"
    curls       = [0.0] * 5
    face_data   = {"blink":0,"mouth":0,"brow":0,"jitter":0}
    hand_lms    = None
    cam_w = cam_h = 0

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: running = False
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE: running = False

        # ── camera frame ──────────────────────────────────────────────────
        ret, frame = cap.read()
        if not ret:
            frame = np.zeros((480, 640, 3), np.uint8)

        frame = cv2.flip(frame, 1)
        cam_h, cam_w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # ── MediaPipe inference ───────────────────────────────────────────
        if MP_AVAILABLE and hands and face_mesh:
            h_results = hands.process(rgb)
            f_results = face_mesh.process(rgb)

            hand_lms = None
            if h_results.multi_hand_landmarks:
                # use first detected hand
                hand_lms = h_results.multi_hand_landmarks[0]
                gesture  = classify_gesture(hand_lms.landmark)
                curls    = finger_curl_scores(hand_lms.landmark)

                # draw on frame
                draw_hand_landmarks_on_frame(
                    frame, hand_lms, gesture, curls, theremin.pitch)

            if f_results.multi_face_landmarks:
                face_data = face_metrics(f_results.multi_face_landmarks[0])

        # ── Kalman-smoothed hand position from MediaPipe ──────────────────
        if hand_lms:
            wrist = hand_lms.landmark[0]
            kx, ky = kalman.update(wrist.x * cam_w, wrist.y * cam_h)
            vx, vy = kalman.velocity
            speed = math.hypot(vx, vy)
            # blend: optical position + theremin field intensity
            traj.append((kx / cam_w, ky / cam_h,
                         theremin.pitch, theremin.volume,
                         theremin.anomaly, speed))

        # ── pygame layout ─────────────────────────────────────────────────
        screen.fill((8, 8, 10))

        # ── camera panel (left) ───────────────────────────────────────────
        cam_panel_w = int(W * 0.55)
        cam_panel_h = int(cam_panel_w * cam_h / max(cam_w, 1))
        cam_panel_h = min(cam_panel_h, H - 20)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_surf = pygame.surfarray.make_surface(
            np.rot90(frame_rgb))
        frame_surf = pygame.transform.scale(frame_surf, (cam_panel_w, cam_panel_h))

        screen.blit(frame_surf, (10, 10))
        pygame.draw.rect(screen, (30,30,40), (10, 10, cam_panel_w, cam_panel_h), 1)

        # trajectory overlay on camera panel
        if len(traj) > 1:
            for i in range(1, len(traj)):
                tx, ty, tp, tv, ta, ts = traj[i]
                ox, oy = traj[i-1][:2]
                age = i / len(traj)
                px_ = int(10 + tx * cam_panel_w)
                py_ = int(10 + ty * cam_panel_h)
                opx = int(10 + ox * cam_panel_w)
                opy = int(10 + oy * cam_panel_h)
                col = lerpC((40,80,200),(220,60,140), tv/(tp+tv+1e-6))
                alpha_s = pygame.Surface((4,4), pygame.SRCALPHA)
                pygame.draw.circle(alpha_s, (*col, int(age*160)), (2,2), 2)
                screen.blit(alpha_s, (px_-2, py_-2))

        # ── right panel ────────────────────────────────────────────────────
        rx = cam_panel_w + 20
        rw = W - rx - 10

        # section: theremin signals
        y_cur = 14
        hdr = font_m.render("THEREMIN", True, (50,50,65))
        screen.blit(hdr, (rx, y_cur)); y_cur += 18

        draw_sparkline(screen, theremin.pitch_hist,
                       rx, y_cur, rw, 38, (80,140,255), "pitch  CC20", font)
        y_cur += 46
        draw_sparkline(screen, theremin.volume_hist,
                       rx, y_cur, rw, 38, (255,80,160), "volume CC2", font)
        y_cur += 46

        # anomaly bar
        aw = int(rw * theremin.anomaly)
        acol = lerpC((40,60,160),(220,80,60), theremin.anomaly)
        pygame.draw.rect(screen, (18,18,22), (rx, y_cur, rw, 14))
        if aw: pygame.draw.rect(screen, acol, (rx, y_cur, aw, 14))
        atxt = font.render(f"anomaly  {theremin.anomaly:.3f}", True, acol)
        screen.blit(atxt, (rx+4, y_cur+1))
        y_cur += 24

        # section: vision / hand
        hdr2 = font_m.render("HANDS", True, (50,50,65))
        screen.blit(hdr2, (rx, y_cur)); y_cur += 18

        draw_finger_state(screen, curls, gesture, rx, y_cur, font)
        y_cur += 90

        # section: face
        hdr3 = font_m.render("FACE", True, (50,50,65))
        screen.blit(hdr3, (rx, y_cur)); y_cur += 18

        face_fields = [
            ("blink",  face_data.get("blink",0),  (180,220,255)),
            ("mouth",  face_data.get("mouth",0),  (255,180,120)),
            ("brow",   face_data.get("brow",0),   (180,255,180)),
        ]
        for lbl_, val_, col_ in face_fields:
            bw_ = int(rw * val_)
            pygame.draw.rect(screen, (18,18,22), (rx, y_cur, rw, 12))
            if bw_: pygame.draw.rect(screen, col_, (rx, y_cur, bw_, 12))
            t_ = font.render(f"{lbl_}  {val_:.2f}", True, col_)
            screen.blit(t_, (rx+4, y_cur))
            y_cur += 16

        y_cur += 10

        # section: fusion state
        hdr4 = font_m.render("FUSION", True, (50,50,65))
        screen.blit(hdr4, (rx, y_cur)); y_cur += 18

        # combined score: anomaly + gesture complexity + face
        gesture_complexity = {"none":0,"fist":0.2,"point":0.5,"two fingers":0.6,
                              "three fingers":0.7,"open":0.8,"hang loose":0.9,
                              "partial":0.4,"thumb":0.3,"pinky":0.3}.get(gesture, 0.4)
        face_activity = (face_data.get("blink",0) + face_data.get("mouth",0) + face_data.get("brow",0)) / 3
        fusion_score = (theremin.anomaly * 0.4 +
                        gesture_complexity * 0.4 +
                        face_activity * 0.2)

        fw = int(rw * fusion_score)
        fcol = lerpC((60,100,180),(255,140,60), fusion_score)
        pygame.draw.rect(screen, (18,18,22), (rx, y_cur, rw, 18))
        if fw: pygame.draw.rect(screen, fcol, (rx, y_cur, fw, 18))
        ft = font_m.render(f"fusion  {fusion_score:.3f}   [{gesture}]", True, fcol)
        screen.blit(ft, (rx+4, y_cur+2))
        y_cur += 28

        # bottom strip
        strip_y = H - 30
        pygame.draw.line(screen, (20,20,24), (0, strip_y-6), (W, strip_y-6))
        fields = [
            ("pitch",     f"{theremin.pitch:.3f}",   (80,140,255)),
            ("volume",    f"{theremin.volume:.3f}",  (255,80,160)),
            ("anomaly",   f"{theremin.anomaly:.3f}", (200,160,60)),
            ("gesture",   gesture,                   (160,200,160)),
            ("blink",     f"{face_data.get('blink',0):.2f}", (180,220,255)),
            ("mouth",     f"{face_data.get('mouth',0):.2f}", (255,180,120)),
            ("fusion",    f"{fusion_score:.3f}",     fcol),
        ]
        cw = (W - 20) // len(fields)
        for i, (lbl_, val_, col_) in enumerate(fields):
            x_ = 10 + i * cw
            screen.blit(font.render(lbl_, True, (45,45,55)), (x_, strip_y))
            screen.blit(font.render(str(val_), True, col_),  (x_, strip_y+14))

        ttl = font.render("THEREMIN + VISION FUSION  |  esc to quit", True, (30,30,40))
        screen.blit(ttl, (W//2 - ttl.get_width()//2, 2))

        pygame.display.flip()

    theremin.stop()
    cap.release()
    if MP_AVAILABLE and hands:
        hands.close()
        face_mesh.close()
    pygame.quit()

if __name__ == '__main__':
    main()
