# 6. The agent harness runs its own tool loop on the Claude Messages API

Date: 2026-11-22
Status: Accepted

## Context
The eval needs an agent that answers each case by calling the MCP server's tools. The harness must run in CI against a local server, see every tool call and result (for the citation check), control the model, prompt and limits exactly, and record usage per call. Anthropic offers several ways to run an agent: a loop over the Messages API (written by hand or with the SDK's tool runner), the Messages API's MCP connector (Anthropic calls a remote MCP server for you), and Managed Agents (Anthropic hosts the loop and a sandbox).

## Options considered
1. **Our own loop on the Messages API**, with the MCP Python SDK as the client: list the server's tools, offer them to Claude as tools, run each `tool_use` through the MCP session, return results, repeat until the final answer. Full visibility and control; works with a local stdio server in CI. We own the loop code.
2. **The MCP connector on the Messages API.** Less code, but the server must be reachable at a public URL, so it can't run against the local pinned cohort in CI, and tool calls happen on Anthropic's side.
3. **Managed Agents.** Anthropic runs the loop and hosts the environment, which is the simplest way to run an agent in production. For an eval it's the wrong shape: the server under test is local, every run should start from a fresh, identical setup we control, and session runtime is billed on top of tokens.

## Decision
Option 1. The harness drives Claude through the Anthropic Python SDK's Messages API and executes tool calls through an MCP client session against the server (stdio locally and in CI, HTTP against the deployed server). The final answer is requested as structured output (spec §5). Models are named in config, never inferred; the harness asserts the model on each response is the one asked for. Adaptive thinking and an explicit effort level are set per run and recorded. No automatic model fallback is configured in eval runs: a refusal is recorded as a refusal, because a silent switch to another model would score the wrong model.

Default agent model: the current Opus (`claude-opus-5-5` at the time of writing), with at least one cheaper model beside it in the comparison. Model ids and prices are confirmed against Anthropic's docs at slice 5, not taken from this ADR.

## Why
The eval's trustworthiness depends on seeing and controlling everything the agent does, and on running it against the pinned local data in CI. Owning the loop is a small amount of code for that. It also mirrors how a real client (Claude Code, Claude Desktop) uses an MCP server, so what's measured is close to what's demonstrated.

## Consequences
- We maintain the loop: tool-result formatting, parallel tool calls, retries with backoff, stop reasons, a step limit and a cost cap.
- The same harness can point at the deployed server to show it works remotely.
- The MCP connector or Managed Agents could later run a demo against the deployed server; that would be a new ADR and wouldn't change the eval.
- Revisit if the SDK's tool runner can drive MCP tools with the same visibility, which would remove most of the loop code.
