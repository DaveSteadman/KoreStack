import re, httpx

LIBRARY = 'http://127.0.0.1:8622'
GRAPH   = 'http://127.0.0.1:8626'
BOOK_ID = 'sciencehistory:2'
CHUNK   = 16000

_STOP = {
    'man','men','time','times','way','ways','fact','thing','things','world','work','works',
    'first','last','great','part','parts','same','such','this','that','these','those',
    'which','what','one','two','three','four','five','many','more','most','place','places',
    'name','names','view','views','form','forms','new','old','long','large','small',
    'early','late','good','life','hand','head','body','line','point','case','kind',
}

def _extract(sent):
    s = sent.strip()
    if len(s) < 20 or s.startswith('#'):
        return []
    out = []
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(discovered|invented|proposed|developed|founded|established|proved|disproved|'
        r'wrote|described|calculated|measured|introduced|studied|applied|created|derived|'
        r'formulated|demonstrated|showed)'
        r'\s+(?:(?:the|a|an|his|her|its|that|how)\s+)?'
        r'([A-Za-z][a-z]{2,}(?:\s+(?:of\s+)?[a-z]{2,}){0,3})', s
    ):
        subj, verb, obj = m.group(1), m.group(2), m.group(3).strip()
        if obj.split()[0].lower() not in _STOP and len(obj) >= 4:
            out.append({'start': subj, 'connection': verb, 'end': obj})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+was\s+(?:a|an)\s+'
        r'(Greek|Roman|Egyptian|Arab|Persian|Babylonian|Chinese|Indian|mathematician|'
        r'philosopher|astronomer|physicist|chemist|biologist|physician|geographer|'
        r'geometer|naturalist|historian|engineer|theologian|logician|scholar|scientist)', s
    ):
        out.append({'start': m.group(1), 'connection': 'is_a', 'end': m.group(2)})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(?:lived|worked|resided|taught|studied)\s+(?:in|at)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})', s
    ):
        out.append({'start': m.group(1), 'connection': 'lived_in', 'end': m.group(2)})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(influenced|inspired|succeeded|preceded)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})', s
    ):
        out.append({'start': m.group(1), 'connection': m.group(2), 'end': m.group(3)})
    return out

client = httpx.Client(timeout=60)
all_conns, offset, cn = [], 0, 0
while True:
    r = client.get(f'{LIBRARY}/books/{BOOK_ID}/chunk', params={'offset': offset, 'length': CHUNK})
    data = r.json()
    text = data.get('chunk', '')
    found = [c for s in re.split(r'(?<=[.!?])\s+', text) for c in _extract(s)]
    all_conns.extend(found)
    cn += 1
    print(f'Chunk {cn:3d}  offset={offset:>7d}  +{len(found):3d}  total={len(all_conns)}')
    if not data.get('has_more'):
        break
    offset = data['next_offset']

seen = set(); unique = []
for c in all_conns:
    k = (c['start'].lower(), c['connection'], c['end'].lower())
    if k not in seen:
        seen.add(k); unique.append(c)

print(f'\nSample connections:')
for c in unique[:25]:
    print(f'  {c["start"]} --{c["connection"]}--> {c["end"]}')
print(f'\nTotal unique: {len(unique)}')

# Submit
submitted, errors = 0, 0
for i in range(0, len(unique), 100):
    batch = unique[i:i+100]
    gr = client.post(f'{GRAPH}/api/connections/by-name/batch', json=batch, timeout=60)
    if gr.is_success:
        result = gr.json()
        submitted += result.get('accepted', len(batch))
        errors    += len(result.get('errors', []))
    else:
        print(f'Batch error: {gr.status_code}')
        errors += len(batch)

print(f'\nSubmitted: {submitted}  Errors: {errors}')
