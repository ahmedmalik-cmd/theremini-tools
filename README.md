# theremini-tools

**Tools for using a Moog Theremini as a sensor, not just an instrument.** The Theremini's two antennas report how close a hand or body is as USB MIDI (pitch on CC20, volume on CC2). In [real & rendered](https://github.com/ahmedmalik-cmd/real-and-rendered) that signal fed the room's point cloud. These are the tools and sketches built along the way: a browser visualizer of the invisible field, MIDI utilities, a servo rig that presses the Theremini's buttons, and the experiments that fused it with cameras and depth sensors.

By **Ahmed Jamal Malik**.

| | |
|---|---|
| [`field-visualizer/`](field-visualizer) | Browser view of the field over Web MIDI: move near the antenna and watch the disturbance bend it, in 2D or 3D. Can add people tracked by a Kinect |
| [`midi/`](midi) | Desktop utilities: a tuner and a calibration console for sending CC, Program Change and notes; a live monitor; a relay that writes the Theremini's state to JSON for Unity |
| [`remote-buttons/`](remote-buttons) | An Arduino with four servos that presses the Theremini's SETUP, EFFECT, ROOT and SCALE buttons, plus a desktop panel to drive it. Built for when the instrument is sealed inside a plinth |
| [`sketches/`](sketches) | Prototyping-phase experiments, kept as they were: field maps, hand triangulation from the two antennas, shaders, and fusions with MediaPipe, monocular depth and a Kinect |

## Field visualizer

Plain HTML and JavaScript modules; no build step. Web MIDI works in Chrome, Edge and Firefox, not Safari.

```bash
cd field-visualizer && python3 -m http.server 8000     # then open http://localhost:8000
```

Click **Connect MIDI** and pick the Theremini. Input modes: MIDI, Kinect, hybrid, and a lab mode with presets for different antenna setups (shielded, foil-coupled, ultrasonic sweep, and so on). Kinect mode reads people and depth from the Kinect bridge in real & rendered ([`src/kinect-bridge`](https://github.com/ahmedmalik-cmd/real-and-rendered/tree/main/src/kinect-bridge), `http://localhost:8182`).

## MIDI utilities

```bash
pip install mido python-rtmidi
python midi/theremini_tuner.py                 # send CC / Program Change / notes, e.g. to switch presets
python midi/theremini_calibrator.py            # sliders and one-shot sends for calibrating pitch and volume
python midi/theremini_live_monitor.py          # print CC movement for 20 s (--seconds to change)
python midi/theremini_midi_relay.py --output state.json   # write the current state to JSON every 50 ms
```

## Remote buttons

Four hobby servos on pins 3, 5, 6 and 9 of an Arduino, each positioned over one of the Theremini's buttons. Flash [`theremini_remote_buttons.ino`](remote-buttons/theremini_remote_buttons.ino), then run `python remote-buttons/remote_buttons_pc.py` (needs `pyserial`). The sketch takes serial commands at 115200 baud: `P <button> <ms>` presses, `H` / `R` hold and release, `CAL` and `CALROOT` run the calibration-entry sequences, `REST` parks every servo, and `ANGLE` adjusts one.

## Sketches

Pygame/OpenGL experiments from the prototyping phase, run on Windows with the Theremini plugged in. They're kept as they were, not polished.

| | |
|---|---|
| `theremin_field_map.py` | The two antenna readings drawn as an electromagnetic field map |
| `theremin_spatial.py` | Every signal derivable from the Theremini, plus a 2D estimate of hand positions triangulated from the two antenna distances |
| `theremin_pointcloud.py` | The field as a particle cloud |
| `theremin_shader.py` | A GLSL shader over a camera feed whose scatter, rigidity and "breath" follow the Theremini |
| `theremin_vision_fusion.py` | Theremini MIDI fused with MediaPipe hands and face mesh: the field reads distance, the camera reads gesture |
| `theremin_depth_splat.py` | Monocular depth ([Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2)) from a camera, drawn as point splats that the Theremini and MediaPipe hands and face disturb. Needs an NVIDIA GPU and torch; run it from inside a Depth Anything V2 checkout with the ViT-L checkpoint in `checkpoints/` |
| `kinect/kinect_theremin_demo.py` | Kinect v1 depth (via the SDK's `Kinect10.dll`) as a point cloud, plus the Theremini and an Arduino ultrasonic sensor. `_comtypes` is the same idea over the COM interface. Windows with the Kinect for Windows SDK 1.8 |
| `kinect/kinect-v1-bridge.ps1` | An earlier, no-build Kinect v1 bridge in PowerShell (serves `/snapshot` on port 8181). Superseded by the C# bridge in real & rendered |

```bash
pip install -r requirements.txt
THEREMINI_PORT="Moog Theremini 0" python sketches/theremin_field_map.py
```

| Setting | Default | |
|---|---|---|
| `THEREMINI_PORT` | `Moog Theremini 0` | MIDI input name (`python -c "import mido; print(mido.get_input_names())"` lists them) |
| `CAMERA_URL` | unset (webcam 0) | a phone camera stream, e.g. DroidCam's `http://<phone-ip>:4747/video` |

---

Code under the [MIT License](LICENSE).
