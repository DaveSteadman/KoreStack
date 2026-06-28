/**
 * formula.js — Expression formula engine for KoreSheet.
 *
 * Supported syntax:
 *   =B1                     — cell reference
 *   =A1+B2*3-C1/2           — arithmetic  (+ - * / ^)
 *   =SUM(A1:D4)             — aggregate functions over ranges
 *   =AVERAGE / COUNT / MIN / MAX  — same
 *   =-A1                    — unary minus
 *   =(A1+B1)*2              — parenthesised sub-expressions
 */

// ── Address utilities ──────────────────────────────────────────────────────

/**
 * Convert a 0-based column index to a column letter string.
 * colLetter(0) → 'A', colLetter(25) → 'Z', colLetter(26) → 'AA'
 */
export function colLetter(idx) {
  let s = '';
  let n = idx + 1;
  while (n > 0) {
    const rem = n % 26 || 26;
    s = String.fromCharCode(64 + rem) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

/**
 * Build a cell address string from 0-based (col, row).
 * addrOf(0, 0) → 'A1'
 */
export function addrOf(col, row) {
  return colLetter(col) + (row + 1);
}

/**
 * Parse the column letter part of a cell reference to a 0-based index.
 * colIndex('A') → 0, colIndex('Z') → 25, colIndex('AA') → 26
 */
export function colIndex(ref) {
  const letters = ref.match(/^[A-Z]+/i)[0].toUpperCase();
  let idx = 0;
  for (const ch of letters) {
    idx = idx * 26 + (ch.charCodeAt(0) - 64);
  }
  return idx - 1;
}

/**
 * Parse the row number part of a cell reference to a 0-based index.
 * rowIndex('A1') → 0, rowIndex('B10') → 9
 */
export function rowIndex(ref) {
  return parseInt(ref.match(/\d+$/)[0], 10) - 1;
}

/**
 * Expand a range string like 'A1:D4' into an array of cell address strings.
 */
export function expandRange(rangeStr) {
  const parts = rangeStr.toUpperCase().split(':');
  const c1 = colIndex(parts[0]);
  const r1 = rowIndex(parts[0]);
  const c2 = colIndex(parts[1]);
  const r2 = rowIndex(parts[1]);

  const addrs = [];
  for (let r = r1; r <= r2; r++) {
    for (let c = c1; c <= c2; c++) {
      addrs.push(addrOf(c, r));
    }
  }
  return addrs;
}

// ── Formula detection ──────────────────────────────────────────────────────

/**
 * Return true if the string is a formula (starts with '=').
 */
export function isFormula(s) {
  return typeof s === 'string' && s.trim().startsWith('=');
}

// ── Tokenizer ──────────────────────────────────────────────────────────────

/**
 * Split a formula body (without the leading '=') into tokens.
 * Returns null on unrecognised characters.
 */
function _tokenize(src) {
  const tokens = [];
  let i = 0;
  while (i < src.length) {
    if (/\s/.test(src[i])) { i++; continue; }

    // Range reference A1:B2 — must be tested before cell reference
    let m = src.slice(i).match(/^([A-Za-z]+\d+:[A-Za-z]+\d+)/);
    if (m) {
      tokens.push({ type: 'RANGE', value: m[1].toUpperCase() });
      i += m[1].length;
      continue;
    }

    // Cell reference A1
    m = src.slice(i).match(/^([A-Za-z]+\d+)/);
    if (m) {
      tokens.push({ type: 'CELL', value: m[1].toUpperCase() });
      i += m[1].length;
      continue;
    }

    // Function name (letters only, immediately followed by '(')
    m = src.slice(i).match(/^([A-Za-z]+)(?=\s*\()/);
    if (m) {
      tokens.push({ type: 'NAME', value: m[1].toUpperCase() });
      i += m[1].length;
      continue;
    }

    // Number literal
    m = src.slice(i).match(/^(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
    if (m) {
      tokens.push({ type: 'NUM', value: parseFloat(m[1]) });
      i += m[1].length;
      continue;
    }

    // Quoted string literal
    if (src[i] === '"') {
      let j = i + 1;
      while (j < src.length && src[j] !== '"') j++;
      tokens.push({ type: 'STR', value: src.slice(i + 1, j) });
      i = j + 1;
      continue;
    }

    // Single-character tokens
    const ch = src[i];
    if (ch === '+' || ch === '-' || ch === '*' || ch === '/' || ch === '^') {
      tokens.push({ type: 'OP', value: ch });
    } else if (ch === '(') {
      tokens.push({ type: 'LPAREN' });
    } else if (ch === ')') {
      tokens.push({ type: 'RPAREN' });
    } else if (ch === ',') {
      tokens.push({ type: 'COMMA' });
    } else {
      return null; // unrecognised character
    }
    i++;
  }
  return tokens;
}

// ── Recursive-descent expression evaluator ────────────────────────────────

function _evalExpr(src, cells, seen) {
  const tokens = _tokenize(src);
  if (!tokens) return '#ERR';

  let pos = 0;
  const peek = ()    => tokens[pos];
  const adv  = ()    => tokens[pos++];
  const eat  = type  => (tokens[pos]?.type === type ? tokens[pos++] : null);

  // Grammar (standard precedence):
  //   expr    = term  (('+' | '-') term)*
  //   term    = power (('*' | '/') power)*
  //   power   = unary ('^' unary)*
  //   unary   = '-' unary | primary
  //   primary = NUM | STR | CELL | NAME '(' args ')' | '(' expr ')'

  function expr() {
    let v = term();
    while (peek()?.type === 'OP' && (peek().value === '+' || peek().value === '-')) {
      const op = adv().value;
      const r  = term();
      if (v === '#ERR' || r === '#ERR') return '#ERR';
      if (typeof v !== 'number' || typeof r !== 'number') return '#ERR';
      v = op === '+' ? v + r : v - r;
    }
    return v;
  }

  function term() {
    let v = power();
    while (peek()?.type === 'OP' && (peek().value === '*' || peek().value === '/')) {
      const op = adv().value;
      const r  = power();
      if (v === '#ERR' || r === '#ERR') return '#ERR';
      if (typeof v !== 'number' || typeof r !== 'number') return '#ERR';
      if (op === '/' && r === 0) return '#DIV0';
      v = op === '*' ? v * r : v / r;
    }
    return v;
  }

  function power() {
    let v = unary();
    while (peek()?.type === 'OP' && peek().value === '^') {
      adv();
      const r = unary();
      if (v === '#ERR' || r === '#ERR') return '#ERR';
      v = Math.pow(v, r);
    }
    return v;
  }

  function unary() {
    if (peek()?.type === 'OP' && peek().value === '-') {
      adv();
      const v = unary();
      if (v === '#ERR') return '#ERR';
      if (typeof v !== 'number') return '#ERR';
      return -v;
    }
    return primary();
  }

  function primary() {
    const t = peek();
    if (!t) return '#ERR';

    if (t.type === 'NUM')  { adv(); return t.value; }
    if (t.type === 'STR')  { adv(); return t.value; }

    if (t.type === 'CELL') {
      adv();
      return _resolveCellValue(t.value, cells, seen);
    }

    if (t.type === 'NAME') {
      adv();
      if (!eat('LPAREN')) return '#ERR';
      return _callAggFn(t.value);
    }

    if (t.type === 'LPAREN') {
      adv();
      const v = expr();
      if (!eat('RPAREN')) return '#ERR';
      return v;
    }

    return '#ERR';
  }

  // Parse aggregate function arguments (already consumed opening '(').
  // Handles one or more comma-separated range refs or expressions.
  function _callAggFn(name) {
    const nums = [];
    while (peek() && peek().type !== 'RPAREN') {
      if (peek()?.type === 'RANGE') {
        const range = adv().value;
        for (const addr of expandRange(range)) {
          const v = _resolveNumericValue(addr, cells, seen);
          if (v === '#ERR') return '#ERR';
          if (v !== null) nums.push(v);
        }
      } else {
        const v = expr();
        if (v === '#ERR') return '#ERR';
        if (typeof v === 'number') nums.push(v);
      }
      if (peek()?.type === 'COMMA') adv();
    }
    if (!eat('RPAREN')) return '#ERR';

    if (name === 'COUNT')   return nums.length;
    if (!nums.length)       return 0;
    if (name === 'SUM')     return +nums.reduce((a, b) => a + b, 0).toPrecision(15);
    if (name === 'AVERAGE') return +(nums.reduce((a, b) => a + b, 0) / nums.length).toPrecision(15);
    if (name === 'MIN')     return Math.min(...nums);
    if (name === 'MAX')     return Math.max(...nums);
    return '#ERR';
  }

  const result = expr();
  if (pos !== tokens.length) return '#ERR'; // unconsumed tokens
  return result;
}

// ── Cell value resolution ──────────────────────────────────────────────────

/**
 * Resolve a cell's value for use in arithmetic expressions.
 * Empty cells evaluate to 0; text cells return the string (arithmetic ops will
 * then return #ERR); formula cells are evaluated recursively.
 */
function _resolveCellValue(addr, cells, seen) {
  if (seen.has(addr)) return '#CYCLE';
  const cell = cells[addr];
  if (!cell) return 0;
  if (cell.formula) {
    seen.add(addr);
    const v = evaluate(cell.formula, cells, seen);
    seen.delete(addr);
    return v;
  }
  const val = cell.value;
  if (val === '' || val == null) return 0;
  const n = Number(val);
  return isNaN(n) ? val : n;
}

/**
 * Resolve a cell's numeric value for aggregate functions.
 * Empty cells and non-numeric cells return null (skipped by aggregates).
 */
function _resolveNumericValue(addr, cells, seen) {
  if (seen.has(addr)) return '#ERR';
  const cell = cells[addr];
  if (!cell) return null;
  if (cell.formula) {
    seen.add(addr);
    const v = evaluate(cell.formula, cells, seen);
    seen.delete(addr);
    return typeof v === 'number' ? v : null;
  }
  const val = cell.value;
  if (val === '' || val == null) return null;
  const n = Number(val);
  return isNaN(n) ? null : n;
}

// ── Public: evaluate ───────────────────────────────────────────────────────

/**
 * Evaluate a formula string against a cells object.
 *
 * @param {string} formula  e.g. '=B1', '=A1+B2*3', '=SUM(A1:D4)'
 * @param {Object} cells    cells object from the sheet model
 * @param {Set<string>} [seen] recursion stack for cycle detection
 * @returns {number|string} computed value, or an error string like '#ERR'
 */
export function evaluate(formula, cells, seen = new Set()) {
  const trimmed = formula.trim();
  if (!trimmed.startsWith('=')) return '#ERR';
  return _evalExpr(trimmed.slice(1).trim(), cells, seen);
}
