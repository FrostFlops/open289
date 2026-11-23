[English](UI.md) | 简体中文。本文是英文版 [UI.md](UI.md) 的译文，若两者有出入，以英文版为准。

# Fluke 289 面板按键图

本文档描述通过 `open289 press`（以及 MCP 的 `press` 工具）所看到的 Fluke 289 面板：每个拨盘位置上每个按键的作用、如何进入某个功能、如何回到测量界面，以及哪些内容无法通过协议看到。本文面向看不到万用表屏幕、却要驱动万用表的 AI 智能体和开发者。

这里的一切都是在 Fluke 289（固件 V1.71）上，经 IR3000 FC 透传发送按键，并在每次按键后检查 LCD 和一次 `QDDB` 显示查询而观察到的。证据等级：除非某条陈述另有说明，均为 VERIFIED（在硬件上观察到）。287 未经测试。按键名称、`--held` 和结果格式见 [README.zh-CN.md](../README.zh-CN.md)（“面板按键”一节）；线路协议见 [PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md)。

记号：F1 到 F4 是从左到右的软键；破折号表示没有标签（该键不起作用）。“主功能 / 副功能”指该选择所产生的 QDDB `function` 和 `secondary_function`。

## 1. 用 `press` 驱动万用表

按键：`F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`。`--held` 对每个按键发送长按。每次按键之后，`press` 返回显示状态（一份 QDDB 快照；查询被阻塞时则是一段文字，见第 2 节），并附带变化、提示和警告（见下文“`press` 报告什么”）。

本次遍历中没有按过 `ONOFF`。除非给出 `--allow-off`（MCP 工具中为 `allow_off`），否则 `press` 会拒绝它。此时万用表显示关机确认，F1 为 OK，F4 为 Cancel；F1 会关掉万用表，适配器随之休眠，这无法从 PC 端撤销（见 README）。因此 ONOFF 之后再按任何一次 F1 都会关掉万用表。

黄金规则：

1. **自己跟踪屏幕。** 软键的含义随屏幕而变，在菜单内还随所选项目而变，但 QDDB 不会显示是否打开了菜单、面板、Setup 或帮助，不会显示任何光标在哪里，也不会显示画出了哪些软键标签（第 2 节）。状态只能确认那些改变了功能、副功能、自动量程、量程、单位、模式、MIN MAX 起始时间、dBm 参考值或温度偏移的效果，以及阻塞状态。一次只按一个键，依据本文预测屏幕，并在效果可见的地方用状态核对；对于光标移动以及打开和关闭浮层，只能依靠你自己的按键历史（`press` 的 `changed`、`note` 和 `warnings` 有所帮助，但它们只是推断；见“`press` 报告什么”）。历史不确定时，在按下一个软键之前，请在表旁的人读一下屏幕。盲目的按键序列一旦出错，就会落进另一个菜单。
2. **菜单会记住光标。** Save 面板、Menu 和 Setup 面板会在上次离开的位置重新打开，所以同一个软键下次可能打开不同的项目，而光标无法通过协议读取。曾观察到 Setup 光标在换拨盘之后仍停在原项目上，也曾观察到换拨盘之后回到了 Reset。除非表旁的人读出了软键标签，否则绝不要在 Setup 中按 F1、F2 或 F3：F1 是所选项目的名称（在 Reset 上，F1 是破折号，F2 和 F3 是重置选项，见 3.7）。
3. **拒绝是无声的。** 当前模式不接受的按键会被应答（`OK`），但什么都不变；`press` 只会把这表现为 `changed: []` 加一条 `note` 或警告。被拒绝的菜单选择会让菜单保持打开。随后的 F4（本意是 Stop 或 Close）就只是关闭菜单，第二次 F4 会打开 Setup，再往后的软键就作用在 Setup 里：F1 打开所选的 Setup 项目（曾有一次是 Instrument，此时下一次 F1 就进入编辑模式），而光标在 Reset 上时（换拨盘之后曾发现它在这里），F2 或 F3 会打开恢复出厂设置的确认，其 F1 为 OK。在任何一个状态未能确认其效果的软键之后，在弄清当前显示的是哪个屏幕之前，不要按 F1、F2 或 F3。
4. **在测量界面和 Measurement 已保存项目查看器中，UP 和 DOWN 改变 LCD 对比度**（其他查看器未记录；在录制的 Trend 视图中它们是缩放）。每按一次 DOWN，数字变浅一级，每按一次 UP，变深一级。除非成对使用，否则不要在那里按它们。在菜单、列表和编辑器中，它们移动光标或改变数值；在 Meter Info 页面上它们滚动；在 INFO 帮助中 DOWN 滚动（UP 未记录）。
5. **有些地方的编辑器立即生效，有些则不。** Setup 的编辑器使用 OK 和 Cancel（Cancel 恢复旧值）。dBm Reference 预设列表和 Temperature Offset 编辑器会让每一次 UP 和 DOWN 立即生效，并且只提供 Close。
6. **长按**对 F2、F4、HOLD、MINMAX 和 BACKLIGHT 的效果与短按相同；长按 HOLD 不会启动 AutoHOLD。例外：长按 `RANGE` 回到自动量程；在录制的 Trend 视图中，长按 LEFT 或 RIGHT 会让光标移动两个采样点而不是一个。长按 INFO 会在上次离开的滚动位置打开帮助。长按 F1、F3、UP、DOWN 和 ONOFF，以及在 Trend 视图之外长按 LEFT 或 RIGHT，都没有试过。LEFT 和 RIGHT 在测量界面上不起作用。
7. **BACKLIGHT** 循环为：关、亮、暗、关。远程按键似乎不会重新开始万用表的自动背光超时（测试时的设置为 5 分钟）。
8. **重绘繁重视图的按键，其应答为 `NO_REPLY`**：在录制摘要中按 F3 Trend，以及在 Trend 视图中按 UP 或 DOWN（缩放）。在所有检查过的情况下，按键都已生效（重绘拖延了万用表的应答）；更早的一次情况中没有检查效果。`press` 把缺失的应答报告为 `METER_ERROR`，并且不再发送剩下的按键。这些视图在按键前后都会阻塞查询，因此状态无法显示该键是否起了作用。不要盲目重复该键（第二次 F3 会回到摘要，第二次 UP 会再次放大）：假定它已生效，如果有关系，请在表旁的人确认。

### `press` 报告什么

在第一个按键之前，`press` 先读一次显示状态（QDDB）作为基线，每个按键之后（经过 `--settle`，默认 1 s）再读一次。每个按键条目带有下列新增字段（原有字段不变）：

- **`changed`**：与按键之前的状态相比有差异的显示状态字段，范围是 `function`、`secondary_function`、`autorange`、`range`、`unit`、`modes` 和 `minmax_start`，以及两个状态都带有读数时的 `db_ref` 和 `temp_offset`。`[]` 表示没有可见变化。`["view"]` 表示一个阻塞页面（结果页或已保存项目查看器）打开或关闭了。`null` 表示无法判断：两个状态都被阻塞，或其中之一未知（出错、链路已断开，或 ONOFF 之后的按键，它没有基线）；ONOFF、关机和 `LINK_CLOSED` 条目总是 `null`。在自动量程下，由输入引起的量程移动不计入：只有当按键前后两个状态都处于自动量程时才不比较 `range`。
- **`note`**（仅在需要时出现）：针对状态无法判定的情形给出的提示。软键没有可见变化（菜单或面板可能已打开、移动或关闭，或者选择被拒绝；在按下一个软键之前先看一看，因为盲按 F4 可能打开 Setup）。软键让 HOLD 开启（F2 Save 打开了 Save 面板，它保持读数并记住自己的光标）。UP 或 DOWN 没有变化（光标或数值移动了，或者在没有光标的屏幕上 LCD 对比度变了：测量界面和 Measurement 已保存项目查看器；其他查看器未记录，在 Trend 视图中 UP 和 DOWN 是缩放）。LEFT 和 RIGHT。INFO（帮助不可见；F4 关闭它）。AutoHOLD 中的 HOLD（取新值，仍留在 AutoHOLD）。HOLD、MINMAX 或 RANGE 什么都没改变（被忽略：在当前功能或模式下不可用，或者页面阻塞按键，例如表笔报警）。自动量程下的长按 RANGE 不会被标记。
- **`warnings`**（顶层）：`{code, message}` 的列表。`KEY_IGNORED`：HOLD、MINMAX 或 RANGE 没有效果。`LEAD_ALERT_POSSIBLE`：在电流功能（`A_*`、`MA_*`、`UA_*`）中出现这样被忽略的按键，除非 REL 可以解释被忽略的 RANGE，或者正在进行的录制可以解释被忽略的 MINMAX。`MENU_STATE_UNKNOWN`：最后一个可能关闭菜单的按键（其后忽略 UP、DOWN、LEFT、RIGHT、INFO 和 BACKLIGHT）是一个没有变化的软键，或者打开了 Save 面板。

`--text` 会打印 `changed:` 行（适用时为 `nothing` 或 `unknown`）、`note:` 行以及 `WARNING CODE: message` 行。如果最后一个按键是软键且没有任何变化，`press` 会在再等一个稳定期（至少 1 s）之后再读一次状态，然后才下结论，因为有些选择（例如低通）需要超过 1 s。如果序列进行中链路断开，`press` 随即停止，并保留已完成的按键结果。在发送按键时发现断开，该条目的 `ack` 为 `LINK_CLOSED`，状态为 "meter powering off"；在按键之后回读状态时发现断开，状态为 "link closed (meter switched off, or the adapter went out of range)"。两种情况下 `changed` 都是 `null`。

如何与黄金规则配合使用：

- `changed` 是效果的证据，不是屏幕的证据。非空的 `changed` 确认了按键做了什么；软键之后的 `[]` 表示屏幕现在未知（菜单或面板可能已打开，或者选择被拒绝）。按规则 1 和规则 3 处理：在下一个软键之前，请表旁的人读一下屏幕，并且不要盲按 F4。
- `null` 表示无法检查：回头依靠你自己的按键历史。
- `MENU_STATE_UNKNOWN` 表示菜单、Save 面板或被拒绝的选择可能仍然打开。在弄清当前显示的是哪个屏幕之前，不要按 F1、F2 或 F3。
- HOLD、MINMAX 或 RANGE 上的 `KEY_IGNORED` 表示当前功能或模式不接受该键，或者某个页面阻塞了按键。不要重复该键。在任何拨盘位置上，阻塞按键的页面都可能是表笔报警（有表笔留在 mA 或 A 插孔中，见第 6 节）。在电流功能之外，`press` 对此只给出 `KEY_IGNORED`，不给出 `LEAD_ALERT_POSSIBLE`：如果第 5 节说该键在这个位置上可用，请用户检查表笔。如果当前是电流功能，见下一条。
- `LEAD_ALERT_POSSIBLE` 表示表笔报警可能正在显示（第 6 节）。警告本身在协议上不可见，读数看起来像正常的近零电流，所以这只是由被忽略的按键得出的怀疑。停止测量，请用户检查表笔插在哪些插孔并读一下万用表屏幕，在用户确认之前，不要把任何读数当作有效。
- `note` 文字提醒的是 `changed` 说不清时该键可能做了什么：在下一个按键之前先读它。

## 2. 通过协议能看到什么

| 情形 | QDDB / 查询 |
|---|---|
| 测量界面 | 可用 |
| HOLD、AutoHOLD、MIN MAX 会话、Peak 会话、REL、低通、正在进行的录制 | 可用；`modes` 显示 `HOLD`、`AUTO_HOLD`、`MIN_MAX_AVG`（MIN MAX 和 Peak；Peak 会话另有副功能 `PEAK_MIN_MAX`）、`REL`、`REL_PERCENT`、`LOW_PASS_FILTER`、`RECORD` |
| Menu、Save 面板（从测量界面打开）、Delete Memory、View Memory 列表、Setup 面板及其各页、INFO 帮助 | 可用（它们覆盖在读数之上；QDDB 的应答与测量界面相同，只是 Save 面板打开期间万用表处于 HOLD） |
| 已保存项目查看器（View Mem） | 被阻塞（`EXECUTION_ERROR`） |
| MIN MAX 和录制的结果页 | 被阻塞（Peak 结果页未检查） |
| 从 MIN MAX 或录制结果页打开的 Save 面板 | 被阻塞 |
| 关机确认（ONOFF 之后） | 被阻塞 |
| 转动拨盘 | 短暂被阻塞；稍后再读一次 |
| 表笔报警警告 | 不可见，见下文 |

被阻塞的查询在 `press` 结果中表现为状态文字 "menu / result page open (measurement queries blocked)"，在 `read` 和其他读数命令中表现为错误 `METER_BUSY`（退出码 5）。尽管措辞如此，菜单和面板并不会阻塞查询：被阻塞的状态意味着已保存项目查看器、结果页、从结果页打开的 Save 面板或关机确认处于打开状态，或者刚刚转动了拨盘。

表笔报警页（"WARNING / Leads connected incorrectly."，所有软键标签为空，背光被强制为最亮）出现在 A 位置上 mA 或 A 插孔都没有插表笔（或两个插孔都插了）的时候，以及 uA 位置上表笔插在 A 插孔的时候（通过协议只观察到了没有表笔的情形，见第 6 节）。它在协议中完全不可见：实时通知、QDDB、QM 和 QDDA 都报告正常的近零电流（实时包和 QDDB 中为 0.001 mA DC，QM 中为 0.002 mA，QDDA 中为 1e-06 A；状态 `NORMAL`；实时包没有副记录），与真实的零读数无法区分。它显示期间，万用表会应答按键但忽略它们（F1 和 HOLD 都没有改变任何东西）。解决办法在物理层面：修正插孔（第 6 节）。表笔改正后警告消失，背光回到之前的状态（遍历中为关）。

其他不可见的内容：

- Menu、Save 面板、View Memory、Delete Memory、Setup 面板、某个 Setup 页面或帮助是否处于打开状态（QDDB 的应答与测量界面相同；名称列表未检查）；
- 每个菜单、面板和列表中的光标位置，以及软键标签；
- 显示的是哪个被阻塞的页面：已保存项目查看器、其 Trend 视图、MIN MAX 或录制结果页（很可能也包括 Peak 结果页）、从结果页打开的 Save 面板以及关机确认给出的阻塞状态都相同，尽管 F4 Close 只在结果页上会丢弃数据，F3 只在部分查看器中才是删除，而关机确认上的 F1 会关掉万用表（那里的 F4 是 Cancel）；
- Setup 的各项数值（蜂鸣器、自动背光超时、自动关机、平滑、事件阈值）、连续性蜂鸣器静音以及 Beep on SHORT/OPEN（它们都不在 QDDB 中）；
- 屏幕对比度以及背光是否点亮。

可见的：dBm 模式下的 dBm 参考值（`DB_REF`）和温度模式下的温度偏移（`TEMP_OFFSET`）。两者都是 QDDB 读数：MCP `press` 的状态不含读数，但它们变化时 `changed` 会列出 `db_ref` 或 `temp_offset`（CLI 和 MCP 都是如此），`read` 会显示它们的数值。

## 3. 所有位置通用的屏幕

### 3.1 测量界面

状态栏：电池、时钟、小读数、日期、蜂鸣器图标（带声波的喇叭 = 蜂鸣器开，单纯的喇叭 = 关）。主界面软键为 F1 Menu、F2 Save、F3 模式键或破折号、F4 Setup。LoZ V~ 没有 Menu（F1 是破折号）。带菜单的位置列在第 5 节。

### 3.2 HOLD 和 AutoHOLD

HOLD 显示 "HOLD" 横幅（`modes` = `HOLD`）；再按一次 HOLD 解除。保持期间软键变为 F1 AutoHOLD、F2 Save、F4 Setup。F1 AutoHOLD 给出 "AutoHOLD" 横幅（`modes` = `AUTO_HOLD`），软键为 F1 Cancel（回到正常）、F2 Save、F4 Setup；在 AutoHOLD 中按 HOLD 会保持 AutoHOLD 并取新值。从测量界面打开 Save 面板也会让万用表进入 HOLD。

### 3.3 MIN MAX 会话和结果页

MINMAX（短按或长按）启动一个会话：出现 "Min Max" 横幅，显示 Maximum、Average 和 Minimum 及其经过的时间和起始日期时间；`modes` = `MIN_MAX_AVG`；F1 Restart（清除数值并重新开始计时）、F4 Stop。会话内：

- HOLD 加上 HOLD 横幅（`modes` = `HOLD,MIN_MAX_AVG`）：数值冻结，计时器继续运行。再按一次 HOLD 解除。
- RANGE 锁定量程（自动变手动），不离开 MIN MAX。
- F4 Stop 或 MINMAX 打开**结果页**（"Stopped" + "Min Max" 横幅）：F1 Restart、F2 Save、F4 Close。QDDB 在此被阻塞。F2 Save 打开只有 "Save" 的 Save 面板（F1 +Name、F2 Save、F4 Close 回到结果页）。F4 Close 或 MINMAX 回到测量界面；量程保持原样。

MIN MAX 在 LoZ V~ 和二极管测试中被忽略（`modes` 保持为空）。

### 3.4 Peak 会话

从 Menu（Peak 项）选择。形式与 MIN MAX 相同，横幅为 "Peak"，显示 Peak Max、Average、Peak Min；副功能 `PEAK_MIN_MAX`，`modes` = `MIN_MAX_AVG`；F1 Restart，F4 Stop 通向结果页（Restart、Save、Close；是否阻塞 QDDB 未检查）。Peak 在温度模式和 "DC,AC" 组合模式下被拒绝（第 5 节）。

### 3.5 REL 和 REL %

Menu 项 REL（F1 REL、F2 REL %）。REL 显示差值读数，下方是 "Reference" 和实时值，带 "REL" 横幅，`modes` = `REL`，并锁定量程（此时 RANGE 和长按 RANGE 都被忽略）。在主界面上 F3 变为 "REL %"，用于切换百分比模式（+/-10 % 条形图，`modes` = `REL_PERCENT`）以及切换回来。在 Menu 中再次选择 REL 则结束 REL；在 REL % 下选择 REL 则切换回 REL。REL % 在 dBV 模式下被拒绝（在 mV~ 上观察到；dBm 未试）；此时 REL 给出相对 dB 读数（QDDB 中的 `REL_REFERENCE`）。REL 在 OL 读数上也能工作（参考值为 OL）。REL 在 "DC,AC" 组合模式下被拒绝（在 uA 上观察到），Peak 在 "DC,AC" 模式和温度下被拒绝（在 mV DC 上观察到）。两者都可以从普通 DC 功能进入。

### 3.6 Save 面板

主界面上按 F2 Save。标题 "Save"，覆盖屏幕下半部分；面板打开期间万用表处于 HOLD。项目：Save、View Memory 和 Delete（在 LoZ V~ 和 V~ 上见到）；在适合录制的位置上会增加 Record（在 V~ 上见到；LoZ V~ 没有）。面板有两列；光标用 LEFT 和 RIGHT 按行顺序移动，用 UP 和 DOWN 按列顺序移动，都会回绕。光标在两次打开之间被记住，且无法通过协议读取。

- LoZ V~ 布局：第 1 列 Save、Delete；第 2 列 View Memory。RIGHT 依次循环 Save、View Memory、Delete；DOWN 依次循环 Save、Delete、View Memory。
- 带 Record 的布局（V~）：第 1 列 Save、Record；第 2 列 View Memory、Delete。按同样的规则，RIGHT 依次循环 Save、View Memory、Record、Delete，DOWN 依次循环 Save、Record、View Memory、Delete（DERIVED，没有逐步走过）。

| 光标所在项 | F1 | F2 | F3 | F4 |
|---|---|---|---|---|
| Save | +Name | Save | - | Close |
| Record | 软键未记录（Recording 页面从此项进入，见下文） | | | 未记录 |
| View Memory | View | - | - | Close |
| Delete | Open | - | - | Close |

- **F2 Save** 保存读数并立即回到测量界面（HOLD 解除）。每次保存增加一个测量项目。
- **+Name** 打开名称列表（Save n、MinMax n、Recording n、Motor n、Panel n、Circuit n、Control n、Battery n；n 是下一个序号）。F1 Save、F3 Reset-#（从未按过：重置计数器）、F4 Close，它直接回到测量界面并解除 HOLD。DOWN 会回绕。
- 面板上的 **F4 Close**：回到测量界面，HOLD 解除。
- **Record** 打开 "Recording" 页面（从 Record 项打开它的按键没有记录）：Set Duration（天 时 分，默认 00 00 01）、Set Sample Interval（分 秒，默认 00 01）、Memory Available。F1 Edit（数字编辑器，OK / Cancel）、F2 Start、F4 Cancel。正在进行的录制显示 "Recording" 横幅、Start Time、Remaining Time、Interval Samples、Events；`modes` = `RECORD`；F4 Stop。录制期间，HOLD 显示 HOLD 横幅，冻结屏幕并清空所有软键标签，因此在解除 HOLD 之前不提供 F4 Stop（录制继续；再按一次 HOLD 解除）。RANGE 可用（自动变手动）；MINMAX 和 F1 被忽略；INFO 打开帮助。到达设定时长或按 Stop 时，会出现 "Stopped" + "Recording" 页面（Duration、Interval Samples、Events；F2 Save、F3 Trend、F4 Close；QDDB 被阻塞）。在 Trend 中：F2 Save、F3 Summary、F4 Close。Save 打开 Save 面板（+Name、Save、Close）。**不保存就 Close 会丢弃该录制。**
- **View Memory，View** 打开 "View Memory" 面板，第 1 列为 Measurement、Min Max、Peak，第 2 列为 Recording；F1 View、F4 Close。进入时解除 HOLD。查看器（全部都阻塞 QDDB）：
  - Measurement：出现 "View Mem" 横幅，全屏显示已保存的读数，状态栏显示其时间和日期。F1 Prev（较旧）、F2 Next（较新，到最新为止）、F3 Delete（从未按过）、F4 Close（直接回到测量界面）。UP 和 DOWN 改变对比度。
  - Recording：摘要（"View Mem" + "Recording" 横幅、最后一个值、条形图、Start Time、Duration、Interval Samples、Events）。F1 Prev、F2 Next、F3 Trend、F4 Close。没有删除键。Trend：整个录制的曲线图，页眉为起始时间和缩放 "x1"，图下方有光标及其数值和时间。LEFT 和 RIGHT 把光标移动一个采样点（长按：两个），UP 放大（以光标为中心 x2，y 轴重新缩放），DOWN 缩小；F3 Summary、F4 Close。
  - Min Max："View Mem" + "Min Max" 横幅、最后一个值、Maximum、Average、Minimum 及其经过的时间、起始日期时间。F1 Prev、F2 Next、F3 Delete（从未按过）、F4 Close。
  - Peak："View Mem" + "Peak" 横幅、Peak Max、Average、Peak Min 及时间。软键相同。
- **Delete，Open** 打开 "Delete Memory" 面板（Measurement、Min Max、Peak、Recording）：F1 View（未探索；它打开的页面从未见过，可能提供删除键，所以不要碰）、F3 Delete All（从未按过）、F4 Close（回到测量界面）。

**从未按过、没有明确要求时智能体不得按的破坏性按键：** Delete（在各查看器中）、Delete All（Delete Memory）、Reset-#（名称列表）。不保存就关闭录制结果页同样会丢失该录制。

这些按键可能被盲按到。Save 面板会在上次选中的项目上重新打开（View Memory 面板未检查），而光标和面板在协议上都不可见。于是，本意是 +Name 的 F2 再 F1，在 Save 光标停留在 Delete 时会打开 Delete Memory，而那里的 F3 是 Delete All（例如本意是主界面模式键如边沿切换、REL % 或连续性，或 Menu 选项如 ms 或 DC,AC 的 F3）。在名称列表中 F3 是 Reset-#。所有查看器给出相同的阻塞状态，所以本意是 Recording 查看器中 Trend 的 F3，在打开的是 Measurement、Min Max 或 Peak 查看器时就是 Delete。打开 Save 面板或 View Memory 之后，除非你自己的按键历史表明打开的是 Recording 查看器或录制结果页，否则绝不要按 F3。

### 3.7 Setup 面板

测量界面上按 F4 Setup，在 HOLD 和 AutoHOLD 中也可以。MIN MAX、Peak 和录制结果页没有 Setup 键；那里的 F4 是 Close。标题 "Setup"，两列：Reset、Meter Info、Instrument、Recording；Display、Calibration、Contrast、Secure Erase。F1 显示所选项目的名称并打开它；F4 Close 回到测量界面。光标被记住，且无法通过协议读取（第 1 节，规则 2）。Setup 及其各页打开时 QDDB 仍然可用。在 Setup 内按 INFO 会显示 Setup 项目的上下文帮助；它的 Close 回到 Setup 面板（而不是测量界面）。帮助中还列有一个 "Software Update" 项，在面板中不可见。

**有意不探索，不要碰：** Display、Calibration、Contrast、Secure Erase，以及隐藏的 Software Update 项。Secure Erase 和 Software Update 是有意没有打开的；从名称判断，它们可能擦除数据或更改固件。

- **Reset** 被选中时：F1 破折号、F2 Setup、F3 Meter、F4 Close。F2 和 F3 各自打开一个 "ATTENTION" 确认（F1 OK、F4 Cancel），随后回到测量界面。Setup："Reset Setup settings, Temperature Offset, and dBm reference selection to factory defaults?"。Meter："Reset all meter settings to factory defaults?"。只按过 Cancel。绝不要按 OK。由于 Setup 面板在协议上不可见，一次误按的 F4，再接 F2 或 F3，然后 F1（本意是 Menu、+Name、REL 或 Edit），就会确认一次恢复出厂设置。确认页上的 F4 是 Cancel。
- **Meter Info**：只读列表（Model、Serial、Firmware Version、校准字段、用 FlukeView Forms 填写的 Operator / Company / Site / Contact Info、可用录制存储器、板 ID、引导程序版本、校准版本）。UP 和 DOWN 一次滚动一行（不改变对比度），到两端即停。F4 Close（直接回到测量界面）。
- **Instrument**：Beeper、dBm Reference、Auto Backlight Timeout、Auto Power Off、Smoothing (AC)。UP 和 DOWN 移动光标，两端不回绕。F1 Edit、F4 Close（直接回到测量界面）。在编辑模式中，数值高亮并带上下标记；F1 OK、F4 Cancel（恢复旧值；每个项目都检查过）。

  | 项目 | 取值 |
  |---|---|
  | Beeper | On、Off |
  | dBm Reference | 4 位数字编辑器（0600 ohm）；LEFT 和 RIGHT 选择数位（到两端即停），UP 和 DOWN 修改数字 |
  | Auto Backlight Timeout | 5、10、15、20、25、30 min、Off（回绕） |
  | Auto Power Off | Off、15、25、35、45、60 min（回绕） |
  | Smoothing (AC) | On、Off |
- **Recording**（页面标题 "Format"）：Event Threshold for AutoHOLD（1 % 一步；见到 4 到 12）和 Event Threshold for Recording（1、4、5、10、15、20、25 %、Off；回绕）。F1 Edit、F4 Close（它返回到哪里没有记录）；编辑方式同上。

### 3.8 INFO 帮助

INFO 打开一个以功能名称为标题的帮助查看器（例如 "Low Impedance Input"、"Measure Volts AC"、"Measure Volts DC"）：先是说明，然后是当前屏幕上每个图标或按键各一段。F1 Prev（上一段）、F2 Next、F3 More...（下翻一页）、F4 Close（回到测量界面；在 Setup 内打开的帮助则关闭到 Setup 面板）。DOWN 也可以滚动；到末尾即停，不回绕。长按 INFO 会在上次离开的滚动位置重新打开帮助。帮助文字只出现在 LCD 上；它打开期间 QDDB 仍然回答测量值。

### 3.9 背光

BACKLIGHT（短按或长按，每次一步）：关、亮、暗、关。它在 MIN MAX 结果页和保存页上也可用（README 中记录的较早观察，本次遍历未重复）。表笔报警警告会强制背光为亮。

## 4. 菜单机制（所有带 Menu 的位置）

F1 Menu 打开一个标题为 "Menu" 的浮层，有两列项目（有些项目含两三个选项，由软键显示）。LEFT 和 RIGHT 按行顺序在项目间移动，UP 和 DOWN 按列顺序移动，都会回绕。软键标签随所选项目而变：按下你想要的选项对应的软键。光标被记住，且无法通过协议读取。F4 Close 关闭菜单。被拒绝的选择会让菜单保持打开。一个选择可能需要时间才生效：低通需要超过 1 s，所以在断定失败之前请再读一次状态。

## 5. 拨盘位置

按遍历的顺序。“Home”列出测量界面上的软键 F1 / F2 / F3 / F4。所有位置都按第 3 节共用 HOLD、Save、Setup、INFO 和 BACKLIGHT；只列出差异。HOLD 然后 F1 AutoHOLD 在 V~、V DC 和 Ohm 上见到过（mV~ 与 V~ 表现相同）。在 LoZ V~ 上，HOLD 下的软键没有记录；在其他位置上只检查了 HOLD 本身；录制期间 HOLD 会清空软键。

### 5.1 LoZ V~（`V_AC_LOZ`）

Home："0.0 VAC LoZ"、"Manual Range 1000 V"，副读数为 VDC。软键：- / Save / - / Setup。没有 Menu。RANGE 和长按 RANGE 都不起作用（固定 1000 V）。F1、F3、LEFT、RIGHT 不起作用。MIN MAX 不起作用。INFO 打开 "Low Impedance Input"。Save 面板没有 Record。其他按键见第 3 节。

### 5.2 V~（`V_AC`）

Home："Auto Range 5 VAC"，条形图 0 到 5。软键 Menu / Save / - / Setup（F3 在 Hz/%/ms、dBm 和 REL 中变成模式键）。RANGE：AUTO 5、MAN 5、MAN 50、MAN 500、MAN 1000、MAN 5、...；长按 RANGE = 自动。INFO "Measure Volts AC"。HOLD 与 AutoHOLD、MIN MAX 见第 3 节。

Menu：第 1 列 VAC、REL、Peak/CF、low-pass（图标）；第 2 列 Hz/%/ms、dBm、dBV。

| 项目 | F1 | F2 | F3 | 结果 |
|---|---|---|---|---|
| VAC | VAC | | | 普通 V AC，自动量程 |
| REL | REL | REL % | | 见 3.5（锁定当前使用的量程） |
| Peak, CF | Peak | CF | | Peak：见 3.4。CF：大字显示波峰因数，下方是 V AC（副功能 `CREST_FACTOR`） |
| low-pass | low-pass | low-pass + Hz | | low-pass 横幅，强制 MAN 1000 V（`modes` = `LOW_PASS_FILTER`）；生效较慢。+ Hz：大字显示频率，下方是 V AC |
| Hz, %, ms | Hz | % | ms | 大字频率，下方 V AC（`HERTZ`）；占空比，下方 Hz（`DUTY_CYCLE`）；脉宽，下方 Hz（`PULSE_WIDTH`） |
| dBm | dBm | dBm, Hz | | dBm，下方是 V AC（或 Hz）；QDDB 中有 `DB_REF`（副功能 `DBM`、`DBM_HERTZ`） |
| dBV | dBV | dBV, Hz | | 同 dBm 但没有参考值（`DBV`、`DBV_HERTZ`） |

在 Hz、占空比和脉宽模式下，主界面的 F3 切换触发边沿（`POSITIVE_EDGE` / `NEGATIVE_EDGE`）。在 dBm 中它是 "Ref"：打开 "dBm Reference"，数值高亮；UP 和 DOWN 循环预设 4、8、16、25、32、50、75、600、1000 ohm，立即生效（没有 OK 或 Cancel），F4 Close。如果改了它，请恢复为 600。在 REL 中，主界面 F3 是 "REL %"。

### 5.3 mV~（`MV_AC`）

Home："Auto Range 500 mVAC"。软键 Menu / Save / - / Setup。RANGE：AUTO 500、MAN 500、MAN 50（输入更大时为 OL）、MAN 500、...；长按 = 自动。Menu 与 V~ 相同，第一项为 "mVAC"；每一项都检查过（副功能与 V~ 相同）。菜单中的 DOWN 从 dBV 回绕到 mVAC。REL % 可直接从 Menu 使用（`REL_PERCENT`）；再次选择则结束。在 dBV 模式下 REL % 被拒绝，而 REL 给出相对 dB 读数。低通生效较慢。

### 5.4 V DC（`V_DC`）

Home：以零为中心的条形图 -5 到 5，"Auto Range 5 VDC"，软键 Menu / Save / - / Setup（F3 在 Hz/%/ms 中变成边沿切换，在 REL 中变成 REL %）。RANGE：AUTO 5、MAN 5、50、500、1000、5、...；长按 = 自动。INFO "Measure Volts DC"。Menu（没有 dBm、dBV 或 low-pass）：第 1 列 VDC、REL、Peak；第 2 列 AC+DC、Hz/%/ms。LEFT 和 RIGHT 的行顺序：[VDC, AC+DC]、[REL, Hz]、[Peak]。

| 项目 | F1 | F2 | F3 | 结果（主功能 / 副功能） |
|---|---|---|---|---|
| VDC | VDC | | | `V_DC` |
| AC+DC | AC+DC | AC, DC | DC, AC | `V_AC_PLUS_DC`（真有效值 AC+DC，单位 "V AC+DC"，无条形图）；`V_AC_OVER_DC`（大字 AC，下方 DC）；`V_DC_OVER_AC`（大字 DC，下方 AC）。这些改变的是主功能，而不是副功能 |
| REL | REL | REL % | | 见 3.5（锁定 MAN 5） |
| Peak | Peak | | | Peak 会话（`V_DC` / `PEAK_MIN_MAX`） |
| Hz, %, ms | Hz | % | ms | `V_DC` / `HERTZ`、`DUTY_CYCLE`、`PULSE_WIDTH`，下方为 VDC；主界面 F3 = 边沿切换 |

### 5.5 mV DC（`MV_DC`）

Home：以零为中心的条形图 -50 到 50，"Auto Range 50 mVDC"。RANGE：AUTO 50、MAN 50、MAN 500、MAN 50、...；长按 = 自动。Menu：第 1 列 mVDC、REL、Peak、AC+DC；第 2 列 Temp、Hz/%/ms。从第一项按 UP 会回绕到最后一项。

| 项目 | F1 | F2 | F3 | 结果 |
|---|---|---|---|---|
| mVDC | mVDC | | | `MV_DC` |
| REL | REL | REL % | | 同 V DC（锁定 MAN 50） |
| Peak | Peak | | | `MV_DC` / `PEAK_MIN_MAX`，保持自动量程。在温度模式和 DC,AC 模式下被拒绝（菜单保持打开）：只能从普通 mVDC 进入 |
| AC+DC | AC+DC | AC, DC | DC, AC | `MV_AC_PLUS_DC`、`MV_AC_OVER_DC`、`MV_DC_OVER_AC` |
| Temp | F | C | | 主功能 `TEMPERATURE`（热电偶），固定手动量程 1350 C / 2462 F，无条形图，QDDB 带有 `TEMP_OFFSET`。主界面软键 Menu / Save / Offset / Setup。没有探头时："ATTENTION Open Thermocouple."（读数 `OPEN_TC`）。RANGE 被忽略。MIN MAX 和 REL 可用（REL 以 C 为单位，QDDB 中的 `REL_REFERENCE` 以 C 为单位） |
| Hz, %, ms | Hz | % | ms | `HERTZ` / `DUTY_CYCLE` / `PULSE_WIDTH`，下方为 mV DC |

温度下的 F3 Offset 打开 "Temperature Offset"：编辑器 +000.0（符号、3 位整数、1 位小数），光标在最后一位，LEFT 和 RIGHT 移动（可以到达符号位），UP 和 DOWN 修改并立即生效（`TEMP_OFFSET` 随之变化，`press` 会在 `changed` 中列出 `temp_offset`）；只有 F4 Close。请保持为 0.0。

### 5.6 Ohm（`OHMS`）

Home：条形图 0 到 500，"Auto Range 500 ohm"。软键 Menu / Save / continuity（蜂鸣器符号）/ Setup。RANGE：AUTO 500 ohm、MAN 500 ohm、5 k、50 k、500 k、5 M、30 M、50 M、500 M、500 ohm、...；长按 = 自动。30 Mohm 这一档只在 QDDB 的量程值中见到，没有在 LCD 上核对。HOLD 与 AutoHOLD、MIN MAX（保持自动量程）以及 REL / REL %（REL 锁定 MAN 500）与别处相同。

- 主界面 F3 continuity：主功能 `CONTINUITY`，"Continuity" 与蜂鸣器图标，大字欧姆读数，固定 MAN 500 ohm，带阈值的条形图和 "Beep on SHORT"；短路时 QDDB 状态为 `SHORT_CIRCUIT`（见到 4.7 ohm）。此后 F3 显示 "ohm"，并以自动量程回到 `OHMS`。仪表蜂鸣器关闭时蜂鸣器无声；开启时未检查。
- Menu：第 1 列 "Ohms, nS, continuity"、REL；第 2 列 "continuity Beeper"。
  - Ohms 项：F1 Ohms、F2 nS、F3 continuity。nS：`CONDUCTANCE`，MAN 50 nS（短路为 OL），软键 Menu / Save / - / Setup。
  - Beeper 项：F1 Beeper 切换连续性蜂鸣器（图标被划掉 = 静音；同时切换到 continuity），F2 Short/Open 切换 "Beep on SHORT" / "Beep on OPEN"。两者都切换过一次并已恢复。

### 5.7 二极管 / 电容（`DIODE_TEST`、`CAPACITANCE`）

一个拨盘位置；万用表会记住两者中上次使用的是哪一个。主界面软键 Menu / Save / - / Setup。Menu："Diode, Cap"（F1 Diode、F2 Cap）、REL（F1 REL、F2 REL %）。

- Cap："OL mF" 与电容符号，"Auto Range 100 mF"。RANGE：AUTO 100 mF、MAN 100 mF、1 nF、10 nF、100 nF、1 uF、10 uF、100 uF、1000 uF、10 mF、100 mF、...；长按 = 自动。REL（锁定量程）、MIN MAX 和 HOLD 可用。
- Diode："V DC" 与二极管符号，固定 MAN 5 V DC；RANGE 和长按 RANGE 被忽略；在 4.7 mV 时状态为 `SHORT_CIRCUIT`。MIN MAX 被忽略。HOLD 可用。

### 5.8 A 位置，表笔插在 mA 插孔（`MA_*`）

A 拨盘位置服务于两个电流插孔。表笔在 mA 插孔时：主界面 "0.00x mADC"，以零为中心的条形图 -50 到 50，"Auto Range 50 mADC"，软键 Menu / Save / - / Setup。RANGE：AUTO 50 mA、MAN 50、MAN 400、MAN 50、...；长按 = 自动。Menu：第 1 列 "AC, DC"（F1 AC、F2 DC）、AC+DC（F1 AC+DC、F2 AC,DC、F3 DC,AC）、REL、Peak/CF；第 2 列 Hz/%/ms。

| 项目 | 结果 |
|---|---|
| AC | `MA_AC`（"mAAC"，条形图 0 到 50） |
| DC | `MA_DC` |
| AC+DC / AC,DC / DC,AC | `MA_AC_PLUS_DC` / `MA_AC_OVER_DC` / `MA_DC_OVER_AC` |
| Peak（从 DC） | `MA_DC` / `PEAK_MIN_MAX` 会话 |
| CF | `MA_AC` / `CREST_FACTOR`（切换到 AC） |
| Hz, %, ms | `MA_AC` / `HERTZ`、`DUTY_CYCLE`、`PULSE_WIDTH`（AC 侧） |
| REL, REL % | 与别处相同（锁定 MAN 50） |

### 5.9 A 位置，表笔插在 A 插孔（`A_*`）

Home "0.0000 ADC"，条形图 -5 到 5，"Auto Range 5 ADC"。RANGE：AUTO 5 A、MAN 5、MAN 10、MAN 5、...；长按 = 自动。Menu 与 mA 插孔相同（光标沿用），结果相同，对应 `A_AC`、`A_DC`、`A_AC_PLUS_DC`、`A_AC_OVER_DC`、`A_DC_OVER_AC`、`A_DC` / `PEAK_MIN_MAX`、`A_AC` / `CREST_FACTOR`、`A_AC` / `HERTZ`、`DUTY_CYCLE`、`PULSE_WIDTH`；REL 锁定 MAN 5。

### 5.10 uA（`UA_*`），表笔插在 mA/uA 插孔

Home "0.01 uADC"，条形图 -500 到 500，"Auto Range 500 uADC"。RANGE：AUTO 500 uA、MAN 500、MAN 5000、MAN 500、...；长按 = 自动。Menu 布局与 mA 和 A 不同：第 1 列 "AC, DC"、AC+DC、REL；第 2 列 "Peak, CF"、Hz/%/ms。LEFT 和 RIGHT 逐行移动并回绕（REL，RIGHT，"AC, DC"；"AC, DC"，RIGHT，Peak）。结果：`UA_AC`、`UA_DC`、`UA_AC_PLUS_DC`、`UA_AC_OVER_DC`、`UA_DC_OVER_AC`、`UA_DC` / `PEAK_MIN_MAX`（取 MAN 500）、`UA_AC` / `CREST_FACTOR`、`UA_AC` / `HERTZ`、`DUTY_CYCLE`、`PULSE_WIDTH`、REL（锁定 MAN 500）。REL 与 Peak 一样，在 "DC, AC" 模式下被拒绝；从普通 DC 可用。

### 5.11 LoZ Ohm（`OHMS_LOW`）

Home 在什么都没接时为 "OL ohm Lo Ohms"，条形图 0 到 50，固定 "Manual Range 50 ohm"；实时状态 `OL_RANGE`。软键 Menu / Save / - / Setup（F3 不起作用）。RANGE 和长按 RANGE 被忽略。Menu 只有 REL（F1 REL、F2 REL %）；REL 即使在 OL 上也能工作（参考值为 OL）。MIN MAX 可用（OL 时 Average 显示横线）。HOLD 可用。

## 6. 插孔规则

- A 拨盘位置需要表笔插在对应的插孔：mA 插孔给出 mA 量程，A 插孔给出 A 量程。两个插孔都插了表笔，或都没插，会触发表笔报警警告。COM 无所谓。
- uA 需要表笔插在 mA 插孔；表笔插在 A 插孔会触发警告。
- 其他每个位置都要求 mA 和 A 插孔为空，否则会出现警告。
- 该警告在协议上不可见（第 2 节）。在 A 和 uA 位置上，如果按键不再起作用，而读数是近零电流（约 0.001 到 0.002 mA，状态 `NORMAL`），请怀疑插孔错了，并请人检查表笔。对于电流功能中被忽略的 HOLD、MINMAX 或 RANGE，`press` 会给出 `LEAD_ALERT_POSSIBLE`，这是同一种怀疑（第 1 节，“`press` 报告什么”）。
- 在其他每个位置上，有表笔留在 mA 或 A 插孔中也会触发同一个警告，但 `press` 对那里被忽略的 HOLD、MINMAX 或 RANGE 只给出 `KEY_IGNORED`，不给出 `LEAD_ALERT_POSSIBLE`（这种情况下读数显示什么，没有通过协议观察过）。如果这样的按键在该位置上本应可用（第 5 节），请怀疑表笔，并请用户检查。

证据：遍历期间，通过协议只观察到 A 位置上两个电流插孔都没有表笔的情形。其他规则是在万用表本机上确认的，没有逐键记录。

## 7. 操作配方

除另有标注外，这些序列都是在万用表上观察到的：Beeper off 是端到端运行过的，其他都是由逐个观察到的步骤拼合而成。在步骤之间读取状态，并记住菜单、面板和光标不会显示在其中（第 2 节）。

- **回到测量界面**：没有哪个键总能到达那里。QDDB 在测量界面上的应答与打开 Menu、Save 面板、View Memory、Delete Memory、Setup 面板、某个 Setup 页面或帮助时相同，所以一直按 F4 直到状态看起来正常，在盲操作时行不通：F4 在测量界面、HOLD 和 AutoHOLD 中会打开 Setup，第二次 F4 又把它关闭。根据自上次已知状态以来按过的键来跟踪屏幕，每打开一个浮层就按一次 F4。下列各项各用一次 F4 关闭：Menu；Save 面板（HOLD 解除）；名称列表（直接回到测量界面，HOLD 解除）；Delete Memory（到测量界面）；Measurement 和 Recording 已保存项目查看器（直接回到测量界面）；Setup 面板；Meter Info 和 Instrument 页面（直接回到测量界面，而不是 Setup 面板）；帮助（到测量界面，或在 Setup 内打开时到 Setup 面板）。从 View Memory 面板、Min Max 和 Peak 查看器、Trend 视图以及 Recording（"Format"）设置页按 F4 Close 会去哪里，没有记录。在 Setup 编辑器中，第一次 F4 是 Cancel，第二次 F4 关闭该页。在有帮助的地方使用状态：
  - `modes` 中有 `MIN_MAX_AVG`：MIN MAX 或 Peak 会话正在运行，F4 是 Stop；F4 Stop 然后 F4 Close 会丢弃未保存的结果。改用 MINMAX 结束 MIN MAX 会话（Peak 会话中的 MINMAX 没有试过）。
  - `modes` 中有 `RECORD`：录制正在运行，F4 是 Stop。它结束录制，随后的 F4 Close 会丢弃它，除非先保存。如果同时开着 HOLD，软键为空：先解除 HOLD。
  - 有 `HOLD` 而没有 `MIN_MAX_AVG`：要么是 HOLD 画面（用 HOLD 解除；那里的 F4 打开 Setup），要么是打开的 Save 面板（F4 Close）。从 Save 面板打开的名称列表也会保持读数（F4 Close），但它打开时 QDDB 是否应答没有检查。
  - `AUTO_HOLD`：用 F1 Cancel 结束。
  - 阻塞状态（第 2 节）：已保存项目查看器、MIN MAX 或录制结果页（很可能也包括 Peak 结果页）、从结果页打开的 Save 面板（F4 Close 回到结果页）或关机确认（F4 Cancel；绝不要按 F1，它会关掉万用表）处于打开状态，或者刚刚转动了拨盘（稍后再读一次状态，然后再按任何键）。结果页上的 F4 Close 会丢弃未保存的结果。

  如果不知道按键历史，请在按 F4 之前，让表旁的人说出屏幕上显示什么。
- **Beeper off**（需要有人看着万用表：Setup 光标和蜂鸣器设置在协议上不可见；如果该面板像其他两列面板那样按列顺序排列（DERIVED），则从 Recording 按 DOWN 会进入第 2 列，即 Display、Calibration、Contrast 和 Secure Erase）：F4 Setup。让人读出 F1 标签，并按 DOWN 直到它显示 Instrument（从 Reset 起按两次 DOWN）。F1 Instrument（页面打开时光标在 Beeper 上；列表不回绕，所以 UP 只能把光标带到 Beeper 为止）。F1 Edit。如果高亮的值是 On，按 UP（"Off"）再按 F1 OK；否则按 F4 Cancel。F4 Close（直接回到测量界面）。状态栏的喇叭图标失去声波；这无法通过协议核对。
- **Beeper on**：路径相同；在编辑模式中按 UP 或 DOWN 直到高亮 "On"，然后 F1 OK（没有在硬件上试过；只观察到从 On 经 UP 变为 Off）。
- **启动、停止并保存 MIN MAX**：MINMAX，然后检查 `modes` 是否显示 `MIN_MAX_AVG`。如果没有，说明 MIN MAX 在此被忽略（LoZ V~、二极管）：停下，不要按 F4。再按一次 MINMAX 停止：状态变为阻塞，意味着显示的是结果页。优先使用 MINMAX 而不是 F4 Stop，因为没有会话在运行时 F4 会打开 Setup。然后 F2 Save（只有 Save 的 Save 面板）再 F2 Save。第二次 F2 之后万用表去了哪里没有记录：如果状态仍然阻塞，按一次 F4 Close 然后再读状态（只要仍然阻塞就重复）；如果 QDDB 有应答，说明你在测量界面，不要再按任何键。要放弃数值，请在结果页上按 MINMAX 或 F4 Close。F1 Restart 清除数值并保持会话继续运行。
- **设置 REL**：F1 Menu，然后把光标移到 REL。Menu 会在上次使用的项目上重新打开，而光标不可见，所以请从已知位置数步数，或让人读出软键。按 F1 REL（或在提供的地方按 F2 REL %），然后读状态。如果 `modes` 显示 `REL` 或 `REL_PERCENT`，就完成了，且 Menu 已关闭。如果功能、量程和模式都没变，说明该选择被拒绝了（例如在 DC,AC 模式下），Menu 仍然打开：恰好按一次 F4。如果其他东西变了，说明选了别的项目，Menu 已关闭：不要按 F4（它会打开 Setup）；而是撤销该选择。在 Ohm 的 Menu 中，"continuity Beeper" 项上的 F1 会让连续性蜂鸣器静音或取消静音（并切换到 `CONTINUITY`），F2 切换 Beep on SHORT/OPEN；这两个设置在协议上都不可见，再次选择即可撤销。要结束 REL，在 Menu 中再次选择 REL（或再次选择 REL % 以离开百分比模式）。
- **更改 dBm 参考值**：只有当状态显示副功能 `DBM` 时才可以（V~ 或 mV~；`DBM_HERTZ` 未检查）。主界面 F3 Ref 打开 "dBm Reference" 页面；每次 UP 或 DOWN 移动一个预设（4、8、16、25、32、50、75、600、1000 ohm）并立即生效。第一次按键之后，检查 `changed` 是否列出了 `db_ref`（CLI 和 MCP 中都适用；MCP `press` 的状态不含读数，所以 `DB_REF` 本身只出现在 CLI 的状态中或单独的 `read` 中）。如果没有，说明页面没有打开，那次按键只是一个对比度步进：反向按一次，并且不要按 F4（在测量界面上它会打开 Setup）。完成后 F4 Close；之后恢复为 600。
- **回到自动量程**：长按 RANGE。在 REL、二极管、LoZ V~ 和 LoZ Ohm 中，RANGE 和长按 RANGE 被忽略，在温度中 RANGE 被忽略。连续性（固定 MAN 500 ohm）、低通（MAN 1000 V）和 nS（MAN 50 nS）也使用固定量程；那里没有试过 RANGE。
- **保存一个读数**（从普通测量界面）：F2 Save 打开 Save 面板并让万用表进入 HOLD；然后按 F2 Save。只有当 Save 面板光标在 Save 上时才会保存，因为面板会在上次使用的项目上重新打开，而光标在协议上不可见（在 View Memory 和 Delete 上 F2 是破折号；在 Record 上没有记录）。第二次 F2 之后，检查 `modes` 不再显示 `HOLD`（已保存，立即回到测量界面）。如果仍显示 `HOLD`，说明面板仍然打开，光标在另一项上：按一次 F4 Close（解除 HOLD），并且不要在那里按 F1 或 F3（光标可能在 Delete 上）。如果第一次 F2 没有带来 `HOLD`，说明 Save 面板没有打开，因为打开着另一个浮层，可能是 Setup：停下，并在下一个软键之前弄清万用表在哪里。
- **录制**（没有端到端验证：从 Record 项打开 Recording 页面的按键没有记录，而且 Save 光标在协议上不可见）：F2 Save；在有人读面板的情况下把光标移到 Record；打开它；如有需要用 F1 Edit 调整 Duration 和 Interval；F2 Start。录制运行期间 `modes` 显示 `RECORD`。状态变为阻塞时，录制已经结束：按 F2 Save，然后在 Save 面板上再按 F2 Save（之后万用表去了哪里没有记录）。不保存就 Close 会丢弃该录制。
- **INFO 帮助**：INFO 为当前屏幕打开帮助页，但其文字只出现在 LCD 上，协议上什么都不会返回，所以它对看不到屏幕的智能体没有帮助。F4 Close 返回（在 Setup 内打开帮助时返回 Setup 面板）。

## 8. 各拨盘位置汇总

| 拨盘 | 主功能（QDDB） | Menu 项 | 主界面 F3 | 手动量程档位 |
|---|---|---|---|---|
| LoZ V~ | `V_AC_LOZ`（副功能 V DC） | 无 | - | 固定 1000 V |
| V~ | `V_AC` | VAC、REL、Peak/CF、low-pass (+Hz)、Hz/%/ms、dBm (+Hz)、dBV (+Hz) | 模式键（边沿、Ref、REL %） | 5、50、500、1000 V |
| mV~ | `MV_AC` | 同 V~，首项为 mVAC | 同 V~ | 500、50 mV |
| V DC | `V_DC`、`V_AC_PLUS_DC`、`V_AC_OVER_DC`、`V_DC_OVER_AC` | VDC、REL、Peak、AC+DC（3 个选项）、Hz/%/ms | 模式键 | 5、50、500、1000 V |
| mV DC | `MV_DC`（及 AC/DC 组合）、`TEMPERATURE` | mVDC、REL、Peak、AC+DC (3)、Temp (F/C)、Hz/%/ms | Temp 中为 Offset | 50、500 mV（Temp 固定） |
| Ohm | `OHMS`、`CONTINUITY`、`CONDUCTANCE` | Ohms/nS/continuity、REL、continuity Beeper（静音、Short/Open） | continuity 和 ohm | 500 ohm 到 500 Mohm（8 个手动量程，30 M 仅在 QDDB 中见到） |
| Diode / Cap | `DIODE_TEST`、`CAPACITANCE` | Diode/Cap、REL | - | Cap 1 nF 到 100 mF；二极管固定 5 V |
| A（mA 插孔） | `MA_AC` / `MA_DC`（及组合） | AC/DC、AC+DC (3)、REL、Peak/CF、Hz/%/ms | - | 50、400 mA |
| A（A 插孔） | `A_AC` / `A_DC`（及组合） | 同 mA | - | 5、10 A |
| uA（mA 插孔） | `UA_AC` / `UA_DC`（及组合） | 相同的项目，Peak/CF 在第 2 列 | - | 500、5000 uA |
| LoZ Ohm | `OHMS_LOW` | REL | - | 固定 50 ohm |

所有位置共有：HOLD（在提供的地方，HOLD 然后 F1 AutoHOLD）、MIN MAX 会话（LoZ V~ 和二极管除外）、Save 面板（在适合录制的位置带 Record）、Setup 面板、INFO 帮助、BACKLIGHT 循环。

看起来像“什么都没发生”的拒绝情形：DC,AC 组合模式下的 Peak 和 REL、温度下的 Peak、dBV 模式下的 REL %、二极管和 LoZ V~ 中的 MIN MAX、量程固定处的 RANGE、表笔报警警告下的每一个键。被拒绝的软键会让菜单保持打开，所以随后一次盲目的 F4 会落到别处（它曾经打开了 Setup）。在按下一个软键之前，始终要知道显示的是哪个屏幕；状态不会显示菜单（第 2 节）。
