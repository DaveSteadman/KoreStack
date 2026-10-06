# KoreTest2

KoreTest2 is the system-test subsystem. It runs prompts and commands against the live interfaces of the whole stack, once per build, and records the results.

## Role in the suite

- **Drives KoreAgent** with the prompts in each case through the same API users call.
- **Judges answers** with the System One model through KoreAgent's `POST /api/work-packet`.
- **Triggered by KoreCron** with `POST /api/sessions`.
- Companion to **KoreUnitTest**, which covers internal code rather than whole-stack behaviour.

## How it works

A session runs only the cases with no result for the current build (`SUITE_VERSION`), for at most one hour. A new build therefore re-tests everything, and an interrupted session resumes where it left off.

## Cases

Each case is a JSON file in `<dataroot>/datacontrol/koretest2/cases`; the filename is its permanent ID. Cases are user data and can be edited freely.

```json
{
  "prompt": "Calculate the factorial of 12.",
  "timeout_seconds": 1200,
  "evaluation": { "type": "python", "assert": "number_equals|479001600" }
}
```

Use `prompts` for a multi-turn exchange. `evaluation.asserts` takes a list, all of which must pass.

| Assertion | Meaning |
|---|---|
| `contains`, `not_contains`, `all_contains`, `none_contains` | Text checks |
| `regex`, `not_regex`, `not_empty` | Pattern and presence |
| `number_equals\|expected[\|\|tolerance]`, `all_numbers\|a\|\|b` | Numeric checks that parse every number in the output |
| `judge\|answers`, `judge\|not_error\|\|0.9`, `judge\|<question>\|\|0.8` | System One probability at or above the threshold (default 0.7) |

A custom evaluator may name a Python `module` and `function` taking `(output, case)`. A judge that cannot reach System One is an error, not a pass. Use deterministic assertions wherever an exact answer exists.

## API and UI

`POST /api/sessions` starts a run, `/status` reports health, and `/ui` shows the grid of cases against builds.

## Troubleshooting

| Problem | What to check |
|---|---|
| Nothing runs | Every case already has a result for this build |
| Judge errors | KoreAgent is up and its System One model is loaded |
| Cases missing | JSON files exist in the datacontrol cases folder |
