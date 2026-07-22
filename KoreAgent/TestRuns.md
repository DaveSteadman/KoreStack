
# Version 0058 / 0.8+dev (Ollama v0.32.2)

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=69m 46s  pass rate=90% (142/158)  prompt tokens=2,683,021  avg tok/s=92.3

# Version 0056 / 0.8+dev (Ollama v0.32.1)

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=76m 7s  pass rate=90% (142/158)  prompt tokens=3,708,116  avg tok/s=100.0

- New TaskPlan 

# Version 0055 / 0.8 (Ollama v0.31.2)

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=42m 5s  pass rate=89% (141/158)  prompt tokens=4,035,947  avg tok/s=97.0

# Version 0052 / 0.7+dev (Ollama v0.31.2)

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=37m 31s  pass rate=87% (134/154)  prompt tokens=3,674,364  avg tok/s=97.8

- Delegate is the cleanest total failure set, but I also have clear issues in context compression, file access, web search, and a couple of assertion mismatches where the agent output is actually acceptable but the test is brittle.

# Version 0045 / 0.7+dev (Ollama v0.30.12)

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=15m 0s  pass rate=98% (79/81)  prompt tokens=7,269,320  avg tok/s=101.5
[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=15m 5s  pass rate=98% (56/57)  prompt tokens=4,241,505  avg tok/s=91.2

# Version 0044 / 0.7

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=15m 0s  pass rate=96% (55/57)  prompt tokens=4,529,046  avg tok/s=86.1

# Version 0043 / 0.6+dev

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=15m 0s  pass rate=98% (79/81)  prompt tokens=7,306,887  avg tok/s=94.9

# Version 0035 / 0.5+dev

[ALL TESTS COMPLETE]  host=http://MONTBLANC:1234  model=nemotron-cascade-2-30b-a3b-nvfp4  elapsed=66m 55s  pass rate=94% (165/176)  prompt tokens=16,120,993  avg tok/s=135.4
- LM-Studio test run
- Normal web search issues

# Version 0032 / 0.5+dev

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=38m 38s  pass rate=97% (171/176)  prompt tokens=6,070,764  avg tok/s=67.1

# Version 0030 / 0.5+dev

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=39m 53s  pass rate=96% (157/164)  prompt tokens=6,749,202  avg tok/s=70.1

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gemma4:26b  elapsed=32m 0s  pass rate=98% (157/160)  prompt tokens=5,794,928  avg tok/s=71.0

# Version [0026 / 0.5] #

[ALL TESTS COMPLETE] host=http://MONTBLANC:11434 model=gemma4:26b elapsed=34m 24s pass rate=99% (214/216)
- First KoreStack test run.
- 2 failures:
    - test_delegate_prompts.json — "Turing Test two-part answer" — Empty final output (model probably ran out of context or timed out mid-delegation)
    - test_koredata_search.json / kd_no_results — "Search returned no results" — this is a known false-failure: the test expects the model to acknowledge no results, which it did correctly; the assert logic apparently flags it as a fail

# Version [0021 / 0.4+dev] #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=nemotron-cascade-2:latest  elapsed=54m 59s  pass rate=88% (143/163)  prompt tokens=5,507,794  avg tok/s=148.4

# Version [0016 / 0.4+dev] #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=nemotron-cascade-2:latest  elapsed=29m 39s  pass rate=89% (126/142)  prompt tokens=4,699,095  avg tok/s=151.0

# Version [0013 / 0.4+dev] #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=nemotron-cascade-2:latest  elapsed=31m 55s  pass rate=96% (137/142)  prompt tokens=4,433,425  avg tok/s=159.5

# Version [0009 / 0.3+dev] #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=nemotron-cascade-2:latest  elapsed=34m 42s  pass rate=95% (138/146)  prompt tokens=4,376,752  avg tok/s=148.9
- Key takeaway: 75% of the failures (6/8) are infrastructure reliability (DuckDuckGo rate-limiting), not model quality issues. The one genuine model failure is the Gutenberg hallucination. The kiwix_relativity assert likely needs recalibrating.

# Version 0.3-rc1 #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=nemotron-3-nano:30b  elapsed=33m 7s  pass rate=89% (130/146)  prompt tokens=4,470,615  avg tok/s=191.8
- Failed a bunch of web skills - DDG performance

# Version 0.2+dev #

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gpt-oss:20b  elapsed=6m 16s  passed=124/124  

[ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=gpt-oss:20b  elapsed=8m 25s  passed=124/124  

# Version 0.1+dev #

[ALL TESTS COMPLETE]  host=http://localhost:11434  model=gpt-oss:20b  elapsed=16m 38s  passed=44/45
- Framework Desktop
- Failed: LLM emitted invalid JSON for the tool invocation

[ALL TESTS COMPLETE]  host=http://localhost:11434  model=gpt-oss:20b  elapsed=47m 11s  passed=86/87 
- Framework Desktop
- [Test: test_wikipedia_prompts.json  Passed 19/20]
- Failed: 300s Timeout on long search

 [ALL TESTS COMPLETE]  host=http://MONTBLANC:11434  model=qwen3.5:27b  elapsed=18m 23s  passed=87/87        
 - Remote Ollama host: 5090

 