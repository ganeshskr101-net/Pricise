// Pricise engine sidecar (the folder keeps its first name, world_model_ts): three Meterless engines in ONE Node process on
// 127.0.0.1 with the same safeguards (loopback Host check, 64 KB body limit, field allowlists, no CORS, no body logs).
// - World Model: the Meterless reference engine (src/, unchanged). backend/engines/world_model.py writes every finding
//   version here; the trace reads them back.
// - Scout Intent: Pricise's implementation of the Scout spec (scout/). backend/engines/scout.py sends untrusted text here.
// - H-MEM: the Meterless reference engine (hmem/src/, unchanged) behind hmem/pricise.ts: the shopper's notes, typed by the
//   user only, checked for secrets and by Scout, kept in HMEM_DATA_DIR (default <repo>/.pricise-data/memory/, git-ignored).
//   backend/engines/hmem.py is the client.
//
// Every route: a request with an Origin header is refused (403: browsers send one, our Python clients do not), and a
// POST or PUT must be Content-Type: application/json (415). Without this, any web page could send a blind "simple"
// cross-site POST to 127.0.0.1 (its Host header passes the loopback check) and plant findings or notes.
//
//   POST /findings                {key, check_id, finding} -> {changed, version, entity_id}
//   GET  /checks/{check_id}/findings                        -> {findings: [{key, version, proposed_by, statuses, finding}]}
//   POST /scout/guard             {texts, surface?}         -> {results: [{level, flags}], packs}   (flags: ids + offsets only)
//   POST /scout/decide            {prompt, user?, surface}  -> Scout decision; its contract is UNSIGNED (the backend signs)
//   GET  /hmem/notes                                        -> {notes: [Note], count}
//   PUT  /hmem/notes/{id}         {kind, text, store?, trust?, origin: "user_typed"} -> {note, created}   (400 = rejected)
//   DELETE /hmem/notes/{id}                                 -> {deleted}
//   POST /hmem/forget             {}                        -> {deleted: n}
//   POST /hmem/query              {query, top?}             -> {results: [{note, relevance}]}
//   GET  /health                                            -> {ok: true}
//
// One finding = one observation (externalId = key, type "finding"). Its text is the canonical JSON of the finding without
// "ts", so re-sending the same finding is skipped by the engine's checksum and a real change is a new version of the SAME
// entity. Versions and status history are read from the event log. In-memory only: a restart starts an empty world.
// H-MEM notes DO survive a restart (hmem/pricise.ts writes them to disk).
// Never logs request bodies. Binds to 127.0.0.1 only. Start: npx tsx server.ts (port from WORLD_MODEL_URL, default 8788).

import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WorldModel, canonicalJson, externalId } from "./src/index.ts";
import type { Entity, WorldEvent } from "./src/index.ts";
import { Scout } from "./scout/index.ts";
import { PriciseMemory, badNote } from "./hmem/index.ts";
import type { NoteInput } from "./hmem/index.ts";

const HOST = "127.0.0.1";
const MAX_BODY = 64 * 1024;
const KEY_RE = /^[0-9a-f]{64}$/;
const BODY_FIELDS = new Set(["key", "check_id", "finding"]);
const GUARD_FIELDS = new Set(["texts", "surface"]);
const DECIDE_FIELDS = new Set(["prompt", "user", "surface"]);
const MAX_TEXTS = 64;
const MAX_PROMPT = 4000;
const SCOUT_PACKS = ["default", "adversarial-tight", "pricise"];
const QUERY_FIELDS = new Set(["query", "top"]);
const MAX_QUERY = 500;
const NOTE_PATH = /^\/hmem\/notes\/([^/]{1,64})$/;

function memoryDir(): string {
  const configured = process.env.HMEM_DATA_DIR?.trim();
  return configured || fileURLToPath(new URL("../../../.pricise-data/memory/", import.meta.url));
}

function badQueryBody(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !QUERY_FIELDS.has(k))) return "only query and top are accepted";
  if (typeof b.query !== "string" || b.query.length > MAX_QUERY) return `query must be a string of at most ${MAX_QUERY} characters`;
  if (b.top !== undefined && (!Number.isInteger(b.top) || (b.top as number) < 1 || (b.top as number) > 20)) return "top must be 1-20";
  return null;
}

/** 403 for any browser request (it carries Origin), 415 for a POST/PUT that is not JSON. null = go on. */
function badTransport(req: http.IncomingMessage): [number, string] | null {
  if (req.headers.origin !== undefined) return [403, "browser requests are not accepted"];
  if ((req.method === "POST" || req.method === "PUT") &&
      (req.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase() !== "application/json") {
    return [415, "Content-Type must be application/json"];
  }
  return null;
}

function createScout(): Scout {
  const registry = fileURLToPath(new URL("./scout/pricise-intents.json", import.meta.url));
  return new Scout({ intentRegistry: registry, policyPack: SCOUT_PACKS, signer: null });
}

function portFrom(url: string | undefined): number {
  const u = new URL(url || "http://127.0.0.1:8788");
  if (u.hostname !== "127.0.0.1" && u.hostname !== "localhost") throw new Error("WORLD_MODEL_URL must point at 127.0.0.1 or localhost");
  return Number(u.port || 80);
}

type Finding = Record<string, unknown> & { check_id: string; agent: string; status: string };

function findingText(finding: Finding): string {
  const { ts: _ts, ...rest } = finding;
  return canonicalJson(rest);
}

function createWorld(): WorldModel {
  return new WorldModel({ storage: "memory", namespace: "pricise" });
}

async function writeFinding(world: WorldModel, key: string, finding: Finding, at: string) {
  const report = await world.ingest([{
    externalId: key, type: "finding", title: String(finding.summary ?? ""), text: findingText(finding), at,
    source: { kind: "agent", by: finding.agent, at },
  }]);
  const id = externalId("finding", "ingest", key);
  return { changed: report.processed > 0, version: versionsOf(world.events(), id).length, entity_id: id };
}

function versionsOf(events: readonly WorldEvent[], id: string): Finding[] {
  const out: Finding[] = [];
  for (const e of events) {
    const p = e.payload as Entity;
    if (e.kind === "upsertEntity" && p.id === id) out.push(JSON.parse(String(p.attrs.text)) as Finding);
  }
  return out;
}

function findingsOf(world: WorldModel, checkId: string) {
  const firstSeen: string[] = []; // arrival order = order of each finding's first event
  const seen = new Set<string>();
  for (const e of world.events()) {
    const p = e.payload as Entity;
    if (e.kind === "upsertEntity" && p.type === "finding" && !seen.has(p.id)) {
      seen.add(p.id);
      firstSeen.push(p.id);
    }
  }
  const rows = [];
  for (const id of firstSeen) {
    const versions = versionsOf(world.events(), id);
    const latest = versions[versions.length - 1];
    if (latest.check_id !== checkId) continue;
    rows.push({
      key: id.slice("finding:ingest:".length), version: versions.length, proposed_by: versions[0].agent,
      statuses: versions.map((v) => v.status), finding: latest,
    });
  }
  return rows;
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json" }); // no CORS headers: web pages cannot read this
  res.end(JSON.stringify(body));
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("too large"));
        req.destroy();
      } else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

function badBody(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !BODY_FIELDS.has(k))) return "only key, check_id and finding are accepted";
  if (typeof b.key !== "string" || !KEY_RE.test(b.key)) return "key must be 64 hex characters";
  if (typeof b.check_id !== "string" || !b.check_id || b.check_id.length > 128) return "check_id must be a short string";
  const f = b.finding as Finding;
  if (!f || typeof f !== "object" || Array.isArray(f)) return "finding must be an object";
  if (f.check_id !== b.check_id || typeof f.agent !== "string" || typeof f.status !== "string") return "finding does not match check_id";
  return null;
}

function badGuardBody(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !GUARD_FIELDS.has(k))) return "only texts and surface are accepted";
  if (!Array.isArray(b.texts) || b.texts.length > MAX_TEXTS || b.texts.some((t) => typeof t !== "string")) {
    return `texts must be a list of at most ${MAX_TEXTS} strings`;
  }
  if (b.surface !== undefined && (typeof b.surface !== "string" || b.surface.length > 32)) return "surface must be a short string";
  return null;
}

function badDecideBody(body: unknown): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const b = body as Record<string, unknown>;
  if (Object.keys(b).some((k) => !DECIDE_FIELDS.has(k))) return "only prompt, user and surface are accepted";
  if (typeof b.prompt !== "string" || !b.prompt || b.prompt.length > MAX_PROMPT) return `prompt must be 1-${MAX_PROMPT} characters`;
  if (typeof b.surface !== "string" || !b.surface || b.surface.length > 32) return "surface must be a short string";
  const u = b.user as Record<string, unknown> | undefined;
  if (u !== undefined && (!u || typeof u !== "object" || typeof u.id !== "string" || typeof u.role !== "string" ||
      u.id.length > 64 || u.role.length > 32 || Object.keys(u).some((k) => k !== "id" && k !== "role"))) {
    return "user must be {id, role} with short strings";
  }
  return null;
}

async function jsonBody(req: http.IncomingMessage): Promise<unknown> {
  try {
    return JSON.parse(await readBody(req));
  } catch {
    return undefined;
  }
}

function createServer(world: WorldModel, memory: PriciseMemory, now: () => Date = () => new Date(),
                      scout: Scout = createScout()): http.Server {
  const startedAt = new Map<string, string>(); // check_id -> provenance time of its first write (same for every version)
  const guard = (text: string) => scout.checkTexts([text])[0].level;
  return http.createServer(async (req, res) => {
    const host = (req.headers.host ?? "").split(":")[0];
    if (host !== "127.0.0.1" && host !== "localhost") return send(res, 403, { error: "local requests only" });
    const url = new URL(req.url ?? "/", `http://${HOST}`);
    const transport = badTransport(req);
    if (transport) {
      console.log(`world-model: ${req.method} ${url.pathname} ${transport[0]}`);
      return send(res, transport[0], { error: transport[1] });
    }
    try {
      if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true });
      const m = url.pathname.match(/^\/checks\/([^/]{1,128})\/findings$/);
      if (req.method === "GET" && m) return send(res, 200, { findings: findingsOf(world, decodeURIComponent(m[1])) });
      if (req.method === "POST" && url.pathname === "/findings") {
        let body: unknown;
        try {
          body = JSON.parse(await readBody(req));
        } catch {
          return send(res, 400, { error: "invalid or too large JSON body" });
        }
        const problem = badBody(body);
        if (problem) return send(res, 400, { error: problem });
        const { key, check_id, finding } = body as { key: string; check_id: string; finding: Finding };
        if (!startedAt.has(check_id)) startedAt.set(check_id, now().toISOString());
        return send(res, 200, await writeFinding(world, key, finding, startedAt.get(check_id)!));
      }
      if (req.method === "POST" && url.pathname === "/scout/guard") {
        const body = await jsonBody(req);
        if (body === undefined) return send(res, 400, { error: "invalid or too large JSON body" });
        const problem = badGuardBody(body);
        if (problem) return send(res, 400, { error: problem });
        const results = scout.checkTexts((body as { texts: string[] }).texts).map(({ level, flags }) => ({ level, flags }));
        return send(res, 200, { results, packs: SCOUT_PACKS });
      }
      if (req.method === "POST" && url.pathname === "/scout/decide") {
        const body = await jsonBody(req);
        if (body === undefined) return send(res, 400, { error: "invalid or too large JSON body" });
        const problem = badDecideBody(body);
        if (problem) return send(res, 400, { error: problem });
        return send(res, 200, await scout.decide(body as { prompt: string; surface: string }));
      }
      if (req.method === "GET" && url.pathname === "/hmem/notes") {
        const notes = memory.list();
        return send(res, 200, { notes, count: notes.length });
      }
      const note = url.pathname.match(NOTE_PATH);
      if (req.method === "PUT" && note) {
        const body = await jsonBody(req);
        if (body === undefined) return send(res, 400, { error: "invalid or too large JSON body" });
        const problem = badNote(body, note[1], guard);
        if (problem) return send(res, 400, { error: `note not stored: ${problem}` });
        const out = memory.put({ ...(body as NoteInput), id: note[1] });
        if (out === "conflict") return send(res, 409, { error: "a different note already has this id" });
        if (out === "full") return send(res, 507, { error: "too many notes: delete some first" });
        return send(res, 200, out);
      }
      if (req.method === "DELETE" && note) return send(res, 200, { deleted: memory.delete(note[1]) });
      if (req.method === "POST" && url.pathname === "/hmem/forget") {
        const body = await jsonBody(req);
        if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).length) {
          return send(res, 400, { error: "body must be {}" });
        }
        return send(res, 200, { deleted: memory.forget() });
      }
      if (req.method === "POST" && url.pathname === "/hmem/query") {
        const body = await jsonBody(req);
        if (body === undefined) return send(res, 400, { error: "invalid or too large JSON body" });
        const problem = badQueryBody(body);
        if (problem) return send(res, 400, { error: problem });
        const { query, top } = body as { query: string; top?: number };
        return send(res, 200, { results: memory.query(query, top ?? 5) });
      }
      return send(res, 404, { error: "not found" });
    } catch (err) {
      console.error(`world-model: ${req.method} ${url.pathname} failed: ${(err as Error).name}`);
      return send(res, 500, { error: "internal error" });
    } finally {
      console.log(`world-model: ${req.method} ${url.pathname} ${res.statusCode}`);
    }
  });
}

const port = portFrom(process.env.WORLD_MODEL_URL);
const memory = new PriciseMemory(path.resolve(memoryDir()));
createServer(createWorld(), memory).listen(port, HOST, () => console.log(`world-model: listening on http://${HOST}:${port}`));
