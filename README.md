# stabilty-data

有机太阳能电池（OPV）稳定性测试数据仓库。原始采集数据经统一脚本处理后存档，便于复现与对比。

## 目录

```
stabilty-data/
├── indoor/       室内恒定光照稳定性测试（ISOS-L 类）
└── outdoor/      室外自然光稳定性测试（暂无数据）
```

## 通用说明

### 测试体系命名

| 简称 | 全名 | 说明 |
|------|------|------|
| MoOx/Ag | ZnO/D18:L8/MoOx/Ag | 空穴传输层 MoOx，Ag 电极 |
| PEDOT-F/Ag | ZnO/D18:L8/PEDOT-F/Ag | 空穴传输层 PEDOT-F，Ag 电极 |

### 参数口径（重要）

原始采集文件的表头里 `Irradiance` / `Cell-Area` 是占位值 `1.000`，导致软件自带的 `Etac(%)` 被放大约 100 倍，**不可直接引用**。本仓库所有效率均为从 IV 曲线重算：

```
Isc  = |I(V=0)|                       Pmax = max(-V·I)
Voc  = V(I=0)                         FF   = Pmax / (Isc·Voc)
Jsc  = Isc / Area (mA/cm²)            PCE  = Pmax / (Area · Irr) × 100 %
```

- 单片受光面积 **Area = 0.1 cm²**
- 名义光强 **0.55 sun = 55 mW/cm²**（`indoor/` 全部数据按此口径）
- 归一化基准：每片**第一轮光强正常**的测量点（PCE/Isc/Voc/FF 四项共用同一点）

## 文件说明（每个测试目录内）

| 文件 | 内容 |
|------|------|
| `report.txt` | 完整文字报告：参数、轮次表、每片指标、分组对比、末端稳定性、QC 警告 |
| `summary_cells.csv` | 每片一行：初始/末态四项参数、归一化、衰减率、T80、末端斜率、稳定标记 |
| `summary_groups.csv` | 每组一行：片中位效率、末点归一化中位与范围、衰减率中位 |
| `points_all.csv` | 长表，每次测量一行（原始量 + 重算指标 + 有效性标记） |
| `rounds.csv` | 逐轮汇总：中位 Isc/PCE、有效片数、光源是否异常、是否测完 |
| `aligned_pce.csv` | 宽表，行=时间、列=电池片（PCE %），可直接进 Origin |
| `aligned_{pce,isc,voc,ff}_norm.csv` | 同上，四项参数的归一化值（%） |
| `aligned_group_mean.csv` | 按轮对齐的分组均值（含参与片数） |
| `fig_pce.png` | 4×3 主图：左=原始值，中=归一化，右=末端斜率（判断是否稳定） |
| `fig_group.png` | 分组对比（均值 ± SD） |
| `fig_ranking.png` | 末点归一化 PCE 排名 |
| `fig_iv_last.png` | 最近一轮 IV 曲线 |
| `stability_report.py` | 数据处理脚本（只读原始数据，输出上表全部文件） |

## 数据处理脚本

`indoor/stability_report.py`，纯 Python（numpy / matplotlib / pandas 可选）。用法见 `indoor/处理脚本说明.md`。

```bash
python stability_report.py --data "D:\zlc2026年11月4日" --suns 0.55 --area 0.1
```

关键行为：

- 原始目录只读，不加文件锁，测试软件运行期间可随时执行
- 从 IV 曲线重算全部参数，不采信表头 `Etac`
- 自动标记并剔除异常点：`VOC_LOW` / `FF_LOW` / `ISC_LOW` / `PMAX_LOW` / `LOW_LIGHT` / `HDR_MISMATCH`
- 片数不足正常轮 80% 的轮次（正在测的最后一轮）不参与分组均值，避免假跳变
- 末端斜率默认取**最后 1 h**（`--tail-hours`），并在尾窗内相邻轮中位 Isc 变化 ≥ 1.5% 时给出「光强跳变」告警，避免把光源变化误判成器件回升/衰减

## 一键刷新 + 推送

仓库根目录的 `publish_indoor.bat`：双击即完成「重新分析 → 复制到 indoor/ → 提交 → 推送」。

```bat
rem 顶部可改的配置
set "DATA_DIR=D:\zlc2026年11月4日"      原始数据目录（含 MEAS/ 与 STAT/）
set "SUNS=0.55"                          名义光强
set "AREA=0.1"                           单片面积 cm²
set "TAIL_HOURS=1"                       末端斜率窗口（小时）
set "STABLE_SLOPE=0.1"                   稳定判据 %/h
set "EXCLUDE=3,7,9"                      固定剔除的通道
set "SCRIPT_SRC=...	ools\stability_report.py"   源脚本（存在则同步到 indoor/）
```

- 运行后控制台会打印两个体系的 `PCE0` 与末点归一化值
- 数据无变化时自动跳过提交；推送失败时本地提交会保留，下次运行自动补推
- 批处理为 GBK 编码、输出全 ASCII，避免中文路径/控制台代码页导致乱码
