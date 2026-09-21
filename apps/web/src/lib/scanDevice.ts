/**
 * Per-device, per-gate identity for the scanner screen. Neither value is
 * validated against a server table today: `device_id` is an opaque forensic
 * tag on `scans` (apps/api never checks it against `scanner_devices`), and
 * there is no gates-list endpoint yet for the frontend to resolve a gate
 * name to the `gate_id` UUID `/api/v1/scans` accepts. So `device_id` is a
 * real, stable UUID minted here and remembered; `gate_id` is left unset and
 * the "gate name" the header shows is a locally-typed label for the
 * operator's own orientation, not sent to the server. This is the seam:
 * once a gate-scoped verification key / gate list ships, swap the label
 * input for a picker that also yields a real `gate_id`.
 */

const DEVICE_ID_KEY = "unigate_scanner_device_id";
const GATE_LABEL_KEY = "unigate_scanner_gate_label";

export function getDeviceId(): string {
  try {
    const existing = window.localStorage.getItem(DEVICE_ID_KEY);
    if (existing) {
      return existing;
    }
    const minted = crypto.randomUUID();
    window.localStorage.setItem(DEVICE_ID_KEY, minted);
    return minted;
  } catch {
    // localStorage unavailable (private mode) — a fresh id per scan attempt
    // still works, it just won't group with earlier attempts from forensics.
    return crypto.randomUUID();
  }
}

export function getGateLabel(): string {
  try {
    return window.localStorage.getItem(GATE_LABEL_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setGateLabel(label: string): void {
  try {
    window.localStorage.setItem(GATE_LABEL_KEY, label);
  } catch {
    // Best-effort convenience — worst case the operator retypes it next shift.
  }
}
