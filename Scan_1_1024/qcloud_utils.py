# -*- coding: utf-8 -*-
"""
qcloud_utils.py

修复点：
- job.status() 可能抛 RuntimeError（例如编译/补偿/映射失败），必须捕获并返回错误信息
- FINISHED / FAILED / CANCELED / 超时均能安全退出
- 提供 configure_options_minimal()：尽最大兼容性关闭 compensate/校正/噪声修正等易触发错误的链路
"""

import time
from typing import Dict, List, Tuple, Any, Optional

from pyqpanda3.qcloud import JobStatus


def _set_if_exists(obj: Any, name: str, value: Any) -> bool:
    """
    兼容性设置：字段存在则赋值；不存在则忽略。
    """
    if hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            return False
    return False


def configure_options_minimal(options: Any) -> Dict[str, Any]:
    """
    为了规避 'invalid compensate qubit pair' 一类错误：
    - 尽可能关闭补偿/校正/噪声修正/误差缓解等
    - 同时尽量关闭会改变物理映射的高级优化（有些平台将 compensate 与特定映射绑定）
    注意：不同版本字段名不同，采用 best-effort。
    返回：实际成功设置的字段字典，便于写入结果 JSON 追溯。
    """
    applied: Dict[str, Any] = {}

    # 1) compensate / compensation / crosstalk compensation
    for name in [
        "compensate",
        "enable_compensate",
        "enable_compensation",
        "compensation",
        "enable_crosstalk_compensation",
        "crosstalk_compensation",
        "enable_global_compensate",
        "global_compensate",
        "enable_pulse_compensate",
        "pulse_compensate",
    ]:
        if _set_if_exists(options, name, False):
            applied[name] = False

    # 2) error mitigation / correction (读出误差缓解、噪声修正等)
    for name in [
        "error_mitigation",
        "enable_error_mitigation",
        "readout_mitigation",
        "enable_readout_mitigation",
        "readout_error_mitigation",
        "enable_readout_error_mitigation",
        "global_correction",
        "enable_global_correction",
        "noise_correction",
        "enable_noise_correction",
    ]:
        if _set_if_exists(options, name, False):
            applied[name] = False

    # 3) mapping / routing / optimization（可选：为稳定性关闭）
    for name in [
        "mapping",
        "enable_mapping",
        "routing",
        "enable_routing",
        "optimizer",
        "enable_optimizer",
        "optimization",
        "enable_optimization",
        "circuit_optimization",
        "enable_circuit_optimization",
    ]:
        if _set_if_exists(options, name, False):
            applied[name] = False

    # 4) fidelity / calibration（可选：为稳定性关闭）
    for name in [
        "fidelity",
        "enable_fidelity",
        "calibration",
        "enable_calibration",
        "use_calibration",
    ]:
        if _set_if_exists(options, name, False):
            applied[name] = False

    return applied


def run_job_get_probs_ex(
    backend,
    progs: List,
    shots: int,
    options,
    poll_interval_sec: float = 2.0,
    max_poll_sec: float = 600.0,
) -> Tuple[Dict[str, float], str]:
    """
    提交 progs（通常长度=1），返回：
      (probs_dict, error_message)
    成功 error_message=""，失败返回 {} + 具体原因。
    """

    t0 = time.time()
    try:
        job = backend.run(progs, int(shots), options)
    except Exception as e:
        return {}, f"backend.run failed: {e}"

    # poll
    while True:
        try:
            st = job.status()
        except RuntimeError as e:
            # 典型：编译/补偿/映射失败会在查询状态时抛出
            return {}, f"job.status runtime error: {e}"
        except Exception as e:
            return {}, f"job.status failed: {e}"

        if st == JobStatus.FINISHED:
            break
        if st == JobStatus.FAILED:
            return {}, f"job ended with status={st}"
        if time.time() - t0 > max_poll_sec:
            return {}, "poll timeout"
        time.sleep(poll_interval_sec)

    # finished: extract result safely
    try:
        res = job.result()
    except Exception as e:
        return {}, f"job.result failed: {e}"

    try:
        probs_list = res.get_probs_list()
    except Exception as e:
        return {}, f"get_probs_list failed: {e}"

    if not probs_list or not isinstance(probs_list, list):
        return {}, "empty probs_list"

    first = probs_list[0]
    if isinstance(first, dict):
        try:
            return {k: float(v) for k, v in first.items()}, ""
        except Exception as e:
            return {}, f"convert probs failed: {e}"

    return {}, "unexpected probs_list element type"


def run_job_get_probs(*args, **kwargs) -> Dict[str, float]:
    """
    兼容旧接口：只返回 probs dict，不返回 error。
    """
    probs, _err = run_job_get_probs_ex(*args, **kwargs)
    return probs
