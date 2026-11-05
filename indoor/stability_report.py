#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
稳定性测试数据后处理脚本（只读，可在测试软件运行期间安全执行）
================================================================

用途
----
扫描 StabLab / SolarStabilityTester 产生的原始数据目录（MEAS + STAT），
重算每个电池片的 IV 参数与效率，做质量检查（QC）、归一化衰减、T80/T90，
并输出 Excel 友好的 CSV + 图，供 Origin/Excel 后续使用。

安全保证（不会打断正在运行的软件）
----------------------------------
* 只以 "rb" 只读方式打开原始文件，读完立刻关闭，不加排他锁；
* 跳过 2 秒内刚被改写的文件（可能正在写入），下次再读；
* 解析容错：文件被截断/只写了一半时，只用完整行，点不够就跳过；
* 输出写到 <数据目录>/REPORT/（与软件的 MEAS/、STAT/ 平级）——
  测试软件只往 MEAS/ 和 STAT/ 写文件，从不扫描其他目录，所以同目录无影响；
  可以用 --out 改到别处（但会被禁止写进 MEAS/、STAT/）；
* 写文件用 .tmp + os.replace 原子替换，别人/别的程序不会读到半个文件；

快速开始
--------
    # 单次分析
    python tools/stability_report.py --data "D:/zlc2026年11月4日"

    # 边测边看（每 120 s 自动刷新，Ctrl+C 退出；不加 --watch 就只跑一次）
    python tools/stability_report.py --data "D:/zlc2026年11月4日" --watch 120
    # 或者直接双击 tools/stability_report_watch.bat

    # 光强口径：默认按 0.55 sun = 55 mW/cm²（原始文件里 Irradiance 是占位值 1.000，
    # 导致表头 Etac 算成 862%）。要改回 1 sun 或换别的光强：
    python tools/stability_report.py --data "..." --suns 1.0
    python tools/stability_report.py --data "..." --irradiance 55 --area 0.1

    # 默认已按 1-8 = ZnO/D18:L8/MoOx/Ag、9-16 = ZnO/D18:L8/PEDOT-F/Ag 分组，并固定剔除 C003/C007/C009
    # 用别的分组（兼容 StabLabProcessor 分组文件格式）/ 取消剔除：
    python tools/stability_report.py --data "..." --groups "StabLab-默认分组.csv"
    python tools/stability_report.py --data "..." --exclude none

输出清单（默认全在 <数据目录>/REPORT/ 里）
----------------------------------------
    report.txt            人读的总结（参数、轮次、每片指标、分组对比、QC 警告、注意事项）
    points_all.csv        长表：每次测量一行（原始 + 重算指标 + 标记）
    summary_cells.csv     每片一行：PCE0 / 末点 / 四个参数及其归一化末值 / 衰减率 / T80
    summary_groups.csv    每组一行：片数 / PCE0 中位 / 末点归一化中位与范围 / T80
    aligned_pce.csv       宽表：行=时间，列=电池片（PCE %），便于 Origin 直接画
    aligned_pce_norm.csv  同上，但归一化到 100%（衰减曲线）
    aligned_isc_norm.csv  同上，但为归一化 Isc（基准点与 PCE 相同）
    aligned_voc_norm.csv  同上，但为归一化 Voc
    aligned_ff_norm.csv   同上，但为归一化 FF
    aligned_group_mean.csv 按轮对齐的分组均值（两个体系对比，含片数）
    rounds.csv            每轮汇总（中位 PCE/Isc、有效片数、光强是否异常、是否测完）
    fig_pce.png           4 行 × 3 列：左=原始值，中=归一化，右=末端斜率
                          （右列用于判断是否达到稳定，绿带内为 |斜率| ≤ 阈值）
    fig_group.png         分组对比（两个体系均值 ± SD）
    fig_ranking.png       末点归一化 PCE 排名
    fig_iv_last.png       最近一轮的 IV 曲线

默认分组 / 剔除
---------------
* 分组：1-8 号通道 = ZnO/D18:L8/MoOx/Ag，9-16 号通道 = ZnO/D18:L8/PEDOT-F/Ag
  （见 DEFAULT_GROUPS；图上/表里显示短名 MoOx/Ag、PEDOT-F/Ag）；
  用 --groups 给 CSV 时以文件为准。
* 固定剔除：C003 / C007 / C009（长期 Voc/FF 不达标，接触不良），见 DEFAULT_EXCLUDE；
  --exclude none 可恢复全部通道，--exclude 3,7,9,13 可改动。
* 分组统计按“轮次”对齐，而不是按精确时间戳：16 片是顺序拍的，
  同一时刻只有 1 片有数据，按时间戳聚合会出现锯齿。

口径说明
--------
原始 TXT 里的 Etac(%) 与实际能量守恒差了 100 倍，因为 Irradiance 字段写的是 1.000。
本脚本不用表头给的值，而是直接从 IV 曲线重算：

    Isc   = |I(V=0)|                  (线性插值)
    Voc   = V(I=0)                    (线性插值)
    Pmax  = max(-V*I)
    FF    = Pmax / (Isc*Voc)
    Jsc   = Isc / Area               [mA/cm²]
    PCE   = Pmax / (Area * Irr) *100 [%]

默认 Area = 0.1 cm²；名义光强默认 0.55 sun = 55 mW/cm²（DEFAULT_SUNS）。
用 --suns 改光强（1 sun = 100 mW/cm²），或 --irradiance 直接给 mW/cm²，--area 改面积。
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402


def _init_console():
    """Windows 控制台默认 GBK，遇到 °/²/⚠ 会 UnicodeEncodeError，这里统一降级处理。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_init_console()

# --------------------------------------------------------------------------
# 默认配置（可用命令行覆盖）
# --------------------------------------------------------------------------
DEFAULT_AREA = 0.1          # cm²，单个电池片受光面积
DEFAULT_IRR = 100.0         # mW/cm²，1 sun
DEFAULT_SUNS = 0.55         # 名义光强（sun）；实际测试用 0.55 sun = 55 mW/cm²
DEFAULT_ROUND_GAP = 60.0    # s，相邻测量时间间隔超过它就算新的一轮
DEFAULT_MIN_AGE = 2.0       # s，文件修改距今小于该值则视为"可能正在写"，跳过
DEFAULT_BASELINE = "first-round"   # first-round | firstN:<n> | best
DEFAULT_TAIL_HOURS = 2.0      # 末端斜率窗口（最后 N 小时）
DEFAULT_STABLE_SLOPE = 0.10   # %/h，|末端斜率| 小于它视为"已稳定"
DEFAULT_REPORT_DIR = "REPORT"     # 默认输出到 <数据目录>/REPORT（软件只写 MEAS/ 与 STAT/，不冲突）

# 默认分组：1-8 号 = ZnO/D18:L8/MoOx/Ag，9-16 号 = ZnO/D18:L8/PEDOT-F/Ag（可用 --groups 覆盖）
# 名字太长，图上/表里用短名（第二个字段）显示，完整名仍写入 CSV 的 group 列
DEFAULT_GROUPS = [
    ("ZnO/D18:L8/MoOx/Ag", "MoOx/Ag", "#38bdf8", range(1, 9)),
    ("ZnO/D18:L8/PEDOT-F/Ag", "PEDOT-F/Ag", "#fb7185", range(9, 17)),
]
GROUP_SHORT: dict[str, str] = {}
# 固定剔除的电池片（长期无效片：接触不良/短路）；--exclude none 可关闭
DEFAULT_EXCLUDE = "3,7,9"

QC = {
    "voc_min": 0.40,        # V，低于此值判为接触不良/短路片
    "ff_min": 40.0,         # %，低于此值判为 S 形/串联电阻过大
    "isc_min": 0.05,        # mA，几乎无电流
    "pmax_min": 0.01,       # mW
    "lamp_low_ratio": 0.85,  # 某轮中位 Isc < 全局中位 * 该比例 -> 判为光强偏低
    "header_tol": 0.05,     # 表头汇总与曲线重算的相对偏差告警阈值
}

PALETTE = [
    "#38bdf8", "#f472b6", "#a3e635", "#fbbf24", "#c084fc", "#f87171",
    "#2dd4bf", "#94a3b8", "#818cf8", "#fb923c", "#e879f9", "#34d399",
    "#facc15", "#60a5fa", "#fda4af", "#4ade80", "#c4b5fd", "#fcd34d",
    "#7dd3fc", "#f9a8d4",
]


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass
class Point:
    """一次测量（一个电池片在某一时刻的一条 IV 曲线 / 一条汇总记录）。"""

    cell: str                       # "C001"
    ts: datetime
    board: str = ""
    temp: float = float("nan")
    rh: float = float("nan")
    area: float = float("nan")
    irr: float = float("nan")
    isc: float = float("nan")       # mA，已取正值
    voc: float = float("nan")       # V
    pmax: float = float("nan")      # mW
    vpmax: float = float("nan")
    ipmax: float = float("nan")
    ff: float = float("nan")        # %
    jsc: float = float("nan")       # mA/cm²
    pce: float = float("nan")       # %
    source: str = "MEAS"            # MEAS（含曲线）| STAT（仅汇总）
    file: str = ""
    npts: int = 0                   # IV 点数
    flags: set = field(default_factory=set)
    round_id: int = -1
    pce_norm: float = float("nan")  # 归一化 PCE，基准 = 100
    isc_norm: float = float("nan")  # 归一化 Isc，基准点与 PCE 同一轮/同一点
    voc_norm: float = float("nan")  # 归一化 Voc
    ff_norm: float = float("nan")   # 归一化 FF
    curve: tuple | None = None      # (V, I) numpy arrays，仅保留最近一轮

    @property
    def valid(self) -> bool:
        return not (self.flags & HARD_FLAGS)

    @property
    def key(self):
        return (self.cell_number, self.ts)


# LOW_LIGHT 也算“不可比”，因为它反映的是光强变了而不是片子衰减
HARD_FLAGS = {"VOC_LOW", "FF_LOW", "ISC_LOW", "PMAX_LOW", "CURVE_BAD", "NAN", "LOW_LIGHT"}


@dataclass
class CellSummary:
    cell: str
    group: str = "All"
    color: str = PALETTE[0]
    points: list = field(default_factory=list)

    def valid_points(self) -> list:
        return [p for p in self.points if p.valid]


# --------------------------------------------------------------------------
# 文本读取（安全、容错）
# --------------------------------------------------------------------------
def read_text_safe(path: Path, min_age: float, retries: int = 2) -> str | None:
    """只读打开文件；文件太新或在被独占写入时返回 None（下轮再读）。"""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age < min_age:
        return None

    for attempt in range(retries + 1):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            break
        except (OSError, PermissionError):
            if attempt == retries:
                return None
            time.sleep(0.15)
    else:  # pragma: no cover
        return None

    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def num(text: str, default: float = float("nan")) -> float:
    """解析 '+8.9003E-001' / '-1.3' / '' 这类字段。"""
    if text is None:
        return default
    text = text.strip().strip('"')
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# 表头识别顺序很重要：vpmax/ipmax 必须先于 pmax
_HEADER_RULES = [
    ("vpmax", lambda k: k.startswith("vpmax")),
    ("ipmax", lambda k: k.startswith("ipmax")),
    ("pmax", lambda k: k.startswith("pmax")),
    ("time", lambda k: k.startswith("time")),
    ("board", lambda k: k.startswith("board")),
    ("cell", lambda k: k.startswith("cellno") or k == "cell"),
    ("temp", lambda k: k.startswith("temperature")),
    ("rh", lambda k: k.startswith("humidity")),
    ("area", lambda k: k.startswith("cellarea") or k.startswith("area")),
    ("irr", lambda k: k.startswith("irradiance")),
    ("isc", lambda k: k.startswith("isc")),
    ("voc", lambda k: k.startswith("voc")),
    ("ff", lambda k: k.startswith("ff")),
    ("pce_hdr", lambda k: k.startswith("etac") or k.startswith("pce") or k.startswith("efficiency")),
]


def map_header(cols: list[str]) -> dict:
    mapping = {}
    for idx, col in enumerate(cols):
        k = norm_key(col)
        if not k:
            continue
        for name, rule in _HEADER_RULES:
            if name not in mapping and rule(k):
                mapping[name] = idx
                break
    return mapping


def parse_time(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def cell_from_name(filename: str) -> str:
    """取文件名中最后一个 C\\d+ 或 _\\d{3}_ 作为通道号。"""
    stem = Path(filename).stem
    m = re.findall(r"[Cc](\d{1,4})", stem)
    if m:
        return f"C{int(m[-1]):03d}"
    m = re.findall(r"(?:^|_)(\d{1,4})(?:_|$)", stem)
    if m:
        return f"C{int(m[-1]):03d}"
    return "C000"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def parse_meas(text: str, filename: str) -> Point | None:
    """MEAS：第 1 行表头、第 2 行汇总、第 3 行曲线表头、其后 V,I,P 逐点。"""
    lines = text.splitlines()
    if len(lines) < 3:
        return None
    try:
        mapping = map_header(lines[0].split(","))
        row = lines[1].split(",")
    except Exception:
        return None
    if "time" not in mapping:
        return None

    def get(name, default=""):
        idx = mapping.get(name)
        return row[idx] if idx is not None and idx < len(row) else default

    ts = parse_time(get("time"))
    if ts is None:
        return None

    cell_raw = get("cell").strip()
    if cell_raw.isdigit():
        cell = f"C{int(cell_raw):03d}"
    elif cell_raw:
        cell = cell_raw
    else:
        cell = cell_from_name(filename)

    volts, amps = [], []
    for line in lines[2:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        v = num(parts[0])
        i = num(parts[1])
        if math.isnan(v) or math.isnan(i):
            continue        # 曲线表头行 / 被截断的不完整行
        volts.append(v)
        amps.append(i)

    if len(volts) < 8:
        return None

    p = Point(
        cell=cell,
        ts=ts,
        board=get("board").strip(),
        temp=num(get("temp")),
        rh=num(get("rh")),
        area=num(get("area")),
        irr=num(get("irr")),
        source="MEAS",
        file=filename,
        npts=len(volts),
    )
    p.curve = (np.asarray(volts, dtype=float), np.asarray(amps, dtype=float))
    p.flags.add("__hdr_pmax=%s" % get("pmax"))       # 延迟到 QC 阶段消费
    p.flags.add("__hdr_ff=%s" % get("ff"))
    return p


def parse_stat(text: str, filename: str) -> list[Point]:
    """STAT：表头 + N 行汇总（同一文件会不断追加）。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    mapping = map_header(lines[0].split(","))
    if "time" not in mapping:
        return []

    out = []
    for line in lines[1:]:
        row = line.split(",")
        if len(row) < 4:
            continue
        def get(name, default=""):
            idx = mapping.get(name)
            return row[idx] if idx is not None and idx < len(row) else default

        ts = parse_time(get("time"))
        if ts is None:
            continue
        cell_raw = get("cell").strip()
        cell = f"C{int(cell_raw):03d}" if cell_raw.isdigit() else (cell_raw or cell_from_name(filename))
        p = Point(
            cell=cell,
            ts=ts,
            board=get("board").strip(),
            temp=num(get("temp")),
            rh=num(get("rh")),
            area=num(get("area")),
            irr=num(get("irr")),
            isc=abs(num(get("isc"))),
            voc=num(get("voc")),
            pmax=num(get("pmax")),
            vpmax=num(get("vpmax")),
            ipmax=abs(num(get("ipmax"))),
            ff=num(get("ff")),
            source="STAT",
            file=filename,
        )
        out.append(p)
    return out


# --------------------------------------------------------------------------
# 指标计算
# --------------------------------------------------------------------------
def metrics_from_curve(V: np.ndarray, I: np.ndarray) -> dict:
    """从 IV 曲线重算 Isc/Voc/Pmax/FF（I 取正值口径）。"""
    order = np.argsort(V)
    Vs, Is = V[order], I[order]
    if np.all(Is > 0) or np.all(Is < 0):
        return {"isc": np.nan, "voc": np.nan, "pmax": np.nan,
                "vpmax": np.nan, "ipmax": np.nan, "ff": np.nan, "bad": True}

    isc = abs(float(np.interp(0.0, Vs, Is)))
    # Voc：I 穿过 0 处的 V。Is 随 Vs 单调上升，去重后插值一次即可
    iu, ui = np.unique(Is, return_index=True)
    voc = abs(float(np.interp(0.0, iu, Vs[ui])))

    P = -Vs * Is
    k = int(np.nanargmax(P))
    pmax, vpmax, ipmax = float(P[k]), float(Vs[k]), abs(float(Is[k]))
    ff = pmax / (isc * voc) * 100 if isc > 0 and voc > 0 else np.nan
    return {"isc": isc, "voc": voc, "pmax": pmax, "vpmax": vpmax,
            "ipmax": ipmax, "ff": ff, "bad": False}


def finish_point(p: Point, area: float, irr: float) -> Point:
    """补齐派生量 + 单点 QC 标记。"""
    hdr_pmax = hdr_ff = None
    for f in list(p.flags):
        if f.startswith("__hdr_pmax="):
            p.flags.discard(f)
            hdr_pmax = num(f.split("=", 1)[1])
        elif f.startswith("__hdr_ff="):
            p.flags.discard(f)
            hdr_ff = num(f.split("=", 1)[1])

    if p.curve is not None:
        m = metrics_from_curve(*p.curve)
        if m["bad"]:
            p.flags.add("CURVE_BAD")
        p.isc, p.voc, p.pmax = m["isc"], m["voc"], m["pmax"]
        p.vpmax, p.ipmax, p.ff = m["vpmax"], m["ipmax"], m["ff"]
        # 与表头汇总值交叉校验（表头给的值如果偏差大 -> 文件/软件层面有问题）
        if hdr_pmax is not None and not math.isnan(hdr_pmax) and p.pmax and not math.isnan(p.pmax):
            denom = max(abs(hdr_pmax), 1e-9)
            if abs(p.pmax - hdr_pmax) / denom > QC["header_tol"]:
                p.flags.add("HDR_MISMATCH")

    p.isc = abs(p.isc) if not math.isnan(p.isc) else p.isc
    p.ipmax = abs(p.ipmax) if not math.isnan(p.ipmax) else p.ipmax

    if any(math.isnan(x) for x in (p.isc, p.voc, p.pmax)):
        p.flags.add("NAN")
    else:
        if p.voc < QC["voc_min"]:
            p.flags.add("VOC_LOW")
        if not math.isnan(p.ff) and p.ff < QC["ff_min"]:
            p.flags.add("FF_LOW")
        if p.isc < QC["isc_min"]:
            p.flags.add("ISC_LOW")
        if p.pmax < QC["pmax_min"]:
            p.flags.add("PMAX_LOW")

    p.jsc = p.isc / area if area > 0 else np.nan
    p.pce = p.pmax / (area * irr) * 100 if area > 0 and irr > 0 else np.nan
    return p


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------
def _prefer(new: Point, old: Point) -> bool:
    """同一片同一时刻出现多条记录时，选更可靠的那条（带完整 IV 曲线 > 只有汇总）。"""
    if old.source == "STAT" and new.source == "MEAS":
        return True
    if old.source == "MEAS" and new.source == "STAT":
        return False
    old_bad = bool({"CURVE_BAD", "NAN"} & old.flags)
    new_bad = bool({"CURVE_BAD", "NAN"} & new.flags)
    if new_bad != old_bad:
        return old_bad
    return new.npts > old.npts


def collect(data_dir: Path, area: float, irr: float, min_age: float, log) -> tuple[list[Point], dict]:
    stats = {"meas_files": 0, "stat_files": 0, "skipped": 0, "bad_files": 0, "curves": {}}
    by_key: dict[tuple[str, datetime], Point] = {}

    def add(points):
        for p in points:
            p = finish_point(p, area, irr)
            k = (p.cell, p.ts)
            old = by_key.get(k)
            if old is None or _prefer(p, old):
                by_key[k] = p

    for sub, parser in (("MEAS", parse_meas), ("STAT", parse_stat)):
        folder = data_dir / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.txt")):
            text = read_text_safe(path, min_age)
            if text is None:
                stats["skipped"] += 1
                continue
            if not text.strip():
                stats["skipped"] += 1
                continue
            try:
                res = parser(text, path.name)
            except Exception as exc:      # 半个文件 / 意外格式：跳过而不是崩溃
                stats["bad_files"] += 1
                log(f"  [警告] 解析失败 {path.name}: {exc}")
                continue
            if res is None:
                stats["skipped"] += 1
                continue
            if isinstance(res, list):
                stats["stat_files"] += 1
                add(res)
            else:
                stats["meas_files"] += 1
                add([res])

    points = list(by_key.values())
    points.sort(key=lambda p: (p.ts, p.cell))
    return points, stats


# --------------------------------------------------------------------------
# 轮次划分与全局 QC
# --------------------------------------------------------------------------
def assign_rounds(points: list[Point], gap: float) -> list[dict]:
    if not points:
        return []
    stamps = sorted({p.ts for p in points})
    rounds, current = [], [stamps[0]]
    for ts in stamps[1:]:
        if (ts - current[-1]).total_seconds() > gap:
            rounds.append(current)
            current = [ts]
        else:
            current.append(ts)
    rounds.append(current)

    index = {}
    for i, stamps_in_round in enumerate(rounds):
        for ts in stamps_in_round:
            index[ts] = i
    for p in points:
        p.round_id = index[p.ts]

    # 每轮的中位 Isc（只用单点 QC 通过的点），用于识别光强异常轮
    infos = []
    for i, stamps_in_round in enumerate(rounds):
        members = [p for p in points if p.round_id == i]
        good = [p for p in members if p.valid and not math.isnan(p.isc)]
        med_isc = float(np.median([p.isc for p in good])) if good else float("nan")
        med_pce = float(np.median([p.pce for p in good])) if good else float("nan")
        infos.append({
            "round_id": i,
            "start": stamps_in_round[0],
            "end": stamps_in_round[-1],
            "cells": len({p.cell for p in members}),
            "valid": len(good),
            "median_isc": med_isc,
            "median_pce": med_pce,
            "lamp_ok": True,
        })

    ref = [r["median_isc"] for r in infos if not math.isnan(r["median_isc"])]
    if ref:
        global_med = float(np.median(ref))
        for r in infos:
            if math.isnan(r["median_isc"]) or global_med <= 0:
                continue
            if r["median_isc"] < QC["lamp_low_ratio"] * global_med:
                r["lamp_ok"] = False
                for p in points:
                    if p.round_id == r["round_id"]:
                        p.flags.add("LOW_LIGHT")

    # LOW_LIGHT 标记后再重算一次有效片数，保证与后续统计口径一致
    for r in infos:
        r["valid"] = len([p for p in points if p.round_id == r["round_id"] and p.valid])
    return infos


# --------------------------------------------------------------------------
# 归一化 / T80 / 衰减率
# --------------------------------------------------------------------------
def pick_baseline(cell_pts: list[Point], round_infos: list[dict], mode: str) -> dict:
    """返回基准值字典 {pce, isc, voc, ff}。四项必须来自同一基准点，四个归一化图才可比。"""
    nan = float("nan")
    empty = {"pce": nan, "isc": nan, "voc": nan, "ff": nan}
    usable = [p for p in cell_pts if p.valid and p.pce == p.pce and p.pce > 0]
    if not usable:
        return empty
    ok_rounds = {r["round_id"] for r in round_infos if r["lamp_ok"]}
    keys = ("pce", "isc", "voc", "ff")

    if mode == "best":
        best = max(usable, key=lambda p: p.pce)
        return {k: getattr(best, k) for k in keys}
    if mode.startswith("firstN:"):
        n = max(1, int(mode.split(":", 1)[1]))
        head = usable[:n]
        return {k: float(np.median([getattr(p, k) for p in head])) for k in keys}
    # first-round：第一轮光强正常且该片有效的那一轮
    for p in usable:
        if p.round_id in ok_rounds:
            return {k: getattr(p, k) for k in keys}
    return {k: getattr(usable[0], k) for k in keys}


def _norm(value: float, base: float) -> float:
    if value != value or base != base or not base or base <= 0:
        return float("nan")
    return value / base * 100.0


def crossing_time(series: list[tuple[float, float]], level: float) -> float:
    """series = [(t_h, norm%)] 升序；返回首次跌破 level 的线性插值时刻。"""
    for (t0, y0), (t1, y1) in zip(series, series[1:]):
        if y0 >= level > y1:
            if y1 == y0:
                return t1
            return t0 + (level - y0) * (t1 - t0) / (y1 - y0)
    return float("nan")


def slope_per_hour(series: list[tuple[float, float]]) -> float:
    if len(series) < 3:
        return float("nan")
    t = np.array([s[0] for s in series])
    y = np.array([s[1] for s in series])
    if t.max() - t.min() < 1e-9:
        return float("nan")
    return float(np.polyfit(t, y, 1)[0])


def window_slope(series: list[tuple[float, float]], t_end: float, hours: float) -> tuple[float, int]:
    """serie 的末端窗口斜率 (%/h) 与参与点数。serie = [(t_h, 归一化值)]。"""
    rows = sorted(r for r in series if r[0] >= t_end - hours)
    if len(rows) < 3:
        return float("nan"), len(rows)
    return slope_per_hour(rows), len(rows)


def _is_stable(slope: float, threshold: float) -> bool:
    return slope == slope and abs(slope) <= threshold


def _median(values) -> float:
    vals = [v for v in values if v == v]
    return float(np.median(vals)) if vals else float("nan")


def round_centers(points) -> dict[int, datetime]:
    """每轮的代表时刻（该轮所有时间戳的中位）——16 片是顺序测的，不能用精确时间戳对齐。"""
    buckets: dict[int, list] = {}
    for p in points:
        buckets.setdefault(p.round_id, []).append(p.ts)
    return {rid: sorted(ts)[len(ts) // 2] for rid, ts in buckets.items()}


def round_completeness(points) -> tuple[dict, int]:
    """每轮的有效片数 & 正常轮应有的片数，用于识别"还在测的最后一轮"。"""
    counts: dict[int, int] = {}
    for p in points:
        if p.valid:
            counts[p.round_id] = counts.get(p.round_id, 0) + 1
    expected = max(counts.values()) if counts else 0
    return counts, expected


def is_complete_round(round_id: int, counts: dict, expected: int) -> bool:
    """片数不足正常轮 80% 的轮次视为未测完，不参与分组均值（否则 n 变化会造假跳变）。"""
    if expected <= 0:
        return False
    return counts.get(round_id, 0) >= max(2, 0.8 * expected)


def group_series(summaries, cells, points, t0, getter) -> dict:
    """按组 + 按轮汇总：均值 / 标准差 / 参与片数（跳过未测完的轮）。"""
    centers = round_centers(points)
    counts, expected = round_completeness(points)
    members: dict[str, dict] = {}
    for cell in cells:
        s = summaries[cell]
        g = members.setdefault(s.group, {"color": s.color, "cells": []})
        g["cells"].append(cell)

    out = {}
    for name, info in members.items():
        buckets: dict[int, list] = {}
        for cell in info["cells"]:
            for p in summaries[cell].valid_points():
                v = getter(p)
                if v == v:
                    buckets.setdefault(p.round_id, []).append(v)
        # 完整度按"组内"判断：整轮片数够、但本组只测到 1~2 片时，组均值会假跳
        group_expected = max((len(v) for v in buckets.values()), default=0)
        need = max(2, 0.8 * group_expected)
        rounds = [r for r in sorted(buckets) if len(buckets[r]) >= need]
        out[name] = {
            "color": info["color"],
            "cells": info["cells"],
            "rounds": rounds,
            "t": [(centers[r] - t0).total_seconds() / 3600.0 for r in rounds],
            "mean": [float(np.mean(buckets[r])) for r in rounds],
            "std": [float(np.std(buckets[r])) for r in rounds],
            "n": [len(buckets[r]) for r in rounds],
        }
    return out


def group_summary(summaries, metrics) -> list[dict]:
    """每组一行：片数、PCE0 中位、末点归一化中位/范围、衰减率中位、T80 情况。"""
    members: dict[str, list] = {}
    for cell, s in summaries.items():
        members.setdefault(s.group, []).append(cell)

    out = []
    for name, cell_list in members.items():
        good = [metrics[c] for c in cell_list if metrics[c]["n_valid"] > 0]
        last_norms = [m["pce_norm_last"] for m in good]
        t80s = [m["t80"] for m in good if m["t80"] == m["t80"]]
        best = max(good, key=lambda m: m["pce_norm_last"]) if good else None
        worst = min(good, key=lambda m: m["pce_norm_last"]) if good else None
        out.append({
            "group": name,
            "cells": sorted(cell_list),
            "n_cells": len(cell_list),
            "n_valid_cells": len(good),
            "pce0_median": _median([m["base"] for m in good]),
            "norm_last_median": _median(last_norms),
            "norm_last_min": min(last_norms) if last_norms else float("nan"),
            "norm_last_max": max(last_norms) if last_norms else float("nan"),
            "slope_median": _median([m["slope"] for m in good]),
            "t80_median": _median(t80s),
            "n_t80": len(t80s),
            "best_cell": best["cell"] if best else "",
            "worst_cell": worst["cell"] if worst else "",
        })
    return out


# --------------------------------------------------------------------------
# 分组 CSV（兼容 StabLabProcessor 格式）
# --------------------------------------------------------------------------
def load_groups(path: Path | None) -> dict[int, tuple[str, str]]:
    """返回 {电池片号: (组名, 颜色)}。不给文件时用 DEFAULT_GROUPS。"""
    if not path:
        mapping = {}
        for name, short, color, numbers in DEFAULT_GROUPS:
            GROUP_SHORT[name] = short
            for n in numbers:
                mapping[n] = (name, color)
        return mapping
    if not path.is_file():
        return {}
    text = read_text_safe(path, 0) or ""
    mapping = {}
    reader = csv.reader(text.splitlines())
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        return {}
    start = 0
    if norm_key(rows[0][0]).startswith("group"):
        start = 1
    for row in rows[start:]:
        if len(row) < 2:
            continue
        name, color = row[0].strip(), row[1].strip() or PALETTE[0]
        chans = ",".join(row[2:])
        for token in chans.replace('"', "").split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                a, _, b = token.partition("-")
                if a.strip().isdigit() and b.strip().isdigit():
                    for n in range(int(a), int(b) + 1):
                        mapping[n] = (name, color)
            elif token.isdigit():
                mapping[int(token)] = (name, color)
    return mapping


def style_for(cell: str, groups: dict, idx: int) -> tuple[str, str]:
    number = int(re.sub(r"\D", "", cell) or 0)
    if number in groups:
        return groups[number]
    return "All", PALETTE[idx % len(PALETTE)]


def group_short(name: str) -> str:
    """长组名取末两级做短名：ZnO/D18:L8/MoOx/Ag -> MoOx/Ag。"""
    if name in GROUP_SHORT:
        return GROUP_SHORT[name]
    parts = [p for p in name.split("/") if p]
    short = "/".join(parts[-2:]) if len(parts) > 2 else name
    GROUP_SHORT[name] = short
    return short


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def atomic_write(path: Path, text: str, encoding: str = "utf-8-sig") -> bool:
    """先写 .tmp 再原子替换；目标被 Excel/Origin 占用时只告警，不中断本轮。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
    try:
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        print(f"  [警告] {path.name} 被其他程序占用（可能正在 Excel/Origin 里打开），"
              f"本轮未更新（{exc.__class__.__name__}）。")
        return False
    return True


def save_fig(fig, path: Path, dpi: int = 160) -> bool:
    """存图同样容错：图片被看图软件打开时不要打断整个刷新。"""
    ok = True
    try:
        fig.savefig(path, dpi=dpi)
    except OSError as exc:
        print(f"  [警告] {path.name} 写入失败（可能被占用），本轮跳过（{exc.__class__.__name__}）。")
        ok = False
    finally:
        plt.close(fig)
    return ok


def fmt(x, digits=4):
    if x is None:
        return ""
    try:
        if x != x or (isinstance(x, float) and (math.isinf(x))):
            return ""
    except TypeError:
        return str(x)
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def write_csv(path: Path, header: list[str], rows: list[list]):
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    atomic_write(path, buf.getvalue())


def setup_font() -> bool:
    """找一个中文字体，找不到就退回英文标签。"""
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimSun"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
        except Exception:
            continue
        plt.rcParams["font.sans-serif"] = [name]
        plt.rcParams["axes.unicode_minus"] = False
        return True
    return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def analyze(args) -> int:
    data_dir = Path(args.data).expanduser()
    if not data_dir.is_dir():
        print(f"[错误] 数据目录不存在: {data_dir}")
        return 2

    out_dir = resolve_out_dir(data_dir, args)
    # 软件只往 <数据目录>\MEAS 和 \STAT 写文件，输出必须避开这两个目录
    for sub in ("MEAS", "STAT"):
        try:
            out_dir.relative_to((data_dir / sub).resolve())
        except ValueError:
            continue
        print(f"[错误] 输出目录不能放在原始数据子目录 {sub} 里（会被当成测量文件），请换一个目录。")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    def log(*a):
        print(*a, flush=True)

    t_start = time.time()
    points, stats = collect(data_dir, args.area, args.irradiance, args.min_age, log)
    if not points:
        log(f"[{datetime.now():%H:%M:%S}] 还没读到有效数据（文件可能刚开始写），稍后再试。")
        return 0

    exclude = parse_exclude(args.exclude)
    if exclude:
        points = [p for p in points if p.cell not in exclude]
        if not points:
            log("提示：所有电池片都被 --exclude 排除了，无数据可算。")
            return 0

    round_infos = assign_rounds(points, args.round_gap)

    groups = load_groups(Path(args.groups) if args.groups else None)
    cells = sorted({p.cell for p in points})
    summaries = {}
    for i, cell in enumerate(cells):
        s = CellSummary(cell=cell)
        s.group, s.color = style_for(cell, groups, i)
        s.points = sorted([p for p in points if p.cell == cell], key=lambda p: p.ts)
        summaries[cell] = s

    # ---- 归一化 ----
    t0 = min(p.ts for p in points if p.valid) if any(p.valid for p in points) else min(p.ts for p in points)
    for cell, s in summaries.items():
        base = pick_baseline(s.points, round_infos, args.baseline)
        for p in s.points:
            p.pce_norm = _norm(p.pce, base["pce"])
            p.isc_norm = _norm(p.isc, base["isc"])
            p.voc_norm = _norm(p.voc, base["voc"])
            p.ff_norm = _norm(p.ff, base["ff"])
        s.base = base["pce"]
        s.base_isc = base["isc"]
        s.base_voc = base["voc"]
        s.base_ff = base["ff"]

    t_end = (max(p.ts for p in points) - t0).total_seconds() / 3600.0

    # ---- 每片指标 ----
    metrics: dict[str, dict] = {}
    for cell, s in summaries.items():
        vp = s.valid_points()
        series = [((p.ts - t0).total_seconds() / 3600.0, p.pce_norm)
                  for p in vp if p.pce_norm == p.pce_norm]
        last = vp[-1] if vp else None
        first = vp[0] if vp else None
        series_by = {
            name: [((p.ts - t0).total_seconds() / 3600.0, getter(p))
                   for p in vp if getter(p) == getter(p)]
            for name, getter in (("pce", lambda q: q.pce_norm), ("isc", lambda q: q.isc_norm),
                                 ("voc", lambda q: q.voc_norm), ("ff", lambda q: q.ff_norm))
        }
        tail = {f"{name}_slope_tail": window_slope(series_by[name], t_end, args.tail_hours)
                for name in series_by}
        metrics[cell] = {
            "cell": cell,
            "group": s.group,
            "series_by": series_by,
            "tail": tail,
            "base": s.base,
            "n_points": len(s.points),
            "n_valid": len(vp),
            "first": first,
            "last": last,
            "series": series,
            "pce_mean": float(np.mean([p.pce for p in vp])) if vp else float("nan"),
            "pce_best": max((p.pce for p in vp), default=float("nan")),
            "pce_norm_last": last.pce_norm if last else float("nan"),
            "isc_norm_last": last.isc_norm if last else float("nan"),
            "voc_norm_last": last.voc_norm if last else float("nan"),
            "ff_norm_last": last.ff_norm if last else float("nan"),
            "t80": crossing_time(series, 80.0),
            "t90": crossing_time(series, 90.0),
            "slope": slope_per_hour(series),
            "flags": sorted({f for p in s.points for f in p.flags if not f.startswith("__")}),
        }

    rows = []
    for cell in sorted(metrics):
        m = metrics[cell]
        first, last = m["first"], m["last"]
        rows.append([
            cell, m["group"],
            m["n_points"], m["n_valid"],
            fmt(m["base"]), fmt(m["pce_norm_last"], 2),
            fmt(last.pce if last else float("nan")),
            fmt(m["pce_mean"]), fmt(m["pce_best"]),
            fmt(m["t80"], 2), fmt(m["t90"], 2), fmt(m["slope"], 2),
            fmt(first.isc if first else float("nan")), fmt(last.isc if last else float("nan")),
            fmt(m["isc_norm_last"], 2), fmt(getattr(summaries[cell], "base_isc", float("nan")), 4),
            fmt(first.jsc if first else float("nan"), 2), fmt(last.jsc if last else float("nan"), 2),
            fmt(first.voc if first else float("nan"), 4), fmt(last.voc if last else float("nan"), 4),
            fmt(m["voc_norm_last"], 2),
            fmt(first.ff if first else float("nan"), 1), fmt(last.ff if last else float("nan"), 1),
            fmt(m["ff_norm_last"], 2),
            first.ts.strftime("%Y-%m-%d %H:%M:%S") if first else "",
            last.ts.strftime("%Y-%m-%d %H:%M:%S") if last else "",
            fmt((last.ts - first.ts).total_seconds() / 3600 if first and last else float("nan"), 2),
            fmt(m["tail"]["pce_slope_tail"][0], 3), fmt(m["tail"]["isc_slope_tail"][0], 3),
            fmt(m["tail"]["voc_slope_tail"][0], 3), fmt(m["tail"]["ff_slope_tail"][0], 3),
            ("Y" if _is_stable(m["tail"]["pce_slope_tail"][0], args.stable_slope)
             else "N" if m["n_valid"] >= 3 else ""),
            ";".join(m["flags"]),
        ])

    write_csv(out_dir / "summary_cells.csv",
              ["cell", "group", "n_points", "n_valid", "PCE0(%)", "PCE_norm_last(%)",
               "PCE_last(%)", "PCE_mean(%)", "PCE_best(%)",
               "T80(h)", "T90(h)", "degr_rate(%/h)",
               "Isc0(mA)", "Isc_last(mA)", "Isc_norm_last(%)", "Isc0_base(mA)",
               "Jsc0(mA/cm2)", "Jsc_last(mA/cm2)",
               "Voc0(V)", "Voc_last(V)", "Voc_norm_last(%)",
               "FF0(%)", "FF_last(%)", "FF_norm_last(%)",
               "first_time", "last_time", "elapsed(h)",
               "PCE_slope_tail(%/h)", "Isc_slope_tail(%/h)", "Voc_slope_tail(%/h)",
               "FF_slope_tail(%/h)", "PCE_stable", "flags"],
              rows)

    # ---- 分组汇总 ----
    gsum = group_summary(summaries, metrics)
    write_csv(out_dir / "summary_groups.csv",
              ["group", "group_short", "n_cells", "n_valid_cells", "PCE0_median(%)",
               "PCE_norm_last_median(%)", "PCE_norm_last_min(%)", "PCE_norm_last_max(%)",
               "degr_rate_median(%/h)", "T80_median(h)", "n_reach_T80",
               "best_cell", "best_norm(%)", "worst_cell", "worst_norm(%)", "cells"],
              [[g["group"], group_short(g["group"]), g["n_cells"], g["n_valid_cells"],
                fmt(g["pce0_median"]),
                fmt(g["norm_last_median"], 2), fmt(g["norm_last_min"], 2),
                fmt(g["norm_last_max"], 2), fmt(g["slope_median"], 2),
                fmt(g["t80_median"], 2), g["n_t80"],
                g["best_cell"], fmt(metrics[g["best_cell"]]["pce_norm_last"], 2) if g["best_cell"] else "",
                g["worst_cell"], fmt(metrics[g["worst_cell"]]["pce_norm_last"], 2) if g["worst_cell"] else "",
                ",".join(g["cells"])] for g in gsum])

    # ---- 长表 ----
    write_csv(out_dir / "points_all.csv",
              ["time", "cell", "round", "source",
               "Isc(mA)", "Voc(V)", "Pmax(mW)", "Vpmax(V)", "Ipmax(mA)",
               "FF(%)", "Jsc(mA/cm2)", "PCE(%)", "PCE_norm(%)",
               "Temp(C)", "RH(%)", "n_iv_points", "valid", "flags", "file"],
              [[p.ts.strftime("%Y-%m-%d %H:%M:%S"), p.cell, p.round_id, p.source,
                fmt(p.isc, 4), fmt(p.voc, 4), fmt(p.pmax, 4), fmt(p.vpmax, 4), fmt(p.ipmax, 4),
                fmt(p.ff, 2), fmt(p.jsc, 3), fmt(p.pce, 4), fmt(p.pce_norm, 2),
                fmt(p.temp, 2), fmt(p.rh, 2), p.npts, int(p.valid),
                ";".join(sorted(f for f in p.flags if not f.startswith("__"))), p.file]
               for p in points])

    # ---- 宽表（对齐）----
    for suffix, getter in (("aligned_pce.csv", lambda p: p.pce),
                           ("aligned_pce_norm.csv", lambda p: p.pce_norm),
                           ("aligned_isc_norm.csv", lambda p: p.isc_norm),
                           ("aligned_voc_norm.csv", lambda p: p.voc_norm),
                           ("aligned_ff_norm.csv", lambda p: p.ff_norm)):
        stamps = sorted({p.ts for p in points})
        lookup = {(p.ts, p.cell): p for p in points}
        body = []
        for ts in stamps:
            row = [ts.strftime("%Y-%m-%d %H:%M:%S"),
                   fmt((ts - t0).total_seconds() / 3600.0, 4)]
            for cell in cells:
                p = lookup.get((ts, cell))
                row.append(fmt(getter(p), 4) if p and not (p.flags & HARD_FLAGS) else "")
            body.append(row)
        write_csv(out_dir / suffix, ["time", "elapsed(h)"] + cells, body)

    # ---- 分组均值宽表（type1 / type2 对比用，按轮对齐）----
    gnames = sorted({summaries[c].group for c in cells})
    if gnames:
        centers = round_centers(points)
        buckets = {g: {} for g in gnames}
        for p in points:
            if not p.valid:
                continue
            buckets[summaries[p.cell].group].setdefault(p.round_id, []).append(p)
        round_ids = sorted({rid for g in gnames for rid in buckets[g]})
        # 每组各自的"应测片数"，用于判断该轮该组是否已测完
        g_need = {g: max(2, 0.8 * max((len(v) for v in buckets[g].values()), default=0))
                  for g in gnames}
        body = []
        for rid in round_ids:
            ts = centers[rid]
            row = [ts.strftime("%Y-%m-%d %H:%M:%S"),
                   fmt((ts - t0).total_seconds() / 3600.0, 4), rid]
            for g in gnames:
                gp = buckets[g].get(rid, [])
                ready = len(gp) >= g_need[g]
                row.append(fmt(np.mean([q.pce for q in gp]), 4) if ready else "")
                row.append(fmt(np.mean([q.pce_norm for q in gp]), 2) if ready else "")
                row.append(len(gp))
            body.append(row)
        header = ["time", "elapsed(h)", "round"]
        for g in gnames:
            header += [f"{group_short(g)}_PCE(%)", f"{group_short(g)}_PCE_norm(%)",
                       f"{group_short(g)}_n"]
        write_csv(out_dir / "aligned_group_mean.csv", header, body)

    # ---- 轮次表 ----
    counts, expected = round_completeness(points)
    write_csv(out_dir / "rounds.csv",
              ["round", "start", "end", "cells", "valid", "median_Isc(mA)",
               "median_PCE(%)", "lamp_ok", "complete"],
              [[r["round_id"], r["start"].strftime("%H:%M:%S"), r["end"].strftime("%H:%M:%S"),
                r["cells"], r["valid"], fmt(r["median_isc"], 4), fmt(r["median_pce"], 4),
                "OK" if r["lamp_ok"] else "LOW",
                "Y" if is_complete_round(r["round_id"], counts, expected) else "N"]
               for r in round_infos])

    # ---- 图 ----
    if not args.no_plots:
        make_plots(out_dir, summaries, cells, points, t0, metrics, args)

    # ---- report.txt ----
    make_report(out_dir, data_dir, args, points, stats, round_infos, summaries,
                cells, t0, exclude, metrics, gsum)
    log(f"[{datetime.now():%H:%M:%S}] 完成：{len(points)} 条记录 / {len(cells)} 片 / "
        f"{len(round_infos)} 轮，用时 {time.time() - t_start:.1f}s -> {out_dir}")
    return 0


def make_plots(out_dir: Path, summaries, cells, points, t0, metrics, args):
    cn = setup_font()

    def t(zh, en):
        return zh if cn else en

    # --- 八联图：四参数 × (原始值 + 归一化)，左列原始、右列归一化 ---
    #     细线 = 单片，粗线 = 分组均值 ± 标准差
    fig, axes = plt.subplots(4, 3, figsize=(19.5, 15.5),
                             gridspec_kw={"width_ratios": [1, 1, 0.85]})
    for r in range(4):
        axes[r][1].sharex(axes[r][0])
    labels = {
        "pce": t("PCE (%)", "PCE (%)"),
        "isc": t("Isc (mA)", "Isc (mA)"),
        "voc": t("Voc (V)", "Voc (V)"),
        "ff": t("FF (%)", "FF (%)"),
    }
    panels = [
        (axes[0][0], lambda p: p.pce, labels["pce"], False),
        (axes[0][1], lambda p: p.pce_norm, t("归一化 PCE (%)", "Normalized PCE (%)"), True),
        (axes[1][0], lambda p: p.isc, labels["isc"], False),
        (axes[1][1], lambda p: p.isc_norm, t("归一化 Isc (%)", "Normalized Isc (%)"), True),
        (axes[2][0], lambda p: p.voc, labels["voc"], False),
        (axes[2][1], lambda p: p.voc_norm, t("归一化 Voc (%)", "Normalized Voc (%)"), True),
        (axes[3][0], lambda p: p.ff, labels["ff"], False),
        (axes[3][1], lambda p: p.ff_norm, t("归一化 FF (%)", "Normalized FF (%)"), True),
    ]
    groups = group_series(summaries, cells, points, t0, lambda p: p.pce)
    for ax, getter, label, is_norm in panels:
        for cell in cells:
            s_ = summaries[cell]
            vp = [p for p in s_.valid_points() if getter(p) == getter(p)]
            if not vp:
                continue
            x = [(p.ts - t0).total_seconds() / 3600 for p in vp]
            ax.plot(x, [getter(p) for p in vp], "-o", ms=1.8, lw=0.8,
                    color=s_.color, alpha=0.32)
        for name, g in group_series(summaries, cells, points, t0, getter).items():
            if not g["t"]:
                continue
            ax.plot(g["t"], g["mean"], "-o", ms=2.6, lw=2.1, color=g["color"],
                    label=f"{group_short(name)} (n={len(g['cells'])})")
            ax.fill_between(g["t"],
                            np.array(g["mean"]) - np.array(g["std"]),
                            np.array(g["mean"]) + np.array(g["std"]),
                            color=g["color"], alpha=0.12, lw=0)
        ax.set_ylabel(label)
        ax.grid(alpha=0.25, lw=0.6)
        if is_norm:
            ax.axhline(100, ls=":", lw=1, color="#64748b", alpha=0.85)
    # 归一化 PCE 那格标出 80% 判据（T80）
    if any(p.valid for p in points):
        t80_ax = axes[0][1]
        t80_ax.axhline(80, ls="--", lw=1, color="#ef4444", alpha=0.7)
        t80_ax.text(0.01, 80.6, t("T80 判据", "T80"), color="#ef4444", fontsize=8)
    for ax in axes[-1][:2]:
        ax.set_xlabel(t("经过时间 (h)", "Elapsed time (h)"))

    # ---------- 第 3 列：末端斜率（判断是否达到稳定）----------
    thr = args.stable_slope
    win = args.tail_hours
    slope_params = [
        ("pce", t("PCE 末端斜率 (%/h)", "PCE tail slope (%/h)")),
        ("isc", t("Isc 末端斜率 (%/h)", "Isc tail slope (%/h)")),
        ("voc", t("Voc 末端斜率 (%/h)", "Voc tail slope (%/h)")),
        ("ff", t("FF 末端斜率 (%/h)", "FF tail slope (%/h)")),
    ]
    for row, (name, label) in enumerate(slope_params):
        ax = axes[row][2]
        data = []
        for cell in cells:
            slope, n = metrics[cell]["tail"][f"{name}_slope_tail"]
            if slope == slope:
                data.append((cell, slope, summaries[cell].color, summaries[cell].group))
        if not data:
            ax.text(0.5, 0.5, t("点数不足，无法拟合", "not enough points"),
                    ha="center", va="center", fontsize=9, color="#64748b")
            ax.set_xticks([])
            ax.set_yticks([])
            continue
        data.sort(key=lambda d: d[1])
        ys = np.arange(len(data))
        ax.barh(ys, [d[1] for d in data], color=[d[2] for d in data], alpha=0.85, height=0.7)
        ax.set_yticks(ys)
        ax.set_yticklabels([d[0] for d in data], fontsize=7)
        # 稳定判据带
        ax.axvspan(-thr, thr, color="#22c55e", alpha=0.13, lw=0, zorder=0)
        for x in (-thr, thr):
            ax.axvline(x, ls="--", lw=1, color="#16a34a", alpha=0.8, zorder=1)
        ax.axvline(0, lw=1, color="#334155", zorder=2)
        # 数值标注
        span = max(max(abs(d[1]) for d in data), thr * 1.3) or 1.0
        for y, (_, slope, _, _) in zip(ys, data):
            offset = span * 0.03
            ax.text(slope + (offset if slope >= 0 else -offset), y, f"{slope:+.2f}",
                    va="center", ha="left" if slope >= 0 else "right", fontsize=6.5,
                    color="#334155")
        # 组均值（竖虚线）
        for gname, ginfo in group_series(summaries, cells, points, t0,
                                         lambda q, _n=name: getattr(q, f"{_n}_norm")).items():
            g_slopes = [d[1] for d in data if d[3] == gname]
            if g_slopes:
                m = float(np.median(g_slopes))
                ax.axvline(m, ls=":", lw=2, color=ginfo["color"], alpha=0.95, zorder=3)
        ax.set_xlim(-span * 1.45, span * 1.45)
        ax.set_ylabel(label, fontsize=8.5)
        ax.tick_params(axis="x", labelsize=7)
        ax.grid(axis="x", alpha=0.25, lw=0.6)
        if row == 0:
            ax.set_title(t(f"最后 {win:g} h 线性斜率（绿带 = |斜率| ≤ {thr:g} %/h 视为稳定）",
                           f"slope of last {win:g} h (green = |slope| <= {thr:g} %/h)"),
                         fontsize=8)
        ax.set_xlabel(t("%/h", "%/h"), fontsize=8)
    handles, labels_ = axes[0][0].get_legend_handles_labels()
    handles.append(plt.Line2D([], [], ls=":", lw=2, color="#64748b"))
    labels_.append(t("第 3 列虚线 = 该组中位斜率", "dotted = group median slope"))
    fig.legend(handles, labels_, loc="lower center", ncol=3, fontsize=9, frameon=False)
    fig.suptitle(t("稳定性测试：原始值（左）/ 归一化（中）/ 末端斜率（右，虚线=组中位）",
                   "Stability: raw (left) / normalized (middle) / tail slope (right)"),
                 fontsize=13, y=0.995)
    fig.tight_layout(rect=(0, 0.035, 1, 0.985))
    save_fig(fig, out_dir / "fig_pce.png")

    # --- 分组对比 ---
    if groups:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharex=True)
        for ax, getter, label in (
            (axes[0], lambda p: p.pce, t("PCE (%)", "PCE (%)")),
            (axes[1], lambda p: p.pce_norm, t("归一化 PCE (%)", "Normalized PCE (%)")),
        ):
            for name, g in group_series(summaries, cells, points, t0, getter).items():
                if not g["t"]:
                    continue
                ax.plot(g["t"], g["mean"], "-o", ms=3.2, lw=2, color=g["color"],
                        label=f"{group_short(name)} (n={len(g['cells'])})")
                ax.fill_between(g["t"],
                                np.array(g["mean"]) - np.array(g["std"]),
                                np.array(g["mean"]) + np.array(g["std"]),
                                color=g["color"], alpha=0.15, lw=0)
            ax.set_ylabel(label)
            ax.set_xlabel(t("经过时间 (h)", "Elapsed time (h)"))
            ax.grid(alpha=0.25, lw=0.6)
            ax.legend(fontsize=8)
        axes[1].axhline(100, ls=":", lw=1, color="#64748b")
        fig.suptitle(t("分组对比（均值 ± 标准差）", "Group comparison (mean ± SD)"), fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        save_fig(fig, out_dir / "fig_group.png")

    # --- 排名 ---
    ranked = []
    for cell in cells:
        s = summaries[cell]
        vp = [p for p in s.valid_points() if p.pce_norm == p.pce_norm]
        if vp:
            ranked.append((cell, vp[-1].pce_norm, s.color))
    if ranked:
        ranked.sort(key=lambda r: r[1])
        fig, ax = plt.subplots(figsize=(7, max(3.0, 0.32 * len(ranked) + 1.2)))
        ax.barh([r[0] for r in ranked], [r[1] for r in ranked],
                color=[r[2] for r in ranked], alpha=0.9)
        for i, (_, v, _) in enumerate(ranked):
            ax.text(v + 0.4, i, f"{v:.1f}%", va="center", fontsize=7)
        ax.axvline(100, ls=":", lw=1, color="#64748b")
        ax.set_xlabel(t("归一化 PCE (%)", "Normalized PCE (%)"))
        ax.set_title(t("末点归一化 PCE 排名", "Final normalized PCE"))
        ax.grid(axis="x", alpha=0.25, lw=0.6)
        fig.tight_layout()
        save_fig(fig, out_dir / "fig_ranking.png")

    # --- 最近一轮 IV ---
    last_curves = [p for p in points if p.curve is not None and p.valid]
    if last_curves:
        last_round = max(p.round_id for p in last_curves)
        fig, ax = plt.subplots(figsize=(7, 5))
        for p in [q for q in last_curves if q.round_id == last_round]:
            V, I = p.curve
            ax.plot(V, I * 1000, lw=1.2, color=summaries[p.cell].color, label=p.cell)
        ax.set_xlabel(t("电压 (V)", "Voltage (V)"))
        ax.set_ylabel(t("电流 (µA)", "Current (µA)"))
        ax.set_title(t(f"最近一轮 IV 曲线（第 {last_round} 轮）", f"IV curves, round {last_round}"))
        ax.grid(alpha=0.25, lw=0.6)
        ax.legend(fontsize=7, ncol=2)
        fig.tight_layout()
        save_fig(fig, out_dir / "fig_iv_last.png")


def make_report(out_dir, data_dir, args, points, stats, round_infos, summaries,
                cells, t0, exclude, metrics, gsum):
    L = []
    L.append("=" * 78)
    L.append("稳定性测试数据处理报告")
    L.append("=" * 78)
    L.append(f"生成时间      : {datetime.now():%Y-%m-%d %H:%M:%S}")
    L.append(f"数据目录      : {data_dir}")
    L.append(f"MEAS 文件     : {stats['meas_files']}    STAT 文件: {stats['stat_files']}   "
             f"暂跳过(写入中/空): {stats['skipped']}   解析失败: {stats['bad_files']}")
    L.append(f"记录数        : {len(points)}   电池片: {len(cells)}   轮次: {len(round_infos)}")
    L.append(f"面积 / 光强   : {args.area} cm² / {args.irradiance:g} mW/cm² "
             f"({args.irradiance / DEFAULT_IRR:g} sun)   (脚本内重算，不用表头 Etac)")
    L.append(f"归一化基准    : {args.baseline}")
    if exclude:
        L.append(f"已剔除电池片  : {', '.join(sorted(exclude))}")
    L.append(f"数据跨度      : {points[0].ts:%Y-%m-%d %H:%M:%S} ~ {points[-1].ts:%Y-%m-%d %H:%M:%S}")

    # 轮次
    L.append("")
    L.append("-" * 78)
    L.append("轮次概览")
    L.append("-" * 78)
    L.append(" 轮 开始     结束     片数 有效  中位Isc(mA) 中位PCE(%) 光强")
    for r in round_infos:
        L.append(f"{r['round_id']:>3} {r['start']:%H:%M:%S}  {r['end']:%H:%M:%S}  "
                 f"{r['cells']:>4} {r['valid']:>4}  {fmt(r['median_isc'], 3):>11} "
                 f"{fmt(r['median_pce'], 3):>10} {'OK' if r['lamp_ok'] else '偏低!'}")
        r["_line"] = True

    # 每片
    L.append("")
    L.append("-" * 78)
    L.append("电池片指标（只统计 QC 通过的点）")
    L.append("-" * 78)
    L.append(" 片      组别          点 有效  PCE0(%) 末点(%)  末点归一  衰减率(%/h)  T80(h)  Voc0    Voc末    FF0    FF末")
    for cell in cells:
        s = summaries[cell]
        m = metrics.get(cell)
        if not m or m["n_valid"] == 0:
            L.append(f"{cell}  {group_short(s.group):<12} {len(s.points):>3} {0:>4}  —— 全部点未通过 QC："
                     f"{';'.join(sorted({f for p in s.points for f in p.flags if not f.startswith('__')}))}")
            continue
        vp = s.valid_points()
        last = m["last"]
        t80 = m["t80"]
        t80_txt = fmt(t80, 2) if t80 == t80 else ("未达" if len(m["series"]) >= 2 else "—")
        L.append(f"{cell}  {group_short(s.group):<12} {len(s.points):>3} {m['n_valid']:>4}  "
                 f"{fmt(s.base, 3):>7} {fmt(last.pce, 3):>7}  {fmt(last.pce_norm, 2):>7}%  "
                 f"{fmt(m['slope'], 2):>10}  {t80_txt:>6}  "
                 f"{fmt(vp[0].voc, 4):>6} {fmt(last.voc, 4):>6}  "
                 f"{fmt(vp[0].ff, 1):>5} {fmt(last.ff, 1):>5}")

    # 分组对比
    L.append("")
    L.append("-" * 78)
    L.append("分组对比（按通道分组：1-8 / 9-16）")
    L.append("-" * 78)
    for g in gsum:
        L.append(f"  {group_short(g['group']):<12} = {g['group']}（{', '.join(g['cells'])}）")
    L.append("")
    L.append(" 组别          片数/有效  PCE0中位  末点归一中位  归一范围       衰减率中位  T80中位  最佳/最差")
    for g in gsum:
        t80_txt = fmt(g["t80_median"], 2) if g["t80_median"] == g["t80_median"] else "未达"
        rng = (f"{fmt(g['norm_last_min'], 2)}~{fmt(g['norm_last_max'], 2)}%"
               if g["n_valid_cells"] else "—")
        tail = (f"{g['best_cell']}({fmt(metrics[g['best_cell']]['pce_norm_last'], 1)}%) / "
                f"{g['worst_cell']}({fmt(metrics[g['worst_cell']]['pce_norm_last'], 1)}%)"
                if g["n_valid_cells"] else "—")
        L.append(f" {group_short(g['group']):<12} {g['n_cells']:>2}/{g['n_valid_cells']:<2}      "
                 f"{fmt(g['pce0_median'], 3):>7}   {fmt(g['norm_last_median'], 2):>8}%   "
                 f"{rng:<16} {fmt(g['slope_median'], 2):>8}  {t80_txt:>6}  {tail}")
    if len(gsum) == 2 and all(g["n_valid_cells"] for g in gsum):
        a, b = gsum[0], gsum[1]
        d_pce = a["pce0_median"] - b["pce0_median"]
        d_norm = a["norm_last_median"] - b["norm_last_median"]
        L.append("")
        L.append(f"  初始效率差：{group_short(a['group'])} - {group_short(b['group'])} = {d_pce:+.3f}%（绝对值）")
        L.append(f"  末点归一化差：{group_short(a['group'])} - {group_short(b['group'])} = {d_norm:+.2f} 个百分点")
        if abs(d_norm) < 1.0:
            L.append("  两个体系衰减幅度当前无显著差异（< 1 个百分点），需继续跟踪。")

    # 末端稳定性
    win = args.tail_hours
    thr = args.stable_slope
    L.append("")
    L.append("-" * 78)
    L.append(f"末端稳定性：最后 {win:g} h 的线性斜率（%/h，判据 |斜率| ≤ {thr:g} %/h 视为稳定）")
    L.append("-" * 78)
    L.append(" 片     组别        PCE斜率   Isc斜率   Voc斜率   FF斜率    PCE稳定")
    stable_cnt = 0
    for cell in cells:
        m = metrics.get(cell)
        if not m or m["n_valid"] == 0:
            continue
        t = m["tail"]
        p_slope = t["pce_slope_tail"][0]
        ok = _is_stable(p_slope, thr)
        stable_cnt += int(ok)
        L.append(f"{cell}  {group_short(m['group']):<12}"
                 f"{fmt(p_slope, 3):>8}  {fmt(t['isc_slope_tail'][0], 3):>8}  "
                 f"{fmt(t['voc_slope_tail'][0], 3):>8}  {fmt(t['ff_slope_tail'][0], 3):>8}"
                 f"   {'是' if ok else '否'}")
    L.append("")
    for name in sorted({m['group'] for m in metrics.values() if m['n_valid']}):
        vals = [metrics[c]["tail"]["pce_slope_tail"][0] for c in cells
                if metrics.get(c) and metrics[c]["n_valid"] and metrics[c]["group"] == name]
        med = _median(vals)
        L.append(f"  {group_short(name):<12} PCE 斜率中位 {fmt(med, 3):>7} %/h  →  "
                 f"{'已达稳定' if _is_stable(med, thr) else '仍在衰减'}")
    L.append(f"  共 {stable_cnt}/{len([m for m in metrics.values() if m['n_valid']])} 片达到稳定判据。")

    # QC 汇总
    all_flags = {}
    for p in points:
        for f in p.flags:
            if not f.startswith("__"):
                all_flags[f] = all_flags.get(f, 0) + 1
    L.append("")
    L.append("-" * 78)
    L.append("QC 标记统计（每个点可能带多个标记）")
    L.append("-" * 78)
    if all_flags:
        for k, v in sorted(all_flags.items(), key=lambda kv: -kv[1]):
            L.append(f"  {k:<15} {v:>5} 点")
    else:
        L.append("  无异常")

    bad_cells = [c for c in cells if not summaries[c].valid_points()]
    if bad_cells:
        L.append("")
        L.append("  ⚠ 全程无有效点的电池片（疑似接触不良/短路）: " + ", ".join(bad_cells))

    # 阶段性结论
    finals = [(c, metrics[c]["pce_norm_last"]) for c in cells
              if metrics.get(c) and metrics[c]["n_valid"] > 0]
    if finals:
        vals = [v for _, v in finals]
        best = max(finals, key=lambda kv: kv[1])
        worst = min(finals, key=lambda kv: kv[1])
        L.append("")
        L.append("-" * 78)
        L.append("阶段性结论（仅基于当前有效点）")
        L.append("-" * 78)
        L.append(f"  可评估片数 {len(finals)} / {len(cells)}，末点归一化 PCE 中位 "
                 f"{float(np.median(vals)):.2f}%，范围 {worst[1]:.2f}%（{worst[0]}）"
                 f" ~ {best[1]:.2f}%（{best[0]}）。")
        up = [c for c, v in finals if v > 100.5]
        if up:
            L.append(f"  明显上升（通常是光浸泡/接触改善，不是衰减）: {', '.join(up)}")
        if any(v < 80 for v in vals):
            L.append("  已有片子跌破 80%，可参考 T80 列。")
        else:
            L.append("  全部片子仍在 80% 以上，T80 未达到，测试需要继续跑。")

    # 注意
    L.append("")
    L.append("-" * 78)
    L.append("读数须知")
    L.append("-" * 78)
    n_valid_rounds = sum(1 for r in round_infos if r["valid"] > 0)
    if n_valid_rounds < 8:
        L.append(f"  * 目前只有 {n_valid_rounds} 轮有效数据，T80/衰减率仅作粗估，"
                 f"不足以支撑正式衰减结论（建议 ≥ 20 轮或 ≥ 2 h）。")
    low = [r for r in round_infos if not r["lamp_ok"]]
    if low:
        L.append(f"  * 第 {', '.join(str(r['round_id']) for r in low)} 轮整体 Isc 偏低"
                 f"（比例 < {QC['lamp_low_ratio']:.0%} 全局中位），已标 LOW_LIGHT 且不参与归一化基准，"
                 f"请确认当时光源是否稳定/是否处于预热阶段。")
    if all_flags.get("HDR_MISMATCH"):
        L.append("  * 存在表头汇总值与 IV 曲线重算值不一致的点（HDR_MISMATCH），"
                 "说明软件记录的那一刻与曲线不是同一次扫描，需核对。")
    L.append("  * 原始文件 Irradiance/Area 写的是占位值 1.000，表头 Etac 因此放大 100 倍；"
             "本报告一律按 --area/--irradiance 重算。")
    L.append("  * 有效性判据：Voc < %.2f V 或 FF < %.0f%% 或 Isc < %.2f mA 即剔除。"
             % (QC["voc_min"], QC["ff_min"], QC["isc_min"]))
    L.append("")
    L.append("输出文件：points_all.csv / summary_cells.csv / summary_groups.csv / "
             "aligned_pce.csv / aligned_pce_norm.csv / aligned_group_mean.csv / "
             "rounds.csv / fig_pce.png / fig_group.png / fig_ranking.png / fig_iv_last.png")
    L.append("=" * 78)

    atomic_write(out_dir / "report.txt", "\n".join(L) + "\n")
    print("\n".join(L))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def parse_exclude(spec: str | None) -> set[str]:
    """'3,7,9' / '003;009' / 'none' -> {'C003','C007','C009'}。"""
    if spec is None:
        spec = DEFAULT_EXCLUDE
    if spec.strip().lower() in ("", "none", "off", "no"):
        return set()
    return {f"C{int(x):03d}" for x in re.findall(r"\d+", spec)}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="稳定性测试数据后处理（只读，可边测边跑）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--data", required=True, help="原始数据目录（含 MEAS/ STAT/）")
    ap.add_argument("--out", help="输出目录（默认 <数据目录>/REPORT）")
    ap.add_argument("--area", type=float, default=DEFAULT_AREA, help="单片面积 cm²")
    ap.add_argument("--suns", type=float, default=DEFAULT_SUNS,
                    help="名义光照强度，单位 sun（1 sun = 100 mW/cm²）")
    ap.add_argument("--irradiance", type=float, default=None,
                    help="直接给光强 mW/cm²，优先级高于 --suns")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help="归一化基准：first-round | firstN:3 | best")
    ap.add_argument("--tail-hours", type=float, default=DEFAULT_TAIL_HOURS,
                    help="末端斜率统计窗口，最后 N 小时")
    ap.add_argument("--stable-slope", type=float, default=DEFAULT_STABLE_SLOPE,
                    help="稳定判据：|末端斜率| 小于该值 (%%/h) 视为已稳定")
    ap.add_argument("--groups", help="分组 CSV（Group,Color,Channels），用于上色")
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE,
                    help="剔除的电池片号，如 3,7,9（默认 %(default)s；写 none 表示不剔除）")
    ap.add_argument("--round-gap", type=float, default=DEFAULT_ROUND_GAP,
                    help="轮次切分间隔 (s)")
    ap.add_argument("--voc-min", type=float, default=QC["voc_min"], help="Voc 有效下限，单位 V")
    ap.add_argument("--ff-min", type=float, default=QC["ff_min"], help="FF 有效下限，单位 %% ")
    ap.add_argument("--isc-min", type=float, default=QC["isc_min"], help="Isc 有效下限，单位 mA")
    ap.add_argument("--min-age", type=float, default=DEFAULT_MIN_AGE,
                    help="文件修改距今小于该秒数则跳过（防止读到半个文件）")
    ap.add_argument("--watch", type=float, default=0,
                    help=">0 时进入轮询模式，每 N 秒刷新一次")
    ap.add_argument("--no-plots", action="store_true", help="不生成图")
    return ap


def resolve_out_dir(data_dir: Path, args) -> Path:
    return (Path(args.out).expanduser() if args.out else data_dir / DEFAULT_REPORT_DIR).resolve()


def _pid_alive(pid: int) -> bool:
    """判断某个 PID 是否还活着（用来识别残留的锁文件）。"""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259            # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def acquire_watch_lock(out_dir: Path) -> Path | None:
    """watch 模式单实例：已有进程在跑就不重复启动，避免两份输出互相覆盖。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = out_dir / ".watch.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or 0)
        except OSError:
            pid = 0
        if _pid_alive(pid):
            print(f"[提示] 已有自动刷新进程在运行（PID {pid}），本次启动取消。")
            print(f"       如需重启，先结束该进程，或删除 {lock} 后重试。")
            return None
        print(f"[提示] 发现残留锁文件（PID {pid} 已不存在），已接管。")
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    QC["voc_min"], QC["ff_min"], QC["isc_min"] = args.voc_min, args.ff_min, args.isc_min
    # 光强口径：--irradiance 直接给 mW/cm²；否则按 --suns 换算（1 sun = 100 mW/cm²）
    if args.irradiance is None:
        args.irradiance = DEFAULT_IRR * args.suns
    args.suns = args.irradiance / DEFAULT_IRR
    if not (args.watch and args.watch > 0):
        return analyze(args)

    data_dir = Path(args.data).expanduser()
    lock = acquire_watch_lock(resolve_out_dir(data_dir, args))
    if lock is None:
        return 0
    print(f"轮询模式：每 {args.watch / 60:.0f} 分钟（{args.watch:.0f} s）刷新一次，"
          f"Ctrl+C 退出。（只读原始文件，不影响测试软件）", flush=True)
    try:
        while True:
            rc = analyze(args)
            if rc != 0:
                return rc
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\n已停止轮询。")
        return 0
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
