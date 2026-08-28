# Product

## Register

product

## Platform

web

## Users

Primary readers call InferHub Chat Completions (streaming) and need to know how each route fared in today’s probe for the shapes we actually probe. InferHub operators are secondary.

They arrive on GitHub Pages, already fluent in OpenAI request shapes.

## Product Purpose

InferHub Watch is a daily probe of InferHub `/v1/chat/completions` streaming shapes. Success is leaving knowing which alias passed today’s scoring checks.

## Positioning

A red cell means InferHub’s JSON missed the documented OpenAI shape for that check. It does not mean InferHub was down.

## Brand Personality

Blunt, forensic, calm. The voice names the wire field and the verdict. It does not hype models or narrate outages.

## Anti-references

This is not an OpenCrabs changelog or parser bug tracker.

## Design Principles

Probe results, cost per M tokens, past runs, then how we test — four containers, nothing else on the board.
Fail is a contract miss on the wire, never a status-page outage.
The alias is the request; the publisher sits under it, not in a second column.
Probe results is the front page: one collapsible group per model, the in-use board route first and audition routes ranked under it. Summary chips plus a colored tests column read the verdict at a glance; the section disappears when there is nothing to rank.
Price is info; it never ranks and must not share pass/fail pill chrome.
Cell notes say what happened for the caller, then the wire field.
Prompt cache is scored on an ordinary streaming completion (no tools) with a ~2k-token prefix and a short pause between retries.
Write for InferHub API callers; OpenCrabs is not on the site.

## Accessibility & Inclusion

No numbered WCAG target. Tables must stay readable and focus must stay visible.
