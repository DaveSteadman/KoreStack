# CodeExecute Skill

## Purpose
- Execute a self-contained Python code snippet and return the captured stdout.
- Use code only for deterministic calculations, parsing, validation, or structured transformations where it materially improves correctness.
- Do not use code to draft, rank, or format a narrative response, report, email, summary, or other editorial output from supplied material. Write that output directly.
- Do not create or read files merely to run code. Use FileAccess only when the user explicitly asks to create, modify, or inspect a file.
- Only Python stdlib is available; third-party packages (numpy, pandas, sympy) are not.
- When sandbox is off (`/sandbox off`), all modules are accessible. To install and use a third-party package, use `subprocess` to pip-install it first, then import normally:
  ```python
  import subprocess, sys
  subprocess.run([sys.executable, "-m", "pip", "install", "numpy"], check=True)
  import numpy as np
  print(np.array([1,2,3]).mean())
  ```
- When the user explicitly requests a generated file, code output may be saved to Working Data and passed to `file_write`.
- Code must use `print()` for all output. Favour simple linear code - avoid complex class hierarchies or deeply nested call stacks.

## Interface
- Module: `KoreAgent/app/system_skills/CodeExecute/code_execute_skill.py`
- Functions:
  - `python_execute(code: str)`

## Parameters

### `python_execute(code)`
- `code` *(required)* - a complete, self-contained Python snippet as a string. Must use `print()` for all output.
  - Allowed stdlib imports: `math`, `itertools`, `collections`, `csv`, `io`, `json`, `re`, `random`, `statistics`, `datetime`, `decimal`, `fractions`, `functools`, `operator`, `string`, `textwrap`, `heapq`, `bisect`, `array`, `calendar`, `time`, `cmath`.
  - Blocked when sandbox is enabled (default): `os`, `sys`, `subprocess`, `open`, `eval`, `exec`, and all file I/O.
  - When sandbox is off: all stdlib and third-party modules are accessible; use `subprocess.run([sys.executable, "-m", "pip", "install", "<pkg>"])` to install packages before importing them.
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
- Generate or validate a mechanical table, list, or sequence from explicit input rules
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

Do not use code merely because an answer contains a list, table, text transformation, or generated prose. For an editorial synthesis of retrieved or supplied material, read the material and answer directly.

## Examples
- `python_execute(code="import math\nfor i in range(1, 6):\n    print(i, math.factorial(i))")` - print factorials 1-5
  - Returns: `"1 1\n2 2\n3 6\n4 24\n5 120"`
- `python_execute(code="print('index,square')\nfor i in range(1, 6):\n    print(i, i*i)")` - generate a deterministic CSV preview
