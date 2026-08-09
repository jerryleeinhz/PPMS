# SR 锁相双栅测试与参数修改指南

本文档说明当前 SR 后端测量的物理量、双栅二维电阻图的使用方法、测试时应修改的
TOML 参数，以及仓库内每个文件的职责。真实硬件运行前仍须完成
[`HARDWARE_VALIDATION_CHECKLIST.md`](HARDWARE_VALIDATION_CHECKLIST.md)。

## 1. SR830/SR865A 测到的是电压

SR830 和 SR865A 的输入在当前接线模型中都是交流电压输入。程序读取每台锁相的
`X`、`Y`、幅值和相位，单位为 V：

- SR830：程序标记为纵向 `xx`；同时使用其 `SINE OUT` 产生交流电压激励；
- SR865A：程序标记为横向 `xy`，外参考来自 SR830 激励链路；
- 两台锁相依次读取 1ω、2ω、3ω；确定普通线性电阻图时通常先使用 SR830 的
  `xx/1ω`。

如果锁相输入前接了电流前置放大器，锁相仍然读到电压，只是需要再除以前置放大器的
跨阻增益才能得到电流；当前配置和代码没有这个换算。

### 1.1 用交流小偏压得到电阻

可以，而且交流小偏压正是锁相测量的常见方式。当前程序把 SR830 的 `SINE OUT`
视为 RMS 电压，并用串联电阻估算 RMS 激励电流：

```text
I_est = V_source_rms / R_series
Rxx_in_phase = X_1w / I_est
Rxx_magnitude = sqrt(X_1w^2 + Y_1w^2) / I_est
```

RMS 电压除以 RMS 电流不需要额外的 `sqrt(2)`。例如当前默认值 `4 mVrms / 100 kΩ`
对应估算电流 `40 nArms`。

这里的 `I_est` 不是实测电流。当串联电阻远大于样品和电流回路中的其他阻抗时，
`V_source/R_series` 才是良好近似；否则需要按真实接线计算电流或增加参考电阻实测。
因此导出列刻意命名为 `x_over_drive_current_ohm` 等，而没有直接命名为绝对电阻。

两台 Keithley 2400 测到的电流是各自栅极的漏电流。`Vgate/Ileak` 是栅介质漏电相关量，
不是样品输运电阻。

## 2. 双栅的掺杂与垂直位移场

在常用的平板电容近似和一种常见符号约定下：

```text
n = [Cb (Vb - Vb0) + Ct (Vt - Vt0)] / e + n0
D = [Cb (Vb - Vb0) - Ct (Vt - Vt0)] / 2 + D0
```

其中 `Cb`、`Ct` 是单位面积栅电容，`Vb0`、`Vt0`、`n0`、`D0` 表示残余掺杂、
功函数差和陷阱电荷等造成的偏移。不同论文可能反转 `D` 的正方向，或报告
`D/epsilon_0`，因此必须先固定本器件的栅极编号、极性和单位。

这个关系给出两个容易混淆的方向：

- 电荷中性线 `n = 0`：在 `Vt`–`Vb` 图上通常为负斜率；很多器件中表现为高电阻脊；
- 零位移场线 `D = 0`：在上述约定下为正斜率；沿该线同时改变两个栅压可改变 `n`
  而保持平均垂直位移场为零。

所以二维电阻图可以提供选线所需的器件特征，但不能在没有电容比、零点偏移或已知
物理特征的情况下，把任意高电阻线自动认定为 `D = 0`。当前软件有意不自动推断：
先测 `grid`，再根据器件标定或图中特征人工选择两个端点，最后用 `paired` 沿该线测量。

双栅器件独立调节 `n` 和 `D` 以及用交流锁相加串联电阻测四端电阻的公开实例见：

- [Spin-orbit proximity in MoS2/bilayer graphene heterostructures](https://doi.org/10.1038/s41467-024-53324-z)：给出 `n`、`D` 定义，并说明使用约 31 Hz 锁相电压和 10/100 MΩ 串联电阻产生 1–50 nA 激励；
- [Higher order gaps in doubly aligned hBN/bilayer graphene](https://doi.org/10.1038/s41467-024-46672-3)：展示 `Vbg`–`Vtg` 二维 `Rxx` 图及 `n`、`D` 方向；
- [Electronic transport in dual-gated bilayer graphene at large displacement fields](https://doi.org/10.1103/PhysRevLett.105.166601)：研究电阻随载流子密度和位移场的变化；
- [Gate-induced insulating state in bilayer graphene devices](https://doi.org/10.1038/nmat2082) 和 [Direct observation of a widely tunable bandgap in bilayer graphene](https://doi.org/10.1038/nature08105)：双栅垂直电场控制的早期实验依据。

这些公式是通用测量思路，不是对当前样品 `Ct/Cb` 和零点偏移的标定值。

## 3. 推荐的两阶段测量

### 3.1 第一阶段：二维 `grid`

在 `config/hardware.local.toml` 的 `[gate_sweep]` 中设置：

```toml
mode = "grid"
start_top_gate_v = 0.0       # 改为样品批准的 Vg1 起点
stop_top_gate_v = 0.0        # 改为样品批准的 Vg1 终点
top_gate_points = 1
start_bottom_gate_v = 0.0    # 改为样品批准的 Vg2 起点
stop_bottom_gate_v = 0.0     # 改为样品批准的 Vg2 终点
bottom_gate_points = 1
source_voltage_v = 0.004     # SR830 SINE OUT，Vrms
frequency_hz = 17.777
```

程序按 Vg1 行、Vg2 蛇形扫描，减少换行时的栅压跳变。每次栅压移动都按
`gate_ramp_step_v` 分步，并在每一步和每个原始锁相样本检查漏电流。

### 3.2 第二阶段：沿选定直线 `paired`

从二维图或独立电容标定得到两个端点 `(Vg1_start, Vg2_start)` 和
`(Vg1_stop, Vg2_stop)` 后：

```toml
mode = "paired"
start_top_gate_v = 0.0       # Vg1_start
stop_top_gate_v = 0.0        # Vg1_stop
top_gate_points = 1          # 改为所需点数
start_bottom_gate_v = 0.0    # Vg2_start
stop_bottom_gate_v = 0.0     # Vg2_stop
bottom_gate_points = 1       # 必须与 top_gate_points 相等
```

`paired` 对两个端点分别线性插值，使 Vg1、Vg2 在每个测量点同步改变。任何配置修改
都会改变配置哈希，因此真实运行前必须重新执行只读诊断，不能复用旧诊断 `run_id`。

## 4. 测试时主要修改哪些参数

只修改本地 `config/hardware.local.toml`。不要把真实 VISA 地址、样品名和实际安全限制
提交到 `hardware.example.toml`；`*.local.toml` 已被 Git 忽略。

| TOML 位置 | 参数 | 含义/单位 |
| --- | --- | --- |
| `[runtime]` | `sample_name` | 样品名称；必须替换占位符 |
| `[connections]` | `sr830_address`、`sr865a_address` | 两台锁相的 VISA 地址 |
| `[connections]` | `gate_top_address`、`gate_bottom_address` | 两台 Keithley 2400 的 VISA 地址 |
| `[connections]` | `ppms_host`、`ppms_port` | MultiPyVu Server 地址和端口 |
| `[instruments]` | `series_resistance_ohm` | 交流激励回路串联电阻；决定 `I_est` |
| `[safety]` | `source_voltage_min_v`、`source_voltage_max_v` | 软件允许的 SR830 RMS 电压边界 |
| `[safety]` | `source_safe_idle_voltage_v` | 每次采样后退回的 RMS 幅值；SR830 通常不能设为真 0 V |
| `[safety]` | `estimated_current_limit_a` | `V_source/R_series` 的软件上限 |
| `[safety]` | `gate_voltage_limit_v` | 两个栅极绝对电压软件上限 |
| `[safety]` | `gate_compliance_limit_a` | 程序允许写入 Keithley 的最大硬件限流值 |
| `[safety]` | `gate_leakage_limit_a` | 软件主动终止阈值；必须严格小于 compliance |
| `[safety]` | `gate_temperature_limit_k` | 高于此温度拒绝开启栅极输出 |
| `[acquisition]` | `averages` | 每个谐波的原始平均次数 |
| `[acquisition]` | `settle_s` | 每次切换谐波后的等待时间 |
| `[acquisition]` | `sample_interval_s` | 同一谐波重复样本之间的间隔 |
| `[acquisition]` | `noise_limit_v` | X/Y 标准差质量阈值 |
| `[gate_sweep]` | `mode` | `grid` 二维图或 `paired` 配对直线 |
| `[gate_sweep]` | `start/stop_top_gate_v` | Vg1 扫描起止值，V |
| `[gate_sweep]` | `top_gate_points` | Vg1 点数 |
| `[gate_sweep]` | `start/stop_bottom_gate_v` | Vg2 扫描起止值，V |
| `[gate_sweep]` | `bottom_gate_points` | Vg2 点数 |
| `[gate_sweep]` | `source_voltage_v` | SR830 交流小偏压，Vrms |
| `[gate_sweep]` | `frequency_hz` | 交流激励/参考频率，Hz |
| `[gate_sweep]` | `target_temperature_k`、`target_field_t` | 固定温度和磁场 |
| `[gate_sweep]` | `gate_ramp_step_v` | 栅压斜坡的最大单步，V |
| `[gate_sweep]` | `gate_ramp_step_delay_s` | 栅压斜坡每步间隔，s |
| `[gate_sweep]` | `gate_settle_s` | 到达一对栅压后、锁相采样前的等待时间，s |
| `[data]` | `database_path` | SQLite 原始数据路径；真实实验应使用本地非同步磁盘 |

`[voltage_sweep]`、`[frequency_sweep]`、`[field_sweep]` 和
`[temperature_field_sweep]` 分别用于其他扫描协议；做 Vg1–Vg2 图时只使用
`[gate_sweep]`，但整份 TOML 仍必须包含并通过所有字段的严格校验。

## 5. 数据记录与二维图字段

SQLite 是原始数据源：

- `runs`：完整配置、配置哈希、Station snapshot、起止时间和状态；
- `events`：诊断、授权、异常和安全清理事件；
- `attempts`：每个条件的接受/拒绝结果和旧版摘要；
- `instrument_samples`：每个原始样本的两台锁相、两台 SMU 和 PPMS 状态；
- `transport_readings`：每个信号/谐波的后端无关长表。

二维普通电阻图建议从 `export-transport-summary` 结果筛选：

```text
backend = sr_lockin
signal = xx
instrument_channel = sr830
harmonic = 1
x axis = gate_bottom_voltage_v_mean
y axis = gate_top_voltage_v_mean
color = x_over_drive_current_ohm
```

只有在锁相相位已调好时，`X/I_est` 才可直接解释为有符号同相电阻。初次检查也可画
`amplitude_over_drive_current_ohm`，但它是非负幅值，会隐藏相位和符号问题。
`gate_*_measured_current_a` 应单独画成栅漏电流图。

原始状态 CSV 包含 SR 的 X/Y、频率、谐波、锁定/过载；两台 Keithley 的栅压、输出、
compliance、漏电流和电流可用标志；PPMS 的温度、磁场、腔体、稳定状态和可选样品角度。

## 6. 推荐测试顺序和命令

1. 复制 `config/hardware.example.toml` 为被 Git 忽略的 `config/hardware.local.toml`。
2. 填写样品名和 VISA 地址，先保持两个栅极均为 0 V、各 1 点。
3. 仿真测试 `grid`；再把仿真配置改为 `paired` 并使用相同点数测试直线。
4. 在 PPMS 电脑执行配置校验和只读诊断。
5. 按硬件检查清单依次验证 0 V、单栅小步、双栅小范围和漏电终止，最后才扩大范围。
6. 导出原始状态和输运汇总，检查漏电图后再看 `xx/1ω` 电阻图。

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-gate config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config config\hardware.local.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control diagnose-hardware config\hardware.local.toml
```

得到诊断 `run_id` 后，真实运行必须明确授权：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control run-hardware-gate config\hardware.local.toml `
  --diagnostic-run-id '<DIAGNOSTIC_RUN_ID>' `
  --confirm 'I CONFIRM REAL HARDWARE CONTROL'
```

控制命令开始后，在第二个PowerShell窗口只读查看最近已提交状态：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control monitor-run `
  'C:\PPMS_Data\ppms_control.sqlite' --latest-running
```

重点观察两台Keithley漏电/输出/compliance设定、SR锁定/过载、栅压读回、温场稳定状态和
最新`xx/1ω`。监视器不连接仪器；栅压ramp期间可能暂时没有新SQLite样本。

运行结束后使用打印出的测量 `RUN_ID`：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control export-samples `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' 'C:\PPMS_Data\instrument_samples.csv'
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control export-transport-summary `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' 'C:\PPMS_Data\transport_summary.csv'
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control plot-data `
  'C:\PPMS_Data\ppms_control.sqlite' 'C:\PPMS_Data\figures\<RUN_ID>' `
  --run-id '<RUN_ID>'
```

`plot-data`会生成电阻二维图、漏电图和manifest；只有提供独立标定的
`config/gate_calibration.local.toml`时才增加`R(n,D)`。完整图形、公式和跳过规则见
`docs/DATA_ANALYSIS.md`。

需要交互修改筛选和图形时，打开`notebooks/transport_analysis.ipynb`；完整CLI操作步骤见
`docs/OPERATING_WORKFLOW.md`。

遇到栅漏电接近阈值、锁相过载/失锁、栅压读回偏差、温场不稳或接线不确定时停止扩大
范围。软件联锁是最后一道辅助保护，不能替代 Keithley 前面板 compliance、串联保护电阻、
正确接地和现场监护。

## 7. 仓库文件职责速查

### 根目录与配置/文档

| 文件 | 用途 |
| --- | --- |
| `README.md` | 安装、命令入口和当前能力总览 |
| `pyproject.toml` | Python 版本、依赖、包发现和 `ppms-control` 命令入口 |
| `.gitignore` | 忽略运行数据库、本地硬件配置、缓存和临时文件 |
| `config/simulation.toml` | 可直接运行的安全仿真参数 |
| `config/hardware.example.toml` | 真实硬件配置模板；不要直接填入本机敏感/样品参数 |
| `config/gate_calibration.example.toml` | 双栅`n-D`坐标的独立标定模板 |
| `docs/DATA_ANALYSIS.md` | 数据绘图命令、论文图映射、公式和质量边界 |
| `docs/OPERATING_WORKFLOW.md` | 扫描、实时监视、导出和Notebook操作方法 |
| `docs/DESIGN_GOALS.md` | 架构、目录、安全边界和设计目标 |
| `docs/PROJECT_HANDOFF.md` | 两个后端的结论、进度、限制和下一阶段 |
| `docs/HARDWARE_DIAGNOSTICS.md` | 只读 VISA/MultiPyVu 诊断步骤 |
| `docs/HARDWARE_VALIDATION_CHECKLIST.md` | 分阶段真实硬件验证记录表 |
| `docs/SR_DUAL_GATE_TEST_GUIDE.md` | 本文：SR 电阻、双栅测试、参数和文件速查 |

`run_data/` 和 `.test-tmp/` 是被 Git 忽略的运行/测试临时目录，不是源代码。

### `src/ppms_control/`

| 文件 | 用途 |
| --- | --- |
| `__init__.py` | 包标识和版本 |
| `__main__.py` | 支持 `python -m ppms_control` |
| `cli.py` | 所有校验、仿真、诊断、硬件运行、ETO 和 CSV 命令 |
| `config.py` | TOML 数据类、字段类型和跨字段安全校验 |
| `models.py` | 测量条件、锁相读数、栅极/PPMS 状态和公共输运模型 |
| `instruments.py` | 仪器协议、QCoDeS 仿真仪器和仿真 Station |
| `real_instruments.py` | SR830/SR865A、Keithley 2400、MultiPyVu 真实适配器 |
| `safety.py` | 激励、温场、双栅斜坡、漏电核验和 fail-closed 清理 |
| `protocols.py` | 五种 SR 扫描条件、蛇形 grid、paired 直线和断点续跑 |
| `acquisition.py` | 1ω/2ω/3ω 采样、状态联读、平均、质量标记和重试 |
| `store.py` | SQLite 表、事务、事件、检查点和 CSV 导出 |
| `analysis.py` | 公共输运长表的平均、圆相位统计和电阻比例列 |
| `plotting.py` | SQLite/ETO只读绘图、拟合、manifest与双栅坐标图 |
| `monitoring.py` | SQLite/WAL只读实时状态、进度和警告面板 |
| `authorization.py` | 真实控制确认词、诊断身份和配置哈希授权 |
| `diagnostics.py` | 只读探测 VISA 标识、锁相参考、SMU/PPMS 状态 |
| `hardware_run.py` | 真实扫描的授权、连接、运行结果和安全收尾 |
| `eto_data.py` | MultiVu ETO 1.2 `.dat` 严格解析与标准化 |
| `eto_follow.py` | 增量跟随增长中的 ETO 文件并原子保存检查点 |
| `ole_inspection.py` | 只读列出活动 MultiVu COM/OLE 对象的方法 |

### `tests/`

| 文件 | 主要覆盖内容 |
| --- | --- |
| `test_config.py` | 严格字段、限值、grid/paired 配置校验 |
| `test_safety.py` | 激励、温场、栅压、漏电和失败清理 |
| `test_real_instruments.py` | 真实适配器在假驱动上的命令/读回行为 |
| `test_integration.py` | 端到端仿真、持久化、恢复和双栅扫描 |
| `test_hardware_run.py` | 真实运行编排、授权和失败路径 |
| `test_diagnostics.py` | 只读硬件诊断和失败报告 |
| `test_authorization.py` | 配置哈希、确认词和诊断 `run_id` |
| `test_cli.py` | 命令参数、输出和各协议入口 |
| `test_analysis.py` | 输运汇总、电阻比例和相位统计 |
| `test_eto_data.py` | ETO 格式、单位换算、增量读取和公共长表 |
| `test_ole_inspection.py` | MultiVu OLE 方法只读枚举 |
| `test_plotting.py` | 论文/Notebook绘图、双栅图和标定严格性 |
| `test_monitoring.py` | 只读并发监视、状态警告和ETO模式 |
| `test_notebook.py` | Notebook语法、清空输出和安全边界 |
| `tests/__init__.py` | 测试包标识 |

测试只验证软件逻辑，不会证明真实接线、样品安全范围、电容比或电阻绝对标定正确。
