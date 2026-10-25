// Pricise bridge to the vendored Markovian engine (./src, unchanged). Written by the Pricise team.
//
// The engine runs a whole chain in one call: run({goal, generator}) calls generator(prompt) once per chunk. Here the
// generator WAITS for the next chunk from the Python wishlist watch (backend/watch.py via backend/engines/markovian.py),
// which sends it over HTTP. So the engine does what it is for (bounded prompts: goal + carryover only, the chunk limit,
// marker handling, token accounting) while Python keeps the checkpoints and the decisions.
//
// Safety:
// - The chunk text and the first carryover are built by Python from checkpoint facts only. Here they must also pass
//   badText(): one-line facts in a small character set, length limits, and the Scout guard (pass only).
// - Never historyDir (the engine would write every chunk's content to disk), never compressorFn (no model call),
//   reflect: false (no extra generator call). The engine's own history stays in memory and is not exposed.
// - At most MAX_OPEN_CHAINS chains at once (429 after that). A chain whose generator waits longer than the chunk
//   timeout (60 s; MARKOVIAN_CHUNK_TIMEOUT_MS can only lower it, for tests) is aborted and dropped.

import { randomBytes } from "node:crypto";
import { Markovian, estimateTokens } from "./index.ts";
import type { ChunkConfig, MarkovianRun } from "./index.ts";

export const MAX_OPEN_CHAINS = 4;
// carryoverTokens 512: the watch's carryover for 20 items, worst case, is 502 estimated tokens (measured), so the engine
// keeps it as sent (marker level) instead of its heuristic fallback. chunkSize must be >= 400 + carryoverTokens + 1200.
export const CHAIN_CONFIG: Partial<ChunkConfig> = { chunkSize: 3000, maxChunks: 8, carryoverTokens: 512, overlapTokens: 0 };
export const CHAIN_ID_RE = /^c-[0-9a-f]{16}$/;
export const MAX_GOAL = 300;
export const MAX_TEXT = 4000;
const SAFE_LINE = /^[A-Za-z0-9 .,:;@=+_\-\[\]]*$/;
const DEFAULT_TIMEOUT_MS = 60_000;

export function chunkTimeoutMs(): number {
  const v = Number(process.env.MARKOVIAN_CHUNK_TIMEOUT_MS);
  return Number.isFinite(v) && v >= 100 ? Math.min(v, DEFAULT_TIMEOUT_MS) : DEFAULT_TIMEOUT_MS;
}

export type GuardFn = (text: string) => "pass" | "warn" | "block";

/** Why this text is not a checkpoint-facts text, or null. Never echoes the text. */
export function badText(text: unknown, max: number, guard?: GuardFn): string | null {
  if (typeof text !== "string" || !text.trim() || text.length > max) return `must be 1-${max} characters`;
  if (text.split("\n").some((line) => !SAFE_LINE.test(line))) {
    return "may hold only checkpoint facts (letters, digits, spaces and . , : ; @ = + - _ [ ])";
  }
  if (guard && guard(text) !== "pass") return "flagged by the Scout guard";
  return null;
}

export type StepReply =
  | { chain_id: string; done: false; step: number; prompt: string; prompt_tokens: number; token_label: "estimated" }
  | { chain_id: string; done: true; status: MarkovianRun["status"]; chunks: number; chunk_tokens: number[];
      total_tokens_used: number; efficiency_percent: number; token_label: MarkovianRun["tokenLabel"] };

interface Deferred<T> { promise: Promise<T>; resolve: (v: T) => void }

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

class Chain {
  step = 0;
  private abort = new AbortController();
  private waiting?: { resolve: (text: string) => void; reject: (err: Error) => void; timer: NodeJS.Timeout };
  private mailbox = deferred<StepReply>();

  constructor(readonly id: string, goal: string, carryover: string, private onEnd: () => void) {
    const engine = new Markovian({ chunkConfig: CHAIN_CONFIG }); // no historyDir, no compressorFn
    engine
      .run({ goal, memoryContext: carryover, generator: (prompt) => this.ask(prompt), abortSignal: this.abort.signal,
             reflect: false })
      .then((run) => this.finish(run), () => this.finish(undefined));
  }

  /** The next reply (the next prompt, or the end of the chain). */
  next(): Promise<StepReply> {
    return this.mailbox.promise;
  }

  /** Python's chunk text for the prompt the generator is waiting on. false = nothing is waiting. */
  give(text: string): boolean {
    const w = this.waiting;
    if (!w) return false;
    clearTimeout(w.timer);
    this.waiting = undefined;
    this.mailbox = deferred<StepReply>();
    w.resolve(text);
    return true;
  }

  close(why: string): void {
    this.abort.abort();
    const w = this.waiting;
    this.waiting = undefined;
    if (w) {
      clearTimeout(w.timer);
      w.reject(new Error(why));
    }
  }

  private ask(prompt: string): Promise<{ text: string }> {
    this.step += 1;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.close("no chunk within the timeout"), chunkTimeoutMs());
      this.waiting = { resolve: (text) => resolve({ text }), reject, timer };
      this.mailbox.resolve({ chain_id: this.id, done: false, step: this.step, prompt,
                             prompt_tokens: estimateTokens(prompt), token_label: "estimated" });
    });
  }

  private finish(run: MarkovianRun | undefined): void {
    this.onEnd();
    this.mailbox.resolve({
      chain_id: this.id, done: true, status: run?.status ?? "errored", chunks: run?.chunks.length ?? 0,
      chunk_tokens: run?.chunks.map((c) => c.tokens) ?? [], total_tokens_used: run?.totalTokensUsed ?? 0,
      efficiency_percent: run?.efficiencyPercent ?? 0, token_label: run?.tokenLabel ?? "estimated",
    });
  }
}

export class ChainRegistry {
  private chains = new Map<string, Chain>();

  get size(): number {
    return this.chains.size;
  }

  /** Opens a chain and returns its first prompt; "full" when MAX_OPEN_CHAINS are open. */
  async open(goal: string, carryover: string): Promise<StepReply | "full"> {
    if (this.chains.size >= MAX_OPEN_CHAINS) return "full";
    const id = `c-${randomBytes(8).toString("hex")}`;
    const chain = new Chain(id, goal, carryover, () => this.chains.delete(id));
    this.chains.set(id, chain);
    return chain.next();
  }

  /** Gives the chunk to the chain; returns the next prompt or the end. "unknown" / "not-waiting" otherwise. */
  async chunk(id: string, text: string): Promise<StepReply | "unknown" | "not-waiting"> {
    const chain = this.chains.get(id);
    if (!chain) return "unknown";
    if (!chain.give(text)) return "not-waiting";
    return chain.next();
  }

  info(id: string): { chain_id: string; step: number } | undefined {
    const chain = this.chains.get(id);
    return chain && { chain_id: id, step: chain.step };
  }

  close(id: string): boolean {
    const chain = this.chains.get(id);
    if (!chain) return false;
    chain.close("closed by the client");
    this.chains.delete(id);
    return true;
  }
}
