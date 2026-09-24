# Upgrade check: 2026-09-24-upgrade-check-v070

**Verdict: OK** (exit 0)

- Command: `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe C:\GitHub\OrchestrANT-lab\benchmarks\upgrade_check.py --lanes geniex-npu,geniex-cpu --out benchmarks/benchmark_results/2026-09-24-upgrade-check-v070 --steps contract,speed,speed-answer,tools --overflow-tokens geniex-npu=6000`
- Started 2026-09-24T17:22:30+00:00, finished 2026-09-24T18:38:56+00:00
- Host: summy-server (Windows 11, ARM64), Python 3.14.6
- Repository: 3260e1e86642c1fa53e1b32ed10259588c6faf86 (dirty tree: tool_sha256 may match no commit)
- Previous run: none named

## Lanes

| Lane | Endpoint | Model | Serving runtime | Previous run's runtime |
|---|---|---|---|---|
| geniex-npu | http://127.0.0.1:18181 | qualcomm/Qwen3-4B-Instruct-2507:W4A16 | geniex v0.7.0 (QAIRT 2.45, llama.cpp 4ff829e) (verified) | - |
| geniex-cpu | http://127.0.0.1:18184 | unsloth/Qwen3-4B-GGUF:Q4_0 | geniex v0.7.0 (QAIRT 2.45, llama.cpp 4ff829e) (verified) | - |

- geniex-npu: lane process pid 19264: C:\Users\jonas\AppData\Local\GenieX CLI\geniex.exe --version; serve flags `serve --compute npu --host 127.0.0.1:18181 --nctx 16384 --keepalive 86400 --log none --skip-update`
- geniex-cpu: lane process pid 12168: C:\Users\jonas\AppData\Local\GenieX CLI\geniex.exe --version; serve flags `serve --compute cpu --host 127.0.0.1:18184 --nctx 16384 --keepalive 86400 --log none --skip-update`

## Steps

| # | Lane | Step | Files | Exit | Status | Seconds | Command |
|---|---|---|---|---|---|---|---|
| 1 | geniex-npu | contract | `geniex-npu-contract.json`, `geniex-npu-contract.log` | 0 | ok | 245.2 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark contract --backend geniex-npu --overflow-tokens 6000 --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-npu-contract.json` |
| 2 | geniex-npu | speed | `geniex-npu-speed.json`, `geniex-npu-speed.log` | 0 | ok | 120.4 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark speed --backend geniex-npu --stream --correctness --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-npu-speed.json` |
| 3 | geniex-npu | speed-answer | `geniex-npu-speed-answer.json`, `geniex-npu-speed-answer.log` | 0 | ok | 360.8 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark speed --backend geniex-npu --stream --max-tokens 2048 --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-npu-speed-answer.json` |
| 4 | geniex-npu | tools | `geniex-npu-tools.json`, `geniex-npu-tools.log` | 0 | ok | 396.5 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe C:\GitHub\OrchestrANT-lab\benchmarks\bench_tools.py --backend geniex-npu --label geniex-npu --repeats 3 --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-npu-tools.json` |
| 5 | geniex-cpu | contract | `geniex-cpu-contract.json`, `geniex-cpu-contract.log` | 0 | ok | 297.3 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark contract --backend geniex-cpu --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-cpu-contract.json` |
| 6 | geniex-cpu | speed | `geniex-cpu-speed.json`, `geniex-cpu-speed.log` | 0 | ok | 328.5 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark speed --backend geniex-cpu --stream --correctness --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-cpu-speed.json` |
| 7 | geniex-cpu | speed-answer | `geniex-cpu-speed-answer.json`, `geniex-cpu-speed-answer.log` | 0 | ok | 605.5 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe -m orchestrant.benchmark speed --backend geniex-cpu --stream --max-tokens 2048 --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-cpu-speed-answer.json` |
| 8 | geniex-cpu | tools | `geniex-cpu-tools.json`, `geniex-cpu-tools.log` | 0 | ok | 2231.5 | `C:\GitHub\OrchestrANT\.venv\Scripts\python.exe C:\GitHub\OrchestrANT-lab\benchmarks\bench_tools.py --backend geniex-cpu --label geniex-cpu --repeats 3 --output C:\GitHub\OrchestrANT-lab\benchmarks\benchmark_results\2026-09-24-upgrade-check-v070\geniex-cpu-tools.json` |
| 9 | all | compare | - | - | skipped: no --previous run named to compare with | - | - |

Exit codes: bench_compare 0 = no regression, 1 = REGRESSION (only when it says so; an unreadable report exits 1 too and is a failure here), 3 = NOTHING COMPARED. `contract --diff` 1 = an answer moved: a finding, not a failure. Each step's whole output is in its `.log`, and `steps.jsonl` has every step's argv, exit code, start, end and duration.
