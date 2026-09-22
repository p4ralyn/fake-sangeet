/**
 * Camera + QR decode for the gate scanner (LLD-v1 §11.5: "BarcodeDetector
 * API, jsQR fallback").
 *
 * Two decoders, because one is not enough at a real door. `BarcodeDetector`
 * is native, fast and battery-cheap, and it is what Android Chrome will use.
 * It does not exist in Safari at all, so an iPhone volunteer would otherwise
 * hold a dead scanner — jsQR decodes the same frames in JS instead. Which
 * decoder won is reported in `status`, because the fallback is visibly slower
 * and the operator should be told rather than left guessing.
 *
 * Frames are decoded at DECODE_FPS, not every animation frame. jsQR is pure
 * CPU on a full-resolution frame; at 30fps it heats the phone and flattens
 * the battery well before the last guest arrives. 8fps is far faster than a
 * person can present a phone.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/** Frames decoded per second. Well above human presentation speed. */
const DECODE_FPS = 8;
/** jsQR only: long edge the frame is downscaled to before decoding. */
const FALLBACK_FRAME_WIDTH = 480;

export type DecoderName = "native" | "jsqr";

export type CameraStatus =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "scanning"; decoder: DecoderName }
  | { kind: "blocked"; reason: string };

/**
 * `torch` is not in lib.dom's `MediaTrackConstraintSet`, but it is the
 * constraint every browser with a controllable torch actually accepts.
 * Declared rather than cast through `unknown`, so the shape stays visible.
 */
interface TorchConstraintSet extends MediaTrackConstraintSet {
  torch?: boolean;
}

interface BarcodeDetectorLike {
  detect: (source: CanvasImageSource) => Promise<{ rawValue: string }[]>;
}
interface BarcodeDetectorCtor {
  new (options?: { formats?: string[] }): BarcodeDetectorLike;
  getSupportedFormats?: () => Promise<string[]>;
}

function nativeDetectorCtor(): BarcodeDetectorCtor | null {
  const ctor = (window as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
  return ctor ?? null;
}

/**
 * Why the camera would not start, in words an operator can act on. The raw
 * DOMException names are the only reliable signal browsers give us here.
 */
function cameraRefusal(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Camera access was refused. Allow the camera for this site, then reload.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No back camera found on this device.";
    case "NotReadableError":
      return "Another app is using the camera. Close it and reload.";
    default:
      return "The camera could not be started on this device.";
  }
}

export interface QrScanner {
  status: CameraStatus;
  /** True while a decoded value is being handled and frames are ignored. */
  paused: boolean;
  torchSupported: boolean;
  torchOn: boolean;
  toggleTorch: () => void;
  /** Resume decoding after a result screen is dismissed. */
  resume: () => void;
}

export interface QrScannerOptions {
  videoRef: React.RefObject<HTMLVideoElement>;
  /** Called once per decoded value. Decoding stays paused until `resume()`. */
  onDecode: (value: string) => void;
  /** False tears the camera down — used when the route unmounts or is idle. */
  active: boolean;
}

export function useQrScanner({ videoRef, onDecode, active }: QrScannerOptions): QrScanner {
  const [status, setStatus] = useState<CameraStatus>({ kind: "idle" });
  const [paused, setPaused] = useState(false);
  const [torchSupported, setTorchSupported] = useState(false);
  const [torchOn, setTorchOn] = useState(false);

  const trackRef = useRef<MediaStreamTrack | null>(null);
  const pausedRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Held in a ref so changing the handler never restarts the camera: a
  // camera restart mid-queue is a visible stall at the gate.
  const onDecodeRef = useRef(onDecode);
  onDecodeRef.current = onDecode;

  const resume = useCallback(() => {
    pausedRef.current = false;
    setPaused(false);
  }, []);

  const toggleTorch = useCallback(() => {
    const track = trackRef.current;
    if (!track) {
      return;
    }
    const next = !torchOn;
    void track
      .applyConstraints({ advanced: [{ torch: next }] as TorchConstraintSet[] })
      .then(() => setTorchOn(next))
      .catch(() => setTorchSupported(false));
  }, [torchOn]);

  useEffect(() => {
    if (!active) {
      setStatus({ kind: "idle" });
      return;
    }

    let cancelled = false;
    let timer: number | undefined;
    let stream: MediaStream | null = null;

    async function start() {
      setStatus({ kind: "starting" });
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          // `ideal`, not `exact`: a laptop with only a front camera should
          // still scan (it is how this gets demonstrated indoors), while a
          // phone still picks the back lens.
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
      } catch (error) {
        if (!cancelled) {
          setStatus({ kind: "blocked", reason: cameraRefusal(error) });
        }
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      const video = videoRef.current;
      if (!video) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      video.srcObject = stream;
      video.setAttribute("playsinline", "true");
      try {
        await video.play();
      } catch {
        // Autoplay refusal on a muted, playsinline stream is rare; the
        // decode loop reads frames regardless of play() resolving.
      }

      const track = stream.getVideoTracks()[0] ?? null;
      trackRef.current = track;
      const capabilities = track?.getCapabilities?.() as { torch?: boolean } | undefined;
      setTorchSupported(Boolean(capabilities?.torch));

      const ctor = nativeDetectorCtor();
      let detector: BarcodeDetectorLike | null = null;
      if (ctor) {
        try {
          detector = new ctor({ formats: ["qr_code"] });
        } catch {
          detector = null;
        }
      }
      let jsQr: typeof import("jsqr").default | null = null;
      if (!detector) {
        // Loaded only when it is the decoder actually in use, so Android
        // Chrome never pays for a decoder it will not run.
        jsQr = (await import("jsqr")).default;
      }
      if (cancelled) {
        return;
      }
      setStatus({ kind: "scanning", decoder: detector ? "native" : "jsqr" });

      async function decodeFrame() {
        const element = videoRef.current;
        if (cancelled || !element || pausedRef.current || element.readyState < 2) {
          return;
        }
        let value: string | null = null;
        try {
          if (detector) {
            const found = await detector.detect(element);
            value = found[0]?.rawValue ?? null;
          } else if (jsQr) {
            value = decodeWithCanvas(element, canvasRef, jsQr);
          }
        } catch {
          // A single unreadable frame is the normal case, not an error.
          return;
        }
        if (value && !cancelled && !pausedRef.current) {
          pausedRef.current = true;
          setPaused(true);
          onDecodeRef.current(value);
        }
      }

      timer = window.setInterval(() => void decodeFrame(), 1000 / DECODE_FPS);
    }

    void start();

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearInterval(timer);
      }
      trackRef.current = null;
      setTorchOn(false);
      stream?.getTracks().forEach((track) => track.stop());
      const video = videoRef.current;
      if (video) {
        video.srcObject = null;
      }
    };
  }, [active, videoRef]);

  return { status, paused, torchSupported, torchOn, toggleTorch, resume };
}

function decodeWithCanvas(
  video: HTMLVideoElement,
  canvasRef: React.MutableRefObject<HTMLCanvasElement | null>,
  jsQr: typeof import("jsqr").default,
): string | null {
  if (!video.videoWidth) {
    return null;
  }
  if (!canvasRef.current) {
    canvasRef.current = document.createElement("canvas");
  }
  const canvas = canvasRef.current;
  const scale = Math.min(1, FALLBACK_FRAME_WIDTH / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) {
    return null;
  }
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const frame = context.getImageData(0, 0, canvas.width, canvas.height);
  // `dontInvert` — a pass QR is always dark-on-light on a phone screen, and
  // attempting inversion doubles the work for every failed frame.
  const found = jsQr(frame.data, frame.width, frame.height, { inversionAttempts: "dontInvert" });
  return found?.data ?? null;
}
