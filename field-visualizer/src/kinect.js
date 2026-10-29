export class KinectBridgeClient {
  constructor({
    baseUrl = "http://localhost:8182",
    onState = () => {},
    onFrame = () => {},
    onError = () => {},
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.onState = onState;
    this.onFrame = onFrame;
    this.onError = onError;
    this.stateTimer = null;
    this.frameTimer = null;
    this.isRunning = false;
  }

  start() {
    if (this.isRunning) {
      return;
    }

    this.isRunning = true;
    this.pollState();
    this.pollFrame();
  }

  stop() {
    this.isRunning = false;
    window.clearTimeout(this.stateTimer);
    window.clearTimeout(this.frameTimer);
    this.stateTimer = null;
    this.frameTimer = null;
  }

  async pollState() {
    if (!this.isRunning) {
      return;
    }

    try {
      const response = await fetch(`${this.baseUrl}/state`, { cache: "no-store" });

      if (!response.ok) {
        throw new Error(`Kinect state request failed: ${response.status}`);
      }

      const state = await response.json();
      this.onState(state);
    } catch (error) {
      this.onError(error);
    } finally {
      this.stateTimer = window.setTimeout(() => this.pollState(), 1500);
    }
  }

  async pollFrame() {
    if (!this.isRunning) {
      return;
    }

    try {
      const response = await fetch(`${this.baseUrl}/frame`, { cache: "no-store" });

      if (!response.ok) {
        throw new Error(`Kinect frame request failed: ${response.status}`);
      }

      const frame = await response.json();
      this.onFrame(frame);
    } catch (error) {
      this.onError(error);
    } finally {
      this.frameTimer = window.setTimeout(() => this.pollFrame(), 120);
    }
  }
}

export function startKinectPolling({
  baseUrl,
  onFrame = () => {},
  onStatus = () => {},
  onError = () => {},
} = {}) {
  const client = new KinectBridgeClient({
    baseUrl,
    onFrame,
    onState: onStatus,
    onError(error) {
      onStatus({
        bridgeOk: false,
        connected: false,
        message: error.message,
      });
      onError(error);
    },
  });

  client.start();
  return client;
}
