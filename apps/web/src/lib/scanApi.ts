/**
 * Thin wrappers over the two scanner endpoints. No logic here — just the
 * typed shape, so Scan.tsx reads as "present this token" / "confirm this
 * choice" rather than fetch plumbing.
 */
import { api } from "./api";
import type { ScanConfirmRequest, ScanRequest, ScanResponse } from "./types";

export function presentScan(body: ScanRequest): Promise<ScanResponse> {
  return api.post<ScanResponse>("/api/v1/scans", body);
}

export function confirmScan(body: ScanConfirmRequest): Promise<ScanResponse> {
  return api.post<ScanResponse>("/api/v1/scans/confirm", body);
}
