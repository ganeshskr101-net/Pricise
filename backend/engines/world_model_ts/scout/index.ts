// Pricise implementation of Meterless Scout Intent (spec: github.com/Meterless/Meterless engines/scout-intent, Apache-2.0).
// Written from the spec; no Meterless code copied (the spec ships no implementation). Node built-ins only, so the
// Meterless eval runner can load it from anywhere: SCOUT_IMPL=/abs/path/to/scout/index.ts npm run evals
//
//   Sense -> Interpret -> Guard -> Route -> Recommend -> ExecutionContract
//
// Implementer's choices (the spec leaves them open):
// - Stage 1 only (no Stage-2 model). The formula's semantic/contextual/recency terms have no data offline, so the
//   weights renormalize over the terms that do: finalScore = lexical. riskPenalty is 0 (a denied intent blocks instead).
// - A clarification or a block issues NO contract: nothing may act until the user answers.
// - Contracts are signed with `signingSecret`, else with a random per-instance key; `signer: null` returns them unsigned
//   (the Pricise sidecar does this; the Python backend signs).
// - checkTexts(): the injection pattern layer alone, for untrusted text that is not a request (store page text).

import { performance } from "node:perf_hooks";
import { readFileSync } from "node:fs";
import { clauses, loadRegistry, round, Scorer, type Candidate, type Intent, type IntentRegistry, type RegistrySource } from "./score.ts";
import {
  InjectionRules, loadPacks, packVersion, piiRedactions, policiesFor, policyCheck, RateLimiter, scopeCheck, toolRefCheck,
  worst, type Flag, type LayerResult, type Level, type Pack, type Redaction, type RiskLevel,
} from "./guard.ts";
import { ephemeralSigner, hmacSigner, signingPayload, ulid, type Signer } from "./contract.ts";

export { verifyContract, canonicalJson } from "./contract.ts";
export { loadRegistry } from "./score.ts";

export type UserRef = { id: string; role: string };
export type DecideInput = { prompt: string; user?: UserRef; surface?: string; context?: unknown };
export type ModelProfile = { id: string; local?: boolean; dataClasses?: string[]; costPer1k?: number; latencyP95Ms?: number; for?: string[] };
export type Risk = { level: RiskLevel; flags: Flag[]; redactions: Redaction[]; reason?: string; approvalsRequired: string[] };
export type Clarification = { trigger: "low-band" | "tie" | "params" | "high-risk"; question: string; options: { intent: string; label: string }[] };
export type ToolStep = { capability: string; tool: string; input: Record<string, unknown> };

export const BANDS = { high: 0.8, medium: 0.55, low: 0.4 };
const TIE_WINDOW = 0.05;
const HIGH_RISK_PROCEED = 0.95;
const SECONDARY_FLOOR = 0.55;
const TTL_MS = 5 * 60 * 1000;
const SCAN_BUDGET_MS = 16;
const MAX_LINEAGE = 500;
const DEFAULT_PROFILES: ModelProfile[] = [
  { id: "local-rules", local: true, dataClasses: ["*"], costPer1k: 0, for: ["general"] },
  { id: "fast", dataClasses: ["public"], costPer1k: 1 },
  { id: "deep", dataClasses: ["public"], costPer1k: 5, for: ["plan", "research"] },
];

export function band(score: number): "high" | "medium" | "low" | "reject" {
  return score >= BANDS.high ? "high" : score >= BANDS.medium ? "medium" : score >= BANDS.low ? "low" : "reject";
}

export class Scout {
  readonly registry: IntentRegistry;
  private readonly packs: Pack[];
  private readonly scorer: Scorer;
  private readonly rules: InjectionRules;
  private readonly known: Set<string>;
  private readonly rate: RateLimiter;
  private readonly profiles: ModelProfile[];
  private readonly signer: Signer | null;
  private readonly lineage = new Map<string, { input: DecideInput; traceId: string }>();
  private readonly onEvent: (name: string, fields: Record<string, unknown>) => void;
  private readonly now: () => Date;

  constructor(options: {
    intentRegistry: RegistrySource;
    policyPack?: string | string[];
    modelProfiles?: string | ModelProfile[];
    signingSecret?: string;
    signer?: Signer | null;
    onEvent?: (name: string, fields: Record<string, unknown>) => void;
    now?: () => Date;
  }) {
    this.registry = loadRegistry(options.intentRegistry);
    this.packs = loadPacks(options.policyPack);
    this.scorer = new Scorer(this.registry);
    this.rules = new InjectionRules(this.packs);
    this.known = new Set(this.registry.intents.flatMap((i) => [...i.capabilities, i.id]));
    const limits = this.packs.reduce((acc, p) => ({ ...acc, ...p.rateLimit }), {} as Pack["rateLimit"] & object);
    this.rate = new RateLimiter(limits.perMinute, limits.highRiskPerMinute);
    this.profiles = typeof options.modelProfiles === "string"
      ? JSON.parse(readFileSync(options.modelProfiles, "utf-8")).profiles
      : options.modelProfiles ?? DEFAULT_PROFILES;
    this.signer = options.signer === null ? null
      : options.signer ?? (options.signingSecret ? hmacSigner(options.signingSecret) : ephemeralSigner().signer);
    this.onEvent = options.onEvent ?? (() => {});
    this.now = options.now ?? (() => new Date());
    this.selfTest();
  }

  /** Startup self-test: a full-registry Stage-1 scan must stay under 16 ms, or construction fails loudly. */
  private selfTest(): void {
    const probes = this.registry.intents.map((i) => i.examples[0]);
    this.scorer.score(probes[0]); // warm-up
    for (const p of probes) {
      const t0 = performance.now();
      this.scorer.score(p);
      const ms = performance.now() - t0;
      if (ms > SCAN_BUDGET_MS) throw new Error(`Scout Stage-1 self-test failed: ${ms.toFixed(1)} ms > ${SCAN_BUDGET_MS} ms`);
    }
  }

  // --------------------------------------------------------------------------- Sense

  async classify(input: { prompt: string; surface?: string }): Promise<{ candidates: Candidate[] }> {
    return { candidates: this.sense(String(input.prompt ?? "")).candidates };
  }

  private sense(prompt: string): { candidates: Candidate[]; secondary: Candidate[] } {
    const whole = this.scorer.score(prompt);
    const parts = clauses(prompt);
    if (parts.length < 2) return { candidates: whole, secondary: [] };
    const byId = new Map(whole.map((c) => [c.intent, { ...c }]));
    const tops: Candidate[] = [];
    for (const part of parts) {
      const top = this.scorer.score(part.text)[0];
      if (!top) continue;
      tops.push({ ...top, span: part.span });
      const c = byId.get(top.intent)!;
      if (top.score > c.score) Object.assign(c, { score: top.score, span: part.span });
    }
    const candidates = [...byId.values()].sort((a, b) => b.score - a.score || (a.span?.[0] ?? 0) - (b.span?.[0] ?? 0));
    const primary = candidates[0];
    const secondary = tops.filter((t) => t.intent !== primary.intent && t.score >= SECONDARY_FLOOR &&
      (!primary.span || t.span![1] <= primary.span[0] || t.span![0] >= primary.span[1]));
    return { candidates, secondary: dedupe(secondary) };
  }

  // --------------------------------------------------------------------------- Guard

  /** Untrusted texts that are not requests (store page text): injection patterns only. One result per text. */
  checkTexts(texts: string[]): LayerResult[] {
    return texts.map((t) => this.rules.check(String(t ?? "")));
  }

  async guard(input: DecideInput & { intent?: string }): Promise<Risk> {
    const prompt = String(input.prompt ?? "");
    const intent = this.intentById(input.intent ?? this.sense(prompt).candidates[0]?.intent ?? "GENERAL");
    return this.guardStack(prompt, intent, input.user ?? { id: "anonymous", role: "*" }, input.surface ?? "chat", true);
  }

  private guardStack(prompt: string, intent: Intent, user: UserRef, surface: string, takeRate: boolean): Risk {
    const layers: LayerResult[] = [this.rules.check(prompt), toolRefCheck(prompt, this.known)];
    layers.push(policyCheck(policiesFor(this.packs, intent.id), user.role, surface));
    layers.push(scopeCheck(intent, user.role, surface));
    const redactions = piiRedactions(prompt);
    if (takeRate && worst(layers.map((l) => l.level)) !== "block") {
      layers.push(this.rate.take(`${user.id}|${intent.id}|${surface}`, intent.riskClass === "high", this.now().getTime()));
    }
    const flags = layers.flatMap((l) => l.flags);
    const blocked = layers.find((l) => l.level === "block");
    const approvalsRequired = [...new Set([
      ...policiesFor(this.packs, intent.id).flatMap((p) => p.requireApproval ?? []),
      ...(intent.riskClass === "high" ? ["user-confirm"] : []),
    ])];
    if (blocked) return { level: "block", flags, redactions, reason: blocked.reason, approvalsRequired };
    const order: RiskLevel[] = ["low", "medium", "high"];
    const warns = layers.filter((l) => l.level === "warn").length;
    const level = order[Math.min(2, order.indexOf(intent.riskClass) + warns)];
    return { level, flags, redactions, approvalsRequired };
  }

  // --------------------------------------------------------------------------- full pipeline

  async decide(input: DecideInput, lineage?: { parentId: string; traceId: string }): Promise<any> {
    const t0 = performance.now();
    const prompt = String(input.prompt ?? "");
    const user = input.user ?? { id: "anonymous", role: "*" };
    const surface = input.surface ?? "chat";
    const traceId = lineage?.traceId ?? ulid();

    // Sense
    const { candidates, secondary } = this.sense(prompt);
    const top = candidates[0] ?? { intent: "GENERAL", score: 0 };
    const topBand = band(top.score);
    const primary = topBand === "reject" ? this.general() : this.intentById(top.intent);

    // Interpret
    const { parameters, references, missing } = bind(primary, prompt);
    const clarification = this.clarify(primary, candidates, topBand, missing);

    // Guard
    const risk = this.guardStack(prompt, primary, user, surface, !clarification);

    // Route + Recommend
    const plan = [primary.id, ...secondary.map((s) => s.intent)];
    const capabilities = [...new Set(plan.flatMap((id) => this.intentById(id).capabilities))];
    const toolPlan: ToolStep[] = risk.level === "block" ? [] : capabilities.map((c) => ({ capability: c, tool: c, input: parameters }));
    const modelProfile = this.pickProfile(primary, risk.redactions.length > 0);
    const intent = {
      primary: { id: primary.id, version: primary.version }, parameters, references, confidence: top.score, band: topBand,
      alternates: candidates.filter((c) => c.intent !== primary.id).slice(0, 3),
    };

    let executionContract: Record<string, unknown> | null = null;
    if (risk.level !== "block" && !clarification) {
      executionContract = this.issue({ intent, risk, toolPlan, modelProfile, user, surface, capabilities, traceId, parentId: lineage?.parentId });
      this.remember(String(executionContract.contractId), input, traceId);
    }
    const latencyMs = round(performance.now() - t0);
    this.onEvent("scout.decision", { traceId, intent: primary.id, band: topBand, latencyMs, stage2: "skipped" });
    if (clarification) this.onEvent("scout.clarification", { traceId, trigger: clarification.trigger });
    return {
      traceId, candidates, intent, intents: plan, risk, toolPlan, clarification, modelProfile, executionContract,
      latencyMs, stage2: "skipped",
    };
  }

  async replan(input: { parent: string; reason: string; state?: unknown }): Promise<any> {
    const parent = this.lineage.get(input.parent);
    if (!parent) throw new Error("unknown parent contract");
    this.onEvent("scout.replan", { parentId: input.parent, reason: String(input.reason ?? "").slice(0, 120) });
    return this.decide({ ...parent.input, context: input.state }, { parentId: input.parent, traceId: parent.traceId });
  }

  // --------------------------------------------------------------------------- helpers

  private clarify(intent: Intent, cands: Candidate[], topBand: string, missing: string[]): Clarification | null {
    const options = cands.slice(0, 3).filter((c) => c.score > 0).map((c) => ({ intent: c.intent, label: this.intentById(c.intent).description.split(/[.;:]/)[0] }));
    const ask = (trigger: Clarification["trigger"], question: string): Clarification => ({ trigger, question, options });
    if (topBand === "low") return ask("low-band", "Which of these did you mean?");
    if (topBand === "medium" && cands[1] && cands[0].score - cands[1].score < TIE_WINDOW) return ask("tie", "Did you mean one of these?");
    if (missing.length) return ask("params", `Please tell me: ${missing.join(", ")}.`);
    if (intent.riskClass === "high" && topBand !== "reject" && cands[0].score < HIGH_RISK_PROCEED) {
      return ask("high-risk", `Please confirm: ${intent.description.split(/[.;:]/)[0]}?`);
    }
    return null;
  }

  private issue(d: {
    intent: any; risk: Risk; toolPlan: ToolStep[]; modelProfile: { id: string }; user: UserRef; surface: string;
    capabilities: string[]; traceId: string; parentId?: string;
  }): Record<string, unknown> {
    const issuedAt = this.now();
    const contract: Record<string, unknown> = {
      contractId: ulid(issuedAt.getTime()), parentId: d.parentId, traceId: d.traceId, intent: d.intent,
      risk: { level: d.risk.level, flags: d.risk.flags, redactions: d.risk.redactions, approvals: [], approvalsRequired: d.risk.approvalsRequired },
      toolPlan: d.toolPlan, modelProfile: d.modelProfile, scope: { user: d.user, surface: d.surface, capabilities: d.capabilities },
      issuedAt: issuedAt.toISOString(), expiresAt: new Date(issuedAt.getTime() + TTL_MS).toISOString(),
      registryVersion: this.registry.version, policyPackVersion: packVersion(this.packs),
    };
    if (this.signer) {
      contract.signedBy = "scout";
      contract.signature = this.signer.sign(signingPayload(contract));
    } else {
      contract.signedBy = "unsigned"; // verifyContract refuses it until a signer (the Pricise backend) signs it
    }
    this.onEvent("contract.issued", { contractId: contract.contractId, intent: d.intent.primary.id, risk: d.risk.level, profile: d.modelProfile.id });
    return contract;
  }

  private pickProfile(intent: Intent, sensitive: boolean): { id: string } {
    const cleared = this.profiles.filter((p) => !sensitive || p.local);
    const pool = cleared.length ? cleared : this.profiles;
    const preferred = pool.filter((p) => p.for?.includes(intent.category));
    const pick = (preferred.length ? preferred : pool).slice().sort((a, b) => (a.costPer1k ?? 0) - (b.costPer1k ?? 0))[0];
    return { id: pick?.id ?? "local-rules" };
  }

  private remember(contractId: string, input: DecideInput, traceId: string): void {
    this.lineage.set(contractId, { input: { prompt: input.prompt, user: input.user, surface: input.surface }, traceId });
    if (this.lineage.size > MAX_LINEAGE) this.lineage.delete(this.lineage.keys().next().value!);
  }

  private intentById(id: string): Intent {
    return this.registry.intents.find((i) => i.id === id) ?? this.general();
  }

  private general(): Intent {
    return this.registry.intents.find((i) => i.id === "GENERAL") ?? {
      id: "GENERAL", category: "general", description: "Safe fallback", parameters: {}, capabilities: [], riskClass: "low",
      surfaces: ["*"], roles: ["*"], examples: ["hello"], version: 1,
    };
  }
}

function dedupe(cands: Candidate[]): Candidate[] {
  const seen = new Set<string>();
  return cands.filter((c) => !seen.has(c.intent) && seen.add(c.intent));
}

// ----------------------------------------------------------------------------- Interpret: parameter binding

const ENTITY_KEYS = new Set(["account", "recipient", "subject"]);
const STAGES = /\b(negotiation|closed-won|closed-lost|discovery|proposal|qualification)\b/i;

function bind(intent: Intent, prompt: string): { parameters: Record<string, unknown>; references: Record<string, unknown>; missing: string[] } {
  const parameters: Record<string, unknown> = {};
  const references: Record<string, unknown> = {};
  for (const [name, p] of Object.entries(intent.parameters?.properties ?? {})) {
    const value = bindOne(name, p, prompt);
    if (value === undefined) continue;
    parameters[name] = value;
    if (ENTITY_KEYS.has(name)) references[name] = { id: `entity:${String(value).toLowerCase()}`, name: value };
  }
  const missing = (intent.parameters?.required ?? []).filter((r) => !(r in parameters));
  return { parameters, references, missing };
}

function bindOne(name: string, p: any, prompt: string): unknown {
  if (Array.isArray(p.enum)) return p.enum.find((v: string) => new RegExp(`\\b${v}s?\\b`, "i").test(prompt));
  if (p.type === "integer") {
    for (const m of prompt.matchAll(/(?<![$\d])\b(\d{1,4})\b(?![-\d%k])/gi)) {
      const n = Number(m[1]);
      if ((p.minimum === undefined || n >= p.minimum) && (p.maximum === undefined || n <= p.maximum)) return n;
    }
    return undefined;
  }
  if (p.type === "number") {
    const m = prompt.match(/\$\s?(\d+(?:\.\d+)?)\s*([km])?\b/i);
    return m ? Number(m[1]) * (m[2]?.toLowerCase() === "k" ? 1e3 : m[2]?.toLowerCase() === "m" ? 1e6 : 1) : undefined;
  }
  if (name === "stage") return prompt.match(STAGES)?.[1]?.toLowerCase();
  if (ENTITY_KEYS.has(name)) {
    const words = [...prompt.matchAll(/\b([A-Z][A-Za-z]+)\b/g)].filter((m) => m.index! > 0).map((m) => m[1]);
    return words.find((w) => !/^(Q\d|I|The|What|Show|Draft|Email|Summarize|Move)$/.test(w));
  }
  return undefined;
}

export type { Candidate, Intent, IntentRegistry, Level, Pack };
