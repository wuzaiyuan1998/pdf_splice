# -*- coding: utf-8 -*-
"""
selftest.py —— 端到端自测（不启动常驻服务，不占端口）

用工作目录里的「奇数页.pdf / 偶数页.pdf」当样本：
  * 奇数页.pdf 页脚是 1/6、3/6、5/6
  * 偶数页.pdf 页脚是 2/6、4/6、6/6
正确答案就是交错成 1..6。

运行：python selftest.py
"""

from __future__ import annotations

import io
import os
import random
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pymupdf                                        # noqa: E402
from PIL import Image                                 # noqa: E402

import pdf_engine as engine                           # noqa: E402

PDF_A = os.path.join(BASE, "奇数页.pdf")
PDF_B = os.path.join(BASE, "偶数页.pdf")
OUT_DIR = os.path.join(BASE, "output")
os.makedirs(OUT_DIR, exist_ok=True)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("[ OK ] " if cond else "[FAIL] ") + name + (("   → " + str(detail)) if detail else ""))
    return bool(cond)


def footer_numbers(path):
    """读出成品每页页脚的 n（来自 n/6 形式），用来验证页序。"""
    doc = pymupdf.open(path)
    nums = []
    try:
        for i in range(doc.page_count):
            page = doc[i]
            r = page.rect
            clip = pymupdf.Rect(r.width * 0.55, r.height * 0.90, r.width, r.height)
            text = page.get_text("text", clip=clip)
            hit = re.findall(r"(\d+)\s*[/／]\s*(\d+)", text)
            nums.append(hit[-1][0] if hit else None)
    finally:
        doc.close()
    return nums


def labels(sess):
    return [it.label for it in sess.out]


def new_session():
    sess = engine.Session()
    sess.load("A", PDF_A)
    sess.load("B", PDF_B)
    return sess


# ============================================================ 空白页样本
# 这两份样本在前面的用例里都是临时生成的（output/ 不入库），只给空白页用例用。
# 文档页码标记写成 A-1 / B-3 这种，导出后按页脚就能核对「删完还剩哪些页、顺序对不对」。

PDF_W, PDF_H = 595, 842                                   # A4（pt）

BLANK_SPEC_A = [("text", "A-1"), ("blank", ""), ("noise", ""), ("text", "A-4")]
BLANK_SPEC_B = [("text", "B-1"), ("blank", ""), ("footer", "B-3"), ("text", "B-4")]

BLANK_A = os.path.join(OUT_DIR, "_t_blank_a.pdf")
BLANK_B = os.path.join(OUT_DIR, "_t_blank_b.pdf")


def make_blank_sample(path, spec):
    """
    造一份「带空白页」的样本。spec 每项是 (kind, marker)：

      text   —— 整页正文 + 页脚标记（正常页）
      footer —— 只有一枚页脚小字（模拟「只印了页码」的页）
      blank  —— 纯空白
      noise  —— 空白 + 满页淡噪点（模拟真实扫描件）
    """
    doc = pymupdf.open()
    for kind, marker in spec:
        page = doc.new_page(width=PDF_W, height=PDF_H)
        if kind == "text":
            for i in range(24):
                page.insert_text((72, 90 + i * 24), "正文样例行 %d，用来验证空白页识别" % i,
                                 fontsize=10.5, fontname="china-s")
        if kind == "noise":
            rnd = random.Random(7)
            for _ in range(260):
                x = rnd.uniform(10, PDF_W - 10)
                y = rnd.uniform(10, PDF_H - 10)
                gray = rnd.uniform(0.62, 0.85)
                page.draw_rect(pymupdf.Rect(x, y, x + 0.9, y + 0.9),
                               color=None, fill=(gray, gray, gray))
        if marker and kind in ("text", "footer"):
            page.insert_text((72, PDF_H - 40), marker, fontsize=10.5, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


def footer_marks(path):
    """读出每页页脚位置的文字（空白页是空串），用来核对删空白页后的页序。"""
    doc = pymupdf.open(path)
    marks = []
    try:
        for i in range(doc.page_count):
            page = doc[i]
            rect = page.rect
            clip = pymupdf.Rect(0, rect.height * 0.86, rect.width, rect.height)
            marks.append(page.get_text("text", clip=clip).strip())
    finally:
        doc.close()
    return marks


# ============================================================ 1. 引擎

def test_engine():
    print("\n=== 1. 引擎：载入与交错 ===")
    count, rots = engine.probe_pdf(PDF_A)
    check("载入 奇数页.pdf 得到 3 页且无旋转", count == 3 and rots == [0, 0, 0], (count, rots))

    sess = new_session()
    sess.build(mode="interleave", first="A", reverse_second=False)
    check("自动交错页序 = A1 B1 A2 B2 A3 B3",
          labels(sess) == ["A1", "B1", "A2", "B2", "A3", "B3"], labels(sess))

    demo = os.path.join(OUT_DIR, "拼接_demo.pdf")
    sess.export_pdf(demo)
    check("导出成品 6 页", pymupdf.open(demo).page_count == 6)
    check("成品页脚依次是 1..6", footer_numbers(demo) == list("123456"), footer_numbers(demo))
    check("导出的 PDF 能正常打开且有元数据",
          bool(pymupdf.open(demo).metadata.get("producer", "").startswith("pdf_splice")))

    print("\n=== 2. 引擎：倒序 / 先 B / 顺序拼接 ===")
    sess = new_session()
    sess.build(mode="interleave", first="A", reverse_second=True)
    sess.export_pdf(os.path.join(OUT_DIR, "_t_rev.pdf"))
    check("后一叠倒序 → 页脚 1,6,3,4,5,2",
          footer_numbers(os.path.join(OUT_DIR, "_t_rev.pdf")) == ["1", "6", "3", "4", "5", "2"],
          footer_numbers(os.path.join(OUT_DIR, "_t_rev.pdf")))

    sess = new_session()
    sess.build(mode="interleave", first="B", reverse_second=False)
    sess.export_pdf(os.path.join(OUT_DIR, "_t_firstb.pdf"))
    check("先 B 交错 → 页脚 2,1,4,3,6,5",
          footer_numbers(os.path.join(OUT_DIR, "_t_firstb.pdf")) == ["2", "1", "4", "3", "6", "5"])

    sess = new_session()
    sess.build(mode="concat", first="A", reverse_second=False)
    sess.export_pdf(os.path.join(OUT_DIR, "_t_concat.pdf"))
    check("顺序拼接 → 页脚 1,3,5,2,4,6",
          footer_numbers(os.path.join(OUT_DIR, "_t_concat.pdf")) == ["1", "3", "5", "2", "4", "6"])

    print("\n=== 3. 引擎：旋转 ===")
    sess = new_session()
    sess.build(mode="interleave")
    sess.rotate("A", 90, 0)                            # 只转 A 的第 1 页
    check("旋转源页后，成品里对应页同步为 90°", sess.out[0].rot == 90, sess.out[0].rot)
    check("成品其他页不受影响",
          all(it.rot == 0 for it in sess.out[1:]), [it.rot for it in sess.out])
    rot_path = os.path.join(OUT_DIR, "_t_rot.pdf")
    sess.export_pdf(rot_path)
    doc = pymupdf.open(rot_path)
    p1 = doc[0]
    check("导出后第 1 页 /Rotate = 90", engine.norm_rot(p1.rotation) == 90, p1.rotation)
    check("导出后第 1 页变成横向（A4 旋转后宽 > 高）", p1.rect.width > p1.rect.height,
          (round(p1.rect.width), round(p1.rect.height)))
    doc.close()

    sess.rotate("A", -90, 0)
    check("反向旋转可还原为 0°", sess.out[0].rot == 0, sess.out[0].rot)

    sess.rotate("B", 90)                               # 整叠旋转
    check("整叠旋转 90° 对 3 页都生效",
          sess.docs["B"].rots == [90, 90, 90], sess.docs["B"].rots)

    print("\n=== 4. 引擎：缩略图 ===")
    png = engine.render_page_png(PDF_A, 0, 0, 200)
    img = Image.open(io.BytesIO(png))
    check("竖版页面缩略图 高 > 宽", img.height > img.width, img.size)
    png_r = engine.render_page_png(PDF_A, 0, 90, 200)
    img_r = Image.open(io.BytesIO(png_r))
    check("旋转 90° 后缩略图 宽 > 高", img_r.width > img_r.height, img_r.size)
    check("缩略图是合法 PNG", png[:8] == b"\x89PNG\r\n\x1a\n")

    print("\n=== 5. 引擎：成品编辑（拖动插入 / 重排 / 删除）===")
    sess = new_session()
    sess.build(mode="interleave")
    sess.insert_out("B", 0, 1)                          # 把 B1 插到第 2 位
    check("插入 B1 到位置 1 → 共 7 页且位置正确",
          labels(sess) == ["A1", "B1", "B1", "A2", "B2", "A3", "B3"], labels(sess))
    check("插入后模式自动切到 manual", sess.mode == "manual", sess.mode)

    sess.move_out(0, 3)                                 # A1 往后挪
    check("移动第 1 页到插入位 3 → A1 落到第 3 位",
          labels(sess) == ["B1", "B1", "A1", "A2", "B2", "A3", "B3"], labels(sess))

    sess.move_out(6, 0)                                 # 末尾挪到最前
    check("末尾页移到最前", labels(sess)[0] == "B3", labels(sess))

    sess.remove_out([0, 1])
    check("删除两页后剩 5 页", len(sess.out) == 5, len(sess.out))

    sess.rotate_out([0], 180)
    check("成品页单独旋转 180°", sess.out[0].rot == 180, sess.out[0].rot)

    sess.clear_out()
    check("清空成品", len(sess.out) == 0)
    try:
        sess.export_pdf(os.path.join(OUT_DIR, "_t_empty.pdf"))
        check("空成品导出应报错", False, "没有抛异常")
    except ValueError as exc:
        check("空成品导出会给出友好提示", "空" in str(exc), str(exc))

    print("\n=== 6. 引擎：按页码自动排序 ===")
    sess = new_session()
    ok, note = sess.autosort()
    check("能读出页脚页码并排序", ok, note)
    check("自动排序结果 = A1 B1 A2 B2 A3 B3",
          labels(sess) == ["A1", "B1", "A2", "B2", "A3", "B3"], labels(sess))

    print("\n=== 7. 引擎：交换 A / B ===")
    sess = new_session()
    sess.build(mode="interleave")
    sess.swap_slots()
    check("交换后 A 叠变成原来 B 的文件", sess.docs["A"].path == PDF_B)
    check("成品来源标记同步交换",
          [it.label for it in sess.out] == ["B1", "A1", "B2", "A2", "B3", "A3"], labels(sess))
    sess = new_session()
    sess.build(mode="interleave")
    sess.swap_slots()
    sess.swap_slots()
    check("交换两次可还原",
          [it.label for it in sess.out] == ["A1", "B1", "A2", "B2", "A3", "B3"], labels(sess))

    print("\n=== 8. 引擎：多文件合并成一叠 ===")
    merged = os.path.join(OUT_DIR, "_t_merged.pdf")
    engine.merge_files([PDF_A, PDF_A], merged)
    check("两个 3 页 PDF 合并成 6 页", pymupdf.open(merged).page_count == 6)

    print("\n=== 9. 引擎：源叠倒序 ===")
    sess = new_session()
    sess.reverse("A")
    check("倒序后 A 叠「位置 → 文件页码」翻转为 [2,1,0]",
          sess.docs["A"].order == [2, 1, 0], sess.docs["A"].order)
    check("倒序后标记为已倒序", sess.docs["A"].reversed is True)

    sess.build(mode="concat", first="A")
    rev_path = os.path.join(OUT_DIR, "_t_revslot.pdf")
    sess.export_pdf(rev_path)
    check("倒序 A 叠后顺序拼接 → 页脚 5,3,1,2,4,6",
          footer_numbers(rev_path) == ["5", "3", "1", "2", "4", "6"], footer_numbers(rev_path))

    sess.reverse("A")
    check("连点两次倒序可还原（order 回到恒等）", sess.docs["A"].order == [0, 1, 2], sess.docs["A"].order)
    check("还原后不再标记为已倒序", sess.docs["A"].reversed is False)
    sess.export_pdf(os.path.join(OUT_DIR, "_t_revundo.pdf"))
    check("还原叠序同样不动成品（导出仍是 5,3,1,2,4,6）",
          footer_numbers(os.path.join(OUT_DIR, "_t_revundo.pdf")) == ["5", "3", "1", "2", "4", "6"],
          footer_numbers(os.path.join(OUT_DIR, "_t_revundo.pdf")))
    sess.build(mode="concat", first="A")
    sess.export_pdf(os.path.join(OUT_DIR, "_t_revback.pdf"))
    check("还原后重新生成 → 页序回到 1,3,5,2,4,6",
          footer_numbers(os.path.join(OUT_DIR, "_t_revback.pdf")) == ["1", "3", "5", "2", "4", "6"],
          footer_numbers(os.path.join(OUT_DIR, "_t_revback.pdf")))

    # 「后一叠倒序」勾选框与 B 叠倒序按钮应当等价
    sess = new_session()
    sess.reverse("B")
    sess.build(mode="interleave", first="A", reverse_second=False)
    sess.export_pdf(os.path.join(OUT_DIR, "_t_revb.pdf"))
    check("倒序 B 叠 + 交错 → 页脚 1,6,3,4,5,2",
          footer_numbers(os.path.join(OUT_DIR, "_t_revb.pdf")) == ["1", "6", "3", "4", "5", "2"],
          footer_numbers(os.path.join(OUT_DIR, "_t_revb.pdf")))

    print("\n=== 9b. 引擎：倒序源叠不影响已生成的成品 ===")
    sess = new_session()
    sess.build(mode="interleave")
    sess.reverse("A")
    check("倒序源叠后成品页数不变", len(sess.out) == 6, len(sess.out))
    keep_path = os.path.join(OUT_DIR, "_t_revkeep.pdf")
    sess.export_pdf(keep_path)
    check("倒序源叠后导出内容仍是 1..6（成品指向的物理页没变）",
          footer_numbers(keep_path) == list("123456"), footer_numbers(keep_path))
    sess.build(mode="interleave")
    sess.export_pdf(os.path.join(OUT_DIR, "_t_revrebuild.pdf"))
    check("重新「生成成品」才按新叠序走（交错 → 5,2,3,4,1,6）",
          footer_numbers(os.path.join(OUT_DIR, "_t_revrebuild.pdf")) == ["5", "2", "3", "4", "1", "6"],
          footer_numbers(os.path.join(OUT_DIR, "_t_revrebuild.pdf")))

    print("\n=== 9c. 引擎：倒序与旋转的一致性 ===")
    sess = new_session()
    sess.build(mode="interleave")
    sess.rotate("A", 90, 0)
    check("旋转 A 叠第 1 页后成品第 1 页跟着转 90°", sess.out[0].rot == 90, sess.out[0].rot)
    sess.reverse("A")
    check("倒序后 rots 跟着反转", sess.docs["A"].rots == [0, 0, 90], sess.docs["A"].rots)
    check("倒序后成品该页来源位置镜像为第 3 页", sess.out[0].index == 2, sess.out[0].index)
    check("倒序后成品的旋转仍跟着同一个物理页（保持 90°）", sess.out[0].rot == 90, sess.out[0].rot)
    sess.rotate("A", 90, 2)
    check("倒序后转同一物理页，成品仍能同步（90 → 180）", sess.out[0].rot == 180, sess.out[0].rot)

    print("\n=== 9d. 引擎：倒序后按页码自动排序 ===")
    sess = new_session()
    sess.reverse("A")
    sess.reverse("B")
    ok, note = sess.autosort()
    sort_path = os.path.join(OUT_DIR, "_t_revsort.pdf")
    sess.export_pdf(sort_path)
    check("两叠都倒序后「按页码排序」仍能还原成 1..6",
          ok and footer_numbers(sort_path) == list("123456"), footer_numbers(sort_path))

    print("\n=== 9e. 引擎：空白页识别与删除 ===")
    blank_sess = engine.Session()
    blank_sess.load("A", BLANK_A)
    blank_sess.load("B", BLANK_B)
    blank_sess.build(mode="interleave", first="A", reverse_second=False)
    check("带空白页的样本交错出 8 页（正文/空白/噪点/只有页码）",
          len(blank_sess.out) == 8, len(blank_sess.out))

    st_blank = engine.page_ink_stats(BLANK_A, 1)          # A 第 2 页：纯空白
    st_noise = engine.page_ink_stats(BLANK_A, 2)          # A 第 3 页：噪点空白
    st_text = engine.page_ink_stats(BLANK_A, 0)           # A 第 1 页：正文
    st_footer = engine.page_ink_stats(BLANK_B, 2)         # B 第 3 页：只有页码
    normal_drop = engine.BLANK_PRESETS["normal"]["drop"]
    normal_dark = engine.BLANK_PRESETS["normal"]["dark"]
    check("纯空白页 dark_ratio = 0", st_blank["dark_ratio"] == 0, st_blank["dark_ratio"])
    check("噪点页 dark_ratio 也是 0，且噪点不抬高最暗块（drop 远低于标准档阈值）",
          st_noise["dark_ratio"] == 0 and st_noise["block_drop"] < normal_drop / 4,
          st_noise["block_drop"])
    check("正文页 dark_ratio 远高于标准档阈值", st_text["dark_ratio"] > normal_dark * 10,
          st_text["dark_ratio"])
    check("正文页最暗块跌落远高于标准档阈值", st_text["block_drop"] > normal_drop * 4,
          st_text["block_drop"])
    check("只有页码的页最暗块跌落高于标准档阈值（标准档能保住它）",
          st_footer["block_drop"] > normal_drop, st_footer["block_drop"])
    check("三档阈值单调递增（严格 ≤ 标准 ≤ 宽松）",
          [engine.BLANK_PRESETS[k]["drop"] for k in ("strict", "normal", "loose")] ==
          sorted(engine.BLANK_PRESETS[k]["drop"] for k in ("strict", "normal", "loose")) and
          [engine.BLANK_PRESETS[k]["dark"] for k in ("strict", "normal", "loose")] ==
          sorted(engine.BLANK_PRESETS[k]["dark"] for k in ("strict", "normal", "loose")))

    scan = blank_sess.scan_blank("normal")
    check("标准灵敏度命中 3 张空白页（A2 / B2 / A3），位置 [2,3,4]",
          scan["count"] == 3 and scan["positions"] == [2, 3, 4], scan["positions"])
    check("扫描不改动成品", len(blank_sess.out) == 8, len(blank_sess.out))
    check("扫描统计了全部 8 页", scan["scanned"] == 8, scan["scanned"])
    check("标准灵敏度保留「只有页码」的页", 5 not in scan["positions"], scan["positions"])
    check("扫描同时给出成品每一页的指标（界面「显示全部页」要用）",
          len(scan["rows"]) == 8 and all("dark_ratio" in r for r in scan["rows"]),
          len(scan.get("rows", [])))
    check("rows 里标记为空白的位置与 positions 完全一致",
          [r["pos"] for r in scan["rows"] if r["blank"]] == scan["positions"],
          [r["pos"] for r in scan["rows"] if r["blank"]])

    strict = blank_sess.scan_blank("strict")
    check("严格灵敏度同样命中 3 张", strict["count"] == 3, strict["positions"])
    loose = blank_sess.scan_blank("loose")
    check("宽松灵敏度连「只有页码」的页也命中（4 张）",
          loose["count"] == 4 and 5 in loose["positions"], loose["positions"])
    try:
        blank_sess.scan_blank("乱写的档位")
        check("非法灵敏度应报错", False, "没有抛异常")
    except ValueError as exc:
        check("非法灵敏度给出可读提示", "灵敏度" in str(exc), str(exc))

    before = labels(blank_sess)
    scan = blank_sess.remove_blank("normal")
    check("删除返回 removed=3", scan["removed"] == 3, scan["removed"])
    check("删除后成品剩 5 页", len(blank_sess.out) == 5, len(blank_sess.out))
    check("删掉的是 A2 / B2 / A3，其余页顺序不变",
          labels(blank_sess) == ["A1", "B1", "B3", "A4", "B4"], labels(blank_sess))

    blank_out = os.path.join(OUT_DIR, "_t_blank_out.pdf")
    blank_sess.export_pdf(blank_out)
    check("导出成品 5 页", pymupdf.open(blank_out).page_count == 5)
    check("导出内容顺序正确（页脚 A-1 B-1 B-3 A-4 B-4）",
          footer_marks(blank_out) == ["A-1", "B-1", "B-3", "A-4", "B-4"], footer_marks(blank_out))

    check("删除后记录为可撤销", blank_sess.undo_info()["available"] is True,
          blank_sess.undo_info())
    restored = blank_sess.undo_remove()
    check("撤销恢复 3 页", restored == 3 and len(blank_sess.out) == 8,
          (restored, len(blank_sess.out)))
    check("撤销后页序与删除前完全一致", labels(blank_sess) == before, labels(blank_sess))
    check("撤销后不再可撤销", blank_sess.undo_info()["available"] is False)
    try:
        blank_sess.undo_remove()
        check("没有可撤销内容时应报错", False, "没有抛异常")
    except ValueError as exc:
        check("无可撤销内容给出可读提示", "撤销" in str(exc), str(exc))

    scan = blank_sess.remove_blank("normal")
    check("撤销后再删仍能命中 3 张（指标缓存算得对）", scan["removed"] == 3, scan["removed"])
    blank_sess.undo_remove()

    blank_sess.remove_blank("normal")
    blank_sess.build(mode="interleave", first="A", reverse_second=False)
    check("重新「生成成品」后旧删除不可撤销（撤销凭据已失效）",
          blank_sess.undo_info()["available"] is False)
    check("重新生成后 8 页回来", len(blank_sess.out) == 8, len(blank_sess.out))

    blank_sess.remove_blank("normal")
    scan = blank_sess.remove_blank("normal")
    check("没有空白页时删除返回 0 且不报错",
          scan["removed"] == 0 and scan["count"] == 0, (scan["removed"], scan["count"]))
    blank_sess.undo_remove()

    blank_sess.build(mode="interleave", first="A")
    blank_sess.remove_out([0])
    check("手动删除成品页也可撤销", blank_sess.undo_info() == {"available": True, "count": 1,
                                                             "label": "删除 1 页"},
          blank_sess.undo_info())
    blank_sess.undo_remove()
    check("手动删除撤销后回到 8 页", len(blank_sess.out) == 8, len(blank_sess.out))

    # 勾选式删除：用户可能只勾一部分命中页（界面默认全勾，人工确认时可取消勾选）
    blank_sess.build(mode="interleave", first="A")
    picked = [p for p in blank_sess.scan_blank("normal")["positions"] if p != 4]
    check("模拟人工取消勾选第 5 页：待删位置变成 [2,3]", picked == [2, 3], picked)
    blank_sess.remove_out(picked)
    check("只删勾选的两页 → 剩 6 页", len(blank_sess.out) == 6, len(blank_sess.out))
    check("没勾选的那页（A3）留在原位",
          labels(blank_sess) == ["A1", "B1", "A3", "B3", "A4", "B4"], labels(blank_sess))
    check("撤销凭据只记勾选的两页", blank_sess.undo_info()["count"] == 2, blank_sess.undo_info())
    blank_sess.undo_remove()
    check("撤销回到 8 页且页序原样",
          len(blank_sess.out) == 8 and labels(blank_sess) == before, labels(blank_sess))

    # 同一页被成品引用两次：两处都要命中、都要删掉
    dup = engine.Session()
    dup.load("A", BLANK_A)
    dup.load("B", BLANK_B)
    dup.build(mode="interleave", first="A")
    dup.insert_out("A", 1, 0)                             # A 第 2 页（空白）再插一份到最前
    check("重复引用的空白页两处都被识别（位置 0 与 3）",
          dup.scan_blank("normal")["positions"] == [0, 3, 4, 5],
          dup.scan_blank("normal")["positions"])
    dup.remove_blank("normal")
    check("重复的空白页两处都被删掉（9 → 5）", len(dup.out) == 5, len(dup.out))
    check("重复的那一页在成品里已经彻底没了",
          [it.label for it in dup.out] == ["A1", "B1", "B3", "A4", "B4"],
          [it.label for it in dup.out])

    empty = engine.Session()
    try:
        empty.scan_blank()
        check("空成品扫描应报错", False, "没有抛异常")
    except ValueError as exc:
        check("空成品扫描给出可读提示", "空" in str(exc), str(exc))

    print("\n=== 10. 引擎：命令行 ===")
    cli_out = os.path.join(OUT_DIR, "_t_cli.pdf")
    rc = engine._main(["--a", PDF_A, "--b", PDF_B, "--out", cli_out])
    check("命令行生成成功且页序正确",
          rc == 0 and footer_numbers(cli_out) == list("123456"), rc)

    cli_rev = os.path.join(OUT_DIR, "_t_cli_rev.pdf")
    rc = engine._main(["--a", PDF_A, "--b", PDF_B, "--out", cli_rev, "--concat", "--reverse-a"])
    check("命令行 --reverse-a 生效 → 5,3,1,2,4,6",
          rc == 0 and footer_numbers(cli_rev) == ["5", "3", "1", "2", "4", "6"], footer_numbers(cli_rev))

    cli_blank = os.path.join(OUT_DIR, "_t_cli_blank.pdf")
    rc = engine._main(["--a", BLANK_A, "--b", BLANK_B, "--out", cli_blank, "--drop-blank", "normal"])
    check("命令行 --drop-blank 直接产出 5 页（删掉 3 张空白页）",
          rc == 0 and pymupdf.open(cli_blank).page_count == 5, rc)


# ============================================================ 2. Flask API

def test_api():
    print("\n=== 10. Flask API 全链路（test_client，不起服务）===")
    import app as web

    client = web.app.test_client()

    r = client.get("/")
    check("首页返回 HTML 且含三列标题",
          r.status_code == 200 and "PDF_A" in r.text and "PDF_out" in r.text)
    check("首页 A / B 两栏各有一个「倒序」按钮", r.text.count('data-act="reverse"') == 2,
          r.text.count('data-act="reverse"'))

    for name, path in (("A", PDF_A), ("B", PDF_B)):
        with open(path, "rb") as fh:
            data = fh.read()
        r = client.post("/api/upload",
                        data={"slot": name, "file": (io.BytesIO(data), os.path.basename(path))},
                        content_type="multipart/form-data")
        js = r.get_json()
        ok = r.status_code == 200 and js["docs"][name]["loaded"] and js["docs"][name]["page_count"] == 3
        check("上传 PDF_%s 成功" % name, ok, js.get("error", ""))

    r = client.get("/api/state")
    check("状态接口可用", r.status_code == 200 and r.get_json()["ok"])

    r = client.get("/api/thumb/A/0?r=0&size=120")
    check("缩略图接口返回 PNG",
          r.status_code == 200 and r.headers["Content-Type"] == "image/png" and r.data[:4] == b"\x89PNG")

    r = client.get("/api/thumb/A/99?r=0")
    check("越界页码返回 404 + JSON 错误", r.status_code == 404 and r.get_json()["ok"] is False)

    # ---- 源叠倒序（对已完成：连点两次即还原，不影响后续用例）----
    r = client.post("/api/reverse_slot", json={"slot": "A"})
    js = r.get_json()
    check("API 倒序 PDF_A 成功",
          js["docs"]["A"]["reversed"] is True and js["docs"]["A"]["order"] == [2, 1, 0],
          js["docs"]["A"]["order"])
    r = client.get("/api/thumb/A/0?r=0&size=120")
    check("倒序后 A 的第 1 张缩略图取的是文件第 3 页（缓存没串页）",
          r.data == engine.render_page_png(PDF_A, 2, 0, 120))
    r = client.get("/api/thumb/A/2?r=0&size=120")
    check("倒序后 A 的第 3 张缩略图取的是文件第 1 页",
          r.data == engine.render_page_png(PDF_A, 0, 0, 120))
    r = client.post("/api/reverse_slot", json={"slot": "A"})
    check("API 再倒序一次可还原", r.get_json()["docs"]["A"]["reversed"] is False)
    r = client.post("/api/reverse_slot", json={"slot": "X"})
    check("倒序非法槽位返回可读错误",
          r.status_code == 400 and "槽位" in r.get_json()["error"], r.get_json().get("error", ""))

    r = client.post("/api/build", json={"mode": "interleave", "first": "A", "reverse_second": False})
    js = r.get_json()
    check("生成成品得到 6 页", js["out_count"] == 6, js["out_count"])
    check("API 生成的页序正确",
          [x["label"] for x in js["out"]] == ["A1", "B1", "A2", "B2", "A3", "B3"],
          [x["label"] for x in js["out"]])

    r = client.post("/api/rotate", json={"slot": "A", "index": 0, "delta": 90})
    check("API 旋转单页成功", r.get_json()["docs"]["A"]["rots"][0] == 90)
    client.post("/api/rotate", json={"slot": "A", "index": 0, "delta": -90})

    r = client.post("/api/out/insert", json={"slot": "B", "index": 0, "at": 1})
    js = r.get_json()
    check("API 拖页插入成功", js["out_count"] == 7 and js["out"][1]["label"] == "B1")

    r = client.post("/api/out/move", json={"from": 0, "to": 3})
    js = r.get_json()
    check("API 成品重排成功", js["out"][2]["label"] == "A1", [x["label"] for x in js["out"]])

    r = client.post("/api/out/rotate", json={"positions": [0], "delta": 180})
    check("API 成品页旋转成功", r.get_json()["out"][0]["rot"] == 180)

    r = client.post("/api/out/remove", json={"positions": [0]})
    check("API 删除成品页成功", r.get_json()["out_count"] == 6)

    r = client.post("/api/autosort")
    js = r.get_json()
    check("API 按页码排序成功", js["autosort"]["ok"] and js["out_count"] == 6, js["autosort"]["note"])

    r = client.post("/api/export", json={"name": "api_selftest"})
    js = r.get_json()
    check("API 导出成功并落盘",
          js["ok"] and os.path.isfile(js["path"]) and js["name"] == "api_selftest.pdf", js.get("error", ""))
    exported = js["path"]

    r = client.get(js["url"])
    check("下载接口返回 PDF",
          r.status_code == 200 and r.headers["Content-Type"] == "application/pdf")
    check("导出文件页数 = 成品页数", pymupdf.open(exported).page_count == 6)

    r = client.post("/api/export", json={"name": "x"})
    check("导出时会自动补 .pdf 后缀", r.get_json()["name"] == "x.pdf")

    r = client.post("/api/swap")
    swapped = r.get_json()["docs"]["A"]["name"]
    check("API 交换 A / B 成功", "偶数页" in swapped, swapped)
    client.post("/api/swap")

    r = client.post("/api/out/clear")
    check("API 清空成品", r.get_json()["out_count"] == 0)
    r = client.post("/api/export", json={})
    check("空成品导出返回可读错误", r.status_code == 400 and "空" in r.get_json()["error"],
          r.get_json().get("error", ""))

    r = client.post("/api/clear_slot", json={"slot": "A"})
    check("API 清空 PDF_A", r.get_json()["docs"]["A"]["loaded"] is False)
    r = client.get("/api/thumb/A/0")
    check("源文件清空后缩略图返回 404", r.status_code == 404)

    r = client.post("/api/clear_slot", json={"slot": "B"})
    check("API 清空 PDF_B", r.get_json()["docs"]["B"]["loaded"] is False)

    # ---- 空白页：扫描 / 删除 / 撤销（换成带空白页的样本，跑完再清空）----
    print("\n=== 11. Flask API：勾选空白页 ===")
    for name, path in (("A", BLANK_A), ("B", BLANK_B)):
        with open(path, "rb") as fh:
            data = fh.read()
        r = client.post("/api/upload",
                        data={"slot": name, "file": (io.BytesIO(data), os.path.basename(path))},
                        content_type="multipart/form-data")
        check("上传带空白页的样本到 PDF_%s" % name,
              r.get_json()["docs"][name]["page_count"] == 4, r.get_json().get("error", ""))

    r = client.post("/api/build", json={"mode": "interleave", "first": "A", "reverse_second": False})
    check("带空白页样本生成 8 页成品", r.get_json()["out_count"] == 8, r.get_json()["out_count"])

    r = client.post("/api/out/blank", json={"preset": "normal"})
    js = r.get_json()
    check("API 扫描空白页：命中 3 张且不改动成品",
          js["blank"]["count"] == 3 and js["out_count"] == 8, js.get("blank"))
    check("API 返回空白页位置 [2,3,4]", js["blank"]["positions"] == [2, 3, 4],
          js["blank"]["positions"])
    check("API 同时给出成品全部页的指标（rows，界面「显示全部页」要用）",
          len(js["blank"]["rows"]) == 8 and all("dark_ratio" in x for x in js["blank"]["rows"]),
          len(js["blank"].get("rows", [])))
    check("rows 里「判为空白」的标记与 positions 一致",
          [x["pos"] for x in js["blank"]["rows"] if x["blank"]] == [2, 3, 4],
          [x["pos"] for x in js["blank"]["rows"] if x["blank"]])
    check("API 快照里带 undo 状态（默认不可撤销）", js["undo"]["available"] is False, js["undo"])

    r = client.post("/api/out/blank", json={"preset": "loose"})
    check("API 宽松灵敏度多命中「只有页码」的页（4 张）",
          r.get_json()["blank"]["count"] == 4, r.get_json()["blank"]["count"])
    check("扫描接口是只读的：连扫两次成品仍是 8 页",
          r.get_json()["out_count"] == 8, r.get_json()["out_count"])

    r = client.post("/api/out/blank", json={"preset": "正常"})
    check("API 非法灵敏度返回可读错误",
          r.status_code == 400 and "灵敏度" in r.get_json()["error"], r.get_json().get("error", ""))

    # 勾选式删除：提交用户勾中的位置（这里模拟人工取消了第 5 页）
    r = client.post("/api/out/remove", json={"positions": []})
    check("勾选为空时删除不生效（也不留撤销凭据）",
          r.get_json()["out_count"] == 8 and r.get_json()["undo"]["available"] is False,
          r.get_json()["undo"])

    r = client.post("/api/out/remove", json={"positions": [2, 3]})
    js = r.get_json()
    check("API 只删勾选的两页 → 剩 6 页", js["out_count"] == 6, js["out_count"])
    check("API 未勾选的那页留在原位（A1 B1 A3 B3 A4 B4）",
          [x["label"] for x in js["out"]] == ["A1", "B1", "A3", "B3", "A4", "B4"],
          [x["label"] for x in js["out"]])
    check("API 删除后可撤销（只记勾选的两页）",
          js["undo"]["available"] is True and js["undo"]["count"] == 2, js["undo"])

    r = client.post("/api/out/undo")
    js = r.get_json()
    check("API 撤销删除恢复 2 页", js["restored"] == 2 and js["out_count"] == 8, js.get("restored"))
    check("撤销后页序回到删除前（A1 B1 A2 B2 A3 B3 A4 B4）",
          [x["label"] for x in js["out"]] == ["A1", "B1", "A2", "B2", "A3", "B3", "A4", "B4"],
          [x["label"] for x in js["out"]])
    check("撤销后 undo 置空", js["undo"]["available"] is False, js["undo"])

    r = client.post("/api/out/undo")
    check("无可撤销内容时返回可读错误",
          r.status_code == 400 and "撤销" in r.get_json()["error"], r.get_json().get("error", ""))

    r = client.post("/api/out/blank", json={})
    check("省略 preset 时用默认档（标准）", r.get_json()["blank"]["preset"] == "normal",
          r.get_json()["blank"]["preset"])

    r = client.post("/api/out/clear")
    check("清空成品后扫描空白页返回可读错误",
          r.get_json()["out_count"] == 0, r.get_json()["out_count"])
    r = client.post("/api/out/blank", json={})
    check("空成品扫描返回 400 + 提示",
          r.status_code == 400 and "空" in r.get_json()["error"], r.get_json().get("error", ""))


# ============================================================ 收尾

def cleanup():
    """删掉自测产生的临时文件，保留 拼接_demo.pdf 供查看。"""
    keep = {"拼接_demo.pdf"}
    for name in os.listdir(OUT_DIR):
        if name.startswith("_t_") or name.startswith("api_selftest") or (name.startswith("x.")):
            try:
                os.remove(os.path.join(OUT_DIR, name))
            except OSError:
                pass
    print("\n自测临时文件已清理（保留了 %s）" % "、".join(sorted(keep)))


if __name__ == "__main__":
    if not (os.path.isfile(PDF_A) and os.path.isfile(PDF_B)):
        raise SystemExit("缺少样本文件：需要在 %s 下放 奇数页.pdf 与 偶数页.pdf" % BASE)

    make_blank_sample(BLANK_A, BLANK_SPEC_A)
    make_blank_sample(BLANK_B, BLANK_SPEC_B)

    test_engine()
    test_api()
    cleanup()

    total = len(RESULTS)
    bad = [name for name, ok in RESULTS if not ok]
    print("\n" + "=" * 60)
    print("自测结果：%d / %d 项通过" % (total - len(bad), total))
    if bad:
        print("未通过：")
        for name in bad:
            print("  - " + name)
    print("=" * 60)
    raise SystemExit(1 if bad else 0)
