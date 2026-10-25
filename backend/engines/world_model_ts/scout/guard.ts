// Scout Guard stage: five independent layers (spec docs/risk-and-policy.md), each pass | warn | block with a reason.
//   1. injection: pack patterns (scout/packs/*.json) + out-of-band tool references
//   2. policy:    pack policies per intent (deny, allowed surfaces/roles, require-approval)
//   3. scope:     the registry's surfaces and roles are the floor
//   4. PII:       redact by data class (recorded on the contract; never blocks)
//   5. rate:      per user + intent + surface, sliding one-minute window
// Evidence carries pattern ids and offsets only, never the matched text, so a payload is never logged.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Intent } from "./score.ts";

export type Level = "pass" | "warn" | "block";
export type RiskLevel = "low" | "medium" | "high" | "block";
export type Flag = { layer: string; kind: string; id: string; level: Level; at?: number };
export type Redaction = { dataClass: string; start: number; end: number };
export type PatternRule = { id: string; kind: string; pattern: string; severity: "warn" | "block"; flags?: string };
export type Policy = {
  intent: string;
  deny?: boolean;
  reason?: string;
  allow?: { roles?: string[]; surfaces?: string[] };
  requireApproval?: string[];
};
export type Pack = {
  name: string;
  version: number;
  patterns?: PatternRule[];
  escalate?: string[];
  policies?: Policy[];
  rateLimit?: { perMinute?: number; highRiskPerMinute?: number };
};
export type LayerResult = { level: Level; flags: Flag[]; reason?: string };

const PACK_DIR = fileURLToPath(new URL("./packs/", import.meta.url));
const PACK_NAME = /^[a-z0-9-]+$/;
const ZERO_WIDTH = /[​-‍⁠﻿­]/g;

export function loadPacks(spec: string | string[] | undefined): Pack[] {
  const names = spec === undefined ? ["default"] : Array.isArray(spec) ? spec : [spec];
  return names.map((n) => {
    if (!n.endsWith(".json") && !PACK_NAME.test(n)) throw new Error(`unknown policy pack "${n}"`);
    return JSON.parse(readFileSync(n.endsWith(".json") ? n : join(PACK_DIR, `${n}.json`), "utf-8")) as Pack;
  });
}

export function packVersion(packs: Pack[]): string {
  return packs.map((p) => `${p.name}@${p.version}`).join("+");
}

/** The text the patterns see: NFKC, zero-width characters removed, runs of spaces collapsed. */
export function normalize(text: string): string {
  return text.normalize("NFKC").replace(ZERO_WIDTH, "").replace(/[ \t]+/g, " ");
}

export function worst(levels: Level[]): Level {
  return levels.includes("block") ? "block" : levels.includes("warn") ? "warn" : "pass";
}

// ----------------------------------------------------------------------------- 1. injection

export class InjectionRules {
  private readonly rules: { rule: PatternRule; re: RegExp; level: Level }[];

  constructor(packs: Pack[]) {
    const escalate = new Set(packs.flatMap((p) => p.escalate ?? []));
    this.rules = packs.flatMap((p) =>
      (p.patterns ?? []).map((rule) => ({
        rule,
        re: new RegExp(rule.pattern, "i" + (rule.flags?.includes("m") ? "m" : "")),
        level: (escalate.has(rule.id) ? "block" : rule.severity) as Level,
      })),
    );
  }

  /** Pattern layer over one untrusted text. The same logic runs in Python (backend/engines/scout.py). */
  check(text: string): LayerResult {
    const t = normalize(text);
    const flags: Flag[] = [];
    for (const { rule, re, level } of this.rules) {
      const m = re.exec(t);
      if (m) flags.push({ layer: "injection", kind: rule.kind, id: rule.id, level, at: m.index });
    }
    const level = worst(flags.map((f) => f.level));
    return { level, flags, reason: level === "block" ? `injection (${flags[0].kind})` : undefined };
  }
}

const TOOL_REF = /\b(?:use|call|invoke|run|open|trigger)\s+(?:the\s+)?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b/gi;

/** Out-of-band tool references: snake_case tools the prompt asks for that no capability in the registry provides. */
export function toolRefCheck(text: string, known: Set<string>): LayerResult {
  const flags: Flag[] = [];
  for (const m of normalize(text).matchAll(TOOL_REF)) {
    const name = m[1].toLowerCase();
    if (!known.has(name) && !known.has(name.replace(/_/g, "."))) {
      flags.push({ layer: "injection", kind: "tool-ref", id: "out-of-band-tool", level: "block", at: m.index });
    }
  }
  return { level: flags.length ? "block" : "pass", flags, reason: flags.length ? "injection (tool-ref)" : undefined };
}

// ----------------------------------------------------------------------------- 2. policy, 3. scope

export function policiesFor(packs: Pack[], intentId: string): Policy[] {
  return packs.flatMap((p) => p.policies ?? []).filter((p) => p.intent === intentId);
}

function allows(list: string[] | undefined, value: string): boolean {
  return !list || list.includes("*") || list.includes(value);
}

export function policyCheck(policies: Policy[], role: string, surface: string): LayerResult {
  for (const p of policies) {
    if (p.deny) return deny("policy", "deny", p.reason ?? "denied by policy");
    if (!allows(p.allow?.roles, role)) return deny("policy", "role", `role "${role}" is not allowed`);
    if (!allows(p.allow?.surfaces, surface)) return deny("policy", "surface", `surface "${surface}" is not allowed`);
  }
  return { level: "pass", flags: [] };
}

export function scopeCheck(intent: Intent, role: string, surface: string): LayerResult {
  if (!allows(intent.roles, role)) return deny("scope", "role", `role "${role}" may not invoke ${intent.id}`);
  if (!allows(intent.surfaces, surface)) return deny("scope", "surface", `${intent.id} is not available on "${surface}"`);
  return { level: "pass", flags: [] };
}

function deny(layer: string, id: string, reason: string): LayerResult {
  return { level: "block", flags: [{ layer, kind: layer, id, level: "block" }], reason };
}

// ----------------------------------------------------------------------------- 4. PII

const PII: [string, RegExp][] = [
  ["email", /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi],
  ["credential", /\b(?:sk|pk|rk|ghp|gho|xox[bap])[-_][A-Za-z0-9_-]{16,}\b|\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g],
  ["ssn", /\b\d{3}-\d{2}-\d{4}\b/g],
  ["card", /\b(?:\d[ -]?){12,18}\d\b/g],
  ["phone", /(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b/g],
];

function luhn(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) d = d * 2 > 9 ? d * 2 - 9 : d * 2;
    sum += d;
  }
  return sum % 10 === 0;
}

/** Spans to redact, by data class. Redaction never blocks; the default is "redact aggressively, log nothing". */
export function piiRedactions(text: string): Redaction[] {
  const out: Redaction[] = [];
  for (const [dataClass, re] of PII) {
    for (const m of text.matchAll(re)) {
      if (dataClass === "card" && !luhn(m[0].replace(/\D/g, ""))) continue;
      const start = m.index!, end = start + m[0].length;
      if (!out.some((r) => start < r.end && end > r.start)) out.push({ dataClass, start, end });
    }
  }
  return out.sort((a, b) => a.start - b.start);
}

export function redact(text: string, redactions: Redaction[]): string {
  let out = text;
  for (const r of [...redactions].reverse()) out = `${out.slice(0, r.start)}[${r.dataClass}]${out.slice(r.end)}`;
  return out;
}

// ----------------------------------------------------------------------------- 5. rate limit

export class RateLimiter {
  private readonly hits = new Map<string, number[]>();

  constructor(private readonly perMinute = 60, private readonly highRiskPerMinute = 10) {}

  take(key: string, highRisk: boolean, now: number): LayerResult {
    const limit = highRisk ? this.highRiskPerMinute : this.perMinute;
    const recent = (this.hits.get(key) ?? []).filter((t) => now - t < 60_000);
    if (recent.length >= limit) return deny("rate", "rate-limit", `more than ${limit} requests a minute`);
    recent.push(now);
    if (this.hits.size > 10_000) this.hits.clear(); // bounded memory; a reset only forgets old windows
    this.hits.set(key, recent);
    return { level: "pass", flags: [] };
  }
}
