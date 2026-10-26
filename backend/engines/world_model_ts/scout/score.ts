// Scout Sense stage: the intent registry (load, compose, validate) and the Stage-1 deterministic scorer.
// Spec: Meterless engines/scout-intent docs/intent-registry.md and docs/scoring.md. Pure rules, no model call.
//
// lexical(intent) = best idf-weighted cosine between the prompt and one of the intent's seeds (its examples at weight 1,
// its description + id at 0.85). Words no intent knows still count in the prompt's length, so off-registry prompts score low.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

export type RiskClass = "low" | "medium" | "high";
export type ParameterSchema = { type?: string; properties?: Record<string, any>; required?: string[] };
export type Intent = {
  id: string;
  category: string;
  description: string;
  parameters: ParameterSchema;
  capabilities: string[];
  riskClass: RiskClass;
  surfaces: string[];
  roles: string[];
  examples: string[];
  version: number;
};
export type IntentRegistry = { version: number; intents: Intent[] };
export type Candidate = { intent: string; score: number; span?: [number, number] };
export type RegistrySource = string | IntentRegistry | (string | IntentRegistry)[];

const ID_RE = /^(GENERAL|[a-z][a-z0-9-]*(\.[a-z0-9-]+)+)$/;
const RISK = new Set(["low", "medium", "high"]);

export function loadRegistry(source: RegistrySource): IntentRegistry {
  if (Array.isArray(source)) {
    const parts = source.map(loadRegistry); // composition: later registries may not redefine an id
    return validate({ version: Math.max(...parts.map((p) => p.version)), intents: parts.flatMap((p) => p.intents) });
  }
  if (typeof source !== "string") return validate(source);
  if (statSync(source).isDirectory()) {
    const intents: Intent[] = [];
    for (const f of readdirSync(source).filter((n) => n.endsWith(".json")).sort()) {
      const doc = JSON.parse(readFileSync(join(source, f), "utf-8"));
      intents.push(...(Array.isArray(doc.intents) ? doc.intents : [doc]));
    }
    return validate({ version: Math.max(1, ...intents.map((i) => i.version)), intents });
  }
  const doc = JSON.parse(readFileSync(source, "utf-8"));
  return validate({ version: Number(doc.version ?? 1), intents: doc.intents ?? [] });
}

function validate(reg: IntentRegistry): IntentRegistry {
  const seen = new Set<string>();
  for (const i of reg.intents) {
    if (!ID_RE.test(i.id)) throw new Error(`intent id "${i.id}" must follow category.name`);
    if (seen.has(i.id)) throw new Error(`intent "${i.id}" is defined twice`);
    if (!Array.isArray(i.examples) || !i.examples.length) throw new Error(`intent "${i.id}" has no examples`);
    if (!RISK.has(i.riskClass)) throw new Error(`intent "${i.id}" has an unknown riskClass`);
    if (!Number.isInteger(i.version)) throw new Error(`intent "${i.id}" needs an integer version`);
    seen.add(i.id);
    i.capabilities ??= [];
    i.surfaces ??= [];
    i.roles ??= [];
    i.parameters ??= { type: "object", properties: {} };
  }
  return reg;
}

// ----------------------------------------------------------------------------- tokens

const STOP = new Set(
  ("a an the and or but to of for in on at by with from into about over under me my our we us i you your it its this " +
    "that these those is are was were be been am do does did should would could can will what whats how why who which " +
    "please s t").split(" "),
);

function stem(w: string): string {
  if (w.length > 4 && w.endsWith("ies")) return w.slice(0, -3) + "y";
  if (w.length > 5 && w.endsWith("ing")) return w.slice(0, -3);
  if (w.length > 4 && w.endsWith("ed")) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) return w.slice(0, -1);
  return w;
}

export function tokens(text: string): string[] {
  const words = text.toLowerCase().replace(/['’]s\b/g, "").match(/[a-z0-9$]+(?:-[a-z0-9]+)*/g) ?? [];
  return words.map((w) => (/^\$?\d/.test(w) ? "#num" : stem(w))).filter((w) => !STOP.has(w));
}

/** Clauses of a composed prompt ("pull X and draft Y, then Z"), each with its character span. */
export function clauses(text: string): { text: string; span: [number, number] }[] {
  const out: { text: string; span: [number, number] }[] = [];
  const sep = /\s*(?:[,;]|\band then\b|\bthen\b|\band also\b|\balso\b|\band\b)\s*/gi;
  let start = 0;
  for (const m of text.matchAll(sep)) {
    out.push({ text: text.slice(start, m.index), span: [start, m.index!] });
    start = m.index! + m[0].length;
  }
  out.push({ text: text.slice(start), span: [start, text.length] });
  return out.filter((c) => tokens(c.text).length >= 2);
}

// ----------------------------------------------------------------------------- scorer

type Vec = Map<string, number>;
type Seed = { intent: string; vec: Vec; norm: number; weight: number };

export class Scorer {
  private readonly seeds: Seed[] = [];
  private readonly idf = new Map<string, number>();
  private readonly unknownIdf: number;

  constructor(readonly registry: IntentRegistry) {
    const docs = registry.intents.map(
      (i) => new Set([...i.examples, i.description, i.id.replace(/[.-]/g, " ")].flatMap(tokens)),
    );
    const n = docs.length;
    const df = new Map<string, number>();
    for (const d of docs) for (const t of d) df.set(t, (df.get(t) ?? 0) + 1);
    for (const [t, c] of df) this.idf.set(t, Math.log(1 + n / c));
    this.unknownIdf = Math.log(1 + n);
    for (const i of registry.intents) {
      for (const ex of i.examples) this.seeds.push(this.seed(i.id, ex, 1));
      this.seeds.push(this.seed(i.id, `${i.description} ${i.id.replace(/[.-]/g, " ")}`, 0.85));
    }
  }

  private seed(intent: string, text: string, weight: number): Seed {
    const vec = this.vec(tokens(text));
    return { intent, vec, norm: norm(vec), weight };
  }

  private vec(toks: string[]): Vec {
    const v: Vec = new Map();
    for (const t of toks) v.set(t, (v.get(t) ?? 0) + (this.idf.get(t) ?? this.unknownIdf));
    return v;
  }

  /** Every intent with its lexical score in [0, 1], best first. */
  score(text: string): Candidate[] {
    const v = this.vec(tokens(text));
    const n = norm(v);
    const best = new Map<string, number>(this.registry.intents.map((i) => [i.id, 0]));
    if (n > 0) {
      for (const s of this.seeds) {
        if (!s.norm) continue;
        let dot = 0;
        for (const [t, w] of v) dot += w * (s.vec.get(t) ?? 0);
        const sim = (dot / (n * s.norm)) * s.weight;
        if (sim > best.get(s.intent)!) best.set(s.intent, sim);
      }
    }
    return [...best].map(([intent, score]) => ({ intent, score: round(score) })).sort((a, b) => b.score - a.score);
  }
}

function norm(v: Vec): number {
  let s = 0;
  for (const w of v.values()) s += w * w;
  return Math.sqrt(s);
}

export function round(x: number): number {
  return Math.round(x * 1000) / 1000;
}
