# Quantum Design PPMS 模块资料归档与调用边界

更新日期：2026-08-13

## 1. 本地公开资料归档

公开可下载资料已归档到：

```text
C:\Users\liy56\OneDrive - Aalto University\Aalto University\Work\Equipment sheet\PPMS
```

除本说明文件外，本次归档载荷共 141 个文件，其中 129 个 PDF，总大小 162,809,942 bytes。
主要内容包括：

- KTH DynaCool 厂商手册集 24 份：基础系统、泵、Resistivity、ETO、VSM、VSM Oven、
  ACMS II、Heat Capacity、TTO、TQ-Mag、Horizontal Rotator、Helium-3、DR、AC-DR、
  Dilatometer 和 van der Pauw-Hall；
- Quantum Design 当前 PPMS/DynaCool 产品资料 29 份；
- Quantum Design 公开 PPMS 应用说明 62 份，覆盖输运、VSM/ACMS、热学、基础系统和
  第三方软件集成；
- PPMS MultiVu 1070-110 A2、ETO 1084-700 B2 等单独索引的参考副本；
- Quantum Design 维护的 MultiPyVu 3.6.1 wheel、source distribution 和 PyPI 页面快照；
- Quantum Design Pharos 公开索引中 420 条 PPMS 关键词命中项的目录清单。

`00_Catalog_and_Sources`目录包含下载来源快照和以下校验清单：

| 文件 | 内容 |
| --- | --- |
| `download_manifest.csv` | Quantum Design 产品资料、应用说明及第一批手册的 URL、大小和 SHA-256 |
| `kth_manufacturer_manual_set_manifest.csv` | KTH 厂商手册集的 URL、大小和 SHA-256 |
| `multipyvu_3.6.1_manifest.csv` | MultiPyVu 3.6.1 发行文件的大小和 SHA-256 |
| `official_pharos_ppms_related_catalog.csv` | Pharos 登录区内 PPMS 相关条目目录；不是已下载文件清单 |

Quantum Design 的 Pharos 索引可以公开浏览，但实际文件下载会跳转到客户登录页面。该登录区
包含当前 MultiVu 安装包、PPMS Commands/Firmware/GPIB 手册、示例 `.seq`、ETO/VSM 软件和
其他维护文件。本归档只登记文件名和目录，不绕过登录验证，也不把这些条目标记为已下载。

第一轮下载中有 6 个旧镜像 URL 失败。Torque、VSM、ACMS 和 TTO 已由 KTH 的相同或更新版本
补齐；NJIT 的旧 PPMS Hardware B5 与 Cryopump C2 源站当前不能从本机连接，因此仍只有 URL
和在线可检索内容，没有本地 PDF。DynaCool D1 基础系统手册及泵手册已经完整下载。

## 2. 模块在 MultiVu 中如何被调用

公开模块手册共同给出的受支持工作流是：

1. 在 MultiVu 的 Option Manager 中激活已安装模块；
2. 模块的控制窗口、Measure 菜单和专用 sequence commands 随即出现；
3. 手动测试使用 immediate mode；
4. 自动测量把专用命令与 Set/Scan Temperature、Set/Scan Field、Wait、循环等系统命令组合到
   `.seq`文件；
5. MultiVu 负责执行模块测量和写入模块自己的 `.dat`/`.raw`文件。

MultiVu 手册说明，sequence 必须先保存到磁盘，之后由 MultiVu 的 Run、Pause/Resume 和 Abort
命令控制。模块测量不是 Model 6000 单独完成的；测量 option 软件和控制电脑必须保持运行。

### 2.1 ETO

ETO 1084-700 Rev. B2 的 Section 5.5 明确列出，在 ETO 激活后，
`Measurement Commands > Electrical Transport`中出现：

- `ETO Resistance`：Ch1/Ch2 可同时测量，sequence 等两通道都完成再继续；
- `ETO dV/dI`：一次只测一个通道；
- `ETO IV`：一次只测一个通道；
- `New Datafile`：新建/版本化或追加 ETO 数据文件。

手册说明了这些命令的界面参数和行为，但没有公开 `.seq`文本中 `ETOR`各位置参数的稳定
编程契约，也没有解释 `default_ETO.qmap`的完整格式。因此可以安全地由 MultiVu GUI 创建和
运行 ETO sequence，不能仅凭现有样例猜测所有 `ETOR`/qmap 参数。

### 2.2 VSM

VSM 1096-100 Rev. B0 Chapter 6 给出的 sequence-mode 命令为：

- `VSM Adv. Measure`；
- `Center Sample`；
- `Moment vs. Field`；
- `Moment vs. Temp.`。

这些命令可以与普通系统命令和循环组合。`Moment vs. Field`用于磁滞回线等场扫，
`Moment vs. Temp.`用于温扫，`Center Sample`在测量中执行样品居中。

### 2.3 其他模块的公开 sequence 入口

| 模块 | 手册公开的主要 sequence 命令 |
| --- | --- |
| Resistivity | `Resistivity`、`Scan Excitation`、`Bridge Setup`、`Change Datafile`、`Datafile Comment` |
| ACMS II | `AC Susceptibility`、`DC Moment`、`Center Sample` |
| TTO | continuous/single `TTO Measure`及`TTO Stop`；continuous 模式启动后 sequence 继续执行，直到 Stop |
| TQ-Mag | `Torque` |
| Horizontal Rotator | `Set Position`、`Scan Position` |
| Heat Capacity | `Field Calibrate`、`New Addenda`、`Puck Calibration Pass 1/2`、`Recalibrate/Verify Table`、`Sample HC`、`Switch Addenda`、`New Datafile`、`Thermal Cycle Temperature` |
| van der Pauw-Hall | `Change Datafile`、`Datafile Comment`、`Mobility`、`Single Hall Effect`、`Single Resistivity` |

这些是 MultiVu sequence editor 的模块入口，不等于外部 Python API。

## 3. 外部软件和 Python 接口

### 3.1 MultiPyVu 3.6.1

MultiPyVu 3.6.1 是 Quantum Design 在 PyPI 上维护的公开 Python 包。它的核心接口包括温度、
磁场、sample chamber、稳定等待和 MultiVu 格式数据文件。

本版本另外公开了两个 option 入口：

```python
client.resistivity.bridge_setup(
    bridge_number=1,
    channel_on=True,
    current_limit_uA=8000.0,
    power_limit_uW=500.0,
    voltage_limit_mV=1000.0,
)
client.resistivity.set_current(bridge_number, current_uA)
resistance, status = client.resistivity.get_resistance(bridge_number)
current, status = client.resistivity.get_current(bridge_number)

client.set_position(position_deg, rate_deg_per_sec)
position_deg, status = client.get_position()
```

边界必须保留：

- BRT Resistivity 接口只适用于装有 BRT CAN module 的 DynaCool/VersaLab；公开文档明确说明它
  没有为 PPMS Model 6000 实现。这是 BRT/Resistivity，不是 ETO；
- Horizontal Rotator 接口支持 PPMS、DynaCool 和 VersaLab，但 option 必须先正确配置；PPMS
  路径最终发送 Model 6000 `MOVE`命令，公开实现允许的设定范围是 -10° 到 370°，且 PPMS
  忽略传入的角速度；
- 公开客户端和命令工厂没有 ETO、VSM、ACMS、TTO、Heat Capacity 或 sequence
  load/run/pause/abort 方法。

因此，本项目的旋转台写接口已经有明确的公开实现来源，不再需要通过猜测 OLE 方法来完成；
但实验室机械限位、0°定义、线缆安全、磁场下转动规则和真实设备验证仍然必须先确认。

### 3.2 WinWrap Basic、OLE 和 QDInstrument

Quantum Design Application Note 1070-209 说明 MultiVu 内置 WinWrap Basic，可读写温度、磁场、
chamber，也可控制第三方仪器和写 MultiVu 数据文件。该说明同时明确其当时没有实现 ACMS、
ACT、VSM 等 option measurements。公开资料中没有更新的 ETO/VSM macro 方法字典。

Application Note 1070-210 说明 QDInstrument 通过 .NET/LabVIEW 到 MultiVu OLE 的桥接提供
temperature、field、chamber、position 等接口，并可对 PPMS Model 6000 发送低层 GPIB 命令。
CAN SDO 的 node/index/subindex 如果不在公开包内，则需要向 Quantum Design 获取，不能猜测。

MultiPyVu 也是通过 MultiVu/OLE 和 socket server 工作。无论使用哪一种接口，都不应再开第二个
进程直接争用 PPMS 的 GPIB/CAN 总线。

## 4. 是否仍需要 OLE JSON

结论分成三种情况：

- **手动在 MultiVu 中运行 ETO/VSM sequence：不需要 JSON。** 模块手册和 GUI 已足够；
- **Python 控制旋转台：不需要 OLE JSON。** MultiPyVu 3.6.1 已公开
  `set_position/get_position`；
- **Python 自动加载、启动、暂停、恢复或中止 ETO/VSM sequence：仍需要实际 PPMS 控制电脑的
  只读 OLE 方法枚举 JSON，或 Quantum Design 针对当前 MultiVu 版本的官方接口说明。** 公开
  MultiPyVu 3.6.1 没有这些方法。

OLE JSON 只能确认实际安装版本暴露了哪些方法和签名；它不能解释 `ETOR`位置参数或 qmap
内部语义。若没有受支持的 sequence 外部接口，稳妥的 ETO 第一版仍应是：程序生成或复用经
MultiVu GUI 验证的 `.seq`模板，操作者在 MultiVu 中启动，Python 只读跟随 ETO `.dat`并写入
公共 SQLite；不要用键鼠自动化冒充正式 API。

## 5. 对本项目的直接影响

1. S5 旋转台不再卡在“未知写方法”，下一步是把 `set_position/get_position`接入严格配置、
   授权扫描、超时/状态检查和模拟测试；真实转动前仍要人工确认机械安全参数。
2. S6 ETO 当前可以可靠做 GUI sequence + 增量文件跟随；若要求 Python 一键启动/停止，仍需
   `inspect-multivu-ole` JSON 或 Quantum Design 官方答复。
3. DynaCool BRT Resistivity 可以作为独立的内置电阻测量后端候选，但不能当作 ETO，也不能
   自动满足 ETO 的双通道、dV/dI、IV 或高次谐波需求。
4. VSM、ACMS II、TTO、Heat Capacity 等模块的公开自动化入口同样是 MultiVu sequence；未发现
   对应的公开 MultiPyVu option API。

## 6. 公开来源

- Quantum Design application notes: <https://qdusa.com/resources/application_notes.html>
- Quantum Design DynaCool/PPMS: <https://qdusa.com/products/dynacool.html>
- Quantum Design Pharos public index: <https://qdusa.com/pharosindex/index.html>
- Quantum Design software access boundary: <https://qdusa.com/support/software_upgrades.html>
- MultiPyVu 3.6.1: <https://pypi.org/project/MultiPyVu/>
- KTH DynaCool manufacturer manual set: <https://www.nanophys.kth.se/nanolab/ppms/4307-004%20Dynacool%20Digital%20User%20Manual%20Set/manuf_manuals.html>
- Western Michigan University PPMS manuals: <https://wmich.edu/physics/ppmslabmanuals>
