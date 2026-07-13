---
title: Task Notes
description: Task.status.notes is an append-only journal, and it is the continuation state - there is no session resume, no handoff preamble, and no chat service.
---

# Task notes

`Task.status.notes` is an append-only journal. **It IS the continuation state.**

There is no session resume, no conversation object key, no handoff preamble, and
no chat service. A pod's turn-0 render is identical every time - the same context
bundle, built the same way, with no resume mode and no special first-turn
preamble. What carries context forward from one pod to the next is the journal.

That is a deliberate simplification. A pod is cheap and disposable; the Task is
the durable object. Everything the next pod needs to know has to be written down
on the Task, by the pod that knew it, before that pod goes away.

---

## The journal

```go
type Note struct {
    At    metav1.Time `json:"at"`
    Agent string      `json:"agent"`  // brainstorm|incident|clarify|refine|review|documentation|implement|operator
    Kind  string      `json:"kind"`   // note|plan|handoff
    Body  string      `json:"body"`   // MaxLength=4096
}
```

| Field | Rule |
|---|---|
| `agent` | **Stamped by the operator's REST layer** from `Task.status.agentKind`. It is not a request-body key, and there is no MCP argument for it. An agent can therefore never produce `agent="operator"`: when `status.agentKind` is empty the write returns **409**, and it is never defaulted. The only writer of `agent="operator"` is the operator itself, in-process |
| `kind` | `note` (an observation), `plan` (the approach), `handoff` (what the next pod needs) |
| `body` | Truncated to 4096 bytes, on a rune boundary |

An agent writes one with `task_note(kind, body)`. It reads them back in its
context bundle, or - when the bundle says some were elided - with
`task_context(notes=all)`.

!!! note "The writer is stamped, never claimed"
    `agent` is the one field an agent might want to lie about, so it is the one
    field an agent cannot set. A note attributed to the operator carries the
    operator's authority in a bundle that is otherwise untrusted content; that
    attribution has to be unforgeable, and the cheapest way to make it
    unforgeable is to never accept it from the wire.

---

## Caps and spilling

At **50** notes the oldest is spilled to `tatara-memory`
(`stats.notesSpilled` increments, and the returned track id is appended to
`stats.notesSpilledRefs`) and dropped from the CR. `MaxItems=60` on the CRD is a
backstop only.

**There is no 409-on-cap.** An agent must always be able to write its handoff.
A note write that cannot be made to fit even after eviction does not fail the
agent: it fails the **Task**, loudly, at `stageReason=object-too-large` - see
[the etcd object budget](task.md#the-etcd-object-budget).

Spilled notes are readable again. `task_context(notes=all)` rehydrates the full
history out of `tatara-memory` from `stats.notesSpilledRefs` and renders it in
order. Notes are the continuation state, so a spilled note that could not be read
back would be continuity silently lost - which is why the `<notes>` element
always carries an elision marker naming the exact call that retrieves the rest.

---

## What replaced what

| Was | Is |
|---|---|
| `Subtask` CRs, fed to a running agent turn by turn | a `plan` note <!-- stale-ok: Subtask --> |
| `tatara-chat` rooms, one per implementation stream | `note` and `handoff` notes on the Task <!-- stale-ok: tatara-chat, chat room --> |
| `chat_handoffs`, `HANDOFF_KEY`, `handover`, `sessionID`, `conversationObjectKey`, `--resume` | a `handoff` note <!-- stale-ok: chat_handoffs, handover, sessionID, conversationObjectKey --> |

Both of those mechanisms are gone. The `Subtask` CRD is deleted, along with its reconciler, its three MCP tools and its three REST endpoints. <!-- stale-ok: Subtask -->
The chat service is archived and its release is removed from the cluster.

---

## Notes in the bundle

Notes render inside the context bundle, wrapped as untrusted content like
everything else in it:

```xml
<notes total="62" rendered="50" elided="12" fetch="task_context(task=tatara-clarify-2026-07-12-m4z8q, notes=all)">
  <note agent="clarify"  at="2026-07-12T10:31Z" kind="handoff" source="agent">Scope locked. 3 repos.</note>
  <note agent="implement" at="2026-07-12T11:02Z" kind="plan" source="agent">Guard the reaper on podStartedAt plus a live-turn probe.</note>
  <note agent="operator" at="2026-07-12T13:00Z" kind="handoff" source="operator">TTL stop. Last turn's final text: ...</note>
</notes>
```

`source` is a render attribute, derived from the writer: `source="agent"` is
stamped for every note whose `agent != "operator"`, and only the operator's
in-process writer produces `source="operator"`.

`total` / `rendered` / `elided` are present unconditionally, even when nothing
was elided. An agent must never have to infer completeness from the **absence**
of an attribute.

---

## The operator writes notes too

The operator writes a note whenever it knows something the next pod will need and
no agent is in a position to write it down.

The clearest case is the **pod TTL stop**. When a pod hits
`podStartedAt + agentPodTTLSeconds`, the operator stops admitting normal turns to
it, waits for the in-flight turn's callback, and then submits exactly one final
turn: *"Your pod is being stopped. Call `task_note(kind=handoff)` with everything
the next pod needs, then stop."* The pod is asked for its own handoff, because it
is the only thing that knows what it was doing.

If that turn does not land - the hard cap at
`t0 + 2 * turnTimeoutSeconds + 60s` expires, or the wrapper returns a 410, 409 or
5xx - the operator writes a **synthetic** handoff note in-process instead, from
the last turn's final text and the list of repos that were pushed, and force-deletes
the pod.

**`Task.status.notes` is therefore never empty after a TTL stop.** Either the
agent wrote a handoff, or the operator wrote one for it.

---

## Audit

Agent pods are not Loki-scraped. The operator is.

So the operator logs **every** note write to its own stdout at INFO
(`action=task_note`, with the Task, the agent kind, the note kind and the byte
count). The journal therefore has an audit trail at Loki's retention rather than
at the Task CR's 48h delivered-TTL - the notes outlive the object they were
written on.

---

## See also

- [Task](task.md#taskstatus) - `status.notes` and the rest of the status surface
- [The Task stage machine](task-stages.md) - what the next pod is, and why there is one
- [MCP tools](mcp-tools.md) - `task_note` and `task_context`
