# PPMS输运控制项目：设计方案、目标、进展与交接

更新日期：2026-08-07

本文档是新会话的首要上下文。继续开发前，应先阅读本文档以及项目根目录的
`README.md`。本文档记录已经确认的实验需求、真实ETO文件格式、软件边界、当前
实现和下一步工作，避免从聊天记录重新推断。

## 1. 最终目标

使用一个Python/QCoDeS代码库协调以下设备：

- Quantum Design DynaCool PPMS：温度、磁场和旋转台角度；
- SR830：交流电压激励及一路锁相测量；
- SR865A：第二路锁相测量；
- 两台Keithley 2400：上下栅压、输出状态、compliance和漏电流；
- DynaCool Electrical Transport Option（ETO）：作为第二种输运测量后端。

最终代码提供两个可切换的测量后端：

1. `SRLockinBackend`：SR830输出交流电压，两台锁相测量`Vxx/Vxy`；
2. `ETOBackend`：由MultiVu/ETO执行测量，程序启动或监控sequence并读取数据。

PPMS环境控制、扫描协议、SQLite数据、导出和分析代码应由两个后端共用。

## 2. 目标实验

所有协议均记录`Vxx`和`Vxy`的`1ω/2ω/3ω`以及完整仪器状态。

1. 零磁场下扫描激励电流；
2. 零磁场下扫描激励频率；
3. 固定激励下扫描磁场大小和旋转台角度；
4. 固定激励下扫描温度和磁场。

需要保存的公共条件至少包括：时间戳、温度、磁场、样品角度、激励电压、实际或
估算电流、频率、上下栅状态以及数据后端。

## 3. 总体架构

```text
ExperimentProtocol
    ├── EnvironmentController
    │      └── MultiPyVu：temperature / field / rotator
    ├── TransportBackend
    │      ├── SRLockinBackend
    │      └── ETOBackend
    └── RunStore
           ├── runs / events
           ├── instrument_samples
           ├── attempts
           └── transport_readings
```

`transport_readings`采用长表：一行只表示一个空间信号和一个谐波，例如
`xy/2ω`。这种结构可以同时容纳：

- SR后端提供的有符号X/Y和相位；
- ETO基波提供的X/Y、幅值和相位；
- 当前ETO 1.2对2ω/3ω提供的dB比值及无符号派生幅值；
- Ch1和Ch2在不同时间、不同数据行获得的情况。

不得为了得到一张宽表而假设ETO的Ch1和Ch2严格同时，也不得为ETO高次谐波
虚构符号、X/Y或相位。

## 4. ETO compliance的已确认结论

“ETO compliance”至少涉及两个不同限制，不能混为一个数值。

### 4.1 电流源输出compliance

ETO四线电阻模式的电流驱动器在反馈开启时近似理想电流源。Quantum Design
ETO用户手册1084-700 Rev. B2给出的电流源输出compliance为**30 V**。含义是：
为了维持设定电流，电流源最多可以施加约30 V；超过后不能保证设定电流。

资料：

- [Quantum Design ETO User's Manual 1084-700 Rev. B2](https://files.wmich.edu/s3fs-public/attachments/u1045/2019/10_1084-700%20Rev%20B2%20ELECTRICAL%20TRANSPORT%20OPTION%20USER%27S%20MANUAL.pdf)
- [Quantum Design Pharos document index](https://qdusa.com/pharosindex/index.html)

### 4.2 电压前置放大器输入范围

这与30 V电流源compliance不同。ETO电压前置放大器最大名义档为4 V；手册说明
实际大约可测到所选档位的110%，即最高档约4.4 V；工程判断时可近似按
4.4–4.5 V的最大输入范围理解。

本项目CrSBr数据中的提示是：

```text
Ch. 1 Input voltage exceeded compliance limit.
Ch. 2 Input voltage exceeded compliance limit.
```

结合实际`Voltage Ampl ChN (V)`，该提示强烈指向**测量输入超量程**，而不是
30 V电流源输出compliance：

| 文件 | 通道 | 最大基波电压 | 超过4.5 V的记录 |
| --- | ---: | ---: | ---: |
| `frequency.dat` | Ch1 | 4.446646 V | 0/125 |
| `frequency.dat` | Ch2 | 5.723483 V | 126/127 |
| `field.dat` | Ch1 | 0.286616 V | 0/1302 |
| `field.dat` | Ch2 | 5.724063 V | 1302/1302 |
| `temperature.dat` | Ch1 | 1.660054 V | 0/1050 |
| `temperature.dat` | Ch2 | 5.724111 V | 1050/1050 |
| `xx_0.2.dat` | Ch2 | 5.724108 V | 20/20 |

因此这些Ch2记录很可能已达到输入钳位或饱和区域。程序保留数据和提示，但分析时
不能把这些点默认视为有效线性读数。解决方向通常是降低激励电流、降低样品电压降
或调整接线/测量方案；由于最高输入档已经不足，单纯选择更高前放档位未必可行。

### 4.3 sequence能告诉我们的内容

现有sequence位于：

```text
C:\Users\liy56\OneDrive - Aalto University\Aalto University\Work\Experiment\PPMS\CrSBr
```

`FULL MEASURMENT.seq`包含：

- 3个`ETODF`数据文件切换；
- 378个`ETOR`测量命令；
- 15–300 K温度循环；
- −10000至+10000 Oe及返回方向的磁场循环；
- 多组电流和频率点。

测试sequence和数据确认当前样品映射为：

```text
ETO Ch1 -> Vxy
ETO Ch2 -> Vxx
```

这只是当前CrSBr接线/sequence的映射，不是ETO的普遍固定定义。后端配置必须显式
提供通道角色。

sequence中的`ETOR`引用`C:\QdDynacool\default_ETO.qmap`。现有文件不足以可靠解释
`ETOR`每一个位置参数或qmap中的前放设置，因此当前阶段不硬编码sequence生成器。

## 5. 真实ETO文件格式

已使用的实际软件版本：

```text
Electrical Transport Option, Release 1.2.0 Build 0
```

每个`.dat`文件有67列。已经确认：

- 基波：幅值、同相电压、正交电压、相位、频率和AC电流；
- 2ω、3ω：相对于基波电压的dB值；
- `Vn = V1 * 10^(dB/20)`只能得到无符号幅值，不能恢复高次谐波相位或正负号；
- 当前完整扫描中Ch1与Ch2分行写入，没有一行同时包含两个通道；
- `Comment`和`ETO Status Code`必须保留；
- `Field (Oe)`在程序中转换为T，`AC Current (mA)`转换为A。

实际文件统计：

| 文件 | 总行数 | Ch1 | Ch2 | 范围 |
| --- | ---: | ---: | ---: | --- |
| `frequency.dat` | 252 | 125 | 127 | 约15 K、零场 |
| `field.dat` | 2604 | 1302 | 1302 | 约15 K、−1至+1 T |
| `temperature.dat` | 2100 | 1050 | 1050 | 约15至300 K、零场 |

这些文件中的`Sample Position`均为0°，尚未提供真实旋转台数据样例。

## 6. 当前实现

### 6.1 已完成

- 严格TOML配置和安全边界；
- QCoDeS模拟SR830、SR865A、两台Keithley 2400和PPMS；
- 真实VISA锁相/SMU适配器；
- MultiPyVu温度、磁场和状态读取；
- 只读硬件连接诊断和真实运行授权；
- 固定温度、固定磁场下的SR830电压幅值扫描；
- SR、SMU和PPMS原始状态逐样本写入SQLite；
- 运行事件、失败尝试、断点续跑及CSV导出；
- ETO 1.2 `.dat`严格解析和`inspect-eto-data`命令；
- ETO的Oe→T、mA→A和dB→无符号幅值转换；
- 后端无关的`TransportReading`模型；
- SQLite `transport_readings`长表；
- ETO记录到公共读数的标准化转换，且要求显式Ch1/Ch2角色。
- SR830和SR865A按`1ω -> 2ω -> 3ω`顺序采集，每个原始样本同时写入公共
  `transport_readings`，因此每个平均样本组保存六种信号/谐波组合；
- 频率扫描、磁场扫描以及固定激励下的温度—磁场网格扫描；
- PPMS旋转台角度和状态的可选读取；没有安装旋转台选件时保持为空且不影响其他协议；
- ETO `.dat`增量跟随读取：延迟未完成的末行、检测截断/已消费内容改写、保存可恢复检查点；
- ETO新增读数和跟随检查点在同一个SQLite事务中提交，并提供`follow-eto-data`命令。
- 公共输运长表导出和按观测/信号/谐波汇总的作图就绪CSV；相位采用圆统计，ETO分时源行不配对。

### 6.2 当前SR后端状态

当前SR运行代码在每个扫描条件下依次设置并读取：

```text
SR830 -> xx/1ω, xx/2ω, xx/3ω
SR865A -> xy/1ω, xy/2ω, xy/3ω
```

每次切换谐波后执行配置的稳定等待，再取得配置数量的平均样本。旧的`attempts`宽表仍保留
`xx/1ω + xy/3ω`摘要以兼容已有分析；完整六路数据以`instrument_samples`和
`transport_readings`为准。

### 6.3 当前ETO后端限制

当前可以严格解析完整ETO文件，也可以增量读取仍在增长的文件并写入公共长表。尚未完成：

- 生成或修改MultiVu sequence；
- 从Python启动、停止和监控sequence；
- 将一个sequence中的Ch1/Ch2分时记录按扫描条件组合成分析视图；
- 验证控制电脑上是否存在可直接操作ETO的OLE/API。

## 7. 关键代码位置

| 文件 | 作用 |
| --- | --- |
| `src/ppms_control/models.py` | 旧锁相模型和新的公共`TransportReading` |
| `src/ppms_control/eto_data.py` | ETO文件解析、检查和标准化 |
| `src/ppms_control/store.py` | SQLite，包括`transport_readings` |
| `src/ppms_control/protocols.py` | 电压、频率、磁场和温度—磁场网格扫描协议 |
| `src/ppms_control/acquisition.py` | SR双通道三谐波读取、平均和质量标记 |
| `src/ppms_control/real_instruments.py` | 真实VISA和MultiPyVu适配器 |
| `src/ppms_control/hardware_run.py` | 真实SR扫描授权入口 |
| `src/ppms_control/eto_follow.py` | ETO增量读取与SQLite原子检查点 |
| `src/ppms_control/analysis.py` | 不强制通道配对的输运汇总和圆相位统计 |
| `config/hardware.example.toml` | GPIB和PPMS Server地址模板 |
| `tests/test_eto_data.py` | ETO解析、标准化和长表存储测试 |

## 8. 运行和验证

在项目根目录使用AI环境：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install --no-deps -e .
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m unittest discover -s tests -v
```

检查真实ETO文件：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control inspect-eto-data `
  'C:\Users\liy56\OneDrive - Aalto University\Aalto University\Work\Experiment\PPMS\CrSBr\CrSBr-1\magnetic field\field.dat'
```

该命令只读，不会修改原始数据。

增量导入正在增长的ETO文件（当前CrSBr映射示例）：

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control follow-eto-data `
  'C:\path\to\measurement.dat' 'C:\PPMS_Data\ppms_control.sqlite' `
  --sample-name 'SAMPLE_NAME' --channel-1-role xy --channel-2-role xx
```

中断后使用输出的`run_id`配合`--resume-run`恢复；已有静态文件可加`--once`。

## 9. 已确定的设计决策

- 一个代码库、两个输运后端；
- 不把物理接线、磁场方向、SMU极性和人工compliance核验作为开发里程碑；
- 保留自动连接诊断、状态记录、软件限值和运行授权；
- ETO通道角色显式配置，不自动猜测；
- ETO Ch1/Ch2分时记录不强制配对；
- ETO 2ω/3ω只作为无符号幅值保存，除非未来文件/API提供相位信息；
- SQLite是数据源，CSV和宽表是派生结果；
- 在实际API能力未知时，优先采用MultiVu sequence加增量文件读取。

## 10. 下一阶段

截至2026-08-07，原计划进度如下：

1. [完成] 两通道、三个谐波的顺序采集；
2. [完成] 频率扫描协议；
3. [完成] 磁场扫描和旋转台角度状态；
4. [完成] 温度—磁场网格扫描；
5. [完成] ETO `.dat`增量跟随读取；
6. [等待实机接口确认] 在确认MultiVu sequence启动/停止/状态接口后实现ETO运行层；
7. [部分完成] 已增加公共长表和作图就绪汇总CSV；具体论文图版式等待实验图形需求。

MultiPyVu 3.6.1公开的客户端和命令工厂没有sequence启停命令；当前开发电脑也没有
DynaCool/MultiVu安装可检查其COM类型库。因此第6项的最小下一步是在PPMS控制电脑上
运行`python -m ppms_control inspect-multivu-ole`，只读检查
`QD.MULTIVU.DYNACOOL.1`活动COM对象的方法列表，并确认是否存在受支持的sequence
加载、启动、停止和状态方法。不得根据猜测调用未知COM方法，也不得硬编码尚未解释的
`ETOR`位置参数。

旋转台目前只读状态。加入真实角度扫描前还必须确认样品杆/线缆允许角度、零点定义、方向和
安全转速；这些机械安全参数不能从MultiPyVu通用API推断。

## 11. 新会话建议提示词

```text
请先阅读ppms_qcodes_control/docs/PROJECT_HANDOFF.md和README.md，检查git diff与测试。
先在PPMS控制电脑上保持MultiVu运行，执行`python -m ppms_control inspect-multivu-ole`，
保存其JSON输出。确认sequence方法后实现ETO运行层；不要猜测ETO ETOR参数或调用未知写接口。
```
