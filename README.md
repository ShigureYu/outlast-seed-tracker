# OPP Seed Tracker

《The Outlast Trials》某个需要「地点 → 标志 → 密码」对应关系的关卡辅助工具。
从游戏日志读出当前的 **Trial ID + Seed**，查本地 SQLite 答案表，用一个半透明置顶悬浮窗把密码显示出来；
表里还没有这个 seed 时，可以直接在悬浮窗里录入并保存。

> ## 免责声明
>
> 本项目仅为个人学习 Python GUI 开发与日志解析的练手之作。
>
> 项目内附带的数据库（`seed.db`）是由朋友个人手动收集，仅供私下小范围交流测试，
> **不保证数据的准确性**，请勿用于任何商业用途或公开传播。
>
> 如果你是该游戏开发商，认为此项目侵犯了您的权益，请联系我，我会立即删除。

## 功能

- 无边框、半透明、始终置顶，可拖拽，双击折叠，`×` / `Esc` 关闭
- 每 60 秒扫一次日志，只在 `(Trial ID, Seed)` 变化时刷新；`F5` 手动刷新
- 数据全部来自数据库，界面按表结构动态生成（列数、顺序、中文名都不写死）
- 查到记录 → 只读展示；没查到 → 按表结构给出可编辑输入框 + 保存

## 结构

| 文件 | 作用 |
|---|---|
| `log_parser.py` | 定位最新日志，解析 `Trial ID` 与 `Seed`（Seed 在日志里是三位重复两遍，如 `543543`） |
| `seed_importer.py` | 把 `data/seed.xlsx` 的答案表导入 `data/seed.db` |
| `seed_lookup.py` | 查表 / 写库 / 关卡元信息，UI 只依赖这一层 |
| `overlay.py` | PyQt6 悬浮窗 |
| `app_paths.py` | 路径解析，保证打包成 exe 后仍能找到 `data/` |

## 使用

```powershell
uv sync                     # 或 pip install pyqt6 openpyxl
python seed_importer.py     # 首次：从 xlsx 生成 data/seed.db
python overlay.py
```

## 打包

```powershell
python seed_importer.py
python -m PyInstaller --noconfirm --onefile --windowed --name OPPOverlay --add-data "data/seed.db;data" overlay.py
```

产物是**单个** `dist/OPPOverlay.exe`。模板数据库会被打进 exe，首次运行时由
`app_paths.ensure_database()` 复制到 exe 同级的 `data/seed.db`——之后的录入写这份副本，
所以升级 exe 只需要覆盖 exe 本身，数据不会丢。

## 数据库

```
TRIAL_META(trial_id TEXT PK, name_zh TEXT, map_name TEXT, labels TEXT)
CHJ_MT02  (seed INTEGER PK, skull TEXT, spiral TEXT, triangle TEXT, star TEXT, sun TEXT)
```

- 每个 Trial 一张答案表，表名就是日志里的 Trial ID
- `labels` 存 `{"列名": "中文名"}`，没有映射时界面直接显示英文列名
- 码值一律 TEXT，保住 `014` 这种前导零；源表缺格存 NULL

## 更新数据（不用发版）

`data/seed.db` 是唯一数据源，改完推上去，用户下次启动就会看到「发现新数据」：

```powershell
# 1. 本地录入 / 直接改 data/seed.db
# 2. 提交并推送
git add data/seed.db
git commit -m "data: 补充 XXX 关的种子"
git push origin master
```

App 启动时拉取 `raw.githubusercontent.com/<repo>/master/data/seed.db`：

- 只补本地没有记录，**不覆盖**已有值；
- 同 seed 两边不同 → 逐条选择（超过 30 处自动切批量模式）；
- 远端行数不足本地一半 → 判定为异常，拒绝合并。

## 新增一个关卡

1. 在 `seed_importer.py` 顶部改四个常量：`SHEET_NAME` / `TRIAL_ID` / `NAME_ZH` / `MAP_NAME`
2. 在 `SYMBOLS` 里按 xlsx 表头顺序写「列名 → 表头」
3. 跑 `python seed_importer.py`（`labels` 列会自动补上中文名）
4. 界面的列数、顺序、名称全部从表结构读，不需要改 UI 代码

