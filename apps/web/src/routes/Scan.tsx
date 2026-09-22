import { useCallback, useEffect, useRef, useState } from "react";
import { ScanDecisionCard } from "../components/ScanDecisionCard";
import { ScanResultScreen, type ResultTone } from "../components/ScanResultScreen";
import { ApiError } from "../lib/api";
import { resultFeedback } from "../lib/feedback";
import { useOnlineStatus } from "../lib/onlineStatus";
import { confirmScan, presentScan } from "../lib/scanApi";
import { getDeviceId, getGateLabel, setGateLabel } from "../lib/scanDevice";
import { useQrScanner } from "../lib/qrScanner";
import { useSession } from "../lib/session";
import type { ScanAction, SecurityScanResponse } from "../lib/types";
import { useWakeLock } from "../lib/wakeLock";
import styles from "./Scan.module.css";

/**
 * The gate scanner (LLD-v1 §11.5). One screen, four states:
 *
 *   scanning  — camera live, nothing decided
 *   decision  — ACTION_REQUIRED: a face to match and a choice to make
 *   admitted  — a transition was recorded (DONE), green, self-clearing
 *   refused   — the scan was refused, scarlet, stays until dismissed
 *
 * ...plus `duplicate`, which is `admitted`'s quieter twin: the server
 * re-serving a scan it already recorded because the operator double-tapped.
 *
 * The phone decides nothing. Which actions are legal, whether a scan needs a
 * decision at all, and whether a repeat is a duplicate are all server
 * answers; this component renders them.
 */

type ScreenState =
  | { kind: "scanning" }
  | { kind: "decision"; response: SecurityScanResponse }
  | { kind: "result"; tone: ResultTone; verdict: string; name: string | null; detail: string | null };

/**
 * Headline per error code. The server's own message becomes the detail line,
 * so the wording a volunteer acts on lives in one place (the API) and cannot
 * drift from what the API actually decided.
 */
const REFUSAL_VERDICTS: Record<string, string> = {
  PASS_REVOKED: "Do not admit",
  UNKNOWN_PASS: "No such pass",
  PASS_EXPIRED: "Pass expired",
  PASS_SUPERSEDED: "Old QR code",
  INVALID_TOKEN: "Not a valid pass",
  INVALID_SIGNATURE: "Not a valid pass",
  TOKEN_WRONG_EVENT: "Wrong event",
  GATE_NOT_PERMITTED: "Wrong gate",
  INVALID_STATE: "Can’t do that",
  DECISION_TOKEN_INVALID: "Decision expired",
  DECISION_TOKEN_EXPIRED: "Decision expired",
};

/** What the operator should do next, when the API message doesn't say. */
const REFUSAL_ADVICE: Record<string, string> = {
  DECISION_TOKEN_INVALID: "Scan them again.",
  DECISION_TOKEN_EXPIRED: "Scan them again.",
};

const DONE_VERDICTS: Record<ScanAction, string> = {
  CHECK_IN: "Checked in",
  EXIT_BREAK: "Out on a break",
  RE_ENTRY: "Back in",
  CHECK_OUT: "Checked out",
  DENIED: "Refused",
  MANUAL_OVERRIDE: "Overridden",
};

export function Scan() {
  const { user } = useSession();
  const online = useOnlineStatus();
  const videoRef = useRef<HTMLVideoElement>(null);

  const [gate, setGate] = useState(() => getGateLabel());
  const [screen, setScreen] = useState<ScreenState>({ kind: "scanning" });
  const [busy, setBusy] = useState(false);
  const [scanCount, setScanCount] = useState(0);

  // The camera runs only once the gate is named, so a volunteer is never
  // pointed at a live scanner they haven't identified yet.
  const ready = gate.trim().length > 0;
  useWakeLock(ready);

  const show = useCallback((next: ScreenState) => {
    setScreen(next);
    if (next.kind === "result") {
      resultFeedback(next.tone === "refused" ? "error" : "success");
    }
  }, []);

  const present = useCallback(
    async (token: string) => {
      setBusy(true);
      try {
        const response = await presentScan({
          token,
          device_id: getDeviceId(),
          client_scan_uuid: crypto.randomUUID(),
          occurred_at: new Date().toISOString(),
        });
        setScanCount((count) => count + 1);
        if (response.status === "ACTION_REQUIRED") {
          show({ kind: "decision", response });
          return;
        }
        show(resultForDone(response));
      } catch (error) {
        show(resultForError(error));
      } finally {
        setBusy(false);
      }
    },
    [show],
  );

  const scanner = useQrScanner({ videoRef, onDecode: (value) => void present(value), active: ready });

  const backToScanning = useCallback(() => {
    setScreen({ kind: "scanning" });
    scanner.resume();
  }, [scanner]);

  const confirm = useCallback(
    async (decisionToken: string, action: ScanAction) => {
      setBusy(true);
      try {
        const response = await confirmScan({ decision_token: decisionToken, action });
        show(resultForDone(response, action));
      } catch (error) {
        show(resultForError(error));
      } finally {
        setBusy(false);
      }
    },
    [show],
  );

  if (!ready) {
    return <GateSetup onReady={setGate} />;
  }

  return (
    <div className={styles.gate}>
      <header className={styles.header}>
        <span className={styles.gateName}>{gate}</span>
        <span className={styles.operator}>{user?.net_id ?? "Signed out"}</span>
        <span className={online ? styles.pillOnline : styles.pillOffline}>
          {online ? "Online" : "Offline"}
        </span>
      </header>

      <div className={styles.viewport}>
        <video ref={videoRef} className={styles.video} muted playsInline />
        <div className={styles.reticle} aria-hidden="true" />
        {scanner.status.kind === "blocked" && (
          <p className={styles.cameraBlocked}>{scanner.status.reason}</p>
        )}
        {scanner.status.kind === "starting" && <p className={styles.cameraNote}>Starting the camera…</p>}
        {busy && <p className={styles.cameraNote}>Checking…</p>}
      </div>

      <footer className={styles.footer}>
        {scanner.torchSupported ? (
          <button
            type="button"
            className={scanner.torchOn ? styles.torchOn : styles.torch}
            onClick={scanner.toggleTorch}
          >
            {scanner.torchOn ? "Torch on" : "Torch"}
          </button>
        ) : (
          <span className={styles.footerNote}>No torch on this device</span>
        )}
        {scanner.status.kind === "scanning" && scanner.status.decoder === "jsqr" && (
          // Worth saying out loud: on the JS fallback a scan takes visibly
          // longer, and an operator who doesn't know that starts re-presenting
          // phones and blaming the QR.
          <span className={styles.footerNote}>Slower decoder</span>
        )}
        <span className={styles.count}>
          {scanCount} {scanCount === 1 ? "scan" : "scans"} this shift
        </span>
      </footer>

      {screen.kind === "decision" && (
        <div className={styles.overlay}>
          <ScanDecisionCard
            response={screen.response}
            busy={busy}
            onConfirm={(action) => {
              if (screen.response.decision_token) {
                void confirm(screen.response.decision_token, action);
              }
            }}
            onDismiss={backToScanning}
            onExpire={() =>
              show({
                kind: "result",
                tone: "refused",
                verdict: "Decision expired",
                name: screen.response.participant_name,
                detail: "Nothing was recorded. Scan them again.",
              })
            }
          />
        </div>
      )}

      {screen.kind === "result" && (
        <div className={styles.overlay}>
          <ScanResultScreen
            tone={screen.tone}
            verdict={screen.verdict}
            name={screen.name}
            detail={screen.detail}
            onDismiss={backToScanning}
          />
        </div>
      )}
    </div>
  );
}

/** A DONE response — either auto-decided, or the confirm that just landed. */
function resultForDone(response: SecurityScanResponse, chosen?: ScanAction): ScreenState {
  const action = response.action ?? chosen ?? null;
  return {
    kind: "result",
    tone: response.duplicate ? "duplicate" : "admitted",
    verdict: response.duplicate
      ? "Already scanned"
      : action
        ? DONE_VERDICTS[action]
        : "Done",
    name: response.participant_name,
    detail: response.duplicate ? "This scan was already recorded. Nothing changed." : null,
  };
}

function resultForError(error: unknown): ScreenState {
  if (error instanceof ApiError) {
    return {
      kind: "result",
      tone: "refused",
      verdict: REFUSAL_VERDICTS[error.code] ?? "Refused",
      name: null,
      detail: REFUSAL_ADVICE[error.code] ?? error.message,
    };
  }
  // Not an API refusal — the request never landed. Say so plainly rather
  // than implying the pass was bad.
  return {
    kind: "result",
    tone: "refused",
    verdict: "No answer",
    name: null,
    detail: "The scanner couldn’t reach the server. Check the connection, then scan again.",
  };
}

/**
 * Names the gate before the camera starts. The label is local to the device
 * (see scanDevice.ts) — there is no gates endpoint yet, so this orients the
 * operator rather than scoping the scan.
 */
function GateSetup({ onReady }: { onReady: (gate: string) => void }) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className={styles.setup}>
      <form
        className={styles.setupForm}
        onSubmit={(event) => {
          event.preventDefault();
          const gate = value.trim();
          if (!gate) {
            return;
          }
          setGateLabel(gate);
          onReady(gate);
        }}
      >
        <h1 className={styles.setupTitle}>Which door is this?</h1>
        <p className={styles.setupNote}>
          Shown in the header so you always know which scanner you are holding. Saved on this
          device.
        </p>
        <input
          ref={inputRef}
          className={styles.setupInput}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Main gate"
          aria-label="Gate name"
        />
        <button type="submit" className={styles.setupSubmit} disabled={!value.trim()}>
          Start scanning
        </button>
      </form>
    </div>
  );
}
