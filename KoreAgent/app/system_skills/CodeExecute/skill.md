# CodeExecute Skill

## Purpose
- Execute a small, self-contained Python calculation and return its stdout.
- Use only when code is essential to establish an exact mechanical result: arithmetic, statistics, conversions, or an explicit algorithm.
- Never use Python to copy, parse, reconstruct, inspect, rank, select, or format Working Data. Use the `working_data_*` tools for that.
- Never use Python for editorial synthesis: reports, summaries, news digests, emails, prose, HTML, bullet points, or selecting stories. Write those outputs directly from the retrieved material.
- Do not create or read files merely to run code. Use FileAccess only when the user explicitly asks to create, modify, or inspect a file.
- Only Python stdlib is available. Code must use `print()` for output and should be short and linear.

## Interface
- Module: `KoreAgent/app/system_skills/CodeExecute/code_execute_skill.py`
- Functions:
  - `python_execute(code: str)`

## Parameters

### `python_execute(code)`
- `code` *(required)* - a complete, self-contained Python snippet as a string. Must use `print()` for all output.
  - Allowed stdlib imports: `math`, `itertools`, `collections`, `csv`, `io`, `json`, `re`, `random`, `statistics`, `datetime`, `decimal`, `fractions`, `functools`, `operator`, `string`, `textwrap`, `heapq`, `bisect`, `array`, `calendar`, `time`, `cmath`.
  - Blocked when sandbox is enabled (default): `os`, `sys`, `subprocess`, `open`, `eval`, `exec`, and all file I/O.
  - When sandbox is off: all modules are accessible except the always-blocked GUI modules.
  - Always blocked regardless of sandbox state: `tkinter`, `turtle` - GUI toolkits require the main thread and will crash when used from the execution thread.
  - To process file content inside a snippet when a file operation was requested: call `file_read` first, then use `io.StringIO(content)` in the snippet - e.g. `csv.reader(io.StringIO(_data))` where `_data` is injected by embedding the content in the code string.
  - Execution timeout: 15 seconds. Sandbox state can be toggled at runtime with `/sandbox on|off`.

## Output
- `python_execute(...)` - returns captured stdout as a plain string. Returns `"Error: ..."` if the snippet raises an exception, times out, or produces no output.

## Appropriate uses
Use `python_execute` when code is needed to establish a deterministic result:

**Arithmetic and maths**
- Any calculation, formula, or numeric result: `calculate`, `compute`, `what is X`, `evaluate`
- Powers, factorials, primes, fibonacci, sequences, series
- Sum, product, average, mean, median, mode, standard deviation
- Compound interest, percentage, ratio, conversion between units

**Structured transformations**
- Multiplication tables, squares/cubes tables, truth tables, lookup tables
- Generate or validate a mechanical numeric table or sequence from explicit rules
- Identity matrix, Pascal's triangle, or other algorithmic numeric output

**String and character operations**
- Count occurrences of a letter or substring: `how many times`, `count the`
- Reverse, sort, check for palindromes, anagram detection
- Any prompt asking to inspect or transform a string value

**Number base and encoding conversions**
- `convert X to binary/hex/octal/decimal`
- ASCII codes, encoding lookups

**Algorithms**
- Collatz sequence, recurrence relations, and bounded numeric simulations

Do not use code merely because an answer has a list, table, target word count, or generated prose.
For editorial synthesis of retrieved or supplied material, use Working Data to select and retrieve
the sources, then answer directly.

## Examples
- `python_execute(code="import math\nfor i in range(1, 6):\n    print(i, math.factorial(i))")` - print factorials 1-5
  - Returns: `"1 1\n2 2\n3 6\n4 24\n5 120"`
- `python_execute(code="print('index,square')\nfor i in range(1, 6):\n    print(i, i*i)")` - generate a deterministic CSV preview
