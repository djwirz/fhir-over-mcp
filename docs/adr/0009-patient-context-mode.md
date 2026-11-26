# 9. Patient-context mode: bound per process, enforced at one choke point

Date: 2026-11-26
Status: Accepted

## Context
In patient-context mode no tool result may contain another patient's data (spec §7, invariant 3). Two things could go wrong: the agent asks for someone else, or the FHIR server returns more than was asked for (FHIR lets a server ignore a search parameter it doesn't support). The tools are already narrow and patient-shaped ([ADR 0002](0002-read-only-task-shaped-grounded-tools.md)). What's left to decide is where the check lives, what a binding attaches to, and what a result says when something was held back.

## Options considered
Where the check lives:
1. **In each tool.** A guard call and a filter in all five. Plain to read, but a sixth tool is unscoped until someone remembers, and tool code holds foreign resources that can end up in an error message.
2. **A wrapper between the tools and the FHIR client.** The tools get the same `read` and `search`, and never see what the wrapper removed. One place to test, and it can refuse anything it has no rule for.
3. **A check on the finished results.** The items don't carry a subject, so it would need to remember every ref it approved, and error messages would bypass it.

What a binding attaches to:
1. **The server process**, set at startup.
2. **Each session or request**, so one server serves many patients. Needed only over HTTP, and it depends on access control that isn't designed yet (spec §13).

## Decision
Option 2 for the check, and a binding per server process.
- `serve --patient Patient/<id>` (`build_server(settings, scope=...)`). A value that isn't a patient reference stops the server from starting; it never falls back to unbound.
- `ScopedReader` guards requests: another patient's id is refused before any request, in the same words whether or not that patient exists. It forces the filter onto every search (`_id` for patients, `patient` for the rest).
- It checks every resource on the way out. A Patient must be the bound one by id; a Condition, MedicationRequest or Observation must have `subject.reference` equal to `Patient/<id>`, relative or absolute on this server. Anything else is dropped, including a missing subject, a versioned reference or another server's.
- It fails closed: a resource type with no rule is refused.
- A Medication is the one read with no subject to check. It's allowed on purpose: it must come back as the Medication asked for, no tool takes a medication id, and the ids followed come from requests that already passed.
- Withheld data is never silent (invariant 5). If some resources are kept, the note says how many were withheld and that the list may be incomplete. If all are, the result is an error, not an empty list.
- A FHIR server's own error text and paging links are logged, not passed to the agent.
- Each refusal and drop is a guard event, counted by kind (`refused`, `dropped`) and logged. Refs appear in the log and not in results.

Tool descriptions, schemas and server instructions are the same in both modes.

## Why
The choke point makes invariant 3 a property of one small class, not a habit every tool has to keep. Refusing and dropping are different signals: a refusal means something asked, which the eval's out-of-scope cases do deliberately, while a drop means the server sent something it shouldn't have and should never happen outside tests. Binding per process is the simplest thing that works over stdio, and it can't be changed by anything the agent says.

## Consequences
- A new tool that needs another resource type must add its rule to `ScopedReader` first.
- A server that writes subjects some other way (versioned, or through a different base URL) would have its data withheld. That fails visibly; widening the accepted forms is a reviewed change.
- "Another patient's data" means resources by subject. Free text inside the bound patient's own resources is copied as recorded, even if it mentions someone else.
- The agent isn't told it's bound until it's refused or a search says so. Telling it up front would change the instructions, which needs an eval run.
- How an HTTP deployment binds a patient is slice 6's decision. If one server must serve several patients, a new ADR supersedes the per-process binding.
