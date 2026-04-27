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

 