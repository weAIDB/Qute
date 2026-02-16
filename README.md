# Qute

Qute is a prototype quantum database that treats quantum computation as a first-class execution option rather than a simulation-only extension of classical systems. Instead of running quantum algorithms on classical simulators or retrofitting existing databases for simulated workloads, Qute focuses on real-device execution and system-level integration.

At a high level, Qute:

1. Compiles an extended SQL form into gate-efficient quantum circuits.
2. Uses a hybrid optimizer to choose between quantum and classical execution plans at runtime.
3. Introduces selective quantum indexing for low-selectivity query patterns.
4. Applies fidelity-preserving storage design under current qubit constraints.

The project is deployed on the real `origin_wukong` quantum processor. This repository contains the open-source prototype and an experiment workflow for low-selectivity equality search using a Grover-style kernel compiled to `U3 + CZ`.

## Purpose

The current prototype in this repository provides a reproducible workflow for low-selectivity equality search experiments:

1. Build a plan from CSV datasets.
2. Probe device bit ordering.
3. Submit Grover circuits to the real backend.
4. Aggregate latency and hit metrics into one merged JSON report.

The implementation uses a block-search strategy (`block_bits`, default `4`) and fixed Grover iteration count (`1`) to stay within hardware depth constraints.

## Repository Structure

- `01_build_low_selectivity_plan.py`: Build the experiment plan JSON from datasets.
- `02_run_low_selectivity_jobs.py`: Run probe + cloud jobs using a plan JSON and API key.
- `grover_kernel.py`: Grover circuit construction using only `U3` and `CZ` primitives.
- `probe_bit_order.py`: Infer `qubit -> bitstring position` mapping on real hardware.
- `qcloud_utils.py`: Robust job submission, polling, option configuration, and error capture.
- `dataset/`: Input datasets (`low_selectivity_data_{k}.csv`).
- `README.md`: Canonical project documentation.

## Runtime Environment

This repository is designed for Origin Quantum CloudIDE (Linux-like workspace with `pyqpanda3` available).

Recommended assumptions:

- Python 3.8+
- `pyqpanda3` installed and importable
- Access to OriginQ Cloud API token
- Network access from CloudIDE to QCloud backend APIs

Quick checks:

```bash
python -V
python -c "import pyqpanda3; print('pyqpanda3 OK')"
python -c "from pyqpanda3.qcloud import QCloudService; print('QCloud OK')"
```

## Data Requirements

Each dataset file must contain a `value` column:

| value |
|---|
| 832 |
| 613 |
| 454 |

Expected naming pattern:

- `low_selectivity_data_0.csv`
- `low_selectivity_data_1.csv`
- ...
- `low_selectivity_data_10.csv`

The row index is treated as RID (record ID).

## End-to-End Usage

### 1) Build the plan (offline)

```bash
python 01_build_low_selectivity_plan.py \
  --dataset-dir /home/project/added/dataset \
  --out /home/project/added/results/low_selectivity_plan.json \
  --k-min 0 --k-max 10 \
  --target-value 100 \
  --nbits-max 10 \
  --shots 2000 \
  --block-bits 4
```

Key arguments:

- `--block-bits`: Active Grover width per block (depth control).
- `--nbits-max`: Fixed measured qubit width for consistent decoding.
- `--target-value`: Equality value for the predicate.

### 2) Run probe + real-device jobs (online)

```bash
export ORIGINQC_API_KEY="YOUR_API_TOKEN"
python 02_run_low_selectivity_jobs.py \
  --api-key "$ORIGINQC_API_KEY" \
  --backend origin_wukong \
  --plan /home/project/added/results/low_selectivity_plan.json \
  --out /home/project/added/results/low_selectivity_experiment_merged.json \
  --grover-iters 1
```

Equivalent direct-token style:

```bash
python 02_run_low_selectivity_jobs.py --api-key "YOUR_API_TOKEN" ...
```

## Output Artifacts

### Plan JSON (`low_selectivity_plan.json`)

Contains:

- Global settings (`k` range, `target_value`, `nbits_max`, `shots`, `block_bits`)
- Per-scale records (`k`, dataset path, target RIDs, representative block fields)

### Merged Result JSON (`low_selectivity_experiment_merged.json`)

Contains:

- `meta`: backend, generation time, probe mapping, applied options, constraints
- `records[]`: per-`k` execution results with timing, probabilities, and hit statistics

Core per-record fields include:

- `status`: `OK`, `FAILED`, `MISSING_DATASET`, or `SKIPPED_NO_TARGET`
- `timing.wall_time_sec`: end-to-end wall time for that submission
- `result.hit.p_any_hit`: probability mass on true-hit RIDs
- `result.hit.top1_hit`: whether top-1 decoded RID is a true hit

## Bit Order and Decoding

The pipeline probes device bit ordering before running experiments.
Decoded local RID is mapped back to global RID using:

`global_rid = (block_id << block_bits) | local_rid`

If probe mapping is incomplete, an identity fallback is used for missing entries and marked in metadata.

## Failure Modes and Mitigations

Common backend issues are captured as `error_message` instead of terminating the full run.

Typical cases:

- Mapping failure (for example, circuit routing rejection)
- Pre-estimate depth or layer overflow
- Poll timeout or empty probability payload

Mitigations:

- Reduce `--block-bits` (for example, `4 -> 3`)
- Keep `--grover-iters 1`
- Ensure circuit construction remains `U3 + CZ` only

## Security and Reproducibility

Security:

- Do not hardcode API tokens.
- Prefer runtime argument or environment variable injection.

Reproducibility checklist:

1. Use the same dataset files and naming pattern.
2. Keep plan parameters unchanged (`k` range, `block_bits`, `shots`, `nbits_max`, `target_value`).
3. Use the same backend (`origin_wukong`).
4. Archive the merged JSON report as the analysis source of truth.

## Notes

- Wall time includes cloud submission, compilation or mapping, queueing, execution, and result retrieval.
- Circuit depth constraints can dominate design decisions more than logical problem size.
