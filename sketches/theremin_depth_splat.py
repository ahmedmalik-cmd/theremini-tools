import os
import sys
import threading
import time

import cv2
import mediapipe as mp
import mido
import numpy as np
import pygame
import torch
import torch.nn.functional as F
from OpenGL.GL import *
from OpenGL.GLU import *
from pygame.locals import *

sys.path.insert(0, ".")
from depth_anything_v2.dpt import DepthAnythingV2

# --- CONFIG -----------------------------------------------------------------
# Phone camera stream (e.g. DroidCam: http://<phone-ip>:4747/video); unset = webcam 0.
DROIDCAM_URL = os.environ.get("CAMERA_URL", "")
CHECKPOINT = "checkpoints/depth_anything_v2_vitl.pth"
ENCODER = "vitl"
INFER_SIZE = 420
POINT_SKIP = 4
DEPTH_SCALE = 3.0
MIDI_PORT = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
DISPLAY_W = 1280
DISPLAY_H = 720

# Lower focal length widens the reconstructed point cloud.
FX, FY = 250.0, 250.0


# --- MIDI -------------------------------------------------------------------
midi_state = {"cc20": 0, "cc2": 0, "program": 0}
midi_lock = threading.Lock()


def midi_thread():
    try:
        port = mido.open_input(MIDI_PORT)
        print(f"[MIDI] connected: {MIDI_PORT}")
        for msg in port:
            if msg.type == "control_change":
                with midi_lock:
                    if msg.control == 20:
                        midi_state["cc20"] = msg.value
                    elif msg.control == 2:
                        midi_state["cc2"] = msg.value
            elif msg.type == "program_change":
                with midi_lock:
                    midi_state["program"] = msg.program
    except Exception as error:
        print(f"[MIDI] not found - running without theremin: {error}")


threading.Thread(target=midi_thread, daemon=True).start()


# --- CAMERA THREAD ----------------------------------------------------------
latest_frame = [None]
frame_lock = threading.Lock()
frame_ready_event = threading.Event()


def camera_thread():
    cap = cv2.VideoCapture(DROIDCAM_URL or 0)
    if not cap.isOpened():
        print("[Camera] DroidCam not found, falling back to webcam 0")
        cap = cv2.VideoCapture(0)
    else:
        print(f"[Camera] connected: {DROIDCAM_URL or 'webcam 0'}")

    while True:
        ret, frame = cap.read()
        if ret:
            with frame_lock:
                latest_frame[0] = frame
            frame_ready_event.set()


threading.Thread(target=camera_thread, daemon=True).start()


# --- FACE / EXPRESSION THREAD ----------------------------------------------
face_state = {
    "people": 0,
    "mouth": 0.0,
    "brow": 0.0,
    "eye": 0.0,
    "motion": 0.0,
    "expression": 0.0,
    "roll": 0.0,
    "yaw": 0.0,
}
face_lock = threading.Lock()
mp_face_mesh = mp.solutions.face_mesh
mp_hands = mp.solutions.hands

hand_state = {
    "hands": 0,
    "x": 0.5,
    "y": 0.5,
    "motion_x": 0.0,
    "motion_y": 0.0,
    "speed": 0.0,
}
hand_lock = threading.Lock()


def _landmark_xy(landmarks, index):
    landmark = landmarks.landmark[index]
    return np.array([landmark.x, landmark.y], dtype=np.float32)


def analyze_faces(rgb_frame, detector, previous_center):
    results = detector.process(rgb_frame)
    if not results.multi_face_landmarks:
        return None, previous_center

    mouth_values = []
    brow_values = []
    eye_values = []
    roll_values = []
    yaw_values = []
    centers = []

    for face_landmarks in results.multi_face_landmarks:
        mouth_top = _landmark_xy(face_landmarks, 13)
        mouth_bottom = _landmark_xy(face_landmarks, 14)
        mouth_left = _landmark_xy(face_landmarks, 78)
        mouth_right = _landmark_xy(face_landmarks, 308)

        left_eye_upper = _landmark_xy(face_landmarks, 159)
        left_eye_lower = _landmark_xy(face_landmarks, 145)
        left_eye_left = _landmark_xy(face_landmarks, 33)
        left_eye_right = _landmark_xy(face_landmarks, 133)
        right_eye_upper = _landmark_xy(face_landmarks, 386)
        right_eye_lower = _landmark_xy(face_landmarks, 374)
        right_eye_left = _landmark_xy(face_landmarks, 362)
        right_eye_right = _landmark_xy(face_landmarks, 263)

        left_brow = _landmark_xy(face_landmarks, 105)
        right_brow = _landmark_xy(face_landmarks, 334)
        nose_tip = _landmark_xy(face_landmarks, 1)

        mouth_width = np.linalg.norm(mouth_right - mouth_left) + 1e-4
        mouth_open = np.linalg.norm(mouth_bottom - mouth_top) / mouth_width

        left_eye_width = np.linalg.norm(left_eye_right - left_eye_left) + 1e-4
        right_eye_width = np.linalg.norm(right_eye_right - right_eye_left) + 1e-4
        left_eye_open = np.linalg.norm(left_eye_upper - left_eye_lower) / left_eye_width
        right_eye_open = np.linalg.norm(right_eye_upper - right_eye_lower) / right_eye_width
        eye_open = 0.5 * (left_eye_open + right_eye_open)

        left_brow_gap = max(0.0, left_eye_upper[1] - left_brow[1]) / left_eye_width
        right_brow_gap = max(0.0, right_eye_upper[1] - right_brow[1]) / right_eye_width
        brow_raise = 0.5 * (left_brow_gap + right_brow_gap)
        eye_mid = 0.5 * (left_eye_left + right_eye_right)
        roll_angle = np.arctan2(
            right_eye_right[1] - left_eye_left[1],
            right_eye_right[0] - left_eye_left[0] + 1e-6,
        )
        yaw_offset = (nose_tip[0] - eye_mid[0]) / (mouth_width + 1e-4)

        face_center = np.mean(
            np.stack(
                [
                    mouth_left,
                    mouth_right,
                    left_eye_left,
                    left_eye_right,
                    right_eye_left,
                    right_eye_right,
                ],
                axis=0,
            ),
            axis=0,
        )

        mouth_values.append(mouth_open)
        brow_values.append(brow_raise)
        eye_values.append(eye_open)
        roll_values.append(float(roll_angle))
        yaw_values.append(float(yaw_offset))
        centers.append(face_center)

    avg_center = np.mean(np.stack(centers, axis=0), axis=0)
    motion = 0.0 if previous_center is None else float(np.linalg.norm(avg_center - previous_center))

    mouth_value = float(np.mean(mouth_values))
    brow_value = float(np.mean(brow_values))
    eye_value = float(np.mean(eye_values))
    roll_value = float(np.mean(roll_values))
    yaw_value = float(np.mean(yaw_values))

    mouth_norm = np.clip((mouth_value - 0.012) / 0.14, 0.0, 1.0)
    brow_norm = np.clip((brow_value - 0.14) / 0.14, 0.0, 1.0)
    eye_norm = np.clip((eye_value - 0.035) / 0.1, 0.0, 1.0)
    motion_norm = np.clip(motion / 0.025, 0.0, 1.0)

    expression = np.clip(
        mouth_norm * 0.42 + brow_norm * 0.24 + eye_norm * 0.14 + motion_norm * 0.2,
        0.0,
        1.0,
    )

    state = {
        "people": len(centers),
        "mouth": mouth_norm,
        "brow": brow_norm,
        "eye": eye_norm,
        "motion": motion_norm,
        "expression": float(expression),
        "roll": float(np.clip(roll_value, -0.7, 0.7)),
        "yaw": float(np.clip(yaw_value * 2.2, -0.7, 0.7)),
    }
    return state, avg_center


def face_thread():
    previous_center = None
    smoothed = face_state.copy()

    with mp_face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=8,
        refine_landmarks=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as detector:
        while True:
            with frame_lock:
                frame = None if latest_frame[0] is None else latest_frame[0].copy()

            if frame is None:
                time.sleep(0.02)
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            analyzed, previous_center = analyze_faces(rgb, detector, previous_center)

            target = analyzed or {
                "people": 0,
                "mouth": 0.0,
                "brow": 0.0,
                "eye": 0.0,
                "motion": 0.0,
                "expression": 0.0,
            }

            smoothing = 0.18 if target["people"] > 0 else 0.08
            smoothed["people"] = target["people"]
            for key in ("mouth", "brow", "eye", "motion", "expression", "roll", "yaw"):
                smoothed[key] += (target[key] - smoothed[key]) * smoothing

            with face_lock:
                face_state.update(smoothed)

            time.sleep(0.03)


threading.Thread(target=face_thread, daemon=True).start()


def analyze_hands(rgb_frame, detector, previous_center):
    results = detector.process(rgb_frame)
    if not results.multi_hand_landmarks:
        return None, previous_center

    centers = []
    for hand_landmarks in results.multi_hand_landmarks:
        wrist = _landmark_xy(hand_landmarks, 0)
        index_mcp = _landmark_xy(hand_landmarks, 5)
        pinky_mcp = _landmark_xy(hand_landmarks, 17)
        palm_center = (wrist + index_mcp + pinky_mcp) / 3.0
        centers.append(palm_center)

    avg_center = np.mean(np.stack(centers, axis=0), axis=0)
    movement = np.zeros(2, dtype=np.float32) if previous_center is None else (avg_center - previous_center)
    speed = float(np.linalg.norm(movement))

    state = {
        "hands": len(centers),
        "x": float(avg_center[0]),
        "y": float(avg_center[1]),
        "motion_x": float(movement[0]),
        "motion_y": float(movement[1]),
        "speed": float(np.clip(speed / 0.06, 0.0, 1.0)),
    }
    return state, avg_center


def hand_thread():
    previous_center = None
    smoothed = hand_state.copy()

    with mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=6,
        model_complexity=0,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as detector:
        while True:
            with frame_lock:
                frame = None if latest_frame[0] is None else latest_frame[0].copy()

            if frame is None:
                time.sleep(0.02)
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            analyzed, previous_center = analyze_hands(rgb, detector, previous_center)

            target = analyzed or {
                "hands": 0,
                "x": 0.5,
                "y": 0.5,
                "motion_x": 0.0,
                "motion_y": 0.0,
                "speed": 0.0,
            }

            smoothing = 0.22 if target["hands"] > 0 else 0.08
            smoothed["hands"] = target["hands"]
            for key in ("x", "y", "motion_x", "motion_y", "speed"):
                smoothed[key] += (target[key] - smoothed[key]) * smoothing

            with hand_lock:
                hand_state.update(smoothed)

            time.sleep(0.02)


threading.Thread(target=hand_thread, daemon=True).start()


# --- DEPTH MODEL ------------------------------------------------------------
model_cfg = {"vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]}}
model = DepthAnythingV2(**model_cfg[ENCODER])
model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=False))
model = model.to("cuda").half().eval()
print("[Depth] model loaded - CUDA fp16")

mean = torch.tensor([0.485, 0.456, 0.406], device="cuda", dtype=torch.float16).view(1, 3, 1, 1)
std = torch.tensor([0.229, 0.224, 0.225], device="cuda", dtype=torch.float16).view(1, 3, 1, 1)


def infer_depth(bgr):
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to("cuda", dtype=torch.float16) / 255.0
    tensor = (tensor - mean) / std
    tensor = F.interpolate(tensor, (INFER_SIZE, INFER_SIZE), mode="bilinear", align_corners=False)

    with torch.no_grad():
        depth = model(tensor)

    depth = F.interpolate(depth.unsqueeze(1), (h, w), mode="bilinear", align_corners=False)
    depth = depth.squeeze().cpu().numpy().astype(np.float32)
    lo, hi = depth.min(), depth.max()
    if hi > lo:
        depth = (depth - lo) / (hi - lo) * DEPTH_SCALE
    return depth


# --- DEPTH THREAD -----------------------------------------------------------
depth_result = [None]
depth_lock = threading.Lock()


def depth_thread():
    while True:
        frame_ready_event.wait()
        frame_ready_event.clear()

        with frame_lock:
            frame = latest_frame[0]

        if frame is None:
            continue

        depth = infer_depth(frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        h, w = depth.shape
        cx, cy = w / 2.0, h / 2.0
        ys, xs = np.mgrid[0:h:POINT_SKIP, 0:w:POINT_SKIP]
        zs = depth[ys, xs].astype(np.float32)
        mask = zs > 0.05
        xi = xs[mask].astype(np.float32)
        yi = ys[mask].astype(np.float32)
        zi = zs[mask]

        x_coords = (xi - cx) / FX * zi
        y_coords = -(yi - cy) / FY * zi
        z_coords = -zi
        pts = np.stack([x_coords, y_coords, z_coords], axis=1)

        sampled_rgb = rgb[ys[mask].astype(int), xs[mask].astype(int)].astype(np.float32) / 255.0
        luminance = sampled_rgb.mean(axis=1)
        grayscale = np.stack([luminance, luminance, luminance], axis=1).astype(np.float32)
        colors = {
            "grayscale": grayscale,
            "color": sampled_rgb.astype(np.float32),
        }

        with depth_lock:
            depth_result[0] = (pts.copy(), colors)


threading.Thread(target=depth_thread, daemon=True).start()


# --- DISTURBANCE ------------------------------------------------------------
# cc2:
#   hand close to volume antenna -> rigid / no breathing
#   hand away from volume antenna -> breathing / soft body
#
# cc20:
#   pitch antenna capacitance disturbance
#   faster change = stronger swish burst

cc20_prev = 0
cc20_delta = 0.0
cc20_vel = 0.0
smoothed_cc20 = 0.0
smoothed_cc2 = 0.0
smoothed_swing = 0.0
swish_energy = 0.0
effect_intensity = 1.0
color_mode = "grayscale"
shape_mode = "wave"
smear_hold = 0.0


def update_caption():
    pygame.display.set_caption(
        f"theremin sculpt - {shape_mode} - intensity: {effect_intensity:.2f} - color: {color_mode}"
    )


def update_runtime_caption(cc20, cc2):
    tool_x = cc20 / 127.0
    force = cc2 / 127.0
    with face_lock:
        roll = face_state["roll"]
        yaw = face_state["yaw"]
    pygame.display.set_caption(
        f"theremin sculpt - {shape_mode} - intensity: {effect_intensity:.2f} "
        f"- color: {color_mode} - tool:{tool_x:.2f} force:{force:.2f} - roll:{roll:.2f} yaw:{yaw:.2f}"
    )


def apply_disturbance(pts, velocities, origin, cc20, cc2, face_metrics, hand_metrics, time_s, dt, intensity):
    global cc20_prev, cc20_delta, cc20_vel, smoothed_cc20, smoothed_cc2, smoothed_swing, swish_energy, smear_hold

    cc20_delta = cc20 - cc20_prev
    cc20_vel = 0.7 * cc20_vel + 0.3 * abs(cc20_delta)
    cc20_prev = cc20

    smoothing = min(max(dt * 2.8, 0.03), 0.18)
    smoothed_cc20 += (cc20 - smoothed_cc20) * smoothing
    smoothed_cc2 += (cc2 - smoothed_cc2) * smoothing
    smoothed_swing += (cc20_vel - smoothed_swing) * min(max(dt * 3.4, 0.04), 0.22)

    tool_position = smoothed_cc20 / 127.0
    tool_force = (smoothed_cc2 / 127.0) ** 1.1
    tool_motion = min(smoothed_swing / 7.0, 1.0)

    # Theremin drives a moving carve band through the sculpture.
    trigger = (0.08 + tool_force * 2.25 + tool_motion * 1.35) * intensity
    swish_energy += (trigger - swish_energy) * min(max(dt * 4.2, 0.06), 0.26)
    swish_energy *= 0.984
    smear_hold += (trigger - smear_hold) * min(max(dt * 2.2, 0.03), 0.12)
    smear_hold *= 0.995

    rigidity = 1.0 - tool_force
    expression = face_metrics["expression"]

    cloud_center = origin.mean(axis=0, keepdims=True)
    body_diff = pts - cloud_center
    body_dist = np.linalg.norm(body_diff, axis=1, keepdims=True) + 0.001
    body_radial = body_diff / body_dist

    tool_center_x = np.interp(tool_position, [0.0, 1.0], [-2.05, 2.05])
    smear_dir_x = np.sign(cc20_delta)
    if smear_dir_x == 0:
        smear_dir_x = -1.0 if tool_position < 0.5 else 1.0
    smear_direction = np.array([smear_dir_x, 0.0, -0.08], dtype=np.float32)
    smear_direction /= np.linalg.norm(smear_direction) + 1e-6
    smear_field = np.repeat(smear_direction[None, :], len(pts), axis=0)

    if shape_mode == "smear":
        # Hand-shaped smear: an oval brush with directional drag.
        oval_center = np.array([tool_center_x, cloud_center[0, 1], cloud_center[0, 2]], dtype=np.float32)
        oval_diff = pts - oval_center
        oval_scale = np.array(
            [
                0.24 + tool_motion * 0.65,
                0.62 + tool_force * 0.55,
                0.7 + tool_force * 0.5,
            ],
            dtype=np.float32,
        )
        oval_norm = (oval_diff / oval_scale[None, :]) ** 2
        carve_mask = np.clip(1.0 - np.sum(oval_norm, axis=1, keepdims=True), 0.0, 1.0) ** 2.2

        drag = smear_field * carve_mask * (0.05 + tool_motion * 1.0 + swish_energy * 0.24 + smear_hold * 0.48)
        compress = (-body_radial) * carve_mask * (0.02 + tool_force * 0.46 + smear_hold * 0.22)
        sculpt_effect = drag + compress
    elif shape_mode == "melt":
        # Melt zone: points lose coherence and slump out of the scene.
        melt_center = np.array([tool_center_x, cloud_center[0, 1], cloud_center[0, 2]], dtype=np.float32)
        melt_diff = pts - melt_center
        melt_dist = np.linalg.norm(melt_diff, axis=1, keepdims=True) + 0.001
        melt_radius = 0.26 + tool_force * 1.05 + tool_motion * 0.18
        carve_mask = np.clip(1.0 - melt_dist / melt_radius, 0.0, 1.0) ** 2.1

        slump = np.repeat(np.array([[0.0, -1.0, -0.22]], dtype=np.float32), len(pts), axis=0)
        melt_pull = slump * carve_mask * (0.04 + tool_force * 0.52 + smear_hold * 0.26)
        dissolve = (-body_radial) * carve_mask * (0.02 + tool_force * 0.22)
        sculpt_effect = melt_pull + dissolve
    elif shape_mode == "focus":
        # Focus lens: compresses local depth and concentrates points inward.
        lens_center = np.array([tool_center_x, cloud_center[0, 1], cloud_center[0, 2]], dtype=np.float32)
        lens_diff = pts - lens_center
        lens_dist = np.linalg.norm(lens_diff, axis=1, keepdims=True) + 0.001
        lens_radius = 0.22 + tool_force * 0.95 + tool_motion * 0.12
        carve_mask = np.clip(1.0 - lens_dist / lens_radius, 0.0, 1.0) ** 2.5

        focus_pull = (-lens_diff / lens_dist) * carve_mask * (0.034 + tool_force * 0.46 + swish_energy * 0.18)
        depth_compress = np.repeat(np.array([[0.0, 0.0, 1.0]], dtype=np.float32), len(pts), axis=0)
        sculpt_effect = focus_pull + depth_compress * carve_mask * (0.02 + tool_force * 0.24)
    else:
        # Wave front: a curved pressure front crossing the body.
        curve = (pts[:, 1:2] - cloud_center[:, 1:2]) ** 2 * (0.55 + tool_force * 0.25)
        wave_center = tool_center_x + curve[:, 0:1] * smear_dir_x
        wave_distance = np.abs(pts[:, 0:1] - wave_center)
        wave_width = 0.1 + tool_motion * 0.5 + tool_force * 0.12
        depth_focus = np.clip(1.0 - np.abs(pts[:, 2:3] - cloud_center[:, 2:3]) / 2.4, 0.0, 1.0)
        carve_mask = (np.clip(1.0 - wave_distance / wave_width, 0.0, 1.0) ** 2.3) * depth_focus

        wave_push = smear_field * carve_mask * (0.034 + tool_motion * 0.56 + swish_energy * 0.16)
        dimple = (-body_radial) * carve_mask * (0.018 + tool_force * 0.34)
        sculpt_effect = wave_push + dimple

    # Keep face breathing only as a subtle ambient base layer.
    breathe_dir = origin - cloud_center
    breathe_len = np.linalg.norm(breathe_dir, axis=1, keepdims=True) + 0.001
    breathe_phase = np.sin(time_s * (0.12 + expression * 0.16))
    breathing = (breathe_dir / breathe_len) * breathe_phase * (0.0015 + expression * 0.007)

    viscosity = 0.9 + rigidity * 0.1
    restore_rate = 0.03 + rigidity * 0.22
    restore = (origin - pts) * restore_rate
    drift = np.random.randn(*pts.shape).astype(np.float32) * 0.000002

    if tool_force <= 0.01:
        pts[:] = origin
        velocities[:] = 0.0
        return pts, velocities, np.zeros((len(pts), 1), dtype=np.float32), rigidity

    acceleration = sculpt_effect * (1.4 + intensity * 3.4) + breathing + restore + drift
    velocities *= 0.54 + viscosity * 0.12
    velocities += acceleration * max(dt * 10.0, 0.1)
    pts += velocities * max(dt * 7.0, 0.05)
    return pts, velocities, carve_mask * (1.2 + tool_force * 1.05), rigidity


# --- PYGAME / OPENGL --------------------------------------------------------
pygame.init()
screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), DOUBLEBUF | OPENGL)
update_caption()

glEnable(GL_DEPTH_TEST)
glPointSize(3.4)
glMatrixMode(GL_PROJECTION)
glLoadIdentity()
gluPerspective(86, DISPLAY_W / DISPLAY_H, 0.01, 100.0)
glMatrixMode(GL_MODELVIEW)

vbo_pts = glGenBuffers(1)
vbo_col = glGenBuffers(1)


def upload_vbo(pts, cols):
    pts_c = np.ascontiguousarray(pts, dtype=np.float32)
    col_c = np.ascontiguousarray(cols, dtype=np.float32)
    glBindBuffer(GL_ARRAY_BUFFER, vbo_pts)
    glBufferData(GL_ARRAY_BUFFER, pts_c.nbytes, pts_c, GL_DYNAMIC_DRAW)
    glBindBuffer(GL_ARRAY_BUFFER, vbo_col)
    glBufferData(GL_ARRAY_BUFFER, col_c.nbytes, col_c, GL_DYNAMIC_DRAW)


def draw_vbo(count):
    glEnableClientState(GL_VERTEX_ARRAY)
    glBindBuffer(GL_ARRAY_BUFFER, vbo_pts)
    glVertexPointer(3, GL_FLOAT, 0, None)
    glEnableClientState(GL_COLOR_ARRAY)
    glBindBuffer(GL_ARRAY_BUFFER, vbo_col)
    glColorPointer(3, GL_FLOAT, 0, None)
    glDrawArrays(GL_POINTS, 0, count)
    glDisableClientState(GL_VERTEX_ARRAY)
    glDisableClientState(GL_COLOR_ARRAY)


def rotate_points(points, center, roll_angle, yaw_angle):
    shifted = points - center

    cos_y = np.cos(yaw_angle)
    sin_y = np.sin(yaw_angle)
    x1 = shifted[:, 0] * cos_y + shifted[:, 2] * sin_y
    z1 = -shifted[:, 0] * sin_y + shifted[:, 2] * cos_y
    y1 = shifted[:, 1]

    cos_z = np.cos(roll_angle)
    sin_z = np.sin(roll_angle)
    x2 = x1 * cos_z - y1 * sin_z
    y2 = x1 * sin_z + y1 * cos_z

    rotated = np.stack([x2, y2, z1], axis=1)
    return rotated + center




rot_x, rot_y = 15.0, 0.0
mouse_down = False
last_mouse = (0, 0)
zoom = -1.85

pts_current = None
pts_origin = None
pts_velocity = None
colors_gl = None
color_sets = None
n_pts = 0
clock = pygame.time.Clock()
frame_idx = 0
last_time_s = time.time()

print("[Main] running - ESC or Q to quit, drag to orbit, scroll to zoom")
print("[Controls] 5=wave  6=smear  7=melt  8=focus  c=color toggle  [-]/[=] intensity")

running = True
while running:
    for event in pygame.event.get():
        if event.type == QUIT:
            running = False
        elif event.type == KEYDOWN:
            if event.key in (K_ESCAPE, K_q):
                running = False
            elif event.key == K_5:
                shape_mode = "wave"
                update_caption()
            elif event.key == K_6:
                shape_mode = "smear"
                update_caption()
            elif event.key == K_7:
                shape_mode = "melt"
                update_caption()
            elif event.key == K_8:
                shape_mode = "focus"
                update_caption()
            elif event.key == K_c:
                color_mode = "color" if color_mode == "grayscale" else "grayscale"
                update_caption()
            elif event.key in (K_MINUS, K_UNDERSCORE, K_LEFTBRACKET):
                effect_intensity = max(0.1, effect_intensity - 0.2)
                update_caption()
            elif event.key in (K_EQUALS, K_PLUS, K_RIGHTBRACKET):
                effect_intensity = min(6.0, effect_intensity + 0.2)
                update_caption()
        elif event.type == MOUSEBUTTONDOWN:
            if event.button == 1:
                mouse_down = True
                last_mouse = event.pos
            elif event.button == 4:
                zoom = min(zoom + 0.15, -0.5)
            elif event.button == 5:
                zoom = max(zoom - 0.15, -12.0)
        elif event.type == MOUSEBUTTONUP:
            if event.button == 1:
                mouse_down = False
        elif event.type == MOUSEMOTION and mouse_down:
            dx = event.pos[0] - last_mouse[0]
            dy = event.pos[1] - last_mouse[1]
            rot_y += dx * 0.35
            rot_x += dy * 0.35
            last_mouse = event.pos

    with depth_lock:
        result = depth_result[0]
        depth_result[0] = None

    if result is not None:
        new_origin, color_sets = result
        if pts_origin is None or pts_current is None or len(new_origin) != len(pts_current):
            pts_origin = new_origin
            pts_current = new_origin.copy()
            pts_velocity = np.zeros_like(pts_current, dtype=np.float32)
        else:
            pts_origin = new_origin
            pts_current = pts_current * 0.9 + new_origin * 0.1
            pts_velocity *= 0.8

        colors_gl = color_sets[color_mode].copy()
        n_pts = len(pts_current)
        upload_vbo(pts_current, colors_gl)

    if pts_current is not None:
        with midi_lock:
            cc20 = midi_state["cc20"]
            cc2 = midi_state["cc2"]
        with face_lock:
            current_face_state = face_state.copy()
        with hand_lock:
            current_hand_state = hand_state.copy()

        update_runtime_caption(cc20, cc2)

        if color_sets is not None:
            colors_gl = color_sets[color_mode].copy()

        now_s = time.time()
        dt = min(max(now_s - last_time_s, 1.0 / 240.0), 1.0 / 20.0)
        last_time_s = now_s

        pts_current, pts_velocity, influence, rigidity = apply_disturbance(
            pts_current,
            pts_velocity,
            pts_origin,
            cc20,
            cc2,
            current_face_state,
            current_hand_state,
            pygame.time.get_ticks() * 0.001,
            dt,
            effect_intensity,
        )

        # Subtle head-follow transform from averaged facial pose.
        center = np.mean(pts_origin, axis=0, keepdims=True)
        head_roll = current_face_state["roll"] * 1.45
        head_yaw = current_face_state["yaw"] * 1.75
        pts_current = rotate_points(pts_current, center, head_roll, head_yaw)

        if colors_gl is not None:
            brightness = 0.82 + rigidity * 0.18
            colors_gl = np.clip(colors_gl * brightness, 0.0, 1.0)
            # Carve region melts in/out of the scene rather than tearing blue.
            accent = np.clip(influence * (1.9 + effect_intensity * 0.45), 0.0, 1.0)
            if color_mode == "grayscale":
                fade = 1.0 - accent * 0.9
                colors_gl = np.clip(colors_gl * fade + accent * 0.02, 0.0, 1.0)
            else:
                fade = 1.0 - accent * 0.85
                colors_gl = np.clip(colors_gl * fade, 0.0, 1.0)

        upload_vbo(pts_current, colors_gl)

    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glLoadIdentity()
    glTranslatef(0.0, 0.0, zoom)
    glRotatef(rot_x, 1, 0, 0)
    glRotatef(rot_y, 0, 1, 0)

    if pts_current is not None:
        draw_vbo(n_pts)

    pygame.display.flip()

    if frame_idx % 60 == 0:
        with midi_lock:
            cc20 = midi_state["cc20"]
            cc2 = midi_state["cc2"]
        with face_lock:
            current_face_state = face_state.copy()
        with hand_lock:
            current_hand_state = hand_state.copy()
        print(
            f"[fps {clock.get_fps():.1f}] cc20={cc20} cc2={cc2} "
            f"pts={n_pts} intensity={effect_intensity:.2f} color={color_mode} "
            f"faces={current_face_state['people']} expr={current_face_state['expression']:.2f} "
            f"mouth={current_face_state['mouth']:.2f} brow={current_face_state['brow']:.2f} "
            f"eye={current_face_state['eye']:.2f} motion={current_face_state['motion']:.2f} "
            f"hands={current_hand_state['hands']} hs={current_hand_state['speed']:.2f}"
        )

    clock.tick(60)
    frame_idx += 1

pygame.quit()
sys.exit(0)
