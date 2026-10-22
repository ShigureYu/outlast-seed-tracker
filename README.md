# OPP Seed Tracker

《The Outlast Trials》某个需要「地点 → 标志 → 密码」对应关系的关卡辅助工具。
从游戏日志读出当前的 **Trial ID + Seed**，查本地 SQLite 答案表，用一个半透明置顶悬浮窗把密码显示出来；
表里还没有这个 seed 时，可以直接在悬浮窗里录入并保存。

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
python -m PyInstaller --noconfirm --onefile --windowed --name OPPOverlay overlay.py
```

产物在 `dist/OPPOverlay.exe`。**exe 旁边必须保留 `data/seed.db`**（数据库不打包进 exe，
否则用户录入的记录会随 onefile 临时目录一起消失）。

## 数据库

```
TRIAL_META(trial_id TEXT PK, name_zh TEXT, map_name TEXT, labels TEXT)
CHJ_MT02  (seed INTEGER PK, skull TEXT, spiral TEXT, triangle TEXT, star TEXT, sun TEXT)
```

- 每个 Trial 一张答案表，表名就是日志里的 Trial ID
- `labels` 存 `{"列名": "中文名"}`，没有映射时界面直接显示英文列名
- 码值一律 TEXT，保住 `014` 这种前导零；源表缺格存 NULL
