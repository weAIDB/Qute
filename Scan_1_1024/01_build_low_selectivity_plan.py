# -*- coding: utf-8 -*-
"""
01_build_low_selectivity_plan.py

数据读取 + 生成实验计划（不含 API、不提交真机）

新增：
- depth limit=500 的约束下，引入 block_bits（默认4）进行分块
- 对每个数据集选择一个“代表性目标”（默认第一个命中RID），映射到 (block_id, local_targets)
- 仍保留完整 targets 以便后续统计/检查
"""

import argparse
import csv
import json
import os
from typing import List, Dict, Any


def read_values_csv(path: str) -> List[int]:
    vals: List[int] = []
    with open(path, "r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if "value" not in (rd.fieldnames or []):
            raise ValueError(f"CSV missing column 'value': {path}")
        for row in rd:
            try:
                vals.append(int(row["value"]))
            except Exception:
                continue
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-dir", type=str, default="/home/project/added/dataset")
    ap.add_argument("--out", type=str, default="/home/project/added/results/low_selectivity_plan.json")
    ap.add_argument("--k-min", type=int, default=0)
    ap.add_argument("--k-max", type=int, default=10)
    ap.add_argument("--target-value", type=int, default=100)

    # 固定测量宽度（建议10，与 probe 对齐）
    ap.add_argument("--nbits-max", type=int, default=10)

    # shots
    ap.add_argument("--shots", type=int, default=2000)

    # 分块：保证深度<500（经验上 b=4 最稳）
    ap.add_argument("--block-bits", type=int, default=4, help="Grover active bits per block (depth-safe), e.g. 4")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    b = int(args.block_bits)
    if b < 1:
        raise ValueError("--block-bits must be >=1")
    block_size = 1 << b
    mask = block_size - 1

    records: List[Dict[str, Any]] = []
    for k in range(args.k_min, args.k_max + 1):
        path = os.path.join(args.dataset_dir, f"low_selectivity_data_{k}.csv")
        if not os.path.exists(path):
            records.append({
                "k": k,
                "status": "MISSING_DATASET",
                "dataset_path": path,
            })
            continue

        values = read_values_csv(path)
        N_file = len(values)
        targets = [i for i, v in enumerate(values) if v == int(args.target_value)]
        M = len(targets)

        # 选择一个“代表性目标”用于 block 内 Grover（低选择率通常 M≈1）
        rep_target = int(targets[0]) if M > 0 else None
        block_id = (rep_target >> b) if rep_target is not None else None
        local_targets = [(rep_target & mask)] if rep_target is not None else []

        records.append({
            "k": k,
            "dataset_path": path,
            "N_file": N_file,
            "N_formula": 1 << k,  # 规模口径仍按 2^k

            "target_value": int(args.target_value),

            # 全量命中集合（用于审计/统计）
            "targets": targets,
            "M": M,

            # block 化 Grover 参数
            "block_bits": b,
            "block_size": block_size,
            "block_id": block_id,
            "local_targets": local_targets,
            "rep_target": rep_target,

            # 电路测量宽度
            "nbits_max": int(args.nbits_max),

            # shots
            "shots": int(args.shots),
        })

    plan = {
        "dataset_dir": args.dataset_dir,
        "k_min": args.k_min,
        "k_max": args.k_max,
        "target_value": int(args.target_value),
        "nbits_max": int(args.nbits_max),
        "shots": int(args.shots),

        # block settings
        "block_bits": b,
        "block_size": block_size,

        "records": records,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"[OK] wrote plan: {args.out}")
    print(f"[INFO] block_bits={b} (block_size={block_size}), nbits_max={args.nbits_max}")


if __name__ == "__main__":
    main()
