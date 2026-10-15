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

    print("\n=== 9. 引擎：命令行 ===")
    cli_out = os.path.join(OUT_DIR, "_t_cli.pdf")
    rc = engine._main(["--a", PDF_A, "--b", PDF_B, "--out", cli_out])
    check("命令行生成成功且页序正确",
          rc == 0 and footer_numbers(cli_out) == list("123456"), rc)


# ============================================================ 2. Flask API

def test_api():
    print("\n=== 10. Flask API 全链路（test_client，不起服务）===")
    import app as web

    client = web.app.test_client()

    r = client.get("/")
    check("首页返回 HTML 且含三列标题",
          r.status_code == 200 and "PDF_A" in r.text and "PDF_out" in r.text)

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
