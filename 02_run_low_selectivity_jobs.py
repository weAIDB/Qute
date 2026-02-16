# -*- coding: utf-8 -*-
"""
02_run_low_selectivity_jobs.py

修复目标：
- 深度上限 500：通过 block_bits (e.g., 4) 限制 Grover 活动比特数，避免 pre-estimate 拒绝
- Grover 迭代统一=1
- 结果解码：测得 local_rid 后回拼 global_rid = (block_id<<b) | local_rid
- 仍保留 probe bit-order，用于稳定 bitstring 解码
"""

import argparse
import json
import os
import time
from typing import Dict, Any, List, Optional

from pyqpanda3.qcloud import QCloudService, QCloudOptions

from qcloud_utils import run_job_get_probs_ex, configure_options_minimal
from grover_kernel import build_grover_prog
from probe_bit_order import infer_bit_order_mapping


def decode_index_from_bitstring(bitstr: str, qubit_to_pos: Dict[int, int], active_nbits: int) -> int:
    """
    将测量 bitstring 解码为 local index（active_nbits 位，qi=0 为 LSB）
    """
    L = len(bitstr)
    idx = 0
    for qi in range(active_nbits):
        pos = qubit_to_pos.get(qi, None)
        if pos is None or pos < 0 or pos >= L:
            continue
        bit = 1 if bitstr[pos] == "1" else 0
        idx |= (bit << qi)
    return idx


def analyze_hit_blocked(
    probs: Dict[str, float],
    qubit_to_pos: Dict[int, int],
    block_id: Optional[int],
    block_bits: int,
    global_targets: List[int],
) -> Dict[str, Any]:
    """
    计算：
    - top1 的 local_rid 与 global_rid
    - top1 是否命中 global_targets
    - p_any_hit：落在 global_targets 的总概率
    """
    if not probs or block_id is None:
        return {
            "p_any_hit": 0.0,
            "top1_bitstring": None,
            "top1_prob": 0.0,
            "top1_local_rid": None,
            "top1_global_rid": None,
            "top1_hit": False,
        }

    b = int(block_bits)
    target_set = set(int(t) for t in global_targets)

    top_bitstr, top_p = max(probs.items(), key=lambda kv: kv[1])
    top_local = decode_index_from_bitstring(top_bitstr, qubit_to_pos, b)
    top_global = (int(block_id) << b) | int(top_local)

    p_any = 0.0
    for bitstr, p in probs.items():
        local = decode_index_from_bitstring(bitstr, qubit_to_pos, b)
        g = (int(block_id) << b) | int(local)
        if g in target_set:
            p_any += float(p)

    return {
        "p_any_hit": float(p_any),
        "top1_bitstring": top_bitstr,
        "top1_prob": float(top_p),
        "top1_local_rid": int(top_local),
        "top1_global_rid": int(top_global),
        "top1_hit": bool(top_global in target_set),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", type=str, required=True)
    ap.add_argument("--backend", type=str, default="origin_wukong")
    ap.add_argument("--plan", type=str, default="/home/project/added/results/low_selectivity_plan.json")
    ap.add_argument("--out", type=str, default="/home/project/added/results/low_selectivity_experiment_merged.json")

    ap.add_argument("--probe-shots", type=int, default=2000)
    ap.add_argument("--poll-interval-sec", type=float, default=2.0)
    ap.add_argument("--max-poll-sec", type=float, default=900.0)

    # 强制迭代=1
    ap.add_argument("--grover-iters", type=int, default=1)

    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    with open(args.plan, "r", encoding="utf-8") as f:
        plan = json.load(f)

    records = plan.get("records", [])
    nbits_max = int(plan.get("nbits_max", 10))

    # ---- probe bit order ----
    probe = infer_bit_order_mapping(
        api_key=args.api_key,
        backend_name=args.backend,
        nbits_max=nbits_max,
        shots=int(args.probe_shots),
        poll_interval_sec=float(args.poll_interval_sec),
    )
    qubit_to_pos = dict(probe.qubit_to_pos)

    fallback_used = False
    for q in range(nbits_max):
        if q not in qubit_to_pos:
            fallback_used = True
            qubit_to_pos[q] = q  # identity fallback

    # ---- QCloud init ----
    service = QCloudService(api_key=args.api_key)
    backend = service.backend(args.backend)

    options = QCloudOptions()
    applied_options = configure_options_minimal(options)  # best-effort，字段不一定存在

    merged: Dict[str, Any] = {
        "meta": {
            "backend": args.backend,
            "plan_path": args.plan,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "nbits_max": nbits_max,
            "probe": {
                "shots": probe.shots,
                "top_bitstring": probe.top_bitstring,
                "qubit_to_pos": probe.qubit_to_pos,
                "fallback_used": fallback_used,
            },
            "options_applied": applied_options,
            "constraints": {
                "max_depth": 500,
                "grover_iters": int(args.grover_iters),
                "block_bits": int(plan.get("block_bits", 4)),
            },
        },
        "records": []
    }

    for r in records:
        if r.get("status") == "MISSING_DATASET":
            merged["records"].append(r)
            continue

        k = int(r["k"])
        shots = int(r.get("shots", plan.get("shots", 2000)))

        # 核心：使用 block_bits，而不是 k 作为 Grover active_nbits
        block_bits = int(r.get("block_bits", plan.get("block_bits", 4)))
        block_id = r.get("block_id", None)
        local_targets = list(r.get("local_targets", []))
        global_targets = list(r.get("targets", []))

        # 若没有命中目标（M=0），仍然可运行但意义不大；这里直接记录并跳过
        if block_id is None or not local_targets:
            merged["records"].append({
                **r,
                "status": "SKIPPED_NO_TARGET",
                "error_message": "No target found in dataset (M=0) so oracle undefined for equality hit test.",
            })
            continue

        prog = build_grover_prog(
            active_nbits=block_bits,     # depth-safe
            nbits_max=nbits_max,         # fixed measured width
            targets=local_targets,       # local oracle
            grover_iters=int(args.grover_iters),  # fixed = 1
        )

        t0 = time.time()
        probs, err = run_job_get_probs_ex(
            backend=backend,
            progs=[prog],
            shots=shots,
            options=options,
            poll_interval_sec=float(args.poll_interval_sec),
            max_poll_sec=float(args.max_poll_sec),
        )
        t1 = time.time()

        hit = analyze_hit_blocked(
            probs=probs,
            qubit_to_pos=qubit_to_pos,
            block_id=int(block_id),
            block_bits=block_bits,
            global_targets=global_targets,
        )

        merged["records"].append({
            "k": k,
            "dataset_path": r.get("dataset_path"),
            "N_file": r.get("N_file"),
            "N_formula": r.get("N_formula"),

            "nbits_max": nbits_max,
            "block_bits": block_bits,
            "block_id": int(block_id),
            "local_targets": local_targets,
            "rep_target": r.get("rep_target"),
            "M": int(r.get("M", 0)),

            "grover_iters": int(args.grover_iters),
            "shots": shots,

            "timing": {
                "wall_time_sec": t1 - t0,
                "submit_ts": t0,
                "finish_ts": t1,
            },
            "result": {
                "hit": hit,
                "probs_topk": sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:16] if probs else [],
            },
            "status": "OK" if probs else "FAILED",
            "error_message": err,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote merged results: {args.out}")
    print("[INFO] probe fallback used:", fallback_used)
    print("[INFO] options applied:", applied_options)


if __name__ == "__main__":
    main()
