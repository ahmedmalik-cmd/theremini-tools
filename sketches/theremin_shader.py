import os
import sys, threading
import cv2
import numpy as np
import mido
import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GLU import *

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Phone camera stream (e.g. DroidCam: http://<phone-ip>:4747/video); unset = webcam 0.
DROIDCAM_URL = os.environ.get("CAMERA_URL", "")
MIDI_PORT    = os.environ.get("THEREMINI_PORT", "Moog Theremini 0")
DISPLAY_W    = 1280
DISPLAY_H    = 720

# ── MIDI ──────────────────────────────────────────────────────────────────────
midi_state = {'cc20': 0, 'cc2': 0}
midi_lock  = threading.Lock()

def midi_thread():
    try:
        port = mido.open_input(MIDI_PORT)
        print(f"[MIDI] connected: {MIDI_PORT}")
        for msg in port:
            if msg.type == 'control_change':
                with midi_lock:
                    if msg.control == 20:
                        midi_state['cc20'] = msg.value
                    elif msg.control == 2:
                        midi_state['cc2'] = msg.value
    except Exception as e:
        print(f"[MIDI] not found — running without theremin: {e}")

threading.Thread(target=midi_thread, daemon=True).start()

# ── CAMERA THREAD ─────────────────────────────────────────────────────────────
latest_frame = [None]
frame_lock   = threading.Lock()

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

threading.Thread(target=camera_thread, daemon=True).start()

# ── GLSL SHADERS ──────────────────────────────────────────────────────────────
VERT_SRC = """
#version 120
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
    v_uv = a_pos * 0.5 + 0.5;
    gl_Position = vec4(a_pos, 0.0, 1.0);
}
"""

FRAG_SRC = """
#version 120
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_time;
uniform float u_scatter;
uniform float u_rigidity;
uniform float u_breath;
uniform vec2  u_resolution;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(
        mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
        mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
        u.y
    );
}

float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 4; i++) {
        v += a * noise(p);
        p  = p * 2.1 + vec2(1.7, 9.2);
        a *= 0.5;
    }
    return v;
}

void main() {
    vec2 uv = v_uv;

    vec2 aspect = vec2(u_resolution.x / u_resolution.y, 1.0);
    vec2 centre = vec2(0.5, 0.5);
    float dist_c = length((uv - centre) * aspect);

    float looseness = 1.0 - u_rigidity;

    // breathing warp
    vec2 breath_noise = vec2(
        fbm(uv * 2.5 + vec2(u_time * 0.08, 0.0)),
        fbm(uv * 2.5 + vec2(0.0, u_time * 0.08) + vec2(5.2, 1.3))
    );
    breath_noise = (breath_noise - 0.5) * 2.0;
    uv += breath_noise * u_breath * 0.03 * looseness;

    // radial scatter from centre
    vec2 to_centre = uv - centre;
    float radial = length(to_centre);
    float scatter_radial = u_scatter * 0.18 / (radial * 3.0 + 0.5);
    vec2 dir = to_centre / (radial + 0.001);
    uv += dir * scatter_radial;

    // turbulent noise on scatter
    vec2 turb = vec2(
        fbm(uv * 3.0 + vec2(u_time * 0.3, 0.0)),
        fbm(uv * 3.0 + vec2(0.0, u_time * 0.3) + vec2(3.1, 7.4))
    );
    turb = (turb - 0.5) * 2.0;
    uv += turb * u_scatter * 0.06 * looseness;
    uv.x += turb.x * u_scatter * 0.04;

    uv = clamp(uv, 0.001, 0.999);
    vec4 col = texture2D(u_tex, uv);

    // desaturate when rigid
    float lum = dot(col.rgb, vec3(0.299, 0.587, 0.114));
    col.rgb = mix(col.rgb, vec3(lum), u_rigidity * 0.7);

    // vignette
    float vig = smoothstep(0.9, 0.4, dist_c);
    col.rgb *= 0.7 + 0.3 * vig;

    // scatter brightens edges
    col.rgb += vec3(u_scatter * 0.12 * (1.0 - vig));

    gl_FragColor = col;
}
"""

def compile_shader(src, stype):
    s = glCreateShader(stype)
    glShaderSource(s, src)
    glCompileShader(s)
    if not glGetShaderiv(s, GL_COMPILE_STATUS):
        raise RuntimeError(f"Shader error:\n{glGetShaderInfoLog(s).decode()}")
    return s

def build_program():
    vs  = compile_shader(VERT_SRC, GL_VERTEX_SHADER)
    fs  = compile_shader(FRAG_SRC, GL_FRAGMENT_SHADER)
    prg = glCreateProgram()
    glAttachShader(prg, vs)
    glAttachShader(prg, fs)
    glLinkProgram(prg)
    if not glGetProgramiv(prg, GL_LINK_STATUS):
        raise RuntimeError(f"Link error:\n{glGetProgramInfoLog(prg).decode()}")
    glDeleteShader(vs)
    glDeleteShader(fs)
    return prg

# ── PYGAME / OPENGL INIT ──────────────────────────────────────────────────────
pygame.init()
screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), DOUBLEBUF | OPENGL)
pygame.display.set_caption('theremin — ESC to quit')

program = build_program()
print("[Shader] compiled OK")

quad_verts = np.array([
    -1.0, -1.0,   1.0, -1.0,   1.0,  1.0,
    -1.0, -1.0,   1.0,  1.0,  -1.0,  1.0,
], dtype=np.float32)

vbo_quad = glGenBuffers(1)
glBindBuffer(GL_ARRAY_BUFFER, vbo_quad)
glBufferData(GL_ARRAY_BUFFER, quad_verts.nbytes, quad_verts, GL_STATIC_DRAW)

tex_id = glGenTextures(1)
glBindTexture(GL_TEXTURE_2D, tex_id)
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

def upload_frame(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = cv2.flip(rgb, 0)
    h, w = rgb.shape[:2]
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                 GL_RGB, GL_UNSIGNED_BYTE, rgb)

loc_pos        = glGetAttribLocation(program,  'a_pos')
loc_tex        = glGetUniformLocation(program, 'u_tex')
loc_time       = glGetUniformLocation(program, 'u_time')
loc_scatter    = glGetUniformLocation(program, 'u_scatter')
loc_rigidity   = glGetUniformLocation(program, 'u_rigidity')
loc_breath     = glGetUniformLocation(program, 'u_breath')
loc_resolution = glGetUniformLocation(program, 'u_resolution')

# ── PHYSICS STATE ─────────────────────────────────────────────────────────────
cc20_prev    = 0
cc20_vel     = 0.0
scatter_val  = 0.0
breath_phase = 0.0

clock      = pygame.time.Clock()
t          = 0.0
frame_idx  = 0
tex_loaded = False

import traceback
print("[Main] running — ESC or Q to quit")

try:
    while True:
        for event in pygame.event.get():
            if event.type == QUIT:
                raise SystemExit
            elif event.type == KEYDOWN:
                if event.key in (K_ESCAPE, K_q):
                    raise SystemExit

        with frame_lock:
            frame = latest_frame[0]
        if frame is not None:
            upload_frame(frame)
            tex_loaded = True

        if not tex_loaded:
            pygame.display.flip()
            clock.tick(60)
            continue

        with midi_lock:
            cc20 = midi_state['cc20']
            cc2  = midi_state['cc2']

        # velocity of pitch antenna — arm swing detection
        raw_vel   = abs(cc20 - cc20_prev)
        cc20_vel  = 0.65 * cc20_vel + 0.35 * raw_vel
        cc20_prev = cc20

        proximity     = (cc20 / 127.0) ** 1.8
        swing_contrib = min(cc20_vel / 12.0, 1.0) * 0.6
        scatter_target = min(proximity + swing_contrib, 1.0)

        # fast attack, slow decay — scatter lingers after a swish
        if scatter_target > scatter_val:
            scatter_val = scatter_val * 0.5  + scatter_target * 0.5
        else:
            scatter_val = scatter_val * 0.92 + scatter_target * 0.08

        rigidity  = (cc2 / 127.0) ** 1.2
        looseness = 1.0 - rigidity

        # frozen when fully rigid
        if rigidity > 0.995:
            scatter_val  = 0.0
            breath_phase = 0.0
            looseness    = 0.0

        breath_phase += 0.008 * looseness
        breath_val    = float(np.sin(breath_phase) * 0.5 + 0.5)

        t += clock.get_time() / 1000.0

        glClear(GL_COLOR_BUFFER_BIT)
        glUseProgram(program)

        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glUniform1i(loc_tex,        0)
        glUniform1f(loc_time,       float(t))
        glUniform1f(loc_scatter,    float(scatter_val))
        glUniform1f(loc_rigidity,   float(rigidity))
        glUniform1f(loc_breath,     breath_val)
        glUniform2f(loc_resolution, float(DISPLAY_W), float(DISPLAY_H))

        glBindBuffer(GL_ARRAY_BUFFER, vbo_quad)
        glEnableVertexAttribArray(loc_pos)
        glVertexAttribPointer(loc_pos, 2, GL_FLOAT, GL_FALSE, 0, None)
        glDrawArrays(GL_TRIANGLES, 0, 6)
        glDisableVertexAttribArray(loc_pos)

        pygame.display.flip()

        if frame_idx % 60 == 0:
            print(
                f"[fps {clock.get_fps():.1f}] "
                f"cc20={cc20} cc2={cc2} | "
                f"scatter={scatter_val:.2f} rigid={rigidity:.2f} breath={breath_val:.2f}"
            )

        clock.tick(60)
        frame_idx += 1

except SystemExit:
    pass
except Exception:
    print("\n[CRASH]")
    traceback.print_exc()
    input("Press Enter to close...")
finally:
    pygame.quit()
    sys.exit(0)
