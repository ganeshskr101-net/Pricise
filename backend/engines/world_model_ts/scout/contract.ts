// Scout execution contract helpers (spec docs/execution-contract.md): canonical JSON, monotonic ULIDs, HMAC signing.
// signature = HMAC_SHA256(secret, canonical_json(contract_without_signature)). An integrity check, not strong crypto.
// The signer is pluggable. Pricise's sidecar runs WITHOUT a signer (contracts come back unsigned): the Pricise backend
// signs and verifies them with its own per-process key, so no secret ever lives in the sidecar.

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export type Signer = { sign(payload: string): string };

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map((v) => (v === undefined ? "null" : canonicalJson(v))).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
}

export function hmacSigner(secret: string | Buffer): Signer {
  return { sign: (payload) => createHmac("sha256", secret).update(payload).digest("hex") };
}

/** A signer with a random key that lives only in this process (used when no secret is configured, e.g. evals). */
export function ephemeralSigner(): { signer: Signer; secret: Buffer } {
  const secret = randomBytes(32);
  return { signer: hmacSigner(secret), secret };
}

export function signingPayload(contract: Record<string, unknown>): string {
  const { signature: _s, ...rest } = contract;
  return canonicalJson(rest);
}

export function verifyContract(
  contract: Record<string, any> | null | undefined,
  opts: { secret: string | Buffer; now?: Date },
): { ok: boolean; reason?: string } {
  if (!contract || typeof contract.signature !== "string" || !contract.signature) return { ok: false, reason: "unsigned" };
  if (contract.signedBy !== "scout") return { ok: false, reason: "unsigned" };
  const want = Buffer.from(hmacSigner(opts.secret).sign(signingPayload(contract)), "hex");
  const got = Buffer.from(contract.signature, "hex");
  if (got.length !== want.length || !timingSafeEqual(got, want)) return { ok: false, reason: "tampered" };
  if (Date.parse(contract.expiresAt) <= (opts.now ?? new Date()).getTime()) return { ok: false, reason: "expired" };
  return { ok: true };
}

// ----------------------------------------------------------------------------- ULID (monotonic within this process)

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
let lastTime = -1;
let lastRandom: number[] = [];

export function ulid(now = Date.now()): string {
  if (now <= lastTime) {
    now = lastTime;
    let i = lastRandom.length - 1;
    while (i >= 0 && lastRandom[i] === 31) lastRandom[i--] = 0;
    if (i >= 0) lastRandom[i]++;
  } else {
    lastTime = now;
    lastRandom = [...randomBytes(16)].map((b) => b & 31);
  }
  let time = "";
  for (let t = now, i = 0; i < 10; i++, t = Math.floor(t / 32)) time = CROCKFORD[t % 32] + time;
  return time + lastRandom.map((r) => CROCKFORD[r]).join("");
}
