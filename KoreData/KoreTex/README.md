# KoreTex

KoreData's text-snippet subsystem. This initial version converts free text into
validated structured JSON for inspection. **Processing is preview-only**: neither
source text nor results are stored, logged or sent to external providers.
Database persistence and statement querying are later work.

## Running

KoreDataGateway starts and stops KoreTex as a child service. The configured port
is `services.koretex.port` (29620) in the suite configuration.
Open the KoreTex panel on KoreData's home page, or `/ui/tex` on the child service.
The shared KoreData navigation includes a Tex tab and the supplied Gentlecons brain icon.
Service logs live beneath the configured KoreData data directory in `Tex/`.

## Processing

Enter up to 20,000 characters and select **Process**. The output is a JSON object
whose fields are chosen by the prompt and the content, not a fixed schema. Emails,
customer relationships, intelligence snippets and other topics can have different
fields. Nested objects, arrays, strings, numbers, booleans and null values are
preserved without dropping extra fields or converting them to strings.

The default prompt encourages descriptive `snake_case` names, consistent naming
for the same concept, related facts grouped into objects, and arrays for repeated
items. It preserves attribution, uncertainty, negation and date precision, without
inventing missing facts such as a year. This supports later querying by field paths
and values. Naming consistency is prompt guidance, not a guarantee or a database
schema; a future ingestion/query layer may need field normalization.
LLM results still need human review. Storage and querying are not implemented yet.

The collapsed **System prompt** field below Source text displays the actual default
prompt and can be edited. Process sends the current field value, even when the field
is collapsed. It must be nonempty and at most 20,000 characters. Prompt edits are
request-local, retained after errors, and reset on page reload; they are not saved.
Edits can add or change fields (for example a nested `datestamp`), not just change
the contents of a predefined set. Only valid JSON with an object at the root is
required; there is no hard-coded field whitelist.

KoreTex reads KoreAgent's current chat model and Ollama host from `/api/status`,
then checks Ollama's `/api/ps` for that exact loaded model (an omitted tag means
`:latest`). There is no hard-coded model, fallback model, model download, or
explicit model-load call. Only local loopback Ollama hosts are supported.

The page checks readiness every three seconds and on focus. **Process** is disabled
while Ollama is unavailable, the configured model is unloaded, source text or prompt is empty, or
processing is in flight. The server independently repeats readiness checks before
every conversion. As with any preflight check, another client can change Ollama
state between the check and the chat request.

Extraction uses Ollama's native `/api/chat` with `format: "json"`, not a fixed
JSON schema. Malformed/incomplete responses, non-object roots and non-finite numbers
are errors, never fabricated empty previews. Requests time out after five minutes.
Errors retain the input and prompt.

## API

- `GET /status`: service health (not model readiness).
- `GET /api/readiness`: `{ready, model, reason}`.
- `POST /api/process`: `{"text": "..."}` returns `{model, result}`.
  An optional `system_prompt` string overrides the default prompt for that request.
  Invalid input returns 422; unavailable dependencies return 503;
  failed/invalid model responses return 502; timeouts return 504.

No save or query endpoint is included in this preview version.
