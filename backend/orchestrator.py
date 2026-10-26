"""Orchestrator: runs every agent for a check, streams their findings as they arrive, then lets the Skeptic challenge them.

Events: {"kind": "finding", "finding": Finding} and {"kind": "final_card", "card": FinalCard}. A card goes out each time an
agent finishes and after each Skeptic result, so the verdict firms up as proof arrives; the last event is always a card.

- An agent that raises or runs past its time limit becomes one "unverified" agent_failed finding; the others carry on.
- Skeptic (rules only, backend/agents/skeptic.py): challenges fake_timer / resetting_timer, fake_discount and best_price
  findings. Rule 1 (evidence) always applies. The matching rule runs only when rechecker(finding) gives its inputs;
  without them the finding keeps its status. A second round runs only when the first result is "unverified".
  A rechecker with a .types attribute is asked only about those types. An "unverified" finding with no evidence
  ("couldn't compare") makes no claim, so the Skeptic leaves it alone.
- The work runs ONCE per check_id: a second stream replays the stored events (and follows a run still in progress).
- traces/{check_id}.json records, for every finding, who proposed and challenged it, its evidence, model, rounds and status.
- Page text is untrusted: before any agent starts, every text field of chk["page"] goes through the Scout guard
  (backend/engines/scout.py). A blocked field is replaced in chk["page"], a warned one is kept; either way ONE
  "hidden_instructions" finding says so, and the check carries on. The guard never stops a check.
- AGENT_ORCH=on: findings also go to the shared World Model (backend/engines/world_model.py). A finding is identified by
  finding_key(); the same finding again emits nothing, a changed one updates its entry. The trace reads the sidecar's
  findings when it is reachable (one warning if they differ from the local entries); otherwise the local entries are used.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, get_args

from backend.agents import skeptic
from backend.agents.skeptic import RuleResult
from backend.contracts import Evidence, FinalCard, Finding
from backend.engines import scout, world_model

log = logging.getLogger("pricise.orchestrator")

Agent = Callable[[dict], AsyncIterator[Finding]]  # same shape as deal.iter_deal; may carry an .agent_name attribute
Rechecker = Callable[[Finding], Awaitable[Optional[dict]]]  # fresh rule inputs, e.g. {"visits": [...]}, or None
CardBuilder = Callable[[dict, list[Finding]], FinalCard]
Event = dict[str, Any]

TRACES_DIR = Path(__file__).resolve().parent.parent / "traces"
AGENT_TIMEOUT_S = 60.0
TIMER_TYPES = {"fake_timer", "resetting_timer"}
CHALLENGED_TYPES = TIMER_TYPES | {"fake_discount", "best_price"}
AGENT_NAMES = set(get_args(Finding.model_fields["agent"].annotation))
MODEL = "orchestrator (no model call)"
_AGENT_DONE = object()


@dataclass
class _Run:
    """The stored events of one check, shared by every stream opened for it."""
    events: list[Event] = field(default_factory=list)
    done: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: Optional[asyncio.Task] = None  # kept so the work isn't garbage-collected if the first stream closes


@dataclass
class _Entry:
    """One finding as it changes: the latest version plus every status it had."""
    finding: Finding
    proposed_by: str
    statuses: list[str]
    key: str = ""  # world_model.finding_key of the first version (AGENT_ORCH=on only)


_RUNS: dict[str, _Run] = {}
_PAGE_REPORTS: dict[str, scout.PageGuard] = {}  # check_id -> guard report from clean_page(), emitted by its run


async def clean_page(chk: dict) -> None:
    """Clean chk["page"] NOW, for callers that build agents from the page before run_check (orchestrator_stub).
    The run still emits the hidden_instructions finding. Never raises."""
    try:
        report = await asyncio.to_thread(scout.guard_page, chk.get("page"))
    except Exception as err:
        log.error("orchestrator check=%s page guard failed: %s", chk.get("check_id"), type(err).__name__)
        return
    if report.hits:
        chk["page"] = report.page
        _PAGE_REPORTS[chk["check_id"]] = report


async def run_check(chk: dict, agents: list[Agent], rechecker: Optional[Rechecker] = None, *,
                    build_card: Optional[CardBuilder] = None, agent_timeout_s: float = AGENT_TIMEOUT_S,
                    traces_dir: Optional[Path] = None) -> AsyncIterator[Event]:
    """Stream a check's events. The first call starts the work; later calls for the same check_id replay it."""
    cid = chk["check_id"]
    run = _RUNS.get(cid)
    if run is None:
        run = _RUNS[cid] = _Run()
        work = _Work(chk, run, build_card or _default_card_builder(chk), traces_dir or TRACES_DIR)
        run.task = asyncio.create_task(work.main(agents, rechecker, agent_timeout_s))
    async for event in _follow(run):
        yield event


async def _follow(run: _Run) -> AsyncIterator[Event]:
    i = 0
    while True:
        while i < len(run.events):
            yield run.events[i]
            i += 1
        if run.done:
            return
        async with run.changed:
            await run.changed.wait_for(lambda: run.done or len(run.events) > i)


def _default_card_builder(chk: dict) -> CardBuilder:
    from backend.orchestrator_stub import card_builder  # the stub's existing card builders

    return card_builder(chk)


class _Work:
    """Does one check's work and publishes its events to the shared _Run."""

    def __init__(self, chk: dict, run: _Run, build_card: CardBuilder, traces_dir: Path) -> None:
        self.chk, self.run, self.build_card, self.traces_dir = chk, run, build_card, traces_dir
        self.entries: list[_Entry] = []
        self.world = world_model.Session(chk["check_id"]) if world_model.enabled() else None
        self.by_key: dict[str, int] = {}  # finding_key -> index in entries

    async def main(self, agents: list[Agent], rechecker: Optional[Rechecker], timeout: float) -> None:
        self.chk["findings"] = []
        try:
            await self._guard_page()
            await self._agents(agents, timeout)
            await self._skeptic(rechecker)
            if not self.run.events or self.run.events[-1]["kind"] != "final_card":
                await self._card()
            await self._write_trace()
        except Exception as err:  # a bug here must not leave streams waiting forever
            log.error("orchestrator check=%s stopped: %s", self.chk["check_id"], type(err).__name__)
        finally:
            self.run.done = True
            async with self.run.changed:
                self.run.changed.notify_all()

    async def _guard_page(self) -> None:
        """Untrusted page text is checked before any agent (or model) reads it. Never raises."""
        early = _PAGE_REPORTS.pop(self.chk["check_id"], None)  # clean_page() already did the work
        if early is not None:
            await self._add(page_text_finding(self.chk["check_id"], early))
            return
        try:
            report = await asyncio.to_thread(scout.guard_page, self.chk.get("page"))
        except Exception as err:  # a guard bug must not stop the check; the model-call gate still applies
            log.error("orchestrator check=%s page guard failed: %s", self.chk["check_id"], type(err).__name__)
            return
        if report.hits:
            self.chk["page"] = report.page
            await self._add(page_text_finding(self.chk["check_id"], report))

    async def _agents(self, agents: list[Agent], timeout: float) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(_drive(a, self.chk, queue, timeout)) for a in agents]
        left = len(tasks)
        try:
            while left:
                item = await queue.get()
                if item is _AGENT_DONE:
                    left -= 1
                    await self._card()
                else:
                    await self._add(item)
        finally:
            for t in tasks:
                t.cancel()

    async def _skeptic(self, rechecker: Optional[Rechecker]) -> None:
        for i, entry in enumerate(list(self.entries)):
            if not challengeable(entry.finding):
                continue
            async for step in challenge(entry.finding, rechecker):
                await self._update(i, step)
                if step.status != "challenged":
                    await self._card()

    async def _add(self, f: Finding) -> None:
        if self.world is None:
            self.entries.append(_Entry(f, f.agent, [f.status]))
            await self._emit_finding(f)
            return
        key = world_model.finding_key(f)
        if key in self.by_key:  # the same finding again (a re-run step): emit only if it changed
            await self._update(self.by_key[key], f)
            return
        await self.world.record(key, f)
        self.by_key[key] = len(self.entries)
        self.entries.append(_Entry(f, f.agent, [f.status], key))
        await self._emit_finding(f)  # new to this stream: always shown once

    async def _update(self, i: int, f: Finding) -> None:
        if self.world is not None and not await self.world.record(self.entries[i].key, f):
            return
        self.entries[i].finding = f
        self.entries[i].statuses.append(f.status)
        await self._emit_finding(f)

    async def _emit_finding(self, f: Finding) -> None:
        self.chk["findings"].append(f.model_dump())
        await self._emit({"kind": "finding", "finding": f})

    async def _card(self) -> None:
        try:
            card = self.build_card(self.chk, [e.finding for e in self.entries])
        except Exception as err:
            log.error("orchestrator check=%s card failed: %s", self.chk["check_id"], type(err).__name__)
            return
        self.chk["final_card"] = card.model_dump()
        await self._emit({"kind": "final_card", "card": card})

    async def _emit(self, event: Event) -> None:
        self.run.events.append(event)
        async with self.run.changed:
            self.run.changed.notify_all()

    async def _trace_rows(self) -> list[dict]:
        """The sidecar's findings when it is reachable (the shared state), else the local entries."""
        local = [trace_row(e) for e in self.entries]
        remote = await self.world.findings() if self.world is not None else None
        if remote is None:
            return local
        try:
            rows = [trace_row(_Entry(Finding.model_validate(r["finding"]), r["proposed_by"], list(r["statuses"])))
                    for r in remote]
        except Exception as err:
            log.warning("orchestrator check=%s world model findings unreadable: %s", self.chk["check_id"], type(err).__name__)
            return local
        if rows != local:
            log.warning("orchestrator check=%s world model has %d findings, local has %d, or they differ; the trace uses "
                        "the world model", self.chk["check_id"], len(rows), len(local))
        return rows

    async def _write_trace(self) -> None:
        cid = self.chk["check_id"]
        path = self.traces_dir / f"{re.sub(r'[^A-Za-z0-9_-]', '', cid) or 'check'}.json"
        trace = {"check_id": cid, "url": self.chk.get("url"), "findings": await self._trace_rows()}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
        except OSError as err:
            log.warning("orchestrator check=%s trace not written: %s", cid, type(err).__name__)


def trace_row(entry: _Entry) -> dict:
    f = entry.finding
    return {"type": f.type, "summary": f.summary, "proposed_by": entry.proposed_by, "challenged_by": f.challenged_by,
            "evidence": [e.model_dump() for e in f.evidence], "model": f.model, "rounds": f.rounds,
            "final_status": f.status, "statuses": entry.statuses}


# ----------------------------------------------------------------------------- agents


def agent_name(agent: Agent) -> str:
    return getattr(agent, "agent_name", None) or getattr(agent, "__name__", "agent")


async def _drive(agent: Agent, chk: dict, queue: asyncio.Queue, timeout: float) -> None:
    """Feed one agent's findings into the queue; a failure becomes one finding. Always ends with _AGENT_DONE."""
    try:
        async with asyncio.timeout(timeout):
            async for f in agent(chk):
                await queue.put(f)
    except Exception as err:
        await queue.put(failed_finding(chk["check_id"], agent_name(agent), err, timeout))
    finally:
        await queue.put(_AGENT_DONE)


def page_text_finding(check_id: str, report: scout.PageGuard) -> Finding:
    """One finding for every page text the guard removed or flagged. Says what we saw, never the text itself."""
    n = len(report.removed)
    places = f"{n} place{'s' if n != 1 else ''}"
    summary = (f"This page has text aimed at AI assistants ({places}). Pricise removed it before any check read it "
               "and did not follow it." if n else
               "This page has text that may be aimed at AI assistants. Pricise kept it but did not follow it.")
    evidence = [Evidence(kind="selector", ref=h.selector, label="Text aimed at AI") if h.selector
                else Evidence(kind="text", ref=f"page.{h.field}") for h in report.hits]
    return Finding(check_id=check_id, agent="page", type="hidden_instructions", status="confirmed" if n else "unverified",
                   summary=summary, evidence=evidence, confidence=0.9 if n else 0.5, model=scout.MODEL)


def failed_finding(check_id: str, name: str, err: BaseException, timeout: float) -> Finding:
    """The error's type only, never its message (it could hold a URL with a key)."""
    why = f"it took longer than {timeout:g} s" if isinstance(err, TimeoutError) else type(err).__name__
    return Finding(check_id=check_id, agent=name if name in AGENT_NAMES else "page", type="agent_failed",
                   status="unverified", summary=f"The {name} check failed ({why}); the rest of the check continues.",
                   confidence=0.0, model=MODEL)


# ----------------------------------------------------------------------------- Skeptic


def challengeable(f: Finding) -> bool:
    """Types the Skeptic challenges, unless the Skeptic already decided them."""
    decided = f.challenged_by == "skeptic" and f.status in ("confirmed", "rejected")
    no_claim = f.status == "unverified" and not f.evidence
    return f.type in CHALLENGED_TYPES and not decided and not no_claim


def apply_rule(f: Finding, inputs: Optional[dict]) -> Optional[RuleResult]:
    """The matching rule, or None when its inputs are missing (then the finding keeps its status)."""
    inputs = inputs or {}
    if f.type in TIMER_TYPES and "visits" in inputs:
        return skeptic.check_timer(f, inputs["visits"])
    if f.type == "fake_discount" and "history_rows" in inputs:
        return skeptic.check_discount(f, inputs["history_rows"], inputs.get("sale_price"))
    if f.type == "best_price" and {"match", "trust_verdict"} <= inputs.keys():
        return skeptic.check_best_price(f, inputs["match"], inputs["trust_verdict"])
    return None


async def fresh_inputs(rechecker: Rechecker, f: Finding) -> Optional[dict]:
    try:
        inputs = await rechecker(f)
    except Exception as err:
        log.info("orchestrator recheck of %s failed: %s", f.type, type(err).__name__)
        return None
    return inputs if isinstance(inputs, dict) else None


def _challenged(f: Finding) -> Finding:
    return f.model_copy(update={"status": "challenged", "challenged_by": "skeptic"})


async def challenge(f: Finding, rechecker: Optional[Rechecker]) -> AsyncIterator[Finding]:
    """Each version of the finding the Skeptic produces: "challenged", then the result, at most 2 rounds."""
    evidence = skeptic.check_evidence(f)
    if not evidence.passed:
        yield _challenged(f)
        yield skeptic.next_status(f, evidence)
        return
    if rechecker is None or f.type not in getattr(rechecker, "types", CHALLENGED_TYPES):
        return  # no fresh inputs: the matching rule is skipped, the finding keeps its status
    while True:
        before = f
        yield (f := _challenged(before))
        result = apply_rule(before, await fresh_inputs(rechecker, before))
        if result is None:
            yield before.model_copy(update={"challenged_by": "skeptic"})  # inputs missing: back to its status
            return
        yield (f := skeptic.next_status(f, result))
        if result.status != "unverified" or f.rounds >= skeptic.MAX_ROUNDS:
            return
