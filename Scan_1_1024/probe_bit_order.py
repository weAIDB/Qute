# -*- coding: utf-8 -*-
"""
probe_bit_order.py (U3 + CZ only)

对每个 qubit 单独施加 X（用 U3 分解），测量前 nbits_max 个 qubit。
用边际概率推断 qubit->bitstring position。
"""

import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import math
from pyqpanda3.core import QProg, U3, measure
from pyqpanda3.qcloud import QCloudService, QCloudOptions

from qcloud_utils import run_job_get_probs_ex, configure_options_minimal


def _u3(q: int, theta: float, phi: float, lam: float):
    try:
        return U3(q, (float(theta), float(phi), float(lam)))
    except Exception:
        return U3(q, float(theta), float(phi), float(lam))


def _x_u3(q: int):
    return _u3(q, math.pi, 0.0, math.pi)


@dataclass
class ProbeResult:
    nbits_max: int
    shots: int
    qubit_to_pos: Dict[int, int]
    pos_marginals: Dict[int, List[float]]
    top_bitstring: Dict[int, Tuple[str, float]]
    options_applied: Dict[str, object]


def _make_probe_prog(nbits_max: int, x_on: int) -> QProg:
    prog = QProg()
    prog << _x_u3(x_on)
    for i in range(nbits_max):
        prog << measure(i, i)
    return prog


def _marginals_from_probs(probs: Dict[str, float]) -> List[float]:
    if not probs:
        return []
    L = len(next(iter(probs.keys())))
    marg = [0.0] * L
    for bitstr, p in probs.items():
        if len(bitstr) != L:
            continue
        for pos, ch in enumerate(bitstr):
            if ch == "1":
                marg[pos] += float(p)
    return marg


def infer_bit_order_mapping(
    api_key: str,
    backend_name: str = "origin_wukong",
    nbits_max: int = 10,
    shots: int = 2000,
    qubits_to_test: Optional[List[int]] = None,
    poll_interval_sec: float = 2.0,
) -> ProbeResult:
    if qubits_to_test is None:
        qubits_to_test = list(range(nbits_max))

    service = QCloudService(api_key=api_key)
    backend = service.backend(backend_name)

    options = QCloudOptions()
    applied = configure_options_minimal(options)

    qubit_to_pos: Dict[int, int] = {}
    pos_marginals: Dict[int, List[float]] = {}
    top_bitstring: Dict[int, Tuple[str, float]] = {}

    for q in qubits_to_test:
        prog = _make_probe_prog(nbits_max, q)
        probs, err = run_job_get_probs_ex(
            backend=backend,
            progs=[prog],
            shots=int(shots),
            options=options,
            poll_interval_sec=float(poll_interval_sec),
        )
        if not probs:
            continue

        top = max(probs.items(), key=lambda kv: kv[1])
        top_bitstring[q] = (top[0], float(top[1]))

        marg = _marginals_from_probs(probs)
        pos_marginals[q] = marg
        if marg:
            best_pos = max(range(len(marg)), key=lambda i: marg[i])
            qubit_to_pos[q] = int(best_pos)

    return ProbeResult(
        nbits_max=nbits_max,
        shots=shots,
        qubit_to_pos=qubit_to_pos,
        pos_marginals=pos_marginals,
        top_bitstring=top_bitstring,
        options_applied=applied,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", type=str, required=True)
    ap.add_argument("--backend", type=str, default="origin_wukong")
    ap.add_argument("--nbits-max", type=int, default=10)
    ap.add_argument("--shots", type=int, default=2000)
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    pr = infer_bit_order_mapping(
        api_key=args.api_key,
        backend_name=args.backend,
        nbits_max=args.nbits_max,
        shots=args.shots,
    )

    print("=== Probe top bitstring (reference) ===")
    for q in sorted(pr.top_bitstring.keys()):
        print(f"X on qubit {q}: top={pr.top_bitstring[q]}")

    print("\n=== Inferred qubit->bitstring position (0=leftmost) ===")
    for q in range(args.nbits_max):
        print(f"qubit {q} -> pos {pr.qubit_to_pos.get(q)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "nbits_max": pr.nbits_max,
                "shots": pr.shots,
                "qubit_to_pos": pr.qubit_to_pos,
                "top_bitstring": pr.top_bitstring,
                "pos_marginals": pr.pos_marginals,
                "options_applied": pr.options_applied,
            }, f, ensure_ascii=False, indent=2)
        print(f"[OK] wrote {args.out}")


if __name__ == "__main__":
    main()
