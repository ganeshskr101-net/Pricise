// Pricise shopper memory on the vendored H-MEM reference engine (./src, Meterless, unchanged). Written by the Pricise team.
//
// What this adds around the engine, and why:
// - Only notes the user typed (origin "user_typed") are accepted. Page text and model output never reach add().
// - Every note passes policy(): no card numbers, keys, tokens, prompts or multi-line text; a Scout block or warn on the
//   text rejects it too (server.ts passes the check in). A rejection says which rule, never the text.
// - Persistence is OURS, not the engine's persistDir: the engine's ledger and sleep backups keep full snapshots of every
//   record (content included), so with persistDir a deleted note would stay on disk. Here state.json holds only the
//   notes, written atomically (temp file + rename); audit.jsonl holds actions without content (time, action, id, kind).
// - Delete and forget really remove: the note leaves state.json, and the engine is REBUILT from the remaining notes, so
//   its in-memory ledger and backups no longer hold the text either.
// - Sleep, dreaming and mining are not used or exposed: sleep can archive (delete) a note the user did not delete.
// - The engine's embedder is a deterministic hash (keyword-style matching, not semantic search).

import fs from "node:fs";
import path from "node:path";
import { HMEM } from "./src/index.ts";

export type NoteKind = "preference" | "card_offer" | "store";
export type StoreTrust = "trusted" | "blocked";
export interface Note {
  id: string;
  kind: NoteKind;
  text: string;
  store?: string;
  trust?: StoreTrust;
  origin: "user_typed";
  created: string;
}
export type NoteInput = Omit<Note, "created">;
export type GuardFn = (text: string) => "pass" | "warn" | "block";

export const NOTE_FIELDS = new Set(["id", "kind", "text", "store", "trust", "origin"]);
export const ORIGINS = new Set(["user_typed"]);
const KINDS = new Set<NoteKind>(["preference", "card_offer", "store"]);
export const ID_RE = /^n-[0-9a-f]{16}$/;
const STORE_RE = /^(?=.{3,253}$)[a-z0-9-]+(\.[a-z0-9-]+)+$/;
export const MAX_TEXT = 200;
const MAX_NOTES = 500;

// Same rules as backend/engines/hmem.py policy_problem() (the Python side also checks .env values).
const SECRET_RES: [string, RegExp][] = [
  ["api key", /\b(sk|pk|rk)[-_][A-Za-z0-9_-]{12,}/],
  ["api key", /\bgh[pousr]_[A-Za-z0-9]{20,}/],
  ["api key", /\bAKIA[0-9A-Z]{16}\b/],
  ["api key", /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
  ["token", /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/],
  ["token", /\bbearer\s+[A-Za-z0-9._~+/=-]{12,}/i],
  ["secret", /\b(password|passwd|pwd|secret|token|api[ _-]?key)\s*[:=]\s*\S{4,}/i],
  ["card security code", /\b(cvv|cvc|cvv2|cid|security code)\b\W{0,3}\d{3,4}\b/i],
  ["private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
];

function luhn(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) d = d * 2 > 9 ? d * 2 - 9 : d * 2;
    sum += d;
  }
  return sum % 10 === 0;
}

function highEntropy(word: string): boolean {
  if (word.length < 24 || !/^[A-Za-z0-9_+/=-]+$/.test(word)) return false;
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/].filter((r) => r.test(word)).length;
  return classes >= 3 || (classes >= 2 && word.length >= 32);
}

/** Why this text may not be stored, or null. Never echoes the text. */
export function policy(text: string, guard?: GuardFn): string | null {
  if (typeof text !== "string" || !text.trim()) return "text is empty";
  if (text.length > MAX_TEXT) return `text is longer than ${MAX_TEXT} characters`;
  if (/[\r\n\u0000-\u001f\u007f]/.test(text)) return "text must be one line without control characters";
  for (const m of text.matchAll(/\d(?:[ -]?\d){12,18}/g)) {
    if (luhn(m[0].replace(/\D/g, ""))) return "looks like a card number";
  }
  if (/\d{12,}/.test(text.replace(/[ -]/g, ""))) return "contains a long number (card or account numbers are not stored)";
  for (const [what, re] of SECRET_RES) if (re.test(text)) return `looks like a ${what}`;
  if (text.split(/\s+/).some(highEntropy)) return "looks like a key or token";
  if (guard) {
    const level = guard(text);
    if (level !== "pass") return `the Scout guard flagged this text (${level})`;
  }
  return null;
}

/** Why this note input is not acceptable, or null. */
export function badNote(body: unknown, id: string, guard?: GuardFn): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !NOTE_FIELDS.has(k))) return "only id, kind, text, store, trust and origin are accepted";
  if (!ID_RE.test(id) || (b.id !== undefined && b.id !== id)) return "id must be n- plus 16 hex characters and match the URL";
  if (typeof b.origin !== "string" || !ORIGINS.has(b.origin)) return "only notes the user typed are stored (origin user_typed)";
  if (typeof b.kind !== "string" || !KINDS.has(b.kind as NoteKind)) return "kind must be preference, card_offer or store";
  if (b.kind === "store") {
    if (typeof b.store !== "string" || !STORE_RE.test(b.store)) return "a store note needs a store domain";
    if (b.trust !== "trusted" && b.trust !== "blocked") return "a store note needs trust: trusted or blocked";
  } else if (b.store !== undefined || b.trust !== undefined) return "store and trust are for store notes only";
  return policy(b.text as string, guard);
}

export class PriciseMemory {
  private notes = new Map<string, Note>();
  private engine!: HMEM;
  private readonly stateFile: string;
  private readonly auditFile: string;

  constructor(private readonly dir: string, private readonly now: () => Date = () => new Date()) {
    this.stateFile = path.join(dir, "state.json");
    this.auditFile = path.join(dir, "audit.jsonl");
    if (fs.existsSync(this.stateFile)) {
      const saved = JSON.parse(fs.readFileSync(this.stateFile, "utf-8")) as { notes: Note[] };
      for (const n of saved.notes) this.notes.set(n.id, n);
    }
    this.rebuild();
  }

  list(): Note[] {
    return [...this.notes.values()].sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id));
  }

  /** Adds a checked note (badNote() first). The same id again: unchanged -> created false; different -> "conflict". */
  put(input: NoteInput): { note: Note; created: boolean } | "conflict" | "full" {
    const old = this.notes.get(input.id);
    if (old) {
      const same = old.kind === input.kind && old.text === input.text && old.store === input.store && old.trust === input.trust;
      return same ? { note: old, created: false } : "conflict";
    }
    if (this.notes.size >= MAX_NOTES) return "full";
    const note: Note = { id: input.id, kind: input.kind, text: input.text, origin: "user_typed", created: this.now().toISOString(),
                         ...(input.kind === "store" ? { store: input.store, trust: input.trust } : {}) };
    this.notes.set(note.id, note);
    this.save();
    this.audit("add", note.id, note.kind);
    this.addToEngine(note);
    return { note, created: true };
  }

  delete(id: string): boolean {
    const note = this.notes.get(id);
    if (!note) return false;
    this.notes.delete(id);
    this.save();
    this.audit("delete", id, note.kind);
    this.rebuild(); // drops the engine's ledger and backups, which still hold the text
    return true;
  }

  forget(): number {
    const n = this.notes.size;
    this.notes.clear();
    this.save();
    this.audit("forget", undefined, undefined, n);
    this.rebuild();
    return n;
  }

  query(text: string, top = 5): { note: Note; relevance: number }[] {
    if (!text.trim() || this.notes.size === 0) return [];
    const result = this.engine.query(text, { topN: top });
    return result.memories
      .map((m) => ({ note: this.notes.get(noteIdOf(m.memory.tags)), relevance: m.relevance }))
      .filter((r): r is { note: Note; relevance: number } => r.note !== undefined);
  }

  private rebuild(): void {
    this.engine = new HMEM({ clock: () => this.now().getTime(), actor: "pricise" }); // in memory only: no persistDir
    for (const note of this.list()) this.addToEngine(note);
  }

  private addToEngine(note: Note): void {
    this.engine.add({
      content: note.text,
      type: note.kind === "card_offer" ? "personal" : "preference",
      layer: "long_term",
      confidence: 1,
      source: note.origin,
      tags: [`note:${note.id}`, `kind:${note.kind}`, ...(note.store ? [`store:${note.store}`, `trust:${note.trust}`] : [])],
      provenance: { origin: "manual", learnedAt: Date.parse(note.created), label: "typed by the user" },
    });
  }

  private save(): void {
    fs.mkdirSync(this.dir, { recursive: true });
    const tmp = `${this.stateFile}.${process.pid}.tmp`;
    const fd = fs.openSync(tmp, "w");
    try {
      fs.writeSync(fd, JSON.stringify({ version: 1, notes: this.list() }, null, 2) + "\n");
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, this.stateFile); // atomic replace: a crash leaves the old file or the new one, never half
  }

  private audit(action: string, id?: string, kind?: string, count?: number): void {
    fs.mkdirSync(this.dir, { recursive: true });
    const entry = { at: this.now().toISOString(), action, ...(id ? { id } : {}), ...(kind ? { kind } : {}),
                    ...(count !== undefined ? { count } : {}) }; // never the text
    fs.appendFileSync(this.auditFile, JSON.stringify(entry) + "\n", "utf-8");
  }
}

function noteIdOf(tags: string[]): string {
  return (tags.find((t) => t.startsWith("note:")) ?? "").slice("note:".length);
}
