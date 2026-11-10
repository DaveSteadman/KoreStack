# KoreTex

KoreData's text extraction preview. It calls the chat model already configured and
loaded in KoreAgent's local Ollama and returns a flat list of evidence-based,
atomic factual statements. Source text and results are not persisted.

## Running

KoreDataGateway starts and stops KoreTex as a child service. Its configured port
is `services.koretex.port` (29620). Open `/ui/tex` or the Tex tab in KoreData.

## Extraction

Enter up to 20,000 characters and select **Process**. The result has one shape:

```json
{
  "statements": [
    {
      "statement": "The library opens at 9 am on Monday.",
      "source_excerpt": "The library opens at 9 am on Monday."
    }
  ]
}
```

Each `statement` expresses one self-contained claim. Its `source_excerpt` is a
contiguous, verbatim passage from the input that includes the claim's context and
qualifiers. Statements preserve uncertainty, attribution, negation, dates, units
and quantities. When there are no factual claims, `statements` is empty. The server
checks the shape and confirms every excerpt appears in the submitted text; users
should still review the model's interpretation of the evidence.

The collapsed **System prompt** field shows the actual default and can be edited
for one request. Edits reset on page reload. A custom prompt can change extraction
guidance, but the response must retain the `statements` shape and source excerpts.

The **Context expansion** panel below the first output has its own editable prompt
and **Expand Statements** button. It sends the original source text and exactly one
extracted statement per model call. The prompt asks for a wider factual view of
that statement, including supported prerequisites, conditions, exceptions and
consequences. The page calls the model sequentially, shows progress, and collates
all returned statements in an editable JSON text area. Exact duplicates are
consolidated first, then one final model call receives the entire collated list.
That call merges equivalent claims and removes repetition within each statement.
Its output names the indices of supporting input statements; the server uses
those indices to restore the original source excerpts and expansion history.
Every input statement must be accounted for, or the collated sourced list is
retained with a warning. Each item includes `expanded_from`, `expanded_from_excerpts` and
`expanded_from_indices` arrays so every originating atomic statement and its
source evidence remains clear. Deduplicated items also have `source_excerpts`
for all supporting passages. A change to the
source text requires a fresh extraction before expansion.
Ollama's JSON schema format constrains expansion output. Unsupported excerpts
are omitted without discarding other sourced statements. If none remain, the
original sourced statement is retained and a warning appears with the collated
JSON, so the preview can continue through the remaining input statements.

KoreTex checks KoreAgent's `/api/status` for its chat model and local Ollama host,
then checks Ollama's `/api/ps` for that loaded model. It calls Ollama's native
`/api/chat` with JSON or a JSON schema response format and temperature zero. The page checks readiness
every three seconds and before a request. Requests time out after five minutes.

## API

- `GET /status`: service health.
- `GET /api/readiness`: `{ready, model, reason}`.
- `POST /api/process`: `{"text": "..."}` returns `{model, result}`. An optional
  `system_prompt` overrides the default for that request. Invalid input returns
  422; unavailable dependencies return 503; invalid model output or request
  errors return 502; timeouts return 504.
- `POST /api/expand-statement`: accepts the original `text`, one `statement` with
  `statement` and `source_excerpt`, and an optional `expansion_prompt`. Returns
  `{model, result, warnings}` with one or more sourced statements. Each request
  makes one model call. An invalid input excerpt is rejected; unusable model
  output falls back to the original sourced statement with a warning.
- `POST /api/collate-statements`: accepts the original `text` and the full list
  of collated expanded statements with evidence and expansion provenance. It
  makes one final model call and returns `{model, result, warnings}`. Invalid
  model output retains the input statements with a warning.

There is no save or query endpoint in this preview version.
