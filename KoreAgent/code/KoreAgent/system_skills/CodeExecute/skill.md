# CodeExecute Skill

## Purpose
- Execute a self-contained Python code snippet and return the captured stdout.
- **Always prefer code over a direct answer for any calculation, sequence, table, string operation, or data generation task** - even when the answer seems obvious from training knowledge. Running code is more reliable and verifiable than recall.
- Only Python stdlib is available; third-party packages (numpy, pandas, sympy) are not.
- When sandbox is off (`/sandbox off`), all modules are accessible. To install and use a third-party package, use `subprocess` to pip-install it first, then import normally:
  ```python
  import subprocess, sys
  subprocess.run([sys.executable, "-m", "pip", "install", "numpy"], check=True)
  import numpy as np
  print(np.array([1,2,3]).mean())
  ```
- When paired with FileAccess, call this skill first to generate the content, then park the output with `scratch_save`, and pass `{scratch:key}` as the content argument to `file_write` - this avoids carrying the full output string as an inline argument through the tool-calling loop.
- Code must use `print()` for all output. Favour simple linear code - avoid complex class hierarchies or deeply nested call stacks.

## Trigger keyword: calculate

## Interface
- Module: `code/KoreAgent/system_skills/CodeExecute/code_execute_skill.py`
- Functions:
  - `run_python_snippet(code: str)`

## Parameters

### `run_python_snippet(code)`
- `code` *(required)* - a complete, self-contained Python snippet as a string. Must use `print()` for all output.
  - Allowed stdlib imports: `math`, `itertools`, `collections`, `csv`, `io`, `json`, `re`, `random`, `statistics`, `datetime`, `decimal`, `fractions`, `functools`, `operator`, `string`, `textwrap`, `heapq`, `bisect`, `array`, `calendar`, `time`, `cmath`.
  - Blocked when sandbox is enabled (default): `os`, `sys`, `subprocess`, `open`, `eval`, `exec`, and all file I/O.
  - When sandbox is off: all stdlib and third-party modules are accessible; use `subprocess.run([sys.executable, "-m", "pip", "install", "<pkg>"])` to install packages before importing them.
  - Always blocked regardless of sandbox state: `tkinter`, `turtle` - GUI toolkits require the main thread and will crash when used from the execution thread.
  - To process file content inside a snippet: call `read_file` first, then use `io.StringIO(content)` in the snippet - e.g. `csv.reader(io.StringIO(_data))` where `_data` is injected by embedding the content in the code string.
  - Execution timeout: 15 seconds. Sandbox state can be toggled at runtime with `/sandbox on|off`.

## Output
- `run_python_snippet(...)` - returns captured stdout as a plain string. Returns `"Error: ..."` if the snippet raises an exception, times out, or produces no output.

## Triggers
Use `run_python_snippet` by default whenever the task involves any of the following - do **not** answer from model knowledge when code can settle it:

**Arithmetic and maths**
- Any calculation, formula, or numeric result: `calculate`, `compute`, `what is X`, `evaluate`
- Powers, factorials, primes, fibonacci, sequences, series
- Sum, product, average, mean, median, mode, standard deviation
- Compound interest, percentage, ratio, conversion between units

**Tables and data generation**
- Multiplication tables, squares/cubes tables, truth tables, lookup tables
- `print a table`, `generate a list`, `produce a list`, `list all X`, `first N of`
- Identity matrix, Pascal's triangle, any structured numeric output

**String and character operations**
- Count occurrences of a letter or substring: `how many times`, `count the`
- Reverse, sort, check for palindromes, anagram detection
- Any prompt asking to inspect or transform a string value

**Number base and encoding conversions**
- `convert X to binary/hex/octal/decimal`
- ASCII codes, encoding lookups

**Iteration and enumeration**
- Collatz sequence, any recurrence relation
- `first N`, `up to N`, `for each`, `from 1 to N`

When in doubt: write code and run it rather than recalling the answer.

## Scratchpad integration
Code output can be large (generated tables, reports, CSV rows).  When the result will be
used in a downstream step, park it with `scratch_save` immediately after execution, then pass
`{scratch:key}` as the `content` argument to `write_file` or `append_file` - this avoids
carrying the full output string inline through subsequent tool-calling rounds.

- `run_python_snippet(...)` → `scratch_save("codeout", <output>)` → `write_file("data/result.txt", "{scratch:codeout}")`

## Examples
- `run_python_snippet(code="import math\nfor i in range(1, 6):\n    print(i, math.factorial(i))")` - print factorials 1-5
  - Returns: `"1 1\n2 2\n3 6\n4 24\n5 120"`
- `run_python_snippet(code="print('index,square')\nfor i in range(1, 6):\n    print(i, i*i)")` - generate CSV content; park with scratch_save then pass to write_file
