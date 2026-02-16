# -*- coding: utf-8 -*-
"""
grover_kernel.py (U3 + CZ only)

原生门集：U3, CZ
本文件显式分解：
- H, X, Z, Rz
- CNOT = H(t) - CZ - H(t)
- Toffoli(CCX) using {CNOT, Rz} (all decomposed to U3+CZ)
- Multi-controlled Z using ancilla ladder of Toffoli + CZ(target)

注意：
- 为了支持 k=10 的多控门，本实现会使用额外 ancilla qubits：
  ancilla 从 index = nbits_max 开始占用 (最多 active_nbits-2 个)
- 测量仅对前 nbits_max 个 qubit 做 measure(i,i)，bit-order probe 也以此为准
"""

import math
from typing import List, Sequence, Tuple, Optional

from pyqpanda3.core import QProg, U3, CZ, measure


# -----------------------------
# U3 helpers (compatible call)
# -----------------------------
def _u3(q: int, theta: float, phi: float, lam: float):
    """
    兼容不同 pyqpanda3 的 U3 调用签名：
    - U3(q, (theta,phi,lam)) 或 U3(q, theta,phi,lam)
    """
    try:
        return U3(q, (float(theta), float(phi), float(lam)))
    except Exception:
        return U3(q, float(theta), float(phi), float(lam))


def _rz(q: int, lam: float):
    # Rz(lam) == U3(0,0,lam)
    return _u3(q, 0.0, 0.0, float(lam))


def _x(q: int):
    # X == U3(pi,0,pi)
    return _u3(q, math.pi, 0.0, math.pi)


def _z(q: int):
    # Z == Rz(pi)
    return _rz(q, math.pi)


def _h(q: int):
    # H == U3(pi/2,0,pi) (global phase ignored)
    return _u3(q, math.pi / 2.0, 0.0, math.pi)


def _t(q: int):
    # T == Rz(pi/4)
    return _rz(q, math.pi / 4.0)


def _tdg(q: int):
    # T† == Rz(-pi/4)
    return _rz(q, -math.pi / 4.0)


# -----------------------------
# 2q primitives (CZ native)
# -----------------------------
def _cnot(prog: QProg, c: int, t: int):
    """
    CNOT(c,t) = H(t) - CZ(c,t) - H(t)
    """
    prog << _h(t)
    prog << CZ(c, t)
    prog << _h(t)


# -----------------------------
# Toffoli (CCX) decomposition using CNOT + Rz
# -----------------------------
def _ccx(prog: QProg, a: int, b: int, t: int):
    """
    Toffoli(CCX) decomposition (standard 6-CNOT version) using only:
    - CNOT (=> H+CZ)
    - T/Tdg (=> Rz)
    - H (=> U3)

    Circuit (one common form):
      H(t)
      CNOT(b,t); Tdg(t)
      CNOT(a,t); T(t)
      CNOT(b,t); Tdg(t)
      CNOT(a,t); T(b); T(t)
      H(t)
      CNOT(a,b); T(a); Tdg(b); CNOT(a,b)
    """
    prog << _h(t)

    _cnot(prog, b, t)
    prog << _tdg(t)

    _cnot(prog, a, t)
    prog << _t(t)

    _cnot(prog, b, t)
    prog << _tdg(t)

    _cnot(prog, a, t)
    prog << _t(b)
    prog << _t(t)

    prog << _h(t)

    _cnot(prog, a, b)
    prog << _t(a)
    prog << _tdg(b)
    _cnot(prog, a, b)


# -----------------------------
# Multi-controlled Z: C^{m}Z using ancilla ladder
# -----------------------------
def _cmz(prog: QProg, controls: Sequence[int], target: int, ancillas: Sequence[int]):
    """
    Apply multi-controlled Z on 'target' controlled by 'controls'.

    - If len(controls)=0: Z(target)
    - If len(controls)=1: CZ(control, target)
    - If len(controls)>=2:
        compute AND(controls) into ancillas ladder using CCX
        apply CZ(last_anc, target)
        uncompute

    ancillas required: len(controls)-1
    """
    m = len(controls)
    if m == 0:
        prog << _z(target)
        return
    if m == 1:
        prog << CZ(controls[0], target)
        return

    need = m - 1
    if len(ancillas) < need:
        raise ValueError(f"not enough ancillas for cmz: need {need}, got {len(ancillas)}")

    # compute
    # anc0 = controls[0] AND controls[1]
    _ccx(prog, controls[0], controls[1], ancillas[0])
    # anci = anci-1 AND controls[i+1]
    for i in range(2, m):
        _ccx(prog, ancillas[i - 2], controls[i], ancillas[i - 1])

    # apply CZ on target with last ancilla as control
    prog << CZ(ancillas[need - 1], target)

    # uncompute
    for i in range(m - 1, 1, -1):
        _ccx(prog, ancillas[i - 2], controls[i], ancillas[i - 1])
    _ccx(prog, controls[0], controls[1], ancillas[0])


# -----------------------------
# Grover building blocks
# -----------------------------
def _bits_of_int(x: int, n: int) -> List[int]:
    return [(x >> i) & 1 for i in range(n)]  # i=0 is LSB for qubit i


def _oracle_phase_flip(
    prog: QProg,
    active_qubits: List[int],
    ancillas: List[int],
    targets: List[int],
):
    """
    对 targets 中每个 basis state 做相位翻转
    实现：对 bit=0 的位先 X，再做 C^{n-1}Z，再还原 X
    """
    n = len(active_qubits)
    if n == 0:
        return
    if n == 1:
        # phase flip on |1> after optional X for target=0
        for t in targets:
            bit0 = t & 1
            if bit0 == 0:
                prog << _x(active_qubits[0])
                prog << _z(active_qubits[0])
                prog << _x(active_qubits[0])
            else:
                prog << _z(active_qubits[0])
        return

    controls = active_qubits[:-1]
    target_q = active_qubits[-1]
    need_anc = max(0, len(controls) - 1)
    anc_use = ancillas[:need_anc]

    for t in targets:
        bits = _bits_of_int(int(t), n)
        for qi, bi in zip(active_qubits, bits):
            if bi == 0:
                prog << _x(qi)

        _cmz(prog, controls=controls, target=target_q, ancillas=anc_use)

        for qi, bi in zip(active_qubits, bits):
            if bi == 0:
                prog << _x(qi)


def _diffusion(prog: QProg, active_qubits: List[int], ancillas: List[int]):
    """
    diffusion: H^n X^n C^{n-1}Z X^n H^n
    """
    n = len(active_qubits)
    if n == 0:
        return
    if n == 1:
        prog << _h(active_qubits[0])
        prog << _x(active_qubits[0])
        prog << _z(active_qubits[0])
        prog << _x(active_qubits[0])
        prog << _h(active_qubits[0])
        return

    for q in active_qubits:
        prog << _h(q)
    for q in active_qubits:
        prog << _x(q)

    controls = active_qubits[:-1]
    target_q = active_qubits[-1]
    need_anc = max(0, len(controls) - 1)
    anc_use = ancillas[:need_anc]
    _cmz(prog, controls=controls, target=target_q, ancillas=anc_use)

    for q in active_qubits:
        prog << _x(q)
    for q in active_qubits:
        prog << _h(q)


def recommended_grover_iters(N: int, M: int) -> int:
    M = max(1, int(M))
    return int(math.floor((math.pi / 4.0) * math.sqrt(float(N) / float(M))))


def build_grover_prog(
    active_nbits: int,
    nbits_max: int,
    targets: List[int],
    grover_iters: int,
    ancilla_start: Optional[int] = None,
) -> QProg:
    """
    active_nbits: k（逻辑比特数）
    nbits_max: 固定测量的 qubits 数（建议 10，与 probe 对齐）
    ancilla_start: ancilla 起始 qubit index；默认=nbits_max
    """
    active_nbits = max(1, int(active_nbits))
    nbits_max = int(nbits_max)
    if active_nbits > nbits_max:
        raise ValueError("active_nbits must be <= nbits_max (measured bits)")

    if ancilla_start is None:
        ancilla_start = nbits_max

    # active qubits are [0..active_nbits-1]
    active_qubits = list(range(active_nbits))

    # ancillas needed for C^{n-1}Z: (controls_count-1) = (active_nbits-1-1)=active_nbits-2 when active_nbits>=2
    anc_needed = max(0, active_nbits - 2)
    ancillas = list(range(ancilla_start, ancilla_start + anc_needed))

    prog = QProg()

    # prepare uniform superposition
    for q in active_qubits:
        prog << _h(q)

    for _ in range(int(grover_iters)):
        _oracle_phase_flip(prog, active_qubits, ancillas, targets)
        _diffusion(prog, active_qubits, ancillas)

    # measure only first nbits_max for stable bit-order mapping
    for i in range(nbits_max):
        prog << measure(i, i)

    return prog
