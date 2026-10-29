"""
kinect_theremin_demo.py
Kinect v1 depth via comtypes COM interface -> point cloud
+ Moog Theremini MIDI + Arduino ultrasonic
"""

import ctypes
import ctypes.wintypes
import pygame
import numpy as np
import mido
import serial
import serial.tools.list_ports
import threading
import time
import sys
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective

# ── Kinect SDK constants ──────────────────────────────────────────────────────
NUI_INITIALIZE_FLAG_USES_DEPTH   = 0x00000020
NUI_IMAGE_TYPE_DEPTH             = 0x00000003   # NUI_IMAGE_TYPE enum value 3
NUI_IMAGE_RESOLUTION_640x480     = 0x00000002   # NUI_IMAGE_RESOLUTION enum value 2
NUI_IMAGE_STREAM_FLAG_ENABLE_NEAR_MODE = 0x00020000

DEPTH_W = 640
DEPTH_H = 480

# ── NUI_LOCKED_RECT ───────────────────────────────────────────────────────────
class NUI_LOCKED_RECT(ctypes.Structure):
    _fields_ = [
        ("Pitch", ctypes.c_int),
        ("size",  ctypes.c_int),
        ("pBits", ctypes.POINTER(ctypes.c_byte)),
    ]

# ── INuiFrameTexture vtable wrapper ──────────────────────────────────────────
# vtable layout (IUnknown first, then INuiFrameTexture):
# 0: QueryInterface, 1: AddRef, 2: Release
# 3: Pitch, 4: BufferLen, 5: LockRect, 6: GetLevelDesc, 7: UnlockRect, 8: CopyToBuffer

LOCK_RECT_FUNC   = ctypes.WINFUNCTYPE(ctypes.HRESULT,
    ctypes.c_void_p,   # this
    ctypes.c_uint,     # Level
    ctypes.POINTER(NUI_LOCKED_RECT),
    ctypes.c_void_p,   # pRect (NULL)
    ctypes.c_uint,     # Flags
)
UNLOCK_RECT_FUNC = ctypes.WINFUNCTYPE(ctypes.HRESULT,
    ctypes.c_void_p,   # this
    ctypes.c_uint,     # Level
)

def lock_texture(texture_ptr):
    """Walk the INuiFrameTexture COM vtable to call LockRect."""
    vtable_ptr = ctypes.cast(texture_ptr, ctypes.POINTER(ctypes.c_void_p))
    vtable     = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))
    lock_fn    = LOCK_RECT_FUNC(vtable[5])
    locked     = NUI_LOCKED_RECT()
    hr         = lock_fn(texture_ptr, 0, ctypes.byref(locked), None, 0)
    return hr, locked

def unlock_texture(texture_ptr):
    vtable_ptr = ctypes.cast(texture_ptr, ctypes.POINTER(ctypes.c_void_p))
    vtable     = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))
    unlock_fn  = UNLOCK_RECT_FUNC(vtable[7])
    unlock_fn(texture_ptr, 0)

# ── NUI_IMAGE_FRAME ───────────────────────────────────────────────────────────
class NUI_IMAGE_FRAME(ctypes.Structure):
    _fields_ = [
        ("liTimeStamp",   ctypes.c_int64),
        ("dwFrameNumber", ctypes.c_uint32),
        ("eImageType",    ctypes.c_uint32),
        ("eResolution",   ctypes.c_uint32),
        ("pFrameTexture", ctypes.c_void_p),
        ("dwFrameFlags",  ctypes.c_uint32),
        ("ViewArea",      ctypes.c_float * 6),
    ]

# ── Kinect context ────────────────────────────────────────────────────────────
class KINECT_CTX:
    def __init__(self):
        self.dll    = ctypes.WinDLL("Kinect10.dll")
        self.sensor = ctypes.c_void_p(None)
        self.stream = ctypes.c_void_p(None)
        self.ok     = False
        self._open()

    def _open(self):
        # Count sensors
        count = ctypes.c_int(0)
        hr = self.dll.NuiGetSensorCount(ctypes.byref(count))
        if hr != 0 or count.value == 0:
            print(f"[kinect] no sensors found (hr={hr:#010x})")
            return
        print(f"[kinect] {count.value} sensor(s) found")

        # Create sensor instance (COM object)
        hr = self.dll.NuiCreateSensorByIndex(0, ctypes.byref(self.sensor))
        if hr != 0 or not self.sensor:
            print(f"[kinect] NuiCreateSensorByIndex failed hr={hr:#010x}")
            return
        print(f"[kinect] sensor created, ptr={self.sensor.value:#010x}")

        # Call NuiInitialize on the sensor COM object via vtable
        # INuiSensor vtable: 0=QI, 1=AddRef, 2=Release, 3=NuiInitialize, ...
        INIT_FUNC = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_uint32)
        vtable_ptr = ctypes.cast(self.sensor, ctypes.POINTER(ctypes.c_void_p))
        vtable     = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))
        init_fn    = INIT_FUNC(vtable[3])
        hr = init_fn(self.sensor, NUI_INITIALIZE_FLAG_USES_DEPTH)
        if hr != 0:
            print(f"[kinect] NuiInitialize (instance) hr={hr:#010x}")
            # fallback: try global NuiInitialize
            hr = self.dll.NuiInitialize(NUI_INITIALIZE_FLAG_USES_DEPTH)
            if hr != 0:
                print(f"[kinect] NuiInitialize (global) hr={hr:#010x}")
                return
            print("[kinect] NuiInitialize (global) OK")
            self._open_stream_global()
            return
        print("[kinect] NuiInitialize (instance) OK")
        self._open_stream_instance(vtable)

    def _open_stream_global(self):
        """Open stream via global NuiImageStreamOpen."""
        stream = ctypes.c_void_p(None)
        hr = self.dll.NuiImageStreamOpen(
            ctypes.c_uint32(NUI_IMAGE_TYPE_DEPTH),
            ctypes.c_uint32(NUI_IMAGE_RESOLUTION_640x480),
            ctypes.c_uint32(0),
            ctypes.c_uint32(2),
            None,
            ctypes.byref(stream),
        )
        if hr != 0:
            print(f"[kinect] NuiImageStreamOpen (global) hr={hr:#010x}")
            return
        self.stream = stream
        self.ok     = True
        self._use_global = True
        print("[kinect] depth stream open (global) — Kinect active")

    def _open_stream_instance(self, vtable):
        """Open stream via instance method (vtable slot 11 for NuiImageStreamOpen)."""
        # INuiSensor vtable indices (from SDK header order):
        # 3=NuiInitialize, 4=NuiShutdown, 5=NuiStatus, 6=NuiGetDeviceConnectionId,
        # 7=NuiGetDeviceIndex, 8=NuiDeviceConnectionId, 9=NuiUniqueId,
        # 10=NuiAudioArrayId, 11=NuiImageStreamOpen
        STREAM_OPEN_FUNC = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,   # this
            ctypes.c_uint32,   # eImageType
            ctypes.c_uint32,   # eResolution
            ctypes.c_uint32,   # dwImageFrameFlags
            ctypes.c_uint32,   # dwFrameLimit
            ctypes.c_void_p,   # hNextFrameEvent
            ctypes.POINTER(ctypes.c_void_p),  # phStreamHandle
        )
        stream   = ctypes.c_void_p(None)
        open_fn  = STREAM_OPEN_FUNC(vtable[11])
        hr = open_fn(
            self.sensor,
            NUI_IMAGE_TYPE_DEPTH,
            NUI_IMAGE_RESOLUTION_640x480,
            0,
            2,
            None,
            ctypes.byref(stream),
        )
        if hr != 0:
            print(f"[kinect] NuiImageStreamOpen (instance) hr={hr:#010x}")
            # try global as fallback
            self._open_stream_global()
            return
        self.stream      = stream
        self.ok          = True
        self._use_global = False
        print("[kinect] depth stream open (instance) — Kinect active")

    def get_frame(self):
        """Returns (DEPTH_H x DEPTH_W) uint16 depth array, or None."""
        if not self.ok:
            return None
        frame = NUI_IMAGE_FRAME()

        if getattr(self, '_use_global', True):
            hr = self.dll.NuiImageStreamGetNextFrame(
                self.stream, 0, ctypes.byref(frame)
            )
        else:
            # instance vtable slot 22 = NuiImageStreamGetNextFrame (approx)
            # safer to use global for get/release
            hr = self.dll.NuiImageStreamGetNextFrame(
                self.stream, 0, ctypes.byref(frame)
            )

        if hr != 0:
            return None

        depth = None
        if frame.pFrameTexture:
            try:
                hr2, locked = lock_texture(frame.pFrameTexture)
                if hr2 == 0 and locked.pBits:
                    n   = DEPTH_H * DEPTH_W
                    raw = np.frombuffer(
                        (ctypes.c_byte * (n * 2)).from_address(
                            ctypes.addressof(locked.pBits.contents)
                        ),
                        dtype=np.uint16
                    ).reshape(DEPTH_H, DEPTH_W).copy()
                    depth = raw
                    unlock_texture(frame.pFrameTexture)
            except Exception as e:
                print(f"[kinect] frame read error: {e}")

        self.dll.NuiImageStreamReleaseFrame(self.stream, ctypes.byref(frame))
        return depth

    def shutdown(self):
        try:
            self.dll.NuiShutdown()
        except:
            pass

# ── App config ────────────────────────────────────────────────────────────────
W, H         = 1280, 720
SUBSAMPLE    = 6
MAX_DEPTH_MM = 4000
FX, FY       = 525.0, 525.0
CX, CY       = 319.5, 239.5

MIDI_IN_PORT = 'Moog Theremini 0'
ARDUINO_BAUD = 9600

# ── Shared state ──────────────────────────────────────────────────────────────
state = {
    'pts':      np.zeros((1, 3), dtype=np.float32),
    'cc2':      64,
    'cc20':     64,
    'ultra_cm': 100.0,
    'lock':     threading.Lock(),
    'source':   'init',
}

# ── Depth helpers ─────────────────────────────────────────────────────────────
def depth_to_points(depth_full):
    us = np.arange(0, DEPTH_W, SUBSAMPLE)
    vs = np.arange(0, DEPTH_H, SUBSAMPLE)
    ug, vg = np.meshgrid(us, vs)
    Z = depth_full[vg, ug].astype(np.float32)
    valid = (Z > 300) & (Z < MAX_DEPTH_MM)
    Zv = Z[valid] / 1000.0
    Xv = (ug[valid] - CX) * Zv / FX
    Yv = -(vg[valid] - CY) * Zv / FY
    return np.stack([Xv, Yv, -Zv], axis=1).astype(np.float32)

def make_synthetic():
    us = np.arange(0, DEPTH_W, dtype=np.float32)
    vs = np.arange(0, DEPTH_H, dtype=np.float32)
    ug, vg = np.meshgrid(us, vs)
    return (1500.0 + 400.0 * np.sin(ug / 35.0) * np.cos(vg / 35.0)).astype(np.float32)

# ── Threads ───────────────────────────────────────────────────────────────────
def kinect_thread(ctx):
    print("[kinect] polling depth frames...")
    while True:
        raw = ctx.get_frame()
        if raw is not None:
            depth_mm = (raw >> 3).astype(np.float32)
            pts = depth_to_points(depth_mm)
            if len(pts) > 0:
                with state['lock']:
                    state['pts']    = pts
                    state['source'] = 'kinect'
        time.sleep(0.030)

def synthetic_thread():
    fake = make_synthetic()
    t0   = time.time()
    print("[synthetic] running")
    while True:
        t    = time.time() - t0
        anim = fake + 80.0 * np.sin(fake / 200.0 + t)
        pts  = depth_to_points(anim)
        with state['lock']:
            state['pts']    = pts
            state['source'] = 'synthetic'
        time.sleep(0.030)

def midi_thread():
    ports = mido.get_input_names()
    name  = next((p for p in ports if MIDI_IN_PORT in p), None)
    if not name:
        print(f"[midi] not found. Available: {ports}")
        return
    print(f"[midi] connected: {name}")
    with mido.open_input(name) as port:
        for msg in port:
            if msg.type == 'control_change':
                with state['lock']:
                    if msg.control == 2:
                        state['cc2']  = msg.value
                    elif msg.control == 20:
                        state['cc20'] = msg.value

def arduino_thread():
    hit = None
    for p in serial.tools.list_ports.comports():
        d = p.description or ''
        if any(k in d for k in ('Arduino', 'CH340', 'USB Serial', 'ttyUSB')):
            hit = p.device
            break
    if not hit:
        print(f"[arduino] not found. Ports: {[p.description for p in serial.tools.list_ports.comports()]}")
        return
    print(f"[arduino] connected: {hit}")
    try:
        ser = serial.Serial(hit, ARDUINO_BAUD, timeout=1)
        time.sleep(2)
        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                try:
                    with state['lock']:
                        state['ultra_cm'] = float(line)
                except ValueError:
                    pass
    except Exception as e:
        print(f"[arduino] error: {e}")

# ── Deformation ───────────────────────────────────────────────────────────────
def deform(pts, cc2, cc20, ultra_cm):
    pts  = pts.copy()
    t    = time.time()
    rig  = cc2 / 127.0
    pts[:, 1] += (1.0 - rig) * 0.05 * np.sin(2 * np.pi * (0.5 + (1.0 - rig) * 0.8) * t)
    sc = (cc20 / 127.0) ** 2 * 0.10
    if sc > 0.005:
        pts += np.random.randn(*pts.shape).astype(np.float32) * sc
    scale     = 1.0 + (np.clip(ultra_cm / 100.0, 0.0, 3.0) - 1.0) * 0.15
    pts[:, 0] *= scale
    pts[:, 1] *= scale
    return pts

# ── GL ────────────────────────────────────────────────────────────────────────
def setup_gl():
    glClearColor(0, 0, 0, 1)
    glDisable(GL_DEPTH_TEST)
    glDisable(GL_BLEND)
    glPointSize(2.5)
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(55.0, W / H, 0.05, 100.0)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

def draw_points(pts, cc20):
    r = 0.2 + (cc20 / 127.0) * 0.7
    g = 0.6 - (cc20 / 127.0) * 0.2
    b = 1.0 - (cc20 / 127.0) * 0.5
    glColor3f(r, g, b)
    glBegin(GL_POINTS)
    for p in pts:
        glVertex3f(float(p[0]), float(p[1]), float(p[2]))
    glEnd()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    pygame.init()
    pygame.display.set_mode((W, H), DOUBLEBUF | OPENGL)
    pygame.display.set_caption("kinect theremin demo")
    clock = pygame.time.Clock()
    setup_gl()

    ctx = None
    try:
        ctx = KINECT_CTX()
    except Exception as e:
        print(f"[kinect] init exception: {e}")

    if ctx and ctx.ok:
        threading.Thread(target=kinect_thread, args=(ctx,), daemon=True).start()
    else:
        print("[kinect] falling back to synthetic")
        threading.Thread(target=synthetic_thread, daemon=True).start()

    threading.Thread(target=midi_thread,    daemon=True).start()
    threading.Thread(target=arduino_thread, daemon=True).start()

    angle   = 0.0
    frame_n = 0

    while True:
        for ev in pygame.event.get():
            if ev.type == QUIT or (ev.type == KEYDOWN and ev.key == K_ESCAPE):
                if ctx: ctx.shutdown()
                pygame.quit(); sys.exit()

        with state['lock']:
            pts    = state['pts'].copy()
            cc2    = state['cc2']
            cc20   = state['cc20']
            ultra  = state['ultra_cm']
            source = state['source']

        pts = deform(pts, cc2, cc20, ultra)

        glClear(GL_COLOR_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -3.5)
        glRotatef(-10.0, 1, 0, 0)
        glRotatef(angle,  0, 1, 0)
        draw_points(pts, cc20)

        angle   = (angle + 0.2) % 360.0
        frame_n += 1
        if frame_n % 90 == 0:
            print(f"[{source}] pts={len(pts):,}  cc2={cc2}  cc20={cc20}  ultra={ultra:.0f}cm  fps={clock.get_fps():.0f}")

        pygame.display.flip()
        clock.tick(60)

if __name__ == '__main__':
    main()
