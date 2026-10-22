# OPP 日志 / 谜题辅助 项目笔记

## 目标
从游戏日志自动取到本局关卡信息（trial / seed），并最终缓解 FunParkIllusion 里
`ROOM → SIGN → 代码` 面板"必须手动解题 + 手动记录"的问题。

## 已完成
- ✅ 从 `Opp.log` 提取最新 seed 的 Python 脚本（脚本路径：**待补**）。
- 提取锚点：日志中的 `Seed: (\d+)`，只出现在两类行：
  - `RB:  GameStageInfo changed. Program ID: ..., Trial ID: ..., Seed: ...`（进场，例：2099 行）
  - `RB:  StageReady. Trial ID: ..., Seed: ...`（阶段就绪，例：2549 行）
  - "最新" = 文件中最后一次匹配，即取末尾那行。

## 已确认的日志事实（2026.10.22 17:16–17:26 这一局）
- 本局：Program `programCoreFP`、Trial/Mission `FPI_MT02`、Stage `FunParkIllusion`、
  难度 Normal、Seed `543543`、1 人。
- 日志里**没有任何谜题内容**：无地名文本、无图标、无正确代码、无按键记录。
  全目录搜 `hourglass|puzzle|keypad|delivery|room.?sign|输入|code=` 只命中
  `backup/123.log` 的移动动画行（`interact_puzzlebox_Exit_152`）。
- 加载粒度只到子关卡：`STREAMING: >> PreloadingLevelNames` 列 20 个 `FPI_*` 子关卡，
  每次全量预加载（2052 / 2248 / 2301 / 2363 / 2440 / 2494 行附近），
  无法据此判断"玩家正站在哪台机器前"。
- 截图对应的 17:23:08，日志完全静默（3087 → 3095 行之间无游戏事件）。

## 结论（避免重复讨论）
1. **seed 推不出答案**，seed 路线到此为止，只作为"这一局是哪个关卡"的标识。
2. 面板是闭合词表：ROOM 地名集合固定、SIGN 是固定位置小图标、数字键 3×3 固定
   → 模板匹配（1–3 ms）远优于 OCR（10–300 ms），OCR 只作兜底。
3. **不读进程内存**：游戏挂 EOS Anti-Cheat 内核驱动（1555 / 2007 行），封号风险。
4. 真正的瓶颈是"手动解题 + 手动记录"，不是"读不到"。

## 下一步（未定）
- A. 影子记录器：检测"输入代码"提示出现 → 存面板截图 + 追加 CSV（时间 / 截图 / 手动补代码）。
- B. 攒够样本后反推 `ROOM × SIGN → 代码` 规则；若跨局固定即可查表。
- C. 屏幕提示答案（外部辅助，不注入、不碰进程）。
- D. 自动填入代码：条款风险最高，暂不考虑。
