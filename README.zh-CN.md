> 本文是 [English README](README.md) 的简体中文翻译，如有出入，以英文版为准。

# open289

[English](README.md) | 简体中文

[![CI](https://github.com/FrostFlops/open289/actions/workflows/ci.yml/badge.svg)](https://github.com/FrostFlops/open289/actions/workflows/ci.yml)

这是 **Fluke 287/289 万用表**的开源 Python 包、命令行界面和 MCP 服务器，通过 **Fluke IR3000 FC** 红外转蓝牙 LE 适配器读取数据。它的目标是让 AI 智能体（Claude Code 或任意 MCP 客户端）无需 Fluke Connect，就能自己读取、记录并下载真实测量数据。它可以读取实时数值（约 4 次读数/秒）、完整的显示状态（功能、量程、HOLD / MIN MAX / REL 模式、MIN / MAX / AVERAGE）、表内保存的所有内容（测量值、MIN MAX 和峰值会话、趋势记录），可以带自动重连地连续数小时记录为 CSV 或 JSONL，可以下载趋势记录，还能按下万用表的面板按键。

万用表是通过适配器对表自身串行命令集（`ID`、`QM`、`QDDB`、`QSRR` 等）的透传来访问的；协议是在真实硬件上摸索出来的，详见 `docs/PROTOCOL.md`。

## 状态与已测试硬件

版本 0.1.0（beta）。2026-11-06 在真实硬件上验证过的配置：一个无线电固件 01.00.01、MSP 固件 01.00.15 的 IR3000 FC，一台固件 V1.71 的 Fluke 289，以及一台使用 Intel 蓝牙的 Windows 11 电脑（Python 和 bleak，基于 WinRT）。

- 只测试过 **289**。287 使用相同的协议，理应可用，但未经测试。
- Linux 和 macOS：bleak 支持它们，且蓝牙路径中没有仅限 Windows 的代码，但从未在真实硬件上试过。CI 在 Linux 和 Windows 上运行全部测试，但只针对虚拟万用表（见[虚拟万用表](#虚拟万用表)）。
- 实测速度：实时读数每秒约 4 次；一次透传命令约 1.3 s（QDDB 显示快照间隔约 1.2 到 1.8 s）；趋势记录的下载速度为每条记录 0.44 s（每次往返最多 8 条记录，以 87 字节能放下的数量为限）。

## 安装

环境要求：Python 3.10+、蓝牙 LE 无线电、一台 Fluke 287/289 和一个 Fluke IR3000 FC。

```
pip install open289
```

这会安装命令 `open289` 和 `open289-mcp`（`python -m open289 ...` 与 `open289 ...` 等价）以及唯一的依赖 `bleak`。

从克隆的仓库安装，使用 uv 或普通 venv：

```
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e .
```

```
python -m venv .venv
.venv\Scripts\pip install -e ".[test]"        # Linux / macOS: .venv/bin/pip
```

Windows 注意事项：

- 打开蓝牙（设置，蓝牙和设备）。不需要配对或绑定。
- 控制台可能使用旧的代码页（GBK、CP1252）；测量字符串中含有 `µ`、`Ω` 和 `°`。CLI 会把自己的 stdout 和 stderr 重新配置为 UTF-8，克隆仓库中的启动脚本则用 `-X utf8` 启动 Python 来处理其余情况：`open289.cmd live -n 10`（Windows）和 `./open289.sh live -n 10`（POSIX shell、Git Bash）。通过 pip 安装时，`python -X utf8 -m open289 ...` 效果相同。
- 如果 pip 的 `Scripts` 文件夹不在 `PATH` 中，请调用 `python -m open289`，或给出 `open289.exe` 的完整路径。

## 硬件连接

1. 把 IR3000 FC 夹在 287/289 背面的红外口上（万用表必须已开机）。
2. 唤醒适配器：按住它的按钮超过 1 s，直到 LED 闪绿灯。它只有在唤醒且未连接时才会发送蓝牙广播。
3. 关闭手机或平板上的 Fluke Connect。**适配器只接受一个蓝牙连接。** 当其他程序占用它时，`open289` 会报告 `NOT_FOUND`（适配器不广播）或 `BUSY`。open289 自身会加锁，因此两个 open289 进程不会争抢适配器：第二个会得到 `BUSY`。
4. 检查：`open289 scan` 会列出适配器；`open289 info` 显示万用表。当适配器已唤醒但没有接在已通电的万用表上时，`info` 会报告 `"meter": {"attached": false}`。

适配器和万用表都会自己进入休眠或关机，且无法远程开机；请用按钮唤醒适配器，用旋钮唤醒万用表。

## 快速开始

```
open289 scan                                # find the adapter
open289 info                                # adapter and meter identity, batteries, clock, saved counts
open289 read                                # the whole display now: function, range, modes, every reading
open289 live -n 20                          # 20 real-time readings, one JSON line each
open289 log --duration 10m --out volts.csv  # log to a file; ends by itself, or: open289 stop / Ctrl+C
open289 stop                                # end a running live / read --every / log cleanly
```

除非给出 `--text`，每条命令都输出 JSON（每行一个对象）；加上 `--sim` 可以在没有硬件的情况下试用全部功能。虚拟万用表的输出如下（并非编造；硬件上的数值会不同，字段相同）：

```
$ open289 --sim scan
{"ok":true,"cmd":"scan","devices":[{"address":"FA:28:90:00:00:01","name":"IR 3000 FC","rssi":-60}]}

$ open289 --sim info
{"ok":true,"cmd":"info","adapter":{"address":"FA:28:90:00:00:01","name":"IR 3000 FC","radio_fw":"01.00.01","msp_fw":"01.00.15","battery_pct":100},"meter":{"attached":true,"model":"FLUKE 289","firmware":"V1.71","serial":"12345678","battery":"FULL","memory_level":"OK","clock_epoch":1794013461,"clock":"2026-11-07 01:04:21.000"},"saved":{"recordings":1,"minmax":0,"peak":1,"measurements":2}}

$ open289 --sim --text info
adapter : FA:28:90:00:00:01  radio fw 01.00.01  MSP fw 01.00.15  battery 100%
meter   : FLUKE 289  firmware V1.71  serial 12345678
battery : FULL   memory: OK   clock: 2026-11-07 01:04:31.000 (meter local time)
saved   : {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}
```

`info.meter.clock` 是万用表自己的时钟（本地时间，按表上设置的为准），不是电脑的时钟。

## 实时采集

| 命令 | 数据来源 | 速率 | 携带内容 |
|---|---|---|---|
| `live` | 适配器的 BLE 通知 | ~4/s，每个通知一行 JSON | 主读数，以及万用表显示副读数时的副读数 |
| `read` | 通过透传发出的一次 `QDDB` 显示查询 | 一次往返，~1.3 s | 整个显示：功能、副功能、量程、自动量程、模式、每个读数 |
| `read --every 1s` | 同上，重复执行 | 每个间隔约一次（至少 ~1.3 s） | 每行一个快照；以一行 `done` 结束 |

需要快速数值和记录时用 `live`；需要知道万用表在做什么（HOLD？MIN MAX？REL？哪个量程？）时用 `read`；需要随时间变化的模式或 MIN / MAX / AVERAGE 时用 `read --every`。`live` 和 `read` 可以通过 `log --snapshot-every`（见下一节）在同一个连接上组合使用。

```
$ open289 --sim live -n 3
{"type":"reading","t":1794027861.982,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"type":"reading","t":1794027862.227,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"type":"reading","t":1794027862.481,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"ok":true,"cmd":"live","event":"done","readings":3,"duration_s":0.748,"rate_hz":4.01,"stopped_by":"count"}
```

### `live` 读数行

| 字段 | 含义 |
|---|---|
| `type` | `"reading"` |
| `t` | 通知到达时的电脑时间，Unix 秒（3 位小数） |
| `value` | 以 **SI 基本单位**表示的数值（V、A、ohm、F、Hz、s、S；摄氏度 / 华氏度和百分比保持原样）：1.5 mV 即 `0.0015`。过载（`OL`）或空白 / 无效显示时为 `null` |
| `unit` | 基本单位（不含 SI 前缀；前缀在 `display` 中）：`V DC`、`V AC`、`V AC+DC`、`A DC`、`Ω`、`S`、`Hz`、`F`、`s`、`°C`、`°F`、`%`、`dB`、`dBV`、`dBm`、`CF` |
| `display` | 万用表显示的文本，带有显示屏上的 SI 前缀和小数位（`5.0625 V DC`、`OL V AC`） |
| `state` | `NORMAL`、`OL_RANGE`（过载）、`BLANK`（自动量程消隐）、`INVALID`（旋钮切换期间） |
| `function` | 实时功能名称（`VDC`、`VAC`、`mVDC`、`Ω`、`Continuity`、`Diode`、`Temp`、`mADC`，……） |
| `attribute` | 功能带有该属性时出现：Hz 的正沿、通断的断开 / 短路 / 毛刺、良好的二极管 |
| `capture` | 读数为捕获值（HOLD 或类似状态）时为 `true` |
| `secondary` | 万用表显示副值时出现：一个对象，含相同的 `value`、`unit`、`display`、`state`、`function` 字段 |
| `raw` | 仅在使用 `--raw` 时出现：16 个通知字节的十六进制 |

主读数和副读数就是万用表主显示和副显示所显示的内容。在电压功能上使用 Hz 时，Hz 值成为主读数，电压成为副读数，与万用表上一致：

```
$ open289 --sim live -n 1            # V AC with the Hz secondary function
{"type":"reading","t":1794027906.375,"value":60.002,"unit":"Hz","display":"60.002 Hz  [positive edge]","state":"NORMAL","function":"VAC","attribute":"positive edge","secondary":{"value":null,"unit":"V AC","display":"OL V AC","state":"OL_RANGE","function":"VAC"}}
```

**`live` 不携带的内容：** 没有模式标志（HOLD、MIN MAX、REL、RECORD），没有 MIN / MAX / AVERAGE 值，没有量程，也没有显示快照词汇表中的功能名称。HOLD 时主读数是保持值，REL 时是相对值。这些内容请使用 `read`（或 `log --snapshot-every`）。`live` 运行到 `-n`、`--for` 或 `open289 stop` 时结束，并打印一行 `done`（`readings`、`duration_s`、`rate_hz`、`stopped_by`：`count`、`duration`、`stop`）；Ctrl+C 结束时不带摘要（退出码 130）。`--timeout`（默认 10 s）会把静默的链路变成错误，而不是无限等待。

### 显示快照（`read`）

```
$ open289 --sim read
{"ok":true,"cmd":"read","t":1794027861.456,"function":"V_DC","secondary_function":"NONE","autorange":true,"range":5.0,"unit":"VDC","modes":["HOLD"],"readings":[{"id":"LIVE","value":5.0613,"unit":"VDC","display":"5.0613 V DC","state":"NORMAL","attribute":"NONE","decimals":4,"time":"2026-11-07 01:04:21.456","ts":1794013461.4560547},{"id":"PRIMARY","value":5.0625,"unit":"VDC","display":"5.0625 V DC","state":"NORMAL","attribute":"NONE","decimals":4,"time":"2026-11-07 01:03:58.325","ts":1794013438.3251953}],"hazard":false,"primary":{"id":"PRIMARY",...}}
```

| 字段 | 含义 |
|---|---|
| `t` | 查询时的电脑时间，Unix 秒 |
| `function` | 主功能：`V_DC`、`V_AC`、`MV_DC`、`OHMS`、`CONTINUITY`、`DIODE_TEST`、`CAPACITANCE`、`TEMPERATURE`、`MA_DC`，…… |
| `secondary_function` | `NONE`、`HERTZ`、`PEAK_MIN_MAX`，…… |
| `autorange`、`range` | 自动量程的开 / 关，以及量程的满量程值（以基本单位表示；`5.0` = 5 V 量程） |
| `unit` | 主单位代码（`VDC`、`OHM`、`CEL`，……） |
| `modes` | 当前启用的模式，例如 `HOLD`、`MIN_MAX_AVG`、`REL`、`RECORD`；空列表 = 无 |
| `readings` | 屏幕上的每个数字，按显示顺序排列（见下） |
| `primary` | `readings` 中 `PRIMARY` 条目的副本 |
| `hazard` | 万用表显示高压警告（闪电符号）时为 `true` |
| `minmax_start` | MIN MAX 的启动时间，万用表本地时间（仅在 MIN MAX 启用时出现） |

`readings` 的每个条目包含：`id`（`LIVE`、`PRIMARY`、`SECONDARY`、`MINIMUM`、`MAXIMUM`、`AVERAGE`、`REL_REFERENCE`、`TEMP_OFFSET`，……）、`value`（SI 基本单位，OL 时为 `null`）、`unit`、`display`、`state`、`attribute`、`decimals`、`time`（万用表本地时间）和 `ts`（同一个万用表时钟值的数值形式：本地挂钟时间编码为纪元秒，不含时区；与 `t` 不可比较）。`LIVE` 是瞬时输入；`PRIMARY` 是主读数，在 HOLD 或 REL 下与 `LIVE` 不同。MIN MAX 启用时，快照还包含 `MINIMUM`、`MAXIMUM` 和 `AVERAGE`：

```
$ open289 --sim press MINMAX ; open289 --sim --text read
00:05:09.566  V_DC   range AUTO 5   modes MIN_MAX_AVG
    LIVE           5.0626 V DC            2026-11-07 01:05:09.567
    PRIMARY        5.0626 V DC            2026-11-07 01:05:09.567
    MINIMUM        5.0612 V DC            2026-11-07 01:05:09.099
    MAXIMUM        5.0631 V DC            2026-11-07 01:05:07.500
    AVERAGE        5.0621 V DC            2026-11-07 01:05:09.567
```

`read --every 1s [-n N] [--for 5m]` 每行输出一个快照（`ok`、`cmd`、`t`，……）；某次查询失败（例如 METER_BUSY）时输出一行 `{"ok":false,...}` 但不结束运行；最后输出 `{"event":"done","snapshots":N,"stopped_by":...}`。

## 记录

`log` 在前台把实时读数写入文件，直到 `--duration`、`open289 stop` 或 Ctrl+C。只要 `--reconnect-for` 允许重试，它就不会因为链路丢失而停止。

```
open289 log --duration 10m --out volts.csv
open289 log --duration forever --out run.jsonl --snapshot-every 10s     # until: open289 stop
open289 log --every 1s --duration 8h --out night.csv                    # one reading per second
```

| 选项 | 默认值 | 含义 |
|---|---|---|
| `--out`、`-o` | `data/log_<time>.csv` | 输出文件（`.csv` 或 `.jsonl`；相对于当前文件夹；默认位于[数据文件夹](#数据文件夹)） |
| `--format csv\|jsonl` | 取自 `--out` 的扩展名，否则为 csv | 文件格式 |
| `--duration`、`-d` | `forever` | 记录时长，从首次连接时开始计算：`30s`、`10m`、`8h`、`1.5h`、`2d`、`forever` |
| `--every` | `0` | 每个间隔一个读数（每个节拍取最近的读数）；`0` = 每个通知都记录，~4/s |
| `--snapshot-every` | `0`（关闭） | 同时按此间隔记录完整显示（模式、MIN / MAX / AVG），例如 `10s` |
| `--status-every` | `10s` | 在 stdout 上输出状态行；`0` = 关闭 |
| `--reconnect-for` | `5m` | 链路丢失后持续重试的时长；`0` = 第一次丢失就结束记录 |

**CSV** 列：`pc_time`（Unix 秒）、`local_time`（电脑本地时间，含毫秒）、`value`、`unit`、`display`、`state`、`function`、`attribute`、`secondary_value`、`secondary_unit`、`secondary_display`：

```
pc_time,local_time,value,unit,display,state,function,attribute,secondary_value,secondary_unit,secondary_display
1794027865.921,2026-11-07 00:04:25.921,5.062,V DC,5.0620 V DC,NORMAL,VDC,,,,
1794027866.168,2026-11-07 00:04:26.167,5.062,V DC,5.0620 V DC,NORMAL,VDC,,,,
```

`value` 以 SI 基本单位表示，过载时为空。使用 `--snapshot-every` 时，CSV 记录会把快照放入旁路文件 `<file>.display.jsonl`，每行一个 JSON 对象：`t`（Unix 秒）、`ack`、`hex`（`QDDB` 的原始应答）和 `display`（与 `read` 快照相同的对象）。查询失败的快照写成 `{"t":..., "ack":"ERROR", "error":{...}}`，记录继续进行。

**JSONL** 每行一个对象，带有 `type`：

| `type` | 内容 |
|---|---|
| `ble` | 一个读数：`live` 行的字段（`t`、`value`、`unit`、`display`、`state`、`function`、`attribute`、`secondary`）加上 `hex`（16 个通知字节） |
| `qddb` | 一个显示快照（`t`、`ack`、`hex`、`display`），仅在使用 `--snapshot-every` 时出现 |
| `event` | `link_lost`（带 `reason`）和 `reconnected`，均带 `t` |

```
{"t": 1794027869.307, "value": 5.0612, "unit": "V DC", "display": "5.0612 V DC", "state": "NORMAL", "function": "VDC", "type": "ble", "hex": "b4c50008020c00000000000000000000"}
```

**抽样。** `--every 1s` 每秒写一行：取当时最新的读数。摘要中的统计量因此只覆盖已写入的行。需要每个通知（或要观察瞬态）时用 `--every 0`，长时间运行时用 `--every`。

**状态行**在运行期间输出到 stdout（最先是一行 `started`，找到并启用适配器后输出 `connected`，之后每隔 `--status-every` 输出一行 `status`）：

```
{"ok":true,"cmd":"log","event":"started","file":"...x.csv","format":"csv","duration_s":3.0,"every_s":0.0,"snapshot_every_s":1.0}
{"ok":true,"cmd":"log","event":"connected","connect_s":2.41,"model":"FLUKE 289","adapter":"FA:28:90:00:00:01"}
{"ok":true,"cmd":"log","event":"status","elapsed_s":3.5,"records":4,"last":"5.0627 V DC"}
```

**连接需要时间。** 每条命令都要先看到适配器的广播。IR3000 FC 每隔几秒才广播一次，而在上一次连接刚结束后，它可能要约 20 s 才重新开始广播（硬件实测）。因此 `--duration` 从首次连接时开始计算（`connect_s` 表示找到适配器用了多久），`--duration 30s` 的记录确实包含约 30 s 的读数。

**重连与间隙。** 当蓝牙链路中断（万用表关机、适配器休眠、超出范围、"10 s 没有实时读数"）时，记录会输出 `link_lost`（带 `retry_for_s`），在 JSONL 文件中写入一个 `link_lost` 事件，每 3 s 重试一次，成功后输出 `reconnected`（带 `gap_s`）并继续追加到同一个文件。如果链路在 `--reconnect-for` 内没有恢复，记录会以 `stopped_by: "link_lost"` 结束，已写入的数据保留。只有当一个读数都没记录到时才会以 `LINK_LOST` 失败；从未连上的记录会立即以真实错误（`NOT_FOUND`、`DETACHED`，……）失败。

**停止。** `open289 stop`（从另一个终端或进程）约一秒内结束它；Ctrl+C、到达时长以及无法恢复的链路丢失同样会结束它。每写一行都会刷新文件，因此进程被杀最多只丢失最后一行。

**摘要**（最后一行，`"event":"done"`）：

```
{"ok":true,"cmd":"log","event":"done","file":"...x.csv","format":"csv","records":12,"packets":12,"snapshots":2,"started":1794027863.2,"ended":1794027868.7,"duration_s":5.535,"connect_s":2.41,"logging_s":3.125,"stopped_by":"duration","gaps":[],"stats":{"V DC":{"n":12,"min":5.0614,"max":5.0629,"mean":5.0622}},"meter":{"model":"FLUKE 289","adapter":"FA:28:90:00:00:01"},"display_file":"...x.csv.display.jsonl"}
```

`records` 为已写入的行数，`packets` 为收到的通知数，`snapshots` 为已获取的显示快照数，`duration_s` 为整条命令的耗时，`connect_s` 为首次连接前的耗时，`logging_s` 为此后的记录时长，`stopped_by`（`duration`、`stop`、`link_lost`、`interrupted`），`gaps`（`{from, to, reason}` 的列表，Unix 秒），`stats`（每种单位主值的 `n`、`min`、`max`、`mean`；过载不计入）以及 `display_file`（仅限带快照的 CSV）。`--text` 输出一个两行的版本，数字相同。

## 已保存数据

```
open289 memory                       # everything saved in the meter (--text --detail lists every reading)
open289 recording 0 --out rec0.csv   # download trend recording 0
open289 recording 0 --out rec0.csv --start 40   # resume an interrupted download (appends)
```

`memory` 先读取各类计数，再读取每个已保存的条目，并输出一个 JSON 对象：`counts`（`measurements`、`minmax`、`peak`、`recordings`）和 `items`。每个条目包含 `kind`（`measurement`、`minmax`、`peak`、`recording`）、`index`（在其类别内的索引：这就是 `recording INDEX` 所取的值）、`name`、`seq`、与显示快照相同的字段（`function`、`modes`、`readings`、`primary`，……）、会话条目的 `start` / `end`，以及记录条目的 `interval_s`、`event_threshold`、`records`、`reading_index` 和 `duration_s`。如果有些条目无法读取，结果中会有 `"ok":false` 和一个 `errors` 列表，退出码为 6（可读取的条目仍会列出）。每个已保存条目都是一次查询，所以 `memory` 的时限是 600 s 而不是 120 s。

```
$ open289 --sim --text memory
saved items: {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}
measurement #0   'Save 1'         2026-09-29 03:15:52.785  TEMPERATURE  34.7 °C
measurement #1   'Save 2'         2026-11-01 01:56:42.182  CONTINUITY  OL Ω [OPEN_CIRCUIT]
peak        #0   'Save 3'         2026-11-06 21:29:01.471  V_DC  0.005 V DC
recording   #0   'Save 4'         2026-11-06 21:40:19.248  V_DC  1.9874 V DC  72 records, interval 1 s, 1.0 min
```

**记录下载。** `recording INDEX` 获取某个记录（万用表的 RECORD 模式）的趋势记录，每条记录写一行。它每次往返最多批量发出 8 个查询，在硬件上每条记录耗时 0.44 s（`--no-batch`：每次往返一个，约 1.4 s）。失败的记录会重试（最多再试 3 次）。如果万用表显示了菜单，下载会以 `METER_BUSY` 停止，并给出提示，说明从哪条记录继续；记录失败（`PROTOCOL_ERROR`）时同样如此。用相同的 `--out` 和 `--start K` 恢复：文件会被追加，不会再写第二个表头。`--limit K` 在记录 K 之前停止（是结束索引，不是数量）。事件：`started`，每 10 s 一次的 `progress`（`done`、`of`），以及 `done`（`records`、`duration_s`、`s_per_record`、`retries`、`file`）。

CSV 列（JSONL 的键相同）：`index`、`start`、`end`（万用表本地时间）、`unit`、`primary`、`primary_state`、`minimum`、`minimum_state`、`average`、`average_state`、`maximum`、`maximum_state`、`samples`、`record_type`（`INTERVAL`，……）、`stable`、`transient`。数值以 SI 基本单位表示；`_state` 为 `OL` 表示该值为空。

```
index,start,end,unit,primary,primary_state,minimum,minimum_state,average,average_state,maximum,maximum_state,samples,record_type,stable,transient
0,2026-11-06 21:40:19.248,2026-11-06 21:40:19.752,VDC,1.4904,NORMAL,1.4903,NORMAL,1.49032,NORMAL,1.4904,NORMAL,5,INTERVAL,STABLE,NON_T
```

## 面板按键

`press KEY [KEY ...]` 按顺序模拟按键，并返回每次按键之后的显示状态。按键：`F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`。

```
open289 press HOLD                    # toggle HOLD
open289 press RANGE                   # step the range (manual)
open289 press RANGE --held            # back to autorange
open289 press MINMAX                  # start MIN MAX
open289 press MINMAX F4               # stop it (opens the result page), then close the page
open289 press ONOFF F1 --allow-off    # switch the meter off for good
```

选项：`--held` 对每个按键发送长按（`HELD_DOWN`）；`--settle S`（默认 1.0）在读回状态之前等待这么久；`--allow-off` 允许 ONOFF。结果的每个条目包含 `key`、`held`、`ack`（`OK`）和 `state`（一个显示快照；万用表显示页面时则是一段文字，见下）。`press` 在遇到第一个万用表未确认的按键时停止。`press` 是唯一会改变万用表状态的命令（`raw --unsafe` 除外）。

```
$ open289 --sim press HOLD
{"ok":true,"cmd":"press","presses":[{"key":"HOLD","held":false,"ack":"OK","state":{"function":"V_DC","secondary_function":"NONE","autorange":true,"range":5.0,"unit":"VDC","modes":[],...}}]}
```

在硬件上观察到的现象：

- `HELD_DOWN` 是长按。长按 `RANGE` = 回到自动量程。长按 `MINMAX` 的效果与短按相同。
- **MIN MAX 有页面。** 第一次 `MINMAX` 启动 MIN MAX（`modes` 中有 `MIN_MAX_AVG`）。第二次停止它并打开**结果页**（F1 重新开始，F2 保存，`MINMAX` 或 F4 关闭）。在结果页按 F2 会打开**保存页**（F1 追加名称字符，F2 保存，F4 关闭）。关闭页面会回到测量屏幕。
- **为什么此时查询会返回 `METER_BUSY`。** 当菜单、结果页、保存页或设置屏幕打开时，万用表会以执行错误应答每一个测量查询（`QDDB`、`QSLS`，……）。open289 把它命名为 `METER_BUSY`（退出码 5）。此时 `press` 会显示 `"state": "menu / result page open (measurement queries blocked)"`。请用按键（`F4`）或在万用表上关闭该页面，然后重试。
- `BACKLIGHT` 循环切换关闭、明亮、昏暗，在各页面中同样有效。
- `ONOFF` 会被拒绝（`UNSAFE_OPERATION`，退出码 7），除非加 `--allow-off`。加上之后，万用表会请求确认（`turning off meter will break communication`）：`F1` 确认，`F4` 取消。按下 F1 之后万用表关机，适配器稍后也会自行关机，两者都无法远程开机。这在电脑上无法撤销。

## 原始命令

`raw COMMAND [COMMAND ...]` 通过透传发送万用表命令，并返回每个应答（`ack`，以及 `text`，二进制应答则为 `binary_hex`）。**默认只允许查询：** `ID`、`IM` 和 `Q*` 命令（`QM`、`QDDB`、`QDDA`、`QBL`、`QSLS`、`QMP clock`，……）可以通过；其他任何命令（`MP`、`CSD` = 清除存储、`RI` = 复位、`OFF`、`PRESS`，……）在发送之前就会以 `UNSAFE_OPERATION` 拒绝。`--unsafe` 会解除该过滤；只有在完全清楚命令作用时才使用，因为有些命令会擦除已保存的数据或让万用表关机。

```
$ open289 --sim raw ID QM
{"ok":true,"cmd":"raw","results":[{"command":"ID","ack":"OK","text":"FLUKE 289,V1.71,12345678"},{"command":"QM","ack":"OK","text":"5.0628E0,VDC,NORMAL,NONE"}]}

$ open289 --sim raw MP
{"ok":false,"cmd":"raw","results":[{"command":"MP","error":{"code":"UNSAFE_OPERATION","message":"meter command b'MP' is not a read-only query","hint":"only ID, IM and Q* queries pass by default; add --unsafe for other commands"}}]}
```

各命令依次发送并等待应答；被拒绝或失败的命令会在它自己的结果中报告，退出码取第一个错误的退出码（拒绝为 7）。超过 87 字节的命令在发送之前就会被拒绝（见[安全](#安全)）。

## 自检

`open289 selftest` 是对适配器、链路和万用表的只读健康检查（绝不会改变万用表）：

| 检查 | 内容 |
|---|---|
| S01 | 适配器在广播 |
| S02 | 连接并启用适配器控制点 |
| S03 | 万用表已连接（`FLUKE 287/289`，而不是未连接时的 `FLUKE PC3000`） |
| S04 | 适配器固件有应答 |
| S05 | 透传 `ID` |
| S06 | 显示快照（`QDDB`）可解码（万用表必须处于测量屏幕） |
| S07 | 5 s 内收到一个实时读数 |
| S08 | 适配器电量 >= 20 % |
| S09 | 万用表电量不是 EMPTY / ALMOST_EMPTY |

结果列出每项检查的 `pass`、`measured` 和 `expected`，以及 `passed` 和 `failed` 计数；有检查失败时退出码为 1。S01、S02 或 S03 失败会提前结束运行（后面的检查依赖这条链路）。

```
$ open289 --sim selftest
{"ok":true,"cmd":"selftest","passed":9,"failed":0,"items":[{"id":"S01","name":"adapter advertising","pass":true,"measured":"FA:28:90:00:00:01","expected":null},...]}
```

## 虚拟万用表

本包含有一个接着 Fluke 289 的 IR3000 FC 模型（`open289/sim.py`），因此整个工具、MCP 服务器和测试套件都可以在没有蓝牙、没有硬件的情况下运行。用 `--sim` 或 `OPEN289_SIM=1` 选择它。

它模拟的是在真实硬件上测得的行为：GATT 表和启用适配器的 CNX ID 写入、18 字节的控制点写入上限、透传的帧格式、输出缓冲区溢出（超过 128 字节无应答，这是建模推测；实测为 181）以及超载时在 373 字节及以上发生的适配器挂死（open289 自身会拒绝任何超过 87 字节的发送）、二进制的 `QDDB` / `QSMR` / `QMMSI` / `QPSI` / `QRSI` / `QSRR` 布局、每秒 4 次的 16 字节实时通知、单连接（第二个进程被拒绝，适配器停止广播）、带页面的面板按键（MIN MAX 结果页和保存页、关机确认、背光循环）、已关机的万用表和进入休眠的适配器、含两个测量值、一个峰值会话和一个 72 条记录的存储数据。从未在硬件上观察到的行为在代码中标记为 GUESS。身份：地址 `FA:28:90:00:00:01`，序列号 `12345678`。它是测试和演示的替身，不是测量仪器："读数"都是合成的。

虚拟设备的状态是一个由所有进程共享的 JSON 文件（[数据文件夹](#数据文件夹)中的 `state/sim_state.json`），因此一条命令里的 `open289 press HOLD` 会被下一条命令看到。

```
python -m open289.sim show                            # the state as JSON
python -m open289.sim reset                           # factory state
python -m open289.sim power-cycle                     # switch adapter and meter on again (clears an adapter hang)
python -m open289.sim set function=V_AC input=open meter_on=false
python -m open289.sim audit [--clear]                 # every GATT write the tool made (state/sim_writes.jsonl)
```

`set` 的键：`function`（`V_DC V_AC MV_DC MV_AC OHMS CONTINUITY DIODE_TEST TEMPERATURE MA_DC`，以及 Hz 副功能对应的 `V_AC_HZ` / `V_DC_HZ`）、`secondary`（`NONE` / `HERTZ`）、`input`（当前功能的输入；`open` 表示 OL）、`inputs.<FUNCTION>`、`hz`、`meter_on`、`adapter_on`、`adapter_hung`、`autorange`、`range_index`、`screen`（`normal`、`minmax_result`、`save_page`、`power_off_confirm`）、`backlight`、`modes`、`seed`、`clock_offset`。环境变量：`OPEN289_SIM_RATE`（每秒的实时通知数，默认 4）、`OPEN289_SIM_DELAY`（一次透传发送的延迟，默认 0.05 s；硬件上约 0.6 s）、`OPEN289_SIM_STATE`、`OPEN289_SIM_AUDIT`（覆盖文件路径）。`OPEN289_NO_BLE=1` 会使真实的蓝牙后端拒绝启动（测试会设置它，因此测试绝不可能碰到硬件）。

## CLI 参考

全局选项放在命令之前：

| 选项 | 含义 |
|---|---|
| `--address`、`-a ADDRESS` | 适配器的 BLE 地址（默认：找到的第一个 IR3000 FC） |
| `--sim` | 使用虚拟的 IR3000 + Fluke 289（无需硬件） |
| `--text` | 输出人类可读的文本而不是 JSON |
| `--verbose`、`-v` | 把适配器控制点的通信记录输出到 stderr |
| `--max-wall MAX_WALL` | 一次性命令的时间限制，单位为秒（默认 120；`memory` 为 600）。`live`、`log`、`recording` 和 `read --every` 没有时间限制 |
| `--version` | 打印版本 |

`open289 <command> --help` 列出某条命令的选项。

| 命令 | 用途 | 选项（默认值） |
|---|---|---|
| `scan` | 查找 IR3000 FC 适配器 | `--seconds SECONDS`（10） |
| `info` | 适配器和万用表的身份、电量、存储水平、时钟、已保存数量 | |
| `read` | 当前完整的显示状态（功能、量程、模式、所有读数） | `--every EVERY`（每个间隔一个快照，例如 `1s`）、`-n`、`--count COUNT`（配合 `--every`：N 个快照后停止）、`--for FOR_`（配合 `--every`：持续这么久后停止，例如 `5m`） |
| `live` | 来自 BLE 通知的实时读数（~4/s），每行一个 JSON | `-n`、`--count COUNT`、`--for FOR_`（例如 `30s`）、`--raw`（包含原始字节）、`--timeout TIMEOUT`（10 s 没有读数即为错误） |
| `log` | 把读数记录到 CSV / JSONL 文件 | `--out`、`-o OUT`（`data/log_<time>.csv`）、`--format {csv,jsonl}`、`--duration`、`-d DURATION`（`forever`）、`--every EVERY`（`0`）、`--snapshot-every SNAPSHOT_EVERY`（`0`）、`--status-every STATUS_EVERY`（`10s`）、`--reconnect-for RECONNECT_FOR`（`5m`） |
| `memory` | 已保存的测量值、MIN MAX、峰值会话和记录 | `--detail`（配合 `--text`：列出每个读数） |
| `recording` | 把趋势记录 INDEX 下载为 CSV / JSONL | `index`、`--out`、`-o OUT`（`data/recording<INDEX>_<time>.csv`）、`--format {csv,jsonl}`、`--start START`（0）、`--limit LIMIT`（结束索引）、`--no-batch` |
| `press` | 模拟面板按键 | `KEY [KEY ...]`、`--held`、`--settle SETTLE`（1.0 s）、`--allow-off` |
| `raw` | 原始万用表命令（除非加 `--unsafe`，否则只有 `ID`、`IM` 和 `Q*`） | `COMMAND [COMMAND ...]`、`--unsafe` |
| `selftest` | 对适配器、链路和万用表的只读健康检查 | |
| `stop` | 干净地结束正在运行的 `live` / `read --every` / `log` | |

时长（`--every`、`--for`、`--duration`、`--snapshot-every`、`--status-every`、`--reconnect-for`）接受 `ms`、`s`、`m`（或 `min`）、`h`、`d`；纯数字表示秒：`500ms`、`30`、`90s`、`5m`、`1.5h`、`2d`。`forever`（也可写 `inf`、`until-stop`）表示不限时（用于 `--duration`）。

`stop` 会写入一个请求文件，正在运行的 `live`、`read --every` 或 `log`（在任何进程中）约一秒内就会看到它；它不需要适配器。`recording` 和一次性命令不响应它。

## 输出格式

每条命令都在 stdout 上输出 JSON，每行一个对象，紧凑格式。一次性命令输出一个对象。`live`、`read --every`、`log` 和 `recording` 输出事件行，并以一个 `"event": "done"` 摘要结束。事件行带有 `"ok": true, "cmd": ..., "event": ...`（`started`、`connected`、`status`、`progress`、`link_lost`、`reconnected`、`done`）；`live` 读数为 `{"type":"reading", ...}`。结果的第一个字段是 `ok`。警告和 `--verbose` 的通信记录输出到 stderr。数值以 SI 基本单位表示；`display` 是万用表给出的文本。时间：`t`、`pc_time`、`started`、`ended` 是电脑上的 Unix 秒；`time`、`start`、`end`、`info.meter.clock` 以及数值形式的 `ts` / `clock_epoch` 是万用表的本地时钟（不含时区）。

错误是一个对象加非零退出码：

```
{"ok":false,"cmd":"read","error":{"code":"METER_BUSY","message":"'QDDB' -> EXECUTION_ERROR","hint":"the meter is on a menu, result page or setup screen: close it on the meter (or with open289 press), then retry"}}
```

`--text` 输出同样数据的人类可读形式：`live` 每个读数一行（`HH:MM:SS.mmm  value unit`，副读数跟在 `|` 之后），`read`、`memory`、`press` 和 `selftest` 是简短的表格，`recording` 每条记录一行，`log` 是简短的摘要。`--text` 下的错误是 `error CODE: message` 加一行 `hint:`。脚本和智能体应使用 JSON。

## 错误与退出码

每个错误都有 `code`、`message` 和（通常有）`hint`；退出码由 code 决定。

| Code | 退出码 | 含义与常见解决办法 |
|---|---|---|
| `INVALID_ARG` | 2 | 参数无法解析或超出范围（时长、按键、索引）：修正命令 |
| `NOT_FOUND` | 3 | 没有 IR3000 FC 在广播：唤醒适配器（按按钮直到 LED 闪绿灯），靠近一些，关闭手机上的 Fluke Connect |
| `BUSY` | 4 | 另一个 open289 进程占用了适配器（等待，或 `open289 stop`）；在 MCP 服务器中还可能是正在运行的后台记录 |
| `METER_BUSY` | 5 | 万用表应答 EXECUTION_ERROR：菜单、结果页、保存页或设置屏幕处于打开状态；在万用表上或用 `press F4` 关闭它 |
| `DETACHED` | 5 | 适配器报告没有通电的万用表（据 Fluke 应用，此时它会报告 `FLUKE PC3000`；在硬件上未观察到）：把它装到红外口上，打开万用表 |
| `NO_DATA` | 5 | 万用表没有该请求对应的数据，例如没有该索引的已保存条目：检查 `memory` |
| `TIMEOUT` | 6 | 一次性命令超过了 `--max-wall`，或 `--timeout` 秒内没有读数：靠近一些，检查万用表是否开机，调大 `--max-wall` |
| `BLE_ERROR` | 6 | 扫描或连接失败：蓝牙是否已打开？（另外：是否设置了 `OPEN289_NO_BLE`） |
| `LINK_LOST` | 6 | 蓝牙链路中断（`log` 会在 `--reconnect-for` 内自行重连） |
| `PROTOCOL_ERROR` | 6 | 意外或无法解码的应答；记录下载会指出从哪条记录继续 |
| `METER_ERROR` | 6 | 万用表不接受该命令（语法错误）或没有应答 |
| `WRITE_FAILED` | 6 | 保留（尚未发出；蓝牙写入失败目前表现为 `INTERNAL`） |
| `UNSAFE_OPERATION` | 7 | 被安全规则拒绝：没有 `--unsafe` 的非查询 `raw`、没有 `--allow-off` 的 ONOFF、超过 87 字节的发送 |
| `STOPPED` | 0 | 保留；被停止的运行会以 `stopped_by: "stop"` 正常结束 |
| `INTERNAL` | 1 | 意外异常（结果中带有最后几行回溯）；其他任何情况都映射为退出码 1 |

退出码 0 表示成功；Ctrl+C 为 130。`selftest` 失败（某项检查未通过，这不是错误对象）以及部分条目无法读取的 `memory` 分别退出 1 和 6。MCP 服务器使用相同的 code，并增加了：工具调用超过 `max_wall` 时的 `TIMEOUT`、`CANCELLED` 和 `SELFTEST_FAILED`。

## MCP 服务器

`open289-mcp`（或 `python -m open289.mcp_server`）通过模型上下文协议（Model Context Protocol，stdio，仅使用标准库；随 `pip install open289` 一并安装）提供万用表服务：

```
claude mcp add open289 -- open289-mcp
```

如果 pip 的 `Scripts` 文件夹不在 `PATH` 中，请给出 `open289-mcp` 的完整路径。其他客户端：`{"mcpServers": {"open289": {"command": "open289-mcp"}}}`。每次工具调用都在子进程中运行一条 CLI 命令，因此适配器是按调用连接和释放的，挂死不会阻塞服务器，CLI 的所有规则都适用（安全过滤、适配器锁、错误码）。服务器自带的说明会告诉智能体先从 `status` 开始。

11 个工具：

| 工具 | 运行 | 主要参数（默认值） |
|---|---|---|
| `status` | `info` | `max_wall`。适配器地址 / 固件 / 电量，万用表型号 / 固件 / 序列号 / 电量 / 存储水平 / 时钟，已保存数量。`meter.attached` 为 false = 没有接在已通电的万用表上 |
| `read` | `read` | `max_wall`。一个显示快照：功能、量程、模式、主读数和所有其他读数 |
| `live` | `live` | `count`（20，1..400）、`seconds`（0.1..120；与 `count` 同时给出时：以先到者为准）、`max_wall`。返回的是**摘要**而不是逐行读数：数量、速率、首 / 末时间、每种单位主读数的 min / max / mean、出现过的功能和单位、最后 5 个读数 |
| `memory` | `memory` | `max_wall`（600）。各类计数和每个已保存条目 |
| `download_recording` | `recording` | `index`（必填）、`out`、`format`、`start`（0）、`limit`、`max_wall`（900）。返回文件、记录数、每条记录的秒数、重试次数、记录头。失败的结果带有 `resume`，给出继续下载所用的 `start` 和 `out` |
| `press` | `press` | `keys`（必填，1..20 个，取自 `F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`）、`held`（false）、`allow_off`（false）、`max_wall`。每次按键之后的显示状态 |
| `query` | `raw` | `commands`（必填，1..10 个，例如 `["ID","QM"]`）、`max_wall`。只读：`ID`、`IM`、`Q*`；其他任何命令在到达万用表之前就会以 `UNSAFE_OPERATION` 拒绝（MCP 中没有 `--unsafe`） |
| `selftest` | `selftest` | `max_wall`。检查项 S01-S09；失败时返回 `SELFTEST_FAILED` 及失败的检查项 |
| `log_start` | 后台运行的 `log` | `duration`（`10m`；`forever` = 直到 `log_stop`；必须 > 0）、`every`（`0`）、`snapshot_every`（`0`）、`out`、`format`、`reconnect_for`（`5m`）。返回任务 id 和文件 |
| `log_status` | 无（读取任务的输出） | `job`（最近一个）。状态（running / done / failed）、目前的记录数、最后一条状态行、文件，结束后还有其摘要 |
| `log_stop` | `stop` | `job`（最近一个）。干净地停止并返回摘要：文件、记录数、时长、每种单位的 min / max / mean、链路间隙。不带 `job` 时，它还会停止在别处（终端、其他客户端）启动的 `log` 或 `live`；`already_ended: true` = 它在此之前已经结束 |

`status`、`read`、`live`、`memory`、`query`、`selftest`、`log_status` 被标记为只读。所有时长都是字符串（`"30s"`），所有数值都是 SI 单位。

**后台记录。** `log_start` 会立即返回（在第一个读数之后，最长 30 s），并给出任务 id；客户端断开连接后记录仍会继续运行，其输出和元数据保存在 `state/mcp_jobs/<job>/` 中，因此重启后的服务器或其他客户端的服务器仍能看到它。用 `log_status` 跟踪它，用 `log_stop` 结束它（或者它在 `duration` 之后结束，或者在链路丢失超过 `reconnect_for` 时结束）。文件中保存数据；工具结果是摘要，绝不会是成百上千个读数。

**BUSY 规则。** 适配器只接受一个连接，因此工具一次只运行一个，并且**在后台记录运行期间，其他所有万用表工具（`status`、`read`、`live`、`memory`、`press`、`query`，……）都会返回 `BUSY`**，直到 `log_stop`（`log_status` 和 `log_stop` 仍可使用）。`BUSY` 也表示另一个 open289（在终端中运行 `live`/`log`，或另一个客户端）占用着适配器。手机上的 Fluke Connect 表现为 `NOT_FOUND`（适配器停止广播）。`METER_BUSY` 则不同：万用表上打开了菜单或结果页；用 `press` `F4` 关闭后重试。对智能体的安全提示：`press` 会改变万用表（HOLD、MIN MAX、量程）；`ONOFF` 需要 `allow_off: true`，并且只应在用户要求时使用：它会让万用表彻底关机，适配器也会随之休眠。

没有 MCP 工具的 CLI 命令：`scan`（服务器自己查找适配器）、`stop`（由 `log_stop` 在内部使用）以及 `raw --unsafe`（有意不暴露）。有 shell 的智能体可以用 CLI 完成所有这些操作。

## 数据文件夹

open289 把文件保存在一个带有两个子文件夹的文件夹中：

- `state/`：适配器锁（`open289.lock`）、停止请求（`stop.request`）、MCP 后台任务（`mcp_jobs/`）以及虚拟万用表的状态（`sim_state.json`、`sim_writes.jsonl`）。
- `data/`：记录（`log_<time>.csv`）和记录下载（`recording<INDEX>_<time>.csv`）的默认存放位置。`--out` 可以写到其他任何位置；相对路径相对于当前文件夹。

该文件夹的位置：如果设置了 `OPEN289_HOME` 则用它；否则，在源码检出中（包旁边有 `pyproject.toml`）就是检出目录本身；否则，对于已安装的包，Windows 上是 `%LOCALAPPDATA%\open289`，其他系统是 `~/.local/share/open289`，因此不会向 `site-packages` 写入任何内容。MCP 服务器的子进程使用同一个文件夹。

## 开发

目录布局：

- `open289/` 包：`cli.py`（命令、输出）、`transport.py`（真实或虚拟后端的选择、发现、连接）、`adapter.py`（IR3000 服务客户端、透传、操作码白名单、查询过滤和 87 字节限制）、`meter.py`（万用表的远程命令和二进制记录解码器）、`reading.py`（实时通知解码器）、`records.py`（可直接转 JSON 的字典）、`logger.py`（带重连的长时间记录）、`lock.py`（单客户端锁）、`errors.py`、`paths.py`、`sim.py`（虚拟万用表）和 `mcp_server.py`（MCP 服务器）。
- `tests/` 针对虚拟万用表的离线测试：解码器（对照真实抓包）、传输层及其安全规则、CLI、MCP 服务器（JSON-RPC、任务、BUSY 规则）以及模拟器本身。
- `docs/PROTOCOL.md`（以及 `PROTOCOL.zh-CN.md`）：协议，附证据等级。
- `tools/` 硬件诊断脚本。
- `.github/workflows/`：`ci.yml` 和 `release.yml`。

```
python -m pip install -e ".[test]"
OPEN289_SIM=1 OPEN289_NO_BLE=1 python -m pytest tests -m "not hw" -q
```

测试会自行设置模拟器环境（`tests/conftest.py`：每个测试使用私有的 `OPEN289_HOME`、`OPEN289_SIM=1`、`OPEN289_NO_BLE=1`、快速模拟器），因此测试绝不可能碰到蓝牙。标记为 `hw` 的测试需要真实的适配器和万用表，默认跳过（`pytest -m hw`）。

CI（`ci.yml`，在推送到 main、拉取请求以及手动触发时运行）：在 Windows 和 Linux 上用 Python 3.10、3.12 和 3.14 运行测试，全部针对虚拟万用表；检查受版本控制的文件中不含本地路径；用 `twine check --strict` 做包构建，并对构建出的 wheel 做冒烟测试（在虚拟万用表上运行 `open289 info`、`open289 live -n 5`）。

发布（`release.yml`）：推送一个与 `open289.__version__` 相同的标签 `vX.Y.Z`（0.1.0 对应 `v0.1.0`）。工作流会运行 CI 检查，拒绝与版本不符的标签，构建，创建带文件的 GitHub release，并通过**可信发布（trusted publishing）**（无需令牌）发布到 PyPI。在 pypi.org 上需做一次性设置：为项目 `open289` 添加一个（待定的）可信发布者，所有者 `FrostFlops`，仓库 `open289`，工作流 `release.yml`，环境 `pypi`，并在仓库设置中创建环境 `pypi`。

## 协议

适配器和万用表如何通信（GATT 表、启用控制点的 CNX ID 写入、透传帧格式、缓冲区限制、16 字节实时数据包布局、二进制的已保存数据记录、按键行为），每条陈述都标有 VERIFIED（在硬件上见到过）或 DERIVED（推导所得，未实测）：[docs/PROTOCOL.md](docs/PROTOCOL.md)（[中文](docs/PROTOCOL.zh-CN.md)）。各功能下观察到的实时编码：见 [docs/PROTOCOL.md](docs/PROTOCOL.md) 的 4.8 节。

## 这里没有什么

Fluke 的应用（Fluke Connect）、从它反编译得到的任何内容，以及私有的逆向工程工作区，都不在本仓库中。适配器的固件映像和升级程序也不会被使用。

## 安全

- 本工具绝不会写入适配器的固件升级特征（MSP 升级程序和无线电映像升级程序；在那里写入错误的值会擦除闪存），也绝不会向适配器发送操作码 `0x90` 到 `0x94`（1550 / 1555 绝缘测试命令，会切换高压）。
- 适配器的输出缓冲区每次发送上限为 **87 字节**：更大的发送得不到应答，或使适配器挂死直到断电重启（373 字节及以上），所以 open289 会拒绝它们。
- `raw` 只允许查询（`ID`、`IM`、`Q*`），除非加 `--unsafe`。`MP` 可以改变设置，`CSD` 会擦除已保存的数据，`RI` 会复位万用表，`OFF` 会让它关机。
- `press ONOFF` 需要 `--allow-off`，并且万用表会请求确认（`F1`）。**OFF 和 ONOFF 无法远程撤销：** 万用表和适配器都无法从电脑上再次开机。
- `press` 在其他方面也会改变万用表状态（HOLD、MIN MAX、量程、在保存页保存测量值）：它不是只读的，智能体只应在被要求时使用。
- 适配器只接受**一个**蓝牙连接：请关闭手机上的 Fluke Connect。open289 持有一把锁，因此同一时间只有一个 open289 进程连接。
- 这是一个测量读取工具，不是安全装置。请遵守万用表的类别等级及其自身的安全规则；open289 不会让测量变得更安全。

## 许可证

GPL-3.0-only（见 `LICENSE`）。Fluke、Fluke Connect 和 IR3000 是 Fluke Corporation 的商标；本项目与 Fluke Corporation 无关联。
