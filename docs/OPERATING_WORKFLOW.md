# PPMS 扫描、实时监视与 Notebook 分析操作手册

更新日期：2026-08-19

本文面向第一次使用本项目 CLI 的实验操作员。完整工作流为：

```text
修改 TOML
    ↓
PowerShell 窗口 1：校验、诊断、仿真或授权真实扫描
    ↓                       ↘
SQLite / ETO 数据            PowerShell 窗口 2：monitor-run 只读监视
    ↓
Jupyter Notebook：质量检查、交互筛选和改图
    ↓
plot-data CLI：可重复地批量生成最终 PNG/PDF/CSV/manifest
```

真实硬件控制只在 PowerShell CLI 中进行。Notebook 不连接仪器，`monitor-run` 也只读取
SQLite；它们不会形成第二个 VISA/MultiPyVu 控制连接。

## 1. CLI 和 PowerShell 是什么

CLI 是 command-line interface，即在 PowerShell 中输入一条命令，让程序按 TOML 中的
全部点完成一次任务。例如：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-gate `
  config\simulation.toml
```

含义分别是：

- `& '...python.exe'`：使用指定 Python；
- `-m ppms_control`：运行本项目；
- `simulate-gate`：选择双栅仿真命令；
- `config\simulation.toml`：使用哪一份参数。

PowerShell 行尾反引号 `` ` `` 表示下一行仍属于同一条命令。也可以把整条命令写成一行。
文档中的 `<RUN_ID>`、`<DIAGNOSTIC_RUN_ID>` 是占位符，使用时连尖括号一起替换。

## 2. 第一次安装和进入目录

本节命令适用于已经具有项目目录和可联网Python环境的开发电脑。首次在完全离线的PPMS
电脑部署时，不要直接复制现有虚拟环境，也不要在那里运行在线`pip install -e`。应先在
联网的64位Windows/Python 3.12电脑制作完整wheelhouse和源码快照，再使用U盘转移；逐条命令、
校验和、最小/完整安装选择及验证顺序见
[OFFLINE_INSTALLATION.md](OFFLINE_INSTALLATION.md)。

打开 PowerShell，进入项目目录：

```powershell
Set-Location 'C:\Users\liy56\OneDrive - Aalto University\Aalto University\Work\Experiment operation\PPMS\ppms_qcodes_control'
```

安装项目、绘图和 Notebook 入口：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install -e '.[analysis,notebook]'
```

`-e` 是可编辑安装；代码在原目录修改后无需重复复制。换电脑、换环境或移动仓库后需要重新
安装。真实 PPMS 电脑还需要：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install -e '.[analysis,notebook,real-ppms]'
```

查看全部入口：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control --help
```

## 3. 配置文件

| 文件 | 使用场景 |
| --- | --- |
| `config/simulation.toml` | 安全仿真；可直接运行 |
| `config/hardware.example.toml` | 真实硬件模板，不直接运行 |
| `config/hardware.local.toml` | 从模板复制后填写的本机配置；被 Git 忽略 |
| `config/gate_calibration.example.toml` | 双栅 `n-D` 标定模板，不代表真实器件数值 |
| `config/gate_calibration.local.toml` | 独立标定后填写；被 Git 忽略 |

创建本地硬件配置：

```powershell
Copy-Item 'config\hardware.example.toml' 'config\hardware.local.toml'
```

至少需要人工确认样品名、GPIB/VISA 地址、PPMS Server、SR830 激励范围、串联电阻、温场
范围、Keithley 极性、硬件 compliance、软件漏电终止值和双栅范围。

## 4. 先做仿真

每次修改配置后先校验：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config `
  config\simulation.toml
```

选择一个扫描命令：

| 测量 | 仿真命令 | 扫描参数位置 |
| --- | --- | --- |
| SR830 输出电压，即估算电流扫描 | `simulate` | `[voltage_sweep]` |
| 频率扫描 | `simulate-frequency` | `[frequency_sweep]` |
| 磁场扫描 | `simulate-field` | `[field_sweep]` |
| 温度—磁场网格 | `simulate-temperature-field` | `[temperature_field_sweep]` |
| 双栅 grid 或 paired 扫描 | `simulate-gate` | `[gate_sweep]` |

例如：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-field `
  config\simulation.toml
```

命令会按配置自动遍历全部点，并逐样本写 SQLite。“批量”指这一过程，不是 Windows
`.bat` 文件。

## 5. 真实硬件：校验、只读诊断、授权运行

### 5.1 校验配置

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config `
  config\hardware.local.toml
```

### 5.2 只读诊断

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control diagnose-hardware `
  config\hardware.local.toml
```

诊断不发送设置命令。成功输出包含：

```json
{
  "database": "C:\\PPMS_Data\\ppms_control.sqlite",
  "run_id": "诊断运行的 UUID",
  "success": true
}
```

复制这个诊断 `run_id`。只要 TOML 有任何变化，就必须重新诊断。

### 5.3 授权真实扫描

| 测量 | 真实硬件命令 |
| --- | --- |
| SR830 输出电压/估算电流扫描 | `run-hardware` |
| 频率扫描 | `run-hardware-frequency` |
| 磁场扫描 | `run-hardware-field` |
| 温度—磁场网格 | `run-hardware-temperature-field` |
| 双栅 grid/paired | `run-hardware-gate` |

例如双栅：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control run-hardware-gate `
  config\hardware.local.toml `
  --diagnostic-run-id '<DIAGNOSTIC_RUN_ID>' `
  --confirm 'I CONFIRM REAL HARDWARE CONTROL'
```

确认词必须完全一致。控制窗口保持打开；不要在第二个程序中重新连接同一组 GPIB/VISA
仪器。

## 6. 第二个 PowerShell 窗口实时查看状态

控制命令开始后，打开第二个 PowerShell，进入同一项目目录并运行：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control monitor-run `
  'C:\PPMS_Data\ppms_control.sqlite' `
  --latest-running
```

`--latest-running`只在启动时选择最新的运行中 run，随后锁定该 run，不会跳到其他任务。
也可以指定已知测量 `run_id`：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control monitor-run `
  'C:\PPMS_Data\ppms_control.sqlite' `
  --run-id '<RUN_ID>'
```

只看一屏后退出：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control monitor-run `
  'C:\PPMS_Data\ppms_control.sqlite' `
  --run-id '<RUN_ID>' --once
```

修改刷新间隔：`--refresh-s 2`。必须大于0。

实时面板显示最近一次**已提交**的：

- run状态、条件进度、attempt、原始样本数和输运行数；
- SR830/SR865A的谐波、X/Y/R、频率、锁定和过载；
- SR830输出设定/读回与明确标注的估算电流；
- 两台Keithley的栅压、输出、compliance设定值、漏电及可用状态；
- PPMS温度、磁场、状态、稳定性、腔体、角度；
- 每个通道的`xx/xy`、`1ω/2ω/3ω`最新输运值；有符号`1ω X`除以非零驱动电流显示
  `R_X=X/I`，只有幅值时仅显示阻抗幅值`|Z|=|V|/I`，不把它称为有符号电阻；
- 失锁、过载、漏电超软件限值、温场不稳、设置/读回超容差、attempt错误及质量标记。

数据库使用 WAL，控制程序每条原始样本都会提交，因此监视器能并发只读。它显示的不是
第二次直接仪器查询：在 PPMS 温场稳定等待或双栅 ramp 期间可能暂时没有新样本，应结合控制
窗口和 MultiVu 判断，不能仅凭更新时间宣称程序卡死。

ETO `follow-eto-data` 没有 SR/SMU 原始状态是正常的；面板会显示 ETO 最新输运行与 checkpoint。
其中 `Last data age` 表示最近一条测量数据的年龄，`heartbeat age` 只表示文件跟随器最近更新了
checkpoint；后者很新并不等于仪器刚产生了新数据。

在监视窗口按 `Ctrl+C` 只停止监视，不停止测量。在控制窗口按 `Ctrl+C` 才会中断控制任务并
进入安全清理。

## 7. 扫描完成后导出和批量绘图

真实或仿真命令结束时会输出测量 `run_id`。用它导出：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control export-samples `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' `
  'C:\PPMS_Data\exports\instrument_samples.csv'

& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control export-transport-summary `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' `
  'C:\PPMS_Data\exports\transport_summary.csv'
```

批量生成标准图：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control plot-data `
  'C:\PPMS_Data\ppms_control.sqlite' 'C:\PPMS_Data\figures\<RUN_ID>' `
  --run-id '<RUN_ID>'
```

该命令默认生成 PNG、PDF、`analysis_records.csv`和`analysis_manifest.json`；存在拟合时增加
`fit_summary.csv`。

## 8. 启动和使用 Notebook

在项目根目录运行：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m jupyter lab `
  notebooks\transport_analysis.ipynb
```

也可以直接在支持 Notebook 的 IDE 中打开
`notebooks/transport_analysis.ipynb`，内核选择`Python (AI)`。

Notebook使用顺序：

1. 运行命令助手，查看要复制到PowerShell的命令；它不会执行命令；
2. 在“集中设置分析参数”cell填写`SOURCE_KIND`、路径和`RUN_ID`；
3. SQLite只填路径不填`RUN_ID`时，会只读列出最近20个runs；
4. ETO必须明确填写Ch1/Ch2到`xx/xy`的映射；
5. 保持栅极标定路径为空，直到有独立器件标定；
6. 使用 **Restart Kernel and Run All Cells**；
7. 先查看数据范围、quality flags和compliance数量；
8. 再查看生成/跳过图清单与PNG预览；
9. 最后一节只用于探索性单图，正式标准图仍以manifest为准。

Notebook默认没有数据路径，因此初次Run All不会读取实验数据，也不会创建分析输出。它唯一
可能写入的位置是用户设置的`OUTPUT_DIR_TEXT`。

## 9. ETO当前工作流

当前仍由操作员在MultiVu中手动运行ETO sequence。程序可以：

- `inspect-eto-data`检查静态文件；
- `follow-eto-data`增量导入增长中的文件；
- `monitor-run`查看ETO checkpoint和最新输运；
- `plot-data`或Notebook分析文件/目录。

Python启动、停止和监控MultiVu sequence尚未实现；必须先在PPMS电脑确认OLE/API。

## 10. 当前不能运行的扫描

- 旋转台角度写控制：当前只读角度/状态；需要确认机械范围、零点、方向、安全转速和线缆
  约束后才能实现；
- ETO sequence启停：等待PPMS电脑上的只读OLE方法枚举；
- 自动判定双栅零电场线：电阻图可帮助选择候选线，但物理定义需要电容/几何或独立判据。

## 11. 安全提醒

- Notebook和监视器不是硬件联锁；
- 软件漏电阈值必须低于Keithley硬件compliance；
- 栅漏电不是样品输运电流；
- SR830输出除以串联电阻只是估算电流；
- ETO compliance/过量程数据必须保留审计标记，不能默认作为有效线性读数；
- 活动SQLite应放在PPMS电脑本地非同步磁盘，结束后再归档到OneDrive；
- 实机必须按`docs/HARDWARE_VALIDATION_CHECKLIST.md`逐级验证。
