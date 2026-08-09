# PPMS 数据分析与论文图生成

更新日期：2026-08-09

本模块把项目 SQLite 长表、MultiVu ETO 1.2 `.dat` 文件或包含多个 `.dat` 的目录转成
统一分析记录，并按输入中实际存在的扫描维度生成图、拟合表和可追溯清单。图形需求综合自：

- `CrSBr_nonlinear_Hall_simple.ipynb` 的电流、频率、温度、磁场和谐波分析；
- *Quantum-Geometry-Induced Anomalous Chiral Transport and Hidden Symmetry Breaking in
  Centrosymmetric 2M-WS2*（PRL 137, 016302, 2026）的输运图类型。

代码不会仅为了补齐论文版式而推断未测量的物理量。每个不能生成的图都会在
`analysis_manifest.json` 中以 `skipped` 和具体原因记录。

## 1. 安装分析依赖

绘图是可选功能；在项目根目录安装 `analysis` extra：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install -e '.[analysis]'
```

## 2. 命令入口

### 2.1 从项目 SQLite 运行生成

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control plot-data `
  'C:\PPMS_Data\ppms_control.sqlite' 'C:\PPMS_Data\figures\RUN_ID' `
  --run-id '<RUN_ID>'
```

SQLite 以只读模式打开。`--run-id` 必须显式给出，避免把不同实验混在一起。

### 2.2 从 ETO 文件或目录生成

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control plot-data `
  'C:\path\to\ETO_data_directory' 'C:\PPMS_Data\figures\ETO_run' `
  --channel-1-role xy --channel-2-role xx
```

目录模式递归读取所有 `.dat`，但每条曲线保留原文件标签。Ch1/Ch2 的角色不能自动猜测；
上例只适用于已经确认的 CrSBr 接线。

默认同时输出 PNG 和 PDF。只需要一种格式时使用 `--format png` 或 `--format pdf`；参数可
重复两次。原始数据库和 ETO 文件不会被修改。

### 2.3 双栅 `n-D` 坐标

先复制并填写独立标定值：

```powershell
Copy-Item 'config\gate_calibration.example.toml' 'config\gate_calibration.local.toml'
```

再运行：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control plot-data `
  'C:\PPMS_Data\ppms_control.sqlite' 'C:\PPMS_Data\figures\RUN_ID' `
  --run-id '<RUN_ID>' --gate-calibration 'config\gate_calibration.local.toml'
```

采用的显式约定为：

```text
n = [Ct(Vt - Vt0) + Cb(Vb - Vb0)] / e + n0
D = [Cb(Vb - Vb0) - Ct(Vt - Vt0)] / 2 + D0
```

电容面密度、偏置和符号约定必须来自器件几何或独立标定。没有标定文件时仍会生成
`R(Vbottom,Vtop)`，但不会生成或宣称 `n-D` 图。

## 3. 生成文件

| 文件 | 内容 |
| --- | --- |
| `analysis_manifest.json` | 输入来源、通道映射、图的生成/跳过状态、所需字段、限制和 compliance 警告 |
| `analysis_records.csv` | SQLite 或 ETO 归一化后的分析长表；每行一个信号/谐波 |
| `fit_summary.csv` | 电流幂律拟合、线性基线等拟合参数；没有可拟合序列时不创建且 manifest 中为 `null` |
| `<figure_key>.png/.pdf` | 实际生成的图；格式由命令参数决定 |

分析时应先看 manifest，再看图。`skipped` 表示输入不满足该图的最低数据条件，不是程序
静默失败。

## 4. 图形清单

### 4.1 CrSBr Notebook 需求

| 图键 | 分析内容 | 最低输入 |
| --- | --- | --- |
| `current_response` | `V1ω` 与 `V2ω/V3ω` 随交流电流 | 至少两个电流点 |
| `harmonic_ratio_db` | ETO 保存的 `2ω/1ω`、`3ω/1ω` dB 比值 | `ratio_db` 与电流 |
| `harmonic_scaling` | `|V2ω|` 对 `I²`、`|V3ω|` 对 `I³`，含线性拟合与 `R²` | 非零电流与对应谐波 |
| `current_nonlinearity` | 基波减去线性电流基线后的残差 | 至少四个电流点 |
| `frequency_response` | 基波/高次谐波幅值及基波相位随频率 | 至少两个频率点 |
| `frequency_normalized_harmonics` | `|V2ω|/I²`、`|V3ω|/I³` 随频率 | 频率、非零电流和高次谐波 |
| `temperature_dependence` | `Vxx/Vxy` 的 `1ω/2ω/3ω` 温度曲线 | 同条件温度跨度至少 0.01 K |
| `field_dependence` | `Vxx/Vxy` 的 `1ω/2ω/3ω` 场扫；保留采集顺序 | 至少两个磁场点 |

`harmonic_scaling` 的拟合结果写入 `fit_summary.csv`，便于比较不同电流区间。当前版本提供
全区间拟合；如果需要指定低/高电流分界，应在实验记录中给出物理分界值后再增加可配置
分段拟合，不能由程序任意选择转折点。

### 4.2 2M-WS2 论文图需求

| 论文图 | 本项目图键 | 当前状态与边界 |
| --- | --- | --- |
| Fig. 2(d–g) | `field_dependence` | 可画 `V1ω/V2ω(B)`；反接电极比较需额外几何/方向元数据 |
| Fig. 2(h,i) | `current_response`, `harmonic_scaling` | 可画 `V1ω(I)`、`V2ω(I²)` 及拟合 |
| Fig. 3(a–c) | `angle_dependence` | 需要至少三个真实 `sample_position_deg` 点 |
| Fig. 3(d) | `temperature_dependence` | 可按固定场/电流/频率分组 |
| Fig. 3(e) | `field_dependence` | 可按温度分组 |
| Fig. 3(f) | `magnetochiral_gamma` | `gamma = 2 V2ω / (V1ω |B| |I|)`；排除近零场 |
| Fig. 4(a) | `transport_phase_overview` | 只生成 `ΔV2ω` 与 `gamma` 的输运子图 |
| Fig. 4(b) | `temperature_field_v2_over_b_map` | 同一源文件/运行中必须有真实二维 `T-B` 覆盖；排除近零场 |
| Fig. 4(c) | `nernst_temperature_field_map` | 当前明确跳过：没有热梯度与 Nernst 标定 |

论文 Fig. 4(a) 中的散射率与 Hall coefficient 也会明确记录为跳过。`Vxy` 原始电压本身
不足以得到 Hall coefficient；至少还需要样品几何、反对称化和提取协议。

### 4.3 双栅器件需求

| 图键 | 内容 |
| --- | --- |
| `gate_resistance_map` | `Rxx = X1ω / Idrive` 的 `Vbottom-Vtop` 二维图 |
| `gate_leakage` | 两台 Keithley 的绝对漏电流二维图或按原始样本顺序的轨迹 |
| `paired_gate_linecut` | 已确定 Vg1/Vg2 轨迹上的电阻与两栅压同步变化 |
| `n_d_resistance_map` | 使用显式电容/偏置标定转换的 `R(n,D)` |

栅漏电流来自 Keithley 读回，不是样品输运电流。二维电阻特征可帮助选择候选轨迹，但软件
不会仅凭电阻图把某条线自动命名为零电场线。

## 5. 数据解释与质量边界

- 有锁相 `X` 时优先使用带符号的 `X`；否则使用无符号幅值。
- 当前 ETO 1.2 的 `2ω/3ω` 是由 dB 比值换算的无符号幅值，不能恢复正负号或相位。
- SR 后端的 `drive_current_a` 是 SR830 输出除以串联电阻的明确标注估算值；未经校准不能
  当成样品实测电流。
- `Rxx` 使用锁相电压除以 `drive_current_a`，不是 Keithley 栅压除以栅漏电流。
- 包含 compliance 评论或质量标记时，所有派生图右下角加红色警告，manifest 同时记录。
  被钳位/过量程的数据仍保留用于审计，但不得默认用于物理结论。
- 温度趋势要求至少 0.01 K 的跨度，防止把 PPMS 稳定波动误判为温度扫描。
- `T-B` 等高图只使用单个源文件或单次 SQLite run 内的真实二维覆盖，不拼接互不对应的
  独立温扫与场扫来制造二维图。
- 图显示测得的相关性，不单独证明磁手性、量子几何或隐藏对称破缺机制。

## 6. 代码位置

| 文件 | 作用 |
| --- | --- |
| `src/ppms_control/plotting.py` | 只读数据载入、物理量转换、分组、拟合、图和 manifest |
| `src/ppms_control/cli.py` | `plot-data` 命令参数和输入类型选择 |
| `config/gate_calibration.example.toml` | `n-D` 坐标的严格标定模板 |
| `tests/test_plotting.py` | 标定拒绝规则、论文/Notebook图套件和双栅线扫测试 |
