/**
 * Non-visual confirmation for a result screen: haptic + a short synthesised
 * tone. §11.5 wants both on every result, not just success — a volunteer at
 * 9pm is not always looking at the screen between scans. Two distinct tones
 * (not just "a beep") so the audio itself carries some of the same signal
 * the colour does, for the same colour-blind-safety reason.
 *
 * No audio asset dependency: the tone is synthesised with WebAudio, which is
 * already a browser built-in.
 */

export function vibrate(pattern: number | number[]): void {
  try {
    if (typeof navigator !== "undefined" && "vibrate" in navigator) {
      navigator.vibrate(pattern);
    }
  } catch {
    // Vibration can be refused or unsupported — never block on it.
  }
}

type AudioContextCtor = typeof AudioContext;

let sharedAudioContext: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  try {
    if (!sharedAudioContext) {
      const ctor: AudioContextCtor | undefined =
        window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
      if (!ctor) {
        return null;
      }
      sharedAudioContext = new ctor();
    }
    if (sharedAudioContext.state === "suspended") {
      // Requires a user gesture on some browsers; every call site here is
      // reached from a scan result triggered by a tap or a camera loop that
      // started after one, so this resolves in practice.
      void sharedAudioContext.resume();
    }
    return sharedAudioContext;
  } catch {
    return null;
  }
}

function beep(frequencyHz: number, durationSeconds: number): void {
  const ctx = getAudioContext();
  if (!ctx) {
    return;
  }
  try {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequencyHz;
    const now = ctx.currentTime;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.22, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + durationSeconds);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(now);
    oscillator.stop(now + durationSeconds + 0.02);
  } catch {
    // Best-effort — a silent scanner is still a working scanner.
  }
}

/** One clear high chirp. */
export function playSuccessTone(): void {
  beep(880, 0.16);
}

/** Two low pulses — audibly different from success even with eyes off the screen. */
export function playErrorTone(): void {
  beep(220, 0.14);
  window.setTimeout(() => beep(220, 0.18), 180);
}

export function resultFeedback(tone: "success" | "error"): void {
  if (tone === "success") {
    vibrate(70);
    playSuccessTone();
  } else {
    vibrate([60, 80, 60]);
    playErrorTone();
  }
}
