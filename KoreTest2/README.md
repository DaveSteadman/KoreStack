# KoreTest2

KoreTest2 is the build-keyed, incremental prompt-test runner.  A daily session
runs only tests with no result for the current `SUITE_VERSION`, for at most one
hour from the session start.

Cases are Markdown files in `Data/datacontrol/koretest2/cases`.  Their filename
is their permanent test ID.  The prose is the prompt and the fenced JSON block
defines the timeout and evaluator:

````markdown
# KoreData_FeedTest_001

Find the five latest AI-feed items.

```json
{
  "timeout_seconds": 1200,
  "evaluation": {
    "type": "python",
    "assert": "not_empty"
  }
}
```
````

The default timeout is 1,200 seconds.  Built-in Python assertions are
`contains`, `not_contains`, `all_contains`, `none_contains`, `regex`,
`not_regex`, and `not_empty`.  A custom evaluator may provide a Python
`module` and `function`; it receives `(output, case)` and returns a truthy pass
value.  Legacy multi-turn exchanges retain their sequence in JSON as `prompts`
and `assertions` when imported.

On the first KoreTest2 session, the existing named prompt exchanges are copied
into individual ID-prefixed case files.  The original KoreTest prompt JSON is
not changed.
