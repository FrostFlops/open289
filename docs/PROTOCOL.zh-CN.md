[English](PROTOCOL.md) | 简体中文。本文是英文版 [PROTOCOL.md](PROTOCOL.md) 的译文，若两者有出入，以英文版为准。

# open289 协议参考

本文档说明 open289 如何通过 Fluke IR3000 FC（一种夹在万用表上的红外转低功耗蓝牙适配器）与 Fluke 287/289 万用表通信。本文面向希望实现或审计客户端的读者。

## 约定与证据等级

每一条陈述都标注了其来源依据。

| 标签 | 含义 |
|---|---|
| VERIFIED | 于 2026-11-06 在真实硬件上观察到：IR3000 FC 射频固件 01.00.01，MSP 固件 01.00.15，Fluke 289 固件 V1.71，使用内置蓝牙的 Windows PC（bleak，基于 WinRT）。仅测试了 289。 |
| DERIVED | 来自 Fluke Connect 应用、关于 287/289 串口协议的公开资料，或由命名和类比推得，但未在硬件上实测。应视为假设。 |

本文使用的示例值：适配器地址 `FA:28:90:00:00:01`，万用表序列号 `12345678`。它们只是占位符，并非真实设备。

除非另有说明，所有多字节整数均为小端序。十六进制转储以空格分隔字节。

## 1. 概述

```
Fluke 289  <--- infrared serial, 115200 8N1 --->  IR3000 FC  <--- Bluetooth LE --->  PC (open289)
```

| 组成部分 | 作用 |
|---|---|
| Fluke 287/289 | 数字万用表。通过红外口使用面向行的串口协议（以 CR 结尾的 ASCII 命令，回复为 ASCII 或二进制）。 |
| Fluke IR3000 FC | 电池供电的适配器，带有 BLE 射频和一颗小型微控制器（“MSP”）。它在同一个 BLE 连接上提供两条相互独立的通路。 |
| open289 | PC 客户端，同时使用这两条通路。 |

适配器在单个连接上提供两条通路（它一次只接受一个 BLE 中心设备，因此运行 Fluke Connect 的手机不得处于连接状态）：

1. 实时流。适配器自行轮询万用表，把显示的读数重新编码为紧凑的二进制记录，以通知形式发送，约每秒 4 次（第 4 节）。这条通路速度快，但只携带显示屏上的内容。
2. 透传通路。写入适配器控制点的命令会被转发到万用表的串口，万用表的原始回复缓存在适配器中，再分块读回（第 5 和第 6 节）。这条通路可访问完整的万用表协议：显示状态、已保存数据、记录、按键操作。它很慢（每条命令约 1.3 秒，第 7 节）。

VERIFIED：两条通路可以在同一个连接上同时工作。透传命令运行期间，实时通知仍持续到达。

## 2. BLE 广播与 GATT 表

### 2.1 广播

VERIFIED。适配器广播本地名称 `IR 3000 FC` 和主服务 `B6981800-7562-11E2-B50D-00163E46F8FE`。无需配对或绑定。客户端应按名称或服务扫描，然后按地址连接。适配器必须处于唤醒状态：按住其按钮超过一秒，直到 LED 闪绿灯。

所有 Fluke 专用 UUID 共用基址 `xxxxxxxx-7562-11E2-B50D-00163E46F8FE`。本文把它们缩写为前八位十六进制数字，例如 `B698290F`。

### 2.2 GATT 表

VERIFIED（取自适配器的完整转储；在 Windows 上连接后 ATT MTU 为 23）。列出“Never write”条目，是因为向这些位置的其他写入可能损坏适配器。

| 服务 | 特征 | 属性 | 用途 | 客户端用法 |
|---|---|---|---|---|
| 1800 Generic Access | 2A00 Device Name | read, write, write-no-response | `IR 3000 FC` | 只读。 |
| 1801 Generic Attribute | 2A05 Service Changed | indicate | 标准 | 忽略。 |
| 180A Device Information | 2A24 Model Number | read | 所连万用表的型号：`FLUKE 289`。DERIVED：适配器未夹在万用表上时为 `FLUKE PC3000`（该字符串见于应用，未实际观察到）。 | 读取以识别万用表。 |
| | 2A25 Serial Number | read | 序列号字符串（以 NUL 填充）。示例 `12345678`。 | 读取。 |
| | 2A26 Firmware Revision | read | 射频固件，`01.00.01`（以空格和 NUL 填充）。 | 读取。 |
| | 2A28 Software Revision | read | `V1.71`，即万用表固件。 | 读取。 |
| | 2A29 Manufacturer Name | read | `Fluke Mfg Co.` | 读取。 |
| 180F Battery | 2A19 Battery Level | read, notify | 适配器电量，百分比。 | 读取。 |
| `B6981800` Reading | `B6982901` | read, notify | 声明为 ASCII 读数的特征。读到的值是固定的 17 字节模式（`01 02 03 04 05` 后跟若干零）。DERIVED：官方应用从不使用它。 | 不使用。 |
| | `B698290F` | read, notify | 实时读数，每条通知 16 字节（第 4 节）。 | 订阅。 |
| `B6981801` CNX | `B6982902` | read, write | 单字节 CNX ID。写入 `01` 会使适配器控制点就绪（第 3 节）。 | 每个连接写入一次 `01`。 |
| | `B6982903` | read, write | 包含设备名称文本 `IR 3000 FC`。用途未查明。 | 从不写入。 |
| | `B6982904`, `B6982905` | write | 用途未查明。 | 从不写入。 |
| | `B698290E` | write, notify | 用途未查明。 | 从不写入。 |
| | `B6982911` | read, write, write-no-response, notify | DERIVED：微控制器固件更新器的控制点。它复用与适配器控制点相同的操作码字节值，但含义具有破坏性（值 `80` 会擦除闪存）。 | NEVER WRITE。 |
| | `B6982912` | write, write-no-response | DERIVED：上述更新器的固件映像数据。 | NEVER WRITE。 |
| `B6981804` Radio image | `B6982913`, `B6982914` | write, write-no-response, notify | DERIVED：BLE 射频固件映像传输。 | NEVER WRITE。 |
| `B6981805` Adapter logging | `B6982918` 控制点 | read, write, write-no-response, notify | 命令通道，以及通往万用表的路径（第 5 节）。 | 写入命令，订阅回复。 |
| | `B6982919` 缓冲区 | notify | 批量数据：万用表的缓冲回复，按通知大小分片。 | 订阅。 |

为什么“never write”清单很重要。更新器控制点 `B6982911` 与适配器控制点 `B6982918` 接受相同的单字节操作码，但 `80` 在适配器上表示“读取缓冲区大小”，在更新器上表示“擦除闪存”。写错 UUID 可能使适配器变砖。open289 将适配器服务的 UUID 硬编码，并且除 `B6982902` 外拒绝向其他任何位置写入。

## 3. 连接流程

除非另有标注，均为 VERIFIED。

1. 扫描 `IR 3000 FC`（或服务 `B6981800`）。按地址连接。不绑定。
2. 发现服务。在所测试的 Windows 协议栈上 ATT MTU 保持为 23，因此一条通知或一次写入最多携带 20 字节有效载荷；与 MTU 无关，适配器会拒绝长于 18 字节的控制点写入（见 5.4）。客户端必须接受不超过协商 MTU 的任意长度通知。
3. 可选：读取 Device Information 特征（型号、序列号、固件）和电量。
4. 以带响应的写入（write-with-response）向 CNX ID 特征 `B6982902` 写入单字节 `01`。
5. 在控制点 `B6982918` 和缓冲区 `B6982919` 上启用通知（CCCD `01 00`）。若需实时数据，还要在 `B698290F` 上启用通知。
6. 向控制点发送命令并读取回复。

就绪（arming）。在写入 CNX ID 之前，控制点接受写入但从不应答；读回时只返回刚写入的那个字节。向 `B6982902` 写入 `01` 之后，控制点会以通知形式应答每条命令。DERIVED：官方应用每次连接都会把它的“#n”工具编号写入 CNX ID（第一个工具为 1，第二个为 2，依此类推）。只试过值 `01`，其他值未经测试。

写入类型。控制点同时声明支持 write 和 write-without-response。VERIFIED：两者产生的回复完全相同。open289 使用 write-with-response。

通知。所有通知都是普通通知（不是 indication）。控制点使用期间，适配器仍会持续发送实时通知。客户端应在每条命令之前清空回复队列，因为超时命令的回复可能迟到，不得误认为是下一条命令的回复。open289 在超时后等待 0.5 秒，然后丢弃过期回复。

单连接。当其他中心设备（如手机）占用连接时，适配器不接受第二个连接。请在所有手机上关闭 Fluke Connect。

## 4. 实时读数

### 4.1 通知格式

VERIFIED。`B698290F` 上的每条通知为 16 字节，约每秒到达 4 次（大约每 250 ms 一次）。

| 偏移 | 大小 | 内容 |
|---|---|---|
| 0 | 8 | 主记录：万用表主显示屏上显示的内容。 |
| 8 | 8 | 副记录：万用表作为副值显示的内容。万用表不显示副值时全为零。 |

DERIVED：其他型号的万用表存在其他长度（单个读数为 8 字节，部分其他 Fluke 型号为 18 字节，带两个额外的模式字节）。289 发送 16 字节。客户端对任何更短或更长的通知，仍应只解码前 8 字节，并忽略其余部分。

读取该特征会返回最近一条通知，因此也可以对该特征进行轮询。

### 4.2 8 字节记录

记录是一个无符号 64 位小端整数 W。字段从最低有效位向上提取：`(W >> offset) & ((1 << width) - 1)`。

| 字段 | 位偏移 | 位宽 | 含义 |
|---|---|---|---|
| counts | 0 | 21 | 显示计数的绝对值，0 到 2,097,151。 |
| state | 21 | 4 | 读数状态（4.4）。 |
| decimals | 25 | 3 | 小数点后的位数，0 到 7。 |
| magnitude | 28 | 3 | SI 前缀代码（4.4）。 |
| sign | 31 | 1 | 1 表示负。 |
| unit | 32 | 8 | 单位代码（4.5）。第 4 字节。 |
| function | 40 | 8 | 万用表功能代码（4.6）。第 5 字节。 |
| valueRange | 48 | 7 | 量程尾数。 |
| rangeDecade | 55 | 3 | 量程指数代码（4.4）。 |
| attribute | 58 | 5 | 属性代码（4.7）。 |
| capture | 63 | 1 | 捕获标志。DERIVED：适配器按钮请求保存时置位；调查期间从未见到被置位。 |

字节捷径：设 `lo` 为字节 0..3 构成的 32 位整数，则 `counts = lo & 0x1FFFFF`，`state = (lo >> 21) & 0xF`，`decimals = (lo >> 25) & 7`，`magnitude = (lo >> 28) & 7`，`sign = lo >> 31`。第 4 字节是 unit，第 5 字节是 function。设 `hi` 为字节 6..7 构成的 16 位整数，则 `valueRange = hi & 0x7F`，`rangeDecade = (hi >> 7) & 7`，`attribute = (hi >> 10) & 0x1F`，`capture = hi >> 15`。

### 4.3 数值与显示公式

| 量 | 公式 |
|---|---|
| 以 SI 基本单位表示的值 | `counts * 10^(magnitudeExponent - decimals)`，sign 为 1 时取负。 |
| 万用表上显示的数字 | `counts * 10^(-decimals)`，sign 为 1 时取负，严格按 `decimals` 位数打印（保留末尾的零）。 |
| 单位文本 | magnitude 代码对应的 SI 前缀，后接单位文本。 |
| 量程 | `valueRange * 10^(rangeDecadeExponent + magnitudeExponent)`。解码器使用此规则；VERIFIED：对于 289，适配器使 valueRange 和 rangeDecade 保持为 0（调查中全部 4,018 个数据包均如此），因此实时流不携带量程。请从 QDDB 获取量程。 |

按状态的显示文本：NORMAL、GREATER_THAN 和 LESS_THAN 打印数字（后两者带前导 `> ` 或 `< `）。OL_RANGE 和 OL_A2D 打印 `OL`，设置了 sign 时打印 `-OL`。BLANK 和 INVALID 打印 `----`。EMPTY 不打印任何内容。

实例，VERIFIED（真实捕获，万用表处于 V DC 档）：主记录 `d9 c5 00 08 02 0c 00 00`。

```
W low word  = 0x0800C5D9
counts      = 0xC5D9 = 50649
state       = 0 (NORMAL)
decimals    = 4
magnitude   = 0 (no prefix)       sign = 0
unit        = 0x02 (V DC)         function = 0x0C = 12 (VDC)
range = 0, attribute = 0, capture = 0
value       = 50649 * 10^(0-4) = 5.0649   ->  "5.0649 V DC"
```

实例，DERIVED（按相同规则构造的合成数据）：`00 00 80 28 0b 28 05 00` 的 state 为 4（OL_RANGE），decimals 为 4，magnitude 为 2（mega），unit 为 11（ohm），function 为 40（resistance），因此显示为 `OL MOhm`。

### 4.4 状态、量级与量程指数表

状态（4 位）。VERIFIED 的代码已标出，其余为 DERIVED。

| 代码 | 名称 | 显示 | 在 289 上是否见到 |
|---|---|---|---|
| 0 | NORMAL | 数字 | 是 |
| 1 | BLANK | `----` | 是（自动量程时的消隐） |
| 2 | INACTIVE | | |
| 3 | INVALID | `----` | 是（转动旋钮期间，字节 `ff ff 7f 00 00 <function> ...`） |
| 4 | OL_RANGE | `OL` | 是 |
| 5 | OL_A2D | `OL` | |
| 6 | OPEN_TC | `Open TC` | |
| 7 | DISCHARGE | | |
| 8 | LEADS | | |
| 9 | GREATER_THAN | `> number` | |
| 10 | MISSING_PHASE | | |
| 11 | ERROR | | |
| 12 | LESS_THAN | `< number` | |
| 13 | EMPTY | 空 | |
| 14, 15 | 未定义 | | |

健壮的客户端应对 14 和 15 作出标记，而不是把它们当作 NORMAL 处理。

量级（3 位，SI 前缀）。整体显示中的单位文本使用该前缀；3 位字段无法编码 tera 或更大的前缀。

| 代码 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| 前缀 | none | G | M | k | m | u (micro) | n | p |
| 指数 | 0 | 9 | 6 | 3 | -3 | -6 | -9 | -12 |

量程指数（3 位）。DERIVED。

| 代码 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| 指数 | 0 | 1 | 2 | 3 | 未定义（按 0 处理） | -3 | -2 | -1 |

### 4.5 单位表（第 4 字节）

在 289 上见到的代码标为“seen”。其余均 DERIVED 自应用，出现在其他 Fluke 型号上。

| 代码 | 单位 | 代码 | 单位 | 代码 | 单位 |
|---|---|---|---|---|---|
| 0 | none | 16 | dB (seen) | 32 | crest factor (seen) |
| 1 | V AC (seen) | 17 | dBm (seen) | 33 | V AC+DC (seen) |
| 2 | V DC (seen) | 18 | W | 34 | A AC+DC |
| 3 | A AC | 19 | J | 35 | percent |
| 4 | A DC (seen) | 20 | H | 36 | V AC / Hz |
| 5 | Hz (seen) | 21 | PSI | 37 | g (acceleration) |
| 6 | percent RH | 22 | m Hg | 38 | m/s^2 |
| 7 | degrees C (seen) | 23 | in Hg | 39 | in/s |
| 8 | degrees F (seen) | 24 | ft H2O | 40 | mm/s |
| 9 | degrees Rankine | 25 | m H2O | 41 | mils |
| 10 | kelvin | 26 | in H2O | 42 | micrometre |
| 11 | ohm (seen) | 27 | in H2O (60 F) | 43 | unknown |
| 12 | siemens (seen) | 28 | bar | 44 | tera-ohm |
| 13 | percent, "duty" (seen) | 29 | pascal | 45 | VA DC |
| 14 | seconds | 30 | g/cm^2 | 46 | undefined |
| 15 | farad (seen) | 31 | dBV (seen) | 47 | VA AC |

有两点不太直观。单位 13 的名称是“duty percent”，但它同样承载相对百分比（REL %）读数。当读数以赫兹为单位（单位 5）时，单位文本为 SI 前缀加 `Hz`；具体测量的是什么由 function 代码说明（见 4.8 中的 Hz 行）。

### 4.6 功能表（第 5 字节）

function 表示读数由万用表的哪个功能产生。在 289 上见到的代码已标出；其余为 DERIVED，属于其他 Fluke 工具。

| 代码 | 名称 | 代码 | 名称 | 代码 | 名称 |
|---|---|---|---|---|---|
| 0 | none | 17 | A AC average | 34 | temperature (seen) |
| 1 | mV AC (seen) | 18 | A AC average + DC | 35 | degrees F |
| 2 | V AC (seen) | 19 | uA DC (seen) | 36 | degrees C |
| 3 | V AC+DC (seen) | 20 | mA DC (seen) | 37 | degrees Rankine |
| 4 | mV AC average | 21 | A DC | 38 | kelvin |
| 5 | V AC average | 22 | Hz mV AC | 39 | continuity (seen) |
| 6 | V AC average + DC | 23 | Hz V AC | 40 | resistance (seen) |
| 7 | V AC LoZ (seen) | 24 | Hz mV AC low-pass | 41 | conductance (seen) |
| 8 | mV AC low-pass | 25 | Hz V AC low-pass | 42 | low ohms (seen) |
| 9 | V AC low-pass | 26 | Hz mV DC | 43 | phase |
| 10 | uV DC | 27 | Hz V DC | 44 | A AC inrush |
| 11 | mV DC (seen) | 28 | Hz uA AC | 45 | capacitance (seen) |
| 12 | V DC (seen) | 29 | Hz mA AC | 46 | diode (seen) |
| 13 | mA AC | 30 | Hz A AC | 47 | V AC / Hz |
| 14 | A AC | 31 | Hz uA DC | 48 to 51 | 48 mV AC+DC, 49 mA AC+DC, 50 uA AC, 51 uA AC+DC |
| 15 | A AC+DC | 32 | Hz mA DC | 52 to 85 | 绝缘与安装测试仪功能 |
| 16 | mA AC average | 33 | Hz A DC | 86 to 95 | pressure (86), field sense (87, 88), V ADC (89), self check (95)；90 到 94 未定义 |

在 289 上，Hz 显示状态是以电压功能代码加单位 5 观察到的（4.8），而不是上表中的 Hz 功能代码。

对于 289 特有的模式，如 dBm、dBV、占空比、脉宽、min/max、峰值、保持或相对值，没有对应的功能代码。它们只作为单位出现，或者根本不出现（4.9）。

### 4.7 属性表（5 位）

| 代码 | 含义 | 代码 | 含义 |
|---|---|---|---|
| 0 | none | 9 | low ohms |
| 1 | open circuit (seen, continuity) | 10 | open glitch |
| 2 | short circuit (seen, continuity) | 11 | short glitch |
| 3 | glitch (seen, continuity) | 12 | peak |
| 4 | good diode (seen) | 13 | sourced |
| 5 | negative edge | 14 | simulated |
| 6 | positive edge (seen, Hz) | 15 | noise |
| 7 | high current | 16 | breakdown |
| 8 | hazardous voltage indicator | 17 to 31 | undefined |

### 4.8 289 各功能的实测编码

VERIFIED，依据一次 20 分钟的调查：期间让万用表依次切换各个功能（4,018 个实时数据包与 659 个 QDDB 显示快照比对，第 6 节）。“Prim/sec”是万用表报告的显示状态；表中给出的是实时流的 function 和 unit 代码。在万用表状态稳定时采集的 3,076 个数据包中，unit、SI 前缀和 decimals 与 QDDB 快照一致，只有 12 个数据包例外，且全部发生在量程切换的瞬间。约 88% 的数值落在相邻 QDDB 快照之间；其余数值对应的是变化快于约 1.8 秒快照间隔的输入。

| 289 显示状态 | 主值：function, unit | 副值 | 备注 |
|---|---|---|---|
| V DC | 12, V DC (2) | none | |
| V AC | 2, V AC (1) | none | |
| V AC with Hz | 2, Hz (5), attribute 6 (positive edge) | 2, V AC (1) | Hz 成为主值，与 LCD 一致。 |
| V DC with Hz | 12, Hz (5), attribute 6 | 12, V DC (2) | |
| V AC with dBm and Hz | 2, dBm (17) | 2, Hz (5) | |
| V AC with dBV | 2, dBV (31) | 2, V AC (1) | 带 REL 时主单位为 dB (16)。 |
| V AC with crest factor | 2, CF (32) | 2, V AC (1) | |
| V AC with REL percent | 2, unit 13 | none | 单位 13 承载 REL 百分比。 |
| mV AC / mV DC | 1 配 unit 1，或 11 配 unit 2；magnitude 为 milli | none | |
| V AC+DC | 3, unit 33 | 3, unit 0 (INVALID) | |
| V AC over DC / V DC over AC | 3, V AC (1) 或 V DC (2) | 3，另一个 | |
| V AC LoZ | 7, V AC (1) | 7, V DC (2) | |
| Ohms / low ohms | 40 或 42, ohm (11) | none | OL 为 state 4。 |
| Continuity | 39, ohm (11), attribute 1 open, 2 short, 3 glitch | none | |
| Conductance | 41, siemens (12), nano 前缀 | none | |
| Capacitance | 45, farad (15) | none | |
| Diode test | 46, V DC (2), attribute 4 good diode | none | |
| Temperature | 34, degrees C (7) 或 degrees F (8) | none | 跟随万用表的单位设置。 |
| mA DC / uA DC | 20 或 19, A DC (4) | none | |

### 4.9 实时数据包不携带的内容

VERIFIED。

| 缺失项 | 后果 |
|---|---|
| 模式标志（HOLD、AUTO HOLD、REL、MIN MAX、RECORD、低通滤波器） | 客户端无法得知某个模式是否已启用。 |
| MIN、MAX 和 AVERAGE 值 | 完全不传输。 |
| 作为万用表设置的量程、自动或手动量程 | 不存在：对于 289，valueRange 和 rangeDecade 始终为 0。 |
| 时间戳 | 无。客户端在数据包到达时打上时间戳。 |
| 低电量 | 无。电量特征 2A19 反映的是适配器，而不是万用表。 |

HOLD 和 REL 只体现在 LCD 主读数所显示的内容中：HOLD 期间是保持的值，REL 期间是相对值。要获取模式以及 min/max/average，请查询显示状态（QDDB，第 6 节）。

## 5. 适配器透传

### 5.1 控制点帧格式

请求以 `[opcode][parameters]` 的形式写入控制点 `B6982918`。没有长度字节、校验和或终止符。参数中的整数为 32 位小端序。

回复以通知形式在同一个特征上到达，格式为 `[status][echoed opcode][payload]`。批量数据单独经 `B6982919` 到达，为无头部的原始字节，按发送顺序排列。一次读取的所有缓冲区通知都先于控制点上的完成回复到达。

由于可能有其他回复在途，请按回显的操作码（第 1 字节）匹配回复，并忽略其他操作码的回复。

### 5.2 状态码

| 状态 | 名称 | 含义 |
|---|---|---|
| 0 | IDLE | DERIVED（名称来自应用）。回复中未见到。 |
| 1 | IN_PROCESS | VERIFIED：作为对 `8E`（发送）和 `82`（读取）的中间回复发出。继续等待最终回复。 |
| 2 | INCORRECT_MODEL | DERIVED。当所连万用表型号系列不符时，可能由型号专用的操作码返回。从未观察到。 |
| 3 | COMPLETE | VERIFIED。成功。 |
| 4 | CANCELLED | DERIVED。视为暂时性失败。未观察到。 |
| 5 | FAILED | DERIVED。视为暂时性失败。未观察到。 |

### 5.3 操作码表

| 操作码 | 名称 | 参数 | 回复载荷 | 状态 |
|---|---|---|---|---|
| `80` | GET_BUFFER_SIZE | 无 | 回复缓冲区的 32 位大小 | VERIFIED。缓冲区为空时返回大小 0。 |
| `81` | CLEAR_BUFFER | 无 | 无 | VERIFIED。每次事务的第一步。它是否同时清空发出方向的缓冲区尚无定论（5.6）。 |
| `82` | GET_BUFFER_DATA | offset (u32), length (u32) | 无；数据经 `B6982919` 到达 | VERIFIED。 |
| `83` | CANCEL_COMMAND | 无 | 无 | DERIVED。客户端允许使用，但从未发送。 |
| `84`, `85` | GET_189_LOG_DATA, GET_189_SAVED_DATA | 未知 | 未知 | DERIVED。已定义，官方应用和 open289 均从未使用。 |
| `86` | GET_1550_LOG_DATA | 无 | 记录数 (u16) | DERIVED。官方应用用于 1550/1555 绝缘测试仪。open289 不使用。 |
| `87` to `8C` | GET_289_SESSION_SUMMARY, RECORDED_SESSION_INFO, RECORDED_SESSION_RECORDS, SAVE_MINMAX, SAVE_PEAK, SAVE_MEASUREMENTS | 未知 | 未知 | DERIVED。以 289 命名，但官方应用从未发送。open289 不使用它们；下载通过透传查询完成。 |
| `8D` | APPEND_DATA_TO_OUT_BUFFER | 万用表命令的原始 ASCII 字节，不含 CR | 无 | VERIFIED。每次写入最多 17 个命令字节。 |
| `8E` | SEND_BUFFER_TO_INSTRUMENT | 无 | 无 | VERIFIED。 |
| `8F` | GET_ADAPTER_VERSION | 无 | ASCII `01.00.15,`（微控制器版本，逗号分隔） | VERIFIED。 |
| `90` | START_TEST | 无 | 状态字节 | DERIVED。在 1550/1555 上启动高压绝缘测试。NEVER SEND。 |
| `91` to `94` | STOP_TEST, SET_TEST_VOLTAGE, SET_TEST_FUNCTION, SET_TIME_LIMIT | 各不相同 | 各不相同 | DERIVED。1550/1555 测试控制。NEVER SEND。 |
| `95` | GET_MODEL_NAME | 无 | ASCII 型号名称 | VERIFIED。接 289 时载荷为空（回复 `03 95`）。 |

open289 默认只允许 `80` 到 `83`、`8D`、`8E`、`8F` 和 `95`（`83` 被允许但从未发送）。`84` 到 `8C` 需要显式选择启用，`90` 到 `94` 被拒绝。

### 5.4 写入大小限制

VERIFIED。长于 18 字节的控制点写入会被拒绝。因此透传命令被拆分为多次 `8D` 写入，每次最多携带 17 个命令字节（操作码加 17 字节为 18）。多次 `8D` 写入会按顺序累积在适配器的发出缓冲区中。

### 5.5 完整事务

一条万用表命令至少需要五次往返：`81`、`8D`（一次或多次）、`8E`、`80`、`82`（一次或多次）。

1. `81` 清空缓冲区。回复 `03 81`。
2. `8D <text>` 追加命令文本。每个 17 字节分片重复一次。每次回复 `03 8D`。不要包含 CR：适配器会自行追加。VERIFIED：发送 `ID` 会返回身份信息，而发送 `ID` 后再跟 CR 会多出一条回复 `1`（语法错误），因为适配器自己追加的 CR 被当作一条空的第二条命令。
3. `8E` 把缓冲区发送给万用表。适配器先回答 `01 8E`（处理中），仅当万用表的完整回复已被捕获后才回答 `03 8E`。这意味着无需轮询缓冲区大小。
4. `80` 返回 `03 80 <size:u32>`，即万用表回复的长度。
5. `82 <offset:u32> <length:u32>` 读取数据。适配器先回答 `01 82`，再把字节以通知形式推送到 `B6982919`（MTU 23 时每条 20 字节），最后发送 `03 82`。对于超过一次读取长度的回复，用递增的偏移量重复读取。open289 每次请求最多读取 400 字节。

带注释的示例，VERIFIED（命令 `QM`，主测量值；`>` 表示写入控制点，`<` 表示通知）：

```
>CP  81                                     CLEAR
<CP  03 81                                  complete
>CP  8d 51 4d                               APPEND "QM"
<CP  03 8d                                  complete
>CP  8e                                     SEND to meter
<CP  01 8e                                  in process (meter is answering)
<CP  03 8e                                  complete: full reply is buffered
>CP  82 00 00 00 00 1b 00 00 00             READ offset 0, length 0x1b = 27
<CP  01 82                                  in process
<BUF 30 0d 35 2e 30 36 32 32 45 30 2c 56 44 43 2c 4e 4f 52 4d 41   "0\r5.0622E0,VDC,NORMA"
<BUF 4c 2c 4e 4f 4e 45 0d                                         "L,NONE\r"
<CP  03 82                                  complete
```

缓冲区中保存的是万用表的原生回复 `0\r5.0622E0,VDC,NORMAL,NONE\r`，共 27 字节。实际使用中，客户端会先询问大小（`80`），此处为 `03 80 1b 00 00 00`，并将其用作长度。

故障处理。如果回复超时，把链路标记为失步，等待 0.5 秒，并在下一条命令之前丢弃过期的控制点回复。适配器还会为实时流自行轮询万用表，透传命令有时会与之冲突：此时万用表会回复语法错误，或适配器什么也不返回。open289 对只读查询在语法错误、无回复或适配器状态 4/5 之后最多重试 2 次。与适配器轮询的冲突在实现中有所说明，但没有任何已存储的捕获包含此类冲突，因此重试路径未被捕获数据覆盖。

### 5.6 缓冲区限制与批处理

| 项目 | 结果 | 状态 |
|---|---|---|
| 单次控制点写入 | 最多 18 字节 | VERIFIED |
| 发出方向（到万用表）缓冲区 | 每次发送合计 87 字节可行 | VERIFIED |
| 发出 181 字节 | 无回复 | VERIFIED |
| 发出 373 字节或更多 | 适配器挂死，直到断电重启 | VERIFIED |
| 回复大小 | 1,208 字节的回复完整返回 | VERIFIED |
| 一次发送中包含多个查询 | 可行；回复按顺序拼接返回 | VERIFIED |

open289 拒绝任何大于 87 字节的发送，因为更大的发送可能使适配器挂死。

批处理。以 CR 分隔的查询共用一次发送；适配器按顺序把它们发给万用表，回复被拼接在一起。在已验证的记录（读数索引 772）中，第一批 `QSRR 772,0` 到 `QSRR 772,7` 发出 87 字节，返回 1,208 字节。一批可容纳 87 字节内能放下的所有命令，最多 8 条。样本编号达到两位数后，只能放下 7 条（83 字节）。由于每个 `QSRR` 回复的长度固定（151 字节：`0` CR `#0`、146 字节载荷、CR），客户端按此帧大小拆分批量回复，并检查每一段都以 `0\r#0` 开头。

悬而未决的 CLEAR 问题。尚不清楚当字节已被追加但从未发送时，`81` 是否会清空发出缓冲区。因此 open289 统计自上次成功发送以来追加的字节数。在一次发送中途失败之后，它会拒绝任何新命令，只要该命令加上这些可能残留的字节后可能超过 87 字节。下一次成功的发送会将计数清零。仓库中的探测脚本 `tools/probe_clear.py` 旨在于硬件上解决这一问题（尚未运行）。

## 6. 经透传的 Fluke 287/289 串口协议

透传通路承载的是万用表自身的串口协议。在万用表一侧，链路为 115200 波特、8 位数据位、无校验、1 位停止位、无流控（DERIVED，来自公开资料；由适配器处理，因此客户端无需配置）。

### 6.1 帧格式与确认

命令是带可选参数的助记符，以 CR 结尾（由适配器添加）。助记符不区分大小写。万用表不回显命令名称。

回复为 `<ack> CR [payload CR]`。VERIFIED：ack 是一个 ASCII 数字。

| Ack | 名称 | 含义 |
|---|---|---|
| `0` | OK | 成功。后面可能跟有载荷。 |
| `1` | SYNTAX_ERROR | 命令未被理解。open289 对只读查询最多重试 2 次。与适配器轮询的冲突在实现中有所说明，但没有任何已存储的捕获包含此类冲突，因此重试路径未被捕获数据覆盖。 |
| `2` | EXECUTION_ERROR | 万用表此刻无法执行（6.9）。VERIFIED。 |
| `5` | NO_DATA | DERIVED。请求没有对应数据（例如不存在的已保存项索引）。 |

ASCII 载荷以逗号分隔；字符串可能带引号。二进制载荷以两个字节 `#0` 开头，以 CR 结尾。没有长度字段。由于字节 `0D` 可能出现在二进制数据内部，解析器应使用各布局中的计数字段或固定长度，而不是搜索 CR。

### 6.2 ASCII 命令

VERIFIED 表示该命令的回复出现在硬件捕获中。

| 命令 | 示例回复（ack 行之后） | 含义 | 状态 |
|---|---|---|---|
| `ID` | `FLUKE 289,V1.71,12345678` | 型号、固件、序列号 | VERIFIED |
| `IM` | 型号、版本、序列号等 | 长格式识别信息 | DERIVED |
| `QM` | `5.0622E0,VDC,NORMAL,NONE` | 主读数：以基本单位表示的值、单位、状态、属性。过载或无效为 `9.99999999E+37`。 | VERIFIED |
| `QDDA` | `V_DC,NONE,AUTO,VDC,5,0,OFF,0.000,0,2,LIVE,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873,PRIMARY,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873` | 文本形式的显示状态：主功能、副功能、量程模式、基本单位、量程、倍数、闪电标志、min/max 起始时间、模式数量及各模式、读数数量，然后是各读数（每个 9 个字段：id、value、unit、multiplier、decimals、digits、state、attribute、timestamp） | VERIFIED |
| `QDDB` | 二进制，34 + 30n 字节（6.5） | 与 QDDA 相同，二进制形式 | VERIFIED |
| `QSLS` | `1,0,1,2` | 已保存项计数：记录、min/max、峰值、测量 | VERIFIED |
| `QSMR i` | 二进制（6.7） | 已保存的测量 i | VERIFIED |
| `QMMSI i` | 二进制（6.7） | 已保存的 min/max 会话 i | VERIFIED |
| `QPSI i` | 二进制（6.7） | 已保存的峰值会话 i | VERIFIED |
| `QRSI i` | 二进制（6.7） | 记录会话 i | VERIFIED |
| `QSRR r,s` | 二进制，146 字节（6.8） | 一条记录的一个记录点；r 是 QRSI 给出的该记录的读数索引，s 是样本编号 | VERIFIED |
| `QSAVNAME n` | `Save` | 存储槽 n（0 到 7）的名称 | VERIFIED |
| `QBL` | `FULL` | 万用表电量 | VERIFIED |
| `QMEMLEVEL` | `OK` | 存储水平（EXHAUSTED, SEVERE, WARNING, OK） | VERIFIED |
| `QMP clock` | `1793999685` | 万用表时钟，单位为秒。本地墙上时间编码为 epoch，无时区。 | VERIFIED |
| `QMP prop`, `MP prop,val` | | 读取或设置万用表属性（自动关机、蜂鸣器、语言等） | DERIVED。设置不属于查询。 |
| `QEMAP name` | `49,0,LIMBO,1,V_AC,...` | 枚举表：先是数量，然后是 key,value 对 | DERIVED。已实现，但仓库日志中没有存储对应的捕获。 |
| `QSN`, `QCCV`, `QCVN`, `QMPQ` | | 序列号、校准计数与版本、带引号字符串的属性 | DERIVED |
| `QLCDBM offset` | 分块的 gzip 压缩 BMP 屏幕截图 | 屏幕截图 | DERIVED。未测试。 |
| `PRESS key[,HELD_DOWN]` | 仅 ack | 模拟按键（6.10） | VERIFIED |
| `OFF` | 仅 ack | 立即关闭万用表电源（6.11） | VERIFIED |
| `LEDT ON\|OFF` | 仅 ack | 电源 LED | DERIVED |
| `CSD ALL\|MEASUREMENT\|MIN_MAX\|PEAK\|RECORDED` | ack | 清除已保存数据。具有破坏性。 | DERIVED。从未发送。 |
| `RMP`, `RI`, `DS` | ack | 重置属性、重置仪表、默认设置。具有破坏性。 | DERIVED。从未发送。 |

### 6.3 二进制基本类型

| 类型 | 编码 |
|---|---|
| u16, s16, u32 | 小端序。 |
| double | 八字节：两个小端序 32 位字，高位字在前（“字交换”）。对于字节 b0 到 b7，大端序 IEEE-754 映像为 `b3 b2 b1 b0 b7 b6 b5 b4`。VERIFIED。 |
| Time | 以 double 表示的 POSIX 秒数，万用表本地时间，无时区。 |
| Overload | 值 `9.99999999e+37`。 |
| String | 最后一条记录之后的 ASCII，直到末尾的 CR（已保存的名称）。 |

校验值：字节 `00 00 14 40 00 00 00 00` 解码为 5.0。若按普通小端序读取，会得到约 5.3e-315，这是错误的。

### 6.4 读数记录（30 字节）

VERIFIED。

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0 | u16 | 读数 id（6.12） |
| 2 | double | 以基本单位表示的值 |
| 10 | u16 | 单位（6.12） |
| 12 | s16 | 单位倍数（SI 前缀的十的幂） |
| 14 | s16 | 小数位数 |
| 16 | s16 | 显示位数 |
| 18 | u16 | 状态（6.12） |
| 20 | u16 | 属性（6.12） |
| 22 | double | 时间戳 |

显示值：`value / 10^multiplier`，按 `decimals` 位数打印。读数 `02 00 | cb 3f 14 40 c8 29 3a 92 | 01 00 | 00 00 | 04 00 | 05 00 | 02 00 | 00 00 | ...` 表示 id 2（PRIMARY），约 5.0623，单位 VDC，倍数 0，4 位小数，5 位显示位数，状态 NORMAL，属性 NONE（真实的 QDDB 捕获）。

### 6.5 QDDB 布局（显示状态）

VERIFIED。`#0` 之后的载荷恰好为 `34 + 30n` 字节。

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0 | u16 | 主功能（qemap `primfunction`） |
| 2 | u16 | 副功能（qemap `secfunction`） |
| 4 | u16 | 量程模式（0 手动，1 自动） |
| 6 | u16 | 基本单位（qemap `unit`） |
| 8 | double | 量程最大值，例如 5.0 或 500.0 |
| 16 | s16 | 单位倍数 |
| 18 | u16 | 闪电标志（0 或 1） |
| 20 | double | min/max 起始时间，不在 min/max 模式时为 0 |
| 28 | u16 | 模式位掩码（6.12） |
| 30 | u16 | 未知，观察到为 0 |
| 32 | u16 | 读数数量 n |
| 34 | 30 x n | 读数记录（6.4） |

示例，VERIFIED（已缩短）：`03 00 | 00 00 | 00 00 | 01 00 | 00 00 14 40 00 00 00 00 ...` 表示 V DC（功能 3），无副功能，手动量程，单位 VDC，量程 5.0（字交换的 double）。未设置任何模式且没有副值时，n 为 2（LIVE、PRIMARY）。存在副显示时会增加 SECONDARY（带 Hz 的 V AC 或 V DC、波峰因数、V AC over DC、V DC over AC、V AC LoZ、V AC+DC：n = 3）。dB 功能会增加 DB_REF（带 dBV 的 V AC、带 dBm 和 Hz 的 V AC：n = 4）。温度会增加 TEMP_OFFSET（n = 3）。HOLD 和 AUTO HOLD 只设置掩码位（n 保持为 2）。REL 会增加 REL_LIVE 和 REL_REFERENCE。MIN MAX 会增加 MINIMUM、MAXIMUM 和 AVERAGE。

HOLD、REL 和 MIN MAX 等模式在这里表现为掩码位，并在记录列表中表现为额外的读数（MINIMUM、MAXIMUM、AVERAGE、REL_LIVE、REL_REFERENCE）。这是看到它们的唯一方式（4.9）。

### 6.6 测量头部

QSMR、QMMSI、QPSI 和 QRSI 包含与 QDDB 偏移 0 到 33 相同的 34 字节块（function、secondary、range mode、unit、range、multiplier、bolt、time、modes、spare、count），其后是 n 个读数和一个名称。

### 6.7 已保存的会话

除非另有标注，均为 VERIFIED。

| 命令 | 头部 | 内容 |
|---|---|---|
| `QSMR i` | 38 字节：u32 序号，然后是 34 字节块 | n 个读数，然后是名称 |
| `QMMSI i`, `QPSI i` | 54 字节：u32 序号，double 起始时间（偏移 4），double 结束时间（偏移 12），然后是位于偏移 20 的 34 字节块 | n 个读数（观察到的顺序：PRIMARY、MINIMUM、MAXIMUM、AVERAGE），然后是名称。对于峰值会话，min 和 max 使用单位 V 或 A (peak)。 |
| `QRSI i` | 78 字节：u32 序号，double 起始（4），double 结束（12），以秒为单位的 double 采样间隔（20），以分数表示的 double 事件阈值（28），u32 读数索引（36），u32 样本数（40），然后是位于偏移 44 的 34 字节块 | n 个读数，然后是名称 |

QRSI 中的读数索引是 QSRR 的第一个参数；样本编号从 0 到样本数减 1。名称是直到末尾 CR 的纯 ASCII。已保存项按 QSLS 报告的顺序从 0 开始编号。

### 6.8 QSRR，一条趋势记录

VERIFIED。固定 146 字节的载荷；在线路上回复为 `0` CR `#0` + 146 字节 + CR，共 151 字节。

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0 | double | 该区间的起始时间 |
| 8 | double | 结束时间 |
| 16 | 30 | MAXIMUM 读数（id 8） |
| 46 | 30 | MINIMUM 读数（id 7） |
| 76 | 30 | AVERAGE 读数（id 9）。该值是总和，而不是平均值。 |
| 106 | u32 | 累加到该总和中的样本数 n |
| 110 | 30 | PRIMARY 读数（id 2），记录起始时刻的值 |
| 140 | u16 | 记录类型（0 input，1 interval） |
| 142 | u16 | 稳定标志（0 不稳定，1 稳定） |
| 144 | u16 | 瞬态状态（0 无，1 量程上调，2 量程下调，3 过载，4 热电偶开路） |

“平均值是总和”规则。万用表在 AVERAGE 读数中存储 n 个样本的总和。当 n 不为零时，真实平均值为 `value / n`。open289 应用了这一除法，并在一份含 72 条记录的记录上对照最小值和最大值进行了验证。

### 6.9 哪些情况会返回 EXECUTION_ERROR

VERIFIED。当万用表上打开了菜单、结果页或保存对话框时，所有测量查询（QM、QDDA、QDDB）以及 QSLS 都返回 ack `2`。在转动旋钮期间也会短暂（几次轮询）出现这种情况。在万用表上关闭该页面后，或通过按键（6.10），查询即可恢复工作。DERIVED：在这类页面打开期间没有发送已保存数据的查询（QSMR、QMMSI、QPSI、QRSI、QSRR）（列表止于 QSLS）；预计它们同样返回 ack 2。DERIVED：公开资料报告称，万用表处于设置模式时返回 `2`。

### 6.10 PRESS 按键与屏幕状态机

`PRESS <key>` 模拟一次按下；`PRESS <key>,HELD_DOWN` 模拟一次长按。回复只有 ack（`0`）。按键（来自万用表的 buttonName 表）：`F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`。已在硬件上 VERIFIED 的有：HOLD、MINMAX、RANGE（含长按）、BACKLIGHT、F2、F4，以及 ONOFF 后接 F1（关机）。F3、UP、DOWN、LEFT、RIGHT 和 INFO 未经实测。DERIVED（来自公开资料）：万用表表中的其他按压类型为 RELEASED 和 REPEATED。按键命令在屏幕变化之前就已返回，因此客户端应等待约一秒，再检查显示状态。open289 默认等待 1.0 秒。

在 289 上验证的效果（V DC 量程 5，自动）：

| 按键 | 效果 |
|---|---|
| `HOLD` | 切换 HOLD 模式位。 |
| `RANGE` | 第一次按下退出自动量程并保持当前量程（AUTO 5 变为 MANUAL 5）；之后每按一次步进一个量程（MANUAL 5 变为 MANUAL 50）。 |
| `RANGE` 长按 | 返回自动量程。 |
| `BACKLIGHT` | 循环切换背光：关、亮、暗。通过查询看不到显示状态的变化。 |
| `MINMAX` | 进入 MIN MAX（模式位 MIN_MAX_AVG，设置 min/max 起始时间）。再次按下（无论是否长按）会停止并打开结果页。长按与短按效果相同。 |
| `ONOFF` | 显示关机确认（6.11）。 |

min/max 的屏幕状态机，VERIFIED。“Queries”指测量查询。

| 状态 | 查询 | 按键 | 下一状态 |
|---|---|---|---|
| 测量屏幕 | OK | `MINMAX` | MIN MAX 运行中 |
| MIN MAX 运行中 | OK，模式 MIN_MAX_AVG | `MINMAX`（或长按） | 结果页 |
| 结果页 | EXECUTION_ERROR | `F1` 重新开始；`F2` 保存；`MINMAX` 或 `F4` 关闭 | 再次运行；保存页；测量屏幕 |
| 保存页（在结果页中用 F2 打开） | EXECUTION_ERROR | `F1` +名称（未实测）；`F2` Save；`F4` Close | 名称输入（未测试）；保存并停留在保存页；测量屏幕 |

在保存页上按 `F2`（Save）再按 `F4` 之后，QSLS 显示多了一个 min/max 条目（测试中 min/max 计数从 0 变为 1），且查询恢复工作。在保存页上按背光键没有任何可见变化。

### 6.11 关机

VERIFIED。按下 `ONOFF` 会显示一条消息，提示关闭万用表将中断通信；`F1` 确认（F1 OK，F4 取消）。原始命令 `OFF` 会立即关闭万用表电源，无需确认。随后 IR3000 会自行关机。无法远程重新开启任何一台设备：请使用万用表旋钮和适配器按钮。

### 6.12 枚举表（qemap）

DERIVED。这些表来自对多个固件世代的 287 和 289 万用表的公开捕获，并与从硬件解码出的布局相符（例如 function 3 = V_DC，unit 1 = VDC）。健壮的客户端可在运行时通过 `QEMAP <name>` 获取它们。

| 表 | 取值 |
|---|---|
| primfunction | 0 LIMBO, 1 V_AC, 2 MV_AC, 3 V_DC, 4 MV_DC, 5 V_AC_OVER_DC, 6 V_DC_OVER_AC, 7 V_AC_PLUS_DC, 8 MV_AC_OVER_DC, 9 MV_DC_OVER_AC, 10 MV_AC_PLUS_DC, 11 A_AC, 12 MA_AC, 13 UA_AC, 14 A_DC, 15 MA_DC, 16 UA_DC, 17 A_AC_OVER_DC, 18 A_DC_OVER_AC, 19 A_AC_PLUS_DC, 20 MA_AC_OVER_DC, 21 MA_DC_OVER_AC, 22 MA_AC_PLUS_DC, 23 UA_AC_OVER_DC, 24 UA_DC_OVER_AC, 25 UA_AC_PLUS_DC, 26 TEMPERATURE, 27 OHMS, 28 CONDUCTANCE, 29 CONTINUITY, 30 CAPACITANCE, 31 DIODE_TEST, 32 V_AC_LOZ, 33 OHMS_LOW, 34 to 48 校准功能（CAL_*） |
| secfunction | 0 NONE, 1 HERTZ, 2 DUTY_CYCLE, 3 PULSE_WIDTH, 4 DBM, 5 DBV, 6 DBM_HERTZ, 7 DBV_HERTZ, 8 CREST_FACTOR, 9 PEAK_MIN_MAX |
| autorange | 0 MANUAL, 1 AUTO |
| unit | 0 NONE, 1 VDC, 2 VAC, 3 VAC_PLUS_DC, 4 V (peak), 5 ADC, 6 AAC, 7 AAC_PLUS_DC, 8 A (peak), 9 OHM, 10 SIE, 11 Hz, 12 S, 13 F, 14 CEL, 15 FAR, 16 PCT, 17 dB, 18 dBV, 19 dBm, 20 CREST_FACTOR |
| bolt | 0 OFF, 1 ON |
| mode（位掩码） | 1 AUTO_HOLD, 2 AUTO_SAVE, 4 HOLD, 8 LOW_PASS_FILTER, 16 MIN_MAX_AVG, 32 RECORD, 64 REL, 128 REL_PERCENT, 256 CALIBRATION |
| readingid | 1 LIVE, 2 PRIMARY, 3 SECONDARY, 4 REL_LIVE, 5 BARGRAPH, 7 MINIMUM, 8 MAXIMUM, 9 AVERAGE, 11 REL_REFERENCE, 12 DB_REF, 13 TEMP_OFFSET |
| state | 0 INACTIVE, 1 INVALID, 2 NORMAL, 3 BLANK, 4 DISCHARGE, 5 OL, 6 OL_MINUS, 7 OPEN_TC |
| attribute | 0 NONE, 1 OPEN_CIRCUIT, 2 SHORT_CIRCUIT, 3 GLITCH_CIRCUIT, 4 GOOD_DIODE, 5 LO_OHMS, 6 NEGATIVE_EDGE, 7 POSITIVE_EDGE, 8 HIGH_CURRENT |
| recordtype | 0 INPUT, 1 INTERVAL |
| isstableflag | 0 UNSTABLE, 1 STABLE |
| transientstate | 0 NON_T, 1 RANGE_UP, 2 RANGE_DOWN, 3 OVERLOAD, 4 OPEN_TC |
| buttonName | 606 UP, 607 DOWN, 608 LEFT, 609 RIGHT, 650 F1, 651 F2, 652 F3, 653 F4, 654 HOLD, 655 MINMAX, 656 RANGE, 657 INFO, 658 BACKLIGHT, 660 ONOFF |
| presstype | 0 PRESSED, 1 HELD_DOWN, 2 RELEASED, 3 REPEATED |
| sessionType（CSD 使用） | 0 ALL, 1 RECORDED, 2 MIN_MAX, 3 PEAK, 4 MEASUREMENT |

请注意，实时记录（第 4 节）与串口协议使用不同的编号：实时单位代码 2 是 V DC，而串口单位代码 1 是 VDC。不要混用这些表。

## 7. 时序

| 操作 | 时间 | 状态 |
|---|---|---|
| 实时通知 | 约每秒 4 次 | VERIFIED |
| 一条透传命令（clear、append、send、size、read） | 约 1.3 秒 | VERIFIED：72 条未批处理的 QSRR（每条一次透传）耗时 98.1 秒，每条 1.36 秒；调查中 QDDB 快照之间至少相隔 1.17 秒 |
| 每次往返一条 `QSRR`（下载记录） | 在 72 条记录的下载中每条记录 1.36 秒 | VERIFIED |
| 每次发送最多 8 条 `QSRR`（87 字节内能放下的数量） | 同一份记录上每条记录 0.44 秒 | VERIFIED |

完整的显示快照（QDDB）是一次透传，因此显示轮询实际上约每 1.5 到 2 秒一次（调查中快照间隔的中位数为 1.8 秒）。实时读数是获得更快更新的唯一途径，这正是 open289 同时提供两者的原因：实时流用于实时采集与记录，透传用于显示状态、已保存数据、记录和按键操作。

## 8. 安全摘要

| 规则 | 原因 |
|---|---|
| 切勿写入 `B6982911`、`B6982912`，或服务 `B6981804` 中的任何内容。 | 固件更新器与射频映像通路。更新器复用适配器的操作码值；`80` 会擦除闪存。一个失误就可能使适配器变砖。 |
| 切勿发送适配器操作码 `90` 到 `94`。 | 它们控制 1550/1555 测试仪上的高压绝缘测试。 |
| 切勿在一次发送中向万用表发送超过 87 字节。 | 181 字节没有得到回复；373 字节或更多会使适配器挂死，直到断电重启。 |
| 默认只传递查询命令（`ID`、`IM`、`Q...`）。 | `MP`、`CSD`（清除存储）和 `RI`（重置）会更改或擦除万用表状态。例外是 `PRESS`，仅限于列出的 14 个按键。 |
| 除非明确允许，否则屏蔽 `ONOFF`。 | 关机会中断通信，且无法远程撤销。万用表会要求确认。 |
| 切勿接受非 ASCII 命令文本。 | 适配器原样转发字节，万用表的反应未定义。 |
| 将 `OFF` 视为破坏性命令。 | 它会立即关闭万用表电源，不作询问。 |
| 只使用一个连接。 | 适配器只接受一个中心设备。 |

## 9. 未决问题

| 问题 | 说明 |
|---|---|
| CLEAR（`81`）是否会清空发出缓冲区？ | 未验证。open289 在发送失败后统计可能残留的字节。已有探测脚本。 |
| 发出缓冲区的确切容量是多少？ | 87 字节可行，181 字节无回复；上限介于两者之间（未测量）。 |
| 最大回复大小与读取分块大小 | 已验证 1,208 字节；更大的回复和更大的单次读取未经测量。 |
| 操作码 `84` 到 `8C` 的参数与回复格式 | 官方应用从未使用；可能是型号专用的宏。猜错时预计返回状态 2 或 5。 |
| `83` CANCEL 的效果，以及 `00` IDLE 和 `02` INCORRECT_MODEL 的表现 | 从未观察到。 |
| CNX ID 中 `01` 以外的值是否有效，`B6982903`、`B6982904`、`B6982905` 和 `B698290E` 有何作用？ | 未测试。未写入。 |
| 实时通知是否需要 CNX ID？ | 控制点使用期间见到了实时通知；这一点并未单独分离测试。 |
| 读取控制点会返回什么？ | VERIFIED：新连接时为 `00`，就绪之前为最后写入的操作码字节（`8f`、`95`、`80`）。就绪之后的读取未经检查。 |
| 脱离状态的适配器（`FLUKE PC3000` 型号字符串）的行为 | 仅来自应用。 |
| 固件更新特征 | 有意从未操作过。 |
| 287 的行为 | 仅测试了 289。据公开资料，287 使用相同的协议。 |
| 针对该硬件的 `QEMAP`、`IM`、`MP`、`QLCDBM`、`LEDT` | 根据公开资料实现或列出；未被已存储的捕获覆盖。 |
| 其他 Fluke 软件所描述的带帧链路层（DLE/STX 加 CRC-16/X-25） | 在任何测试中，适配器均未使用。 |
