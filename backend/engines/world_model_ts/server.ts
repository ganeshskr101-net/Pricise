// Pricise World Model sidecar: the Meterless reference engine (src/, unchanged) behind a tiny HTTP API on 127.0.0.1.
// The orchestrator (backend/engines/world_model.py) writes every finding version here; the trace reads them back.
//
//   POST /findings                {key, check_id, finding} -> {changed, version, entity_id}
//   GET  /checks/{check_id}/findings                        -> {findings: [{key, version, proposed_by, statuses, finding}]}
//   GET  /health                                            -> {ok: true}
//
// One finding = one observation (externalId = key, type "finding"). Its text is the canonical JSON of the finding without
// "ts", so re-sending the same finding is skipped by the engine's checksum and a real change is a new version of the SAME
// entity. Versions and status history are read from the event log. In-memory only: a restart starts an empty world.
// Never logs request bodies. Binds to 127.0.0.1 only. Start: npx tsx server.ts (port from WORLD_MODEL_URL, default 8788).

import http from "node:http";
import { WorldModel, canonicalJson, externalId } from "./src/index.ts";
import type { Entity, WorldEvent } from "./src/index.ts";

const HOST = "127.0.0.1";
const MAX_BODY = 64 * 1024;
const KEY_RE = /^[0-9a-f]{64}$/;
const BODY_FIELDS = new Set(["key", "check_id", "finding"]);

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

function createServer(world: WorldModel, now: () => Date = () => new Date()): http.Server {
  const startedAt = new Map<string, string>(); // check_id -> provenance time of its first write (same for every version)
  return http.createServer(async (req, res) => {
    const host = (req.headers.host ?? "").split(":")[0];
    if (host !== "127.0.0.1" && host !== "localhost") return send(res, 403, { error: "local requests only" });
    const url = new URL(req.url ?? "/", `http://${HOST}`);
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
createServer(createWorld()).listen(port, HOST, () => console.log(`world-model: listening on http://${HOST}:${port}`));
