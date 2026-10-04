# KoreTest2

KoreTest2 is the build-keyed, incremental prompt-test runner.  A daily session
runs only tests with no result for the current `SUITE_VERSION`, for at most one
hour from the session start.

Cases are pure JSON files in `Data/datacontrol/koretest2/cases`.  Their filename
is their permanent test ID.  `prompt` is the prompt (or `prompts` for a multi-turn
exchange) and `evaluation` defines the check:

```json
{
  "prompt": "Calculate the factorial of 12.",
  "timeout_seconds": 1200,
  "evaluation": { "type": "python", "assert": "number_equals|479001600" }
}
```

`evaluation.asserts` may hold a list of assertions, all of which must pass
(`assert` with a single string still works).  The `judge` assertion asks the
System One decision model, through KoreAgent's `/api/work-packet` route, whether a
question is true of the prompt/response pair, and passes when the probability is at
least the threshold (default 0.7):

```json
"asserts": [
  "number_equals|479001600",
  "judge|answers",
  "judge|not_error||0.9",
  "judge|Does the response name a city in France?||0.8"
]
```

`judge|answers` and `judge|not_error` are built-in questions; any other text after
`judge|` is used as the question itself.  A judge failure to reach System One is an
error, not a pass.  The probability is recorded with each result.  Use the `clef`
model for judging; deterministic checks should remain exact assertions.

The default timeout is 1,200 seconds.  Built-in Python assertions are
`contains`, `not_contains`, `all_contains`, `none_contains`, `regex`,
`not_regex`, `not_empty`, and the numeric checks `number_equals|expected[||tolerance]`
and `all_numbers|a||b||c`.  Numeric checks parse every number in the output, so
`479,001,600`, `479001600.0` and `4.79001600e8` are all equal; use them instead of
string matching for numeric answers.  A custom evaluator may provide a Python
`module` and `function`; it receives `(output, case)` and returns a truthy pass
value.  Legacy multi-turn exchanges retain their sequence in JSON as `prompts`
and `assertions` when imported.

On the first KoreTest2 session, the existing named prompt exchanges are copied
into individual ID-prefixed JSON case files.  The original KoreTest prompt JSON is
not changed.
