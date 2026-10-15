# PDF 单面拼接器（pdf_splice）

解决「打印机只能单面扫描」的问题：同一份文档先扫一面得到一叠 PDF，翻面再扫另一面得到另一叠，
本工具把两叠**逐页交错**拼成一份完整 PDF。

界面是三列：

```
PDF_A  |  PDF_B  |  PDF_out
```

## 快速开始

1. 双击 `start.bat`（会自动打开浏览器，地址 `http://127.0.0.1:8770/`，端口被占则自动往后找）
2. 左栏 `PDF_A` 载入先扫的那一叠，中栏 `PDF_B` 载入翻面后扫的那一叠
   - 点栏内空白处选文件，也可以直接把 PDF 拖进栏里
   - 一次可以选多个文件，会按选择顺序合并成一叠
3. 点右上「**生成成品**」→ 右栏出现交错后的成品
4. 确认无误后点「**导出 PDF**」，文件落在 `output/` 目录（也可以直接点「输出目录」打开）

命令行参数：`start.bat --port 9000 --no-browser`

## 功能

| 功能 | 位置 | 说明 |
| --- | --- | --- |
| 布局样式 | 顶栏右侧「样式」 | **网格**：一屏看多页，适合快速定位；**竖向预览**：三栏都变成整页纵向排列、逐页往下滚，适合核对扫描质量。选择会记在浏览器里 |
| PDF 转向 | 每张缩略图右上角 `↺` `↻` | 单页旋转 90°；栏头「↺ 全部 / ↻ 全部」整叠旋转 |
| 放大预览 | 点击任意缩略图 | 大图查看，`←` `→` 翻页，`R` 右转，`Esc` 关闭 |
| 奇偶页交错拼接 | 右栏「自动交错」+「先 A / 先 B」 | A、B 逐页交替：A1 B1 A2 B2 … |
| 后一叠倒序 | 右栏勾选框 | 翻面后那一叠堆叠顺序常常是反的，勾上即倒序取页 |
| 顺序拼接 | 右栏「顺序拼接」 | 先全部 A，再全部 B（两摞独立文档时用） |
| 按页码排序 | 右栏「按页码排序」 | 读页脚 / 页眉的 `n/N` 页码，按真实页码自动排序 |
| 拖动某一页拼接 | 从 A / B 拖到右栏 | 拖到右栏任意位置即插入该处；成品内拖动可重排 |
| 删除成品页 | 成品行右侧 `×` | 也可以把成品页拖回 A / B 栏 |
| 导出文件名 | 右栏输入框 | 留空则自动命名 `拼接_年月日_时分秒.pdf` |

旋转规则：转源页时，成品里**由这一页生成、且没被单独转过**的页会跟着转；已经单独转过的成品页保持不动。

## 文件结构

```
pdf_splice/
├─ start.bat          双击启动（Windows）
├─ app.py             Flask 后端：REST API + 静态文件托管
├─ pdf_engine.py      核心引擎：载入 / 旋转 / 页序 / 缩略图 / 导出（可单独命令行使用）
├─ selftest.py        端到端自测（56 项，不起服务、不占端口）
├─ requirements.txt   flask + pymupdf
├─ static/
│   ├─ index.html     三列界面骨架
│   ├─ style.css      浅色 / 深色两套主题 × 网格 / 竖向预览两种样式
│   └─ app.js         前端逻辑（拖拽、旋转、预览、导出）
├─ docs/              两套样式的界面截图（view-grid.png / view-preview.png）
├─ uploads/           载入的 PDF 暂存（替换同一栏时会自动清理旧文件）
└─ output/            导出的成品
```

引擎也能脱离界面直接用：

```bash
python pdf_engine.py --a 奇数页.pdf --b 偶数页.pdf --out 成品.pdf
python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --reverse-second
python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --autosort
```

## 自测

```bash
python selftest.py
```

用工作目录里的 `奇数页.pdf`（页脚 1/6、3/6、5/6）和 `偶数页.pdf`（页脚 2/6、4/6、6/6）
验证交错页序、倒序、旋转、拖动插入 / 重排 / 删除、页码自动排序、导出内容，以及全部 API。

## REST API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 取完整状态快照 |
| POST | `/api/upload` | 表单 `slot=A/B` + `file`（可多个） |
| POST | `/api/rotate` | `{slot, delta, index?}`（不传 index 为整叠） |
| POST | `/api/build` | `{mode:'interleave'/'concat', first:'A'/'B', reverse_second}` |
| POST | `/api/autosort` | 按页脚页码排序 |
| POST | `/api/out/insert` | `{slot, index, at}`；`all:true` 整叠插入 |
| POST | `/api/out/move` | `{from, to}` |
| POST | `/api/out/remove` | `{positions:[...]}` |
| POST | `/api/out/rotate` | `{positions:[...], delta}` |
| POST | `/api/out/clear` | 清空成品 |
| POST | `/api/clear_slot` | `{slot}` 卸载某一叠 |
| POST | `/api/swap` | 交换 A / B |
| GET | `/api/thumb/<slot>/<index>?r=&size=` | 页面缩略图 PNG |
| POST | `/api/export` | `{name}` 导出成品，返回磁盘路径与下载 URL |
| GET | `/api/download/<name>?inline=1` | 下载 / 内联预览成品 |
| POST | `/api/open_output` | 在资源管理器里打开输出目录 |
