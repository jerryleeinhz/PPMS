# PPMS控制软件：离线电脑首次安装

更新日期：2026-08-19

本文说明如何在一台联网的Windows电脑上准备完整离线安装包，再通过U盘复制到没有网络的
PPMS电脑。推荐使用wheel方式，因为wheel（`.whl`）是Python的预构建安装包；离线电脑只需
从本地目录读取这些文件，不访问PyPI，也不现场编译依赖。

## 1. 安装包包含什么、不包含什么

一个完整离线包应包含：

```text
PPMS_Offline_Bundle/
├── wheelhouse/                         # 项目wheel和全部Python依赖wheel
├── ppms_qcodes_control-source.zip      # 配置模板、文档、Notebook和源码快照
├── wheelhouse-sha256.csv               # 每个wheel的SHA-256
├── source-sha256.txt                   # 源码压缩包的SHA-256
└── source-commit.txt                   # 制作安装包时的Git commit
```

wheel不会包含以下系统软件，必须另外使用实验室认可的官方离线安装程序：

- 64位Python 3.12；
- NI-VISA，以及使用NI GPIB接口时的NI-488.2；
- DynaCool MultiVu及PPMS控制器驱动；
- 仪器USB/GPIB驱动和实验室要求的许可证。

`MultiPyVu==3.6.1`的Python客户端/Server组件属于`real-ppms`依赖，会进入wheelhouse；离线安装
后仍需按实验室现有方式启动Server，并确认它能连接本机MultiVu。

项目要求Python `>=3.12,<3.14`。首次部署建议固定使用64位Python 3.12，并在联网电脑和
离线电脑上使用相同的Python主次版本、Windows系统和CPU架构。不要把Linux、macOS、32位
Windows或其他Python版本生成的wheelhouse混用。

项目wheel只包含可安装的`ppms_control` Python包，不包含仓库根目录的`config/`、`docs/`
和`notebooks/`，因此还必须携带源码快照。

## 2. 在联网电脑上制作离线包

### 2.1 准备环境

以下命令在项目根目录的PowerShell中逐条运行。先确认工作区和版本；用于实验的离线包应从
已提交、已测试的commit制作：

```powershell
Set-Location 'C:\Users\liy56\OneDrive - Aalto University\Aalto University\Work\Experiment operation\PPMS\ppms_qcodes_control'
git status --short
git log -1 --oneline
```

`git status --short`应没有输出。如果有未提交文件，先确认这些改动是否应进入离线版本。

指定联网电脑上64位Python 3.12的位置，并检查版本与架构：

```powershell
$BuildPython = 'C:\Users\liy56\.conda\envs\AI\python.exe'
& $BuildPython -c "import platform,sys; print(sys.version); print(platform.architecture())"
```

输出应是Python 3.12和`64bit`。如果当前AI环境将来升级到3.13，仍可使用3.13，但离线电脑
必须安装相同版本，并重新制作wheelhouse。

### 2.2 生成wheelhouse和源码快照

下面使用`C:\PPMS_Offline_Bundle`作为输出目录。若该目录已经存在，先人工改用一个新的空
目录，例如在名称后加日期；不要把新旧wheel混在一起。

```powershell
$Bundle = 'C:\PPMS_Offline_Bundle'
$Wheelhouse = Join-Path $Bundle 'wheelhouse'
$SourceZip = Join-Path $Bundle 'ppms_qcodes_control-source.zip'
New-Item -ItemType Directory -Path $Wheelhouse

& $BuildPython -m pip wheel `
  --wheel-dir $Wheelhouse `
  --only-binary=:all: `
  '.[real-ppms,analysis,notebook]'

git archive --format=zip `
  --output=$SourceZip `
  HEAD

git rev-parse HEAD | Set-Content -Encoding ASCII `
  (Join-Path $Bundle 'source-commit.txt')
```

这一个wheelhouse同时支持两种安装：

- PPMS控制电脑最小安装：`real-ppms`；
- 同一台电脑还要画图或打开Notebook：`real-ppms,analysis,notebook`。

`--only-binary=:all:`要求第三方依赖必须有wheel；若命令报“no matching distribution”，不要
删除这个保护参数后继续。应先检查联网电脑的Python版本、Windows架构和报错的包，避免把
需要现场编译的源码包带到实验电脑。

### 2.3 生成校验和并检查内容

```powershell
Get-ChildItem -LiteralPath $Wheelhouse -File |
  Get-FileHash -Algorithm SHA256 |
  Select-Object @{Name='File';Expression={Split-Path $_.Path -Leaf}},Hash |
  Export-Csv -NoTypeInformation -Encoding UTF8 `
    (Join-Path $Bundle 'wheelhouse-sha256.csv')

Get-FileHash -Algorithm SHA256 `
  (Join-Path $Bundle 'ppms_qcodes_control-source.zip') |
  Format-List |
  Out-File -Encoding ASCII (Join-Path $Bundle 'source-sha256.txt')

Get-ChildItem -LiteralPath $Wheelhouse -File | Sort-Object Name |
  Select-Object Name,Length
```

列表中应有类似`ppms_qcodes_control-0.1.0-py3-none-any.whl`的项目wheel，以及QCoDeS、
PyVISA、NumPy、MultiPyVu等依赖。完整安装还应有Matplotlib、JupyterLab和IPython相关
wheel。`wheelhouse`中不应出现`.tar.gz`或`.zip`源码依赖。

将整个`C:\PPMS_Offline_Bundle`、Python 3.12离线安装程序以及厂商驱动安装程序复制到U盘。
复制完成后，建议在U盘上再次计算SHA-256并和两个校验文件比较。

## 3. 在离线PPMS电脑上安装

### 3.1 先安装系统软件

断开仪器控制操作并关闭正在运行的测量任务，然后按实验室流程安装：

1. 64位Python 3.12；
2. NI-VISA和需要时的NI-488.2；
3. DynaCool MultiVu、PPMS控制器和外部仪器驱动。

离线电脑不需要安装Conda。把Python官网的`Windows installer (64-bit)`提前放入U盘，在离线
电脑上安装到已知位置，例如`C:\Python312`，或者记录安装程序显示的实际`python.exe`路径。
不要使用或修改MultiVu可能自带的Python环境。

不要把`pip`包安装成功等同于硬件驱动已经可用。wheel只能安装Python层依赖。

下面假设U盘盘符是`D:`，Python安装在`C:\Python312\python.exe`。实际路径不同时，只修改
这两个位置。

复制后可先自动核对U盘中的wheel：

```powershell
$BundleOnUsb = 'D:\PPMS_Offline_Bundle'
$Expected = Import-Csv (Join-Path $BundleOnUsb 'wheelhouse-sha256.csv')
$Mismatch = foreach ($Item in $Expected) {
  $Wheel = Join-Path (Join-Path $BundleOnUsb 'wheelhouse') $Item.File
  $Actual = (Get-FileHash -Algorithm SHA256 $Wheel).Hash
  if ($Actual -ne $Item.Hash) { $Item.File }
}
if ($Mismatch) { throw "SHA-256 mismatch: $Mismatch" }
'All wheel checksums OK'
```

只有看到`All wheel checksums OK`才继续。源码zip的哈希可用`Get-FileHash`计算，并与
`source-sha256.txt`中的`Hash`比较。

### 3.2 创建独立Python环境

Python自带的`venv`就是这里需要的环境管理工具，不依赖Conda。先确认基础Python，再创建
一次项目专用环境：

```powershell
New-Item -ItemType Directory -Path 'C:\PPMS_Control'
$BasePython = 'C:\Python312\python.exe'
& $BasePython --version
& $BasePython -m venv 'C:\PPMS_Control\.venv'
$Python = 'C:\PPMS_Control\.venv\Scripts\python.exe'
& $Python --version
```

使用独立虚拟环境可以避免修改MultiVu或其他实验软件自带的Python环境。

如果希望在当前PowerShell窗口中直接输入`python`，可以激活环境：

```powershell
& 'C:\PPMS_Control\.venv\Scripts\Activate.ps1'
python -c "import sys; print(sys.executable)"
```

输出必须是`C:\PPMS_Control\.venv\Scripts\python.exe`。激活后PowerShell提示符通常出现
`(.venv)`。关闭PowerShell后激活状态自动消失；每个新窗口都需要重新激活。退出当前环境：

```powershell
deactivate
```

若实验室PowerShell策略禁止运行`Activate.ps1`，不需要修改系统执行策略，也不影响安装和
测量。保持使用下面这种完整路径即可：

```powershell
$Python = 'C:\PPMS_Control\.venv\Scripts\python.exe'
& $Python -m ppms_control --help
```

本项目文档优先使用`& $Python ...`，因为它不依赖激活状态，也更不容易误用其他Python。

### 3.3 从本地wheelhouse安装

推荐PPMS控制电脑先安装最小运行组件：

```powershell
$Wheelhouse = 'D:\PPMS_Offline_Bundle\wheelhouse'
& $Python -m pip install `
  --no-index `
  --find-links $Wheelhouse `
  'ppms-qcodes-control[real-ppms]'
```

`--no-index`是关键：它禁止pip访问网络；`--find-links`指定唯一的软件包来源。

如果这台离线电脑还需要论文图和Jupyter Notebook，改用完整安装命令：

```powershell
& $Python -m pip install `
  --no-index `
  --find-links $Wheelhouse `
  'ppms-qcodes-control[real-ppms,analysis,notebook]'
```

不需要先执行最小命令再执行完整命令，二者选择一个即可。离线部署使用wheel，不使用
`pip install -e`；后者是源码开发模式。

### 3.4 解压配置、文档和Notebook

```powershell
$SourceRoot = 'C:\PPMS_Control\workspace'
New-Item -ItemType Directory -Path $SourceRoot
Expand-Archive `
  -LiteralPath 'D:\PPMS_Offline_Bundle\ppms_qcodes_control-source.zip' `
  -DestinationPath $SourceRoot
```

以后运行时，Python程序来自虚拟环境中安装的wheel，TOML、文档和Notebook来自
`C:\PPMS_Control\workspace`。测量SQLite应放在例如`C:\PPMS_Data`的本地非同步目录，不要
把活动数据库写入U盘、OneDrive或网络盘。

## 4. 离线安装后的验证顺序

### 4.1 验证Python包，不连接仪器

```powershell
& $Python -m pip check
& $Python -c "import ppms_control,qcodes,pyvisa,numpy,MultiPyVu; print('Python imports OK')"
& $Python -m ppms_control --help
```

三条命令都应成功。`pip check`应输出`No broken requirements found.`。

### 4.2 先运行仿真

```powershell
Set-Location $SourceRoot
& $Python -m ppms_control validate-config 'config\simulation.toml'
& $Python -m ppms_control simulate 'config\simulation.toml'
```

仿真成功后才进入真实硬件配置。仿真不连接VISA或PPMS。

### 4.3 创建并校验真实硬件配置

```powershell
Copy-Item 'config\hardware.example.toml' 'config\hardware.local.toml'
```

人工填写并复核`hardware.local.toml`中的样品名、VISA地址、PPMS Server、本地数据库路径、
SR830激励与串联电阻、温场范围、SMU极性、硬件compliance、软件漏电阈值和双栅范围。然后：

```powershell
& $Python -m ppms_control validate-config 'config\hardware.local.toml'
& $Python -m ppms_control diagnose-hardware 'config\hardware.local.toml'
```

`diagnose-hardware`只读查询仪器身份和状态。首次安装不要直接运行`run-hardware*`；必须按
`docs/HARDWARE_VALIDATION_CHECKLIST.md`完成接线和限值确认，再使用诊断产生的run ID授权。

## 5. 离线更新与回退

每次代码或依赖更新都重新制作一个全新的、有日期或commit标识的离线目录，不在旧
wheelhouse中增量覆盖。离线电脑更新时：

1. 记录当前`source-commit.txt`并备份`hardware.local.toml`；
2. 创建新的虚拟环境，而不是直接破坏已验证环境；
3. 从新wheelhouse安装并重新执行`pip check`、导入检查、仿真和只读诊断；
4. 验证通过后才把真实测量入口切换到新环境；旧虚拟环境保留到新版本完成实机验收。

配置文件和SQLite数据不应打进wheel，也不应通过更新命令覆盖。`hardware.local.toml`包含本机
真实参数且被Git忽略，必须单独备份并人工比较新模板字段。

## 6. 常见问题

### wheel是什么？

它是以`.whl`结尾的Python预构建分发文件。一个wheelhouse就是安装所需wheel的本地目录。
pip加`--no-index --find-links`后只从该目录解析和安装。

### 为什么不能只复制项目wheel？

项目还依赖QCoDeS、PyVISA、NumPy和可选的MultiPyVu、Matplotlib、JupyterLab。离线电脑无法
临时下载缺少的依赖，所以必须在联网电脑上把整个依赖闭包一起放入wheelhouse。

### 为什么还要源码zip？

当前项目wheel不会携带硬件TOML模板、操作手册和Notebook。源码zip提供这些运行资产；真正
被Python导入的代码仍来自已安装wheel，便于知道离线电脑使用的是哪个确定版本。

### `No matching distribution found`怎么办？

优先检查联网和离线电脑是否都是64位Windows及相同Python主次版本，并确认所有wheel都从
同一次构建的wheelhouse复制。不要联网临时补包，也不要去掉`--no-index`绕过审计。

### 安装成功是否代表可以控制仪器？

不是。安装成功只证明Python依赖完整。真实控制还依赖NI驱动、MultiVu Server、正确地址、
接线、量程、compliance和样品安全范围，并必须经过只读诊断与分级硬件验证。
