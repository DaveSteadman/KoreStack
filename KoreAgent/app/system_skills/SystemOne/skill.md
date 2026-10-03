# System One Decision Skill

## Purpose

Use the separately loaded Ollama System One model for structured decisions over text, JSON, or optional base64 images. This is a decision interface, not a chat model: provide named typed questions and use its probabilities or scores to support the normal chat model's response.

## Interface

- Module: `KoreAgent/app/system_skills/SystemOne/system_one_skill.py`
- Function: `system_one_decide(state: str | dict | list[object], questions: dict, images: list[str] = None)`

## Parameters

### `system_one_decide()`

- `state` - Text, JSON object, or JSON array that the decision model should judge.
- `questions` - One to 64 named question objects. Every question requires `type` (`choice`, `noul`, or `score`) and `instructions`. `choice` needs a criteria object, `score` needs a lowest-to-highest criteria array, and `noul` may optionally describe true and false criteria.
- `images` - Optional base64-encoded PNG, JPEG, or WebP images. Do not pass URLs or data URLs.

## Output

Returns the configured model name, named answer objects, and input/output token usage. A `choice` answer contains the selected option and probabilities; `noul` is the probability that the answer is true; a `score` includes its probability-weighted level, legend, and probabilities.

## When to call it

Call this for classification, routing, policy checks, confidence-aware yes/no checks, or ordinal scoring when a typed decision is more suitable than free-form reasoning. Do not call it merely to generate text, answer open-ended questions, or replace the normal chat model.

## Examples

- Route a support request: `system_one_decide(state="I was charged twice and need the extra payment refunded.", questions={"team": {"type": "choice", "instructions": "Which team owns this request?", "criteria": {"billing": "Payments and refunds", "technical": "Bugs and integrations", "other": "None of the above"}}})`
- Check a proposed operation: `system_one_decide(state="send_email(to=all-customers, subject=FINAL NOTICE)", questions={"harm": {"type": "noul", "instructions": "Could this operation cause harm?"}})`
