# -*- coding: utf-8 -*-
"""
app.py —— PDF 拼接器 Web 服务

启动：
    python app.py                # 默认端口 8770（被占用则自动往后找）
    python app.py --port 9000 --no-browser

浏览器打开后是三列界面：PDF_A | PDF_B | PDF_out
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import pdf_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 2048 * 1024 * 1024        # 单个请求最大 2GB
app.config["JSON_AS_ASCII"] = False

session = engine.Session()                                    # 单用户本地工具：全局一份状态


# ---------------------------------------------------------------- 工具


def _purge_slot_files(slot: str) -> None:
    """清掉某槽位之前上传的临时文件（避免 uploads 无限膨胀）。"""
    prefix = "%s_" % slot
    for name in os.listdir(UPLOAD_DIR):
        if name.startswith(prefix):
            try:
                os.remove(os.path.join(UPLOAD_DIR, name))
            except OSError:
                pass


def _json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


@app.errorhandler(Exception)
def _on_error(exc):                                            # noqa: ANN001
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        if request.path.startswith("/api/"):
            return _json_error(exc.description or exc.name, exc.code or 500)
        return exc
    app.logger.exception("接口出错")
    return _json_error("%s: %s" % (type(exc).__name__, exc), 500)


@app.after_request
def _no_cache_html(resp):                                      # noqa: ANN001
    if request.path in ("/", "/index.html"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _free_port(start: int = 8770, tries: int = 40) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("从 %d 起连续 %d 个端口都被占用了，请用 --port 指定" % (start, tries))


# ---------------------------------------------------------------- 页面


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------- API


@app.get("/api/state")
def api_state():
    snap = session.snapshot()
    snap["output_dir"] = OUTPUT_DIR
    snap["upload_dir"] = UPLOAD_DIR
    return jsonify(snap)


@app.post("/api/upload")
def api_upload():
    """载入一叠 PDF。slot=A/B；一次可以传多个文件，按顺序合并成一叠。"""
    slot = (request.form.get("slot") or "A").upper()
    if slot not in ("A", "B"):
        return _json_error("槽位必须是 A 或 B")

    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        return _json_error("没有收到文件")

    _purge_slot_files(slot)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    try:
        if len(files) == 1:
            name = engine.safe_filename(files[0].filename, "input.pdf")
            dst = os.path.join(UPLOAD_DIR, "%s_%s_%s" % (slot, stamp, name))
            files[0].save(dst)
            display = files[0].filename
        else:
            parts = []
            for k, item in enumerate(files):
                part = os.path.join(UPLOAD_DIR, "_part_%s_%s_%02d.pdf" % (slot, stamp, k))
                item.save(part)
                parts.append(part)
            dst = os.path.join(UPLOAD_DIR, "%s_%s_merged.pdf" % (slot, stamp))
            engine.merge_files(parts, dst)
            for part in parts:
                try:
                    os.remove(part)
                except OSError:
                    pass
            display = " + ".join(item.filename for item in files)
    except Exception as exc:
        return _json_error("读取 PDF 失败：%s" % exc)

    try:
        session.load(slot, dst, display)
    except Exception as exc:
        return _json_error("解析 PDF 失败：%s" % exc)

    return jsonify(session.snapshot())


@app.post("/api/clear_slot")
def api_clear_slot():
    data = request.get_json(silent=True) or {}
    slot = str(data.get("slot", "")).upper()
    _purge_slot_files(slot)
    session.clear_slot(slot)
    return jsonify(session.snapshot())


@app.post("/api/rotate")
def api_rotate():
    """{slot:'A', delta:90, index:3}   不传 index 表示整叠旋转。"""
    data = request.get_json(silent=True) or {}
    try:
        session.rotate(data.get("slot"), int(data.get("delta", 0)), data.get("index"))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/reverse_slot")
def api_reverse_slot():
    """{slot:'A'|'B'}  把这一叠的页序整个倒过来（再调一次即还原）。成品内容不变。"""
    data = request.get_json(silent=True) or {}
    try:
        session.reverse(data.get("slot"))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/build")
def api_build():
    """{mode:'interleave'|'concat', first:'A'|'B', reverse_second:bool}"""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode not in (None, "interleave", "concat"):
        return _json_error("mode 只能是 interleave 或 concat")
    try:
        session.build(mode=mode, first=data.get("first"), reverse_second=data.get("reverse_second"))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/autosort")
def api_autosort():
    try:
        ok, note = session.autosort()
    except Exception as exc:
        return _json_error(str(exc))
    snap = session.snapshot()
    snap["autosort"] = {"ok": ok, "note": note}
    return jsonify(snap)


@app.post("/api/out/insert")
def api_out_insert():
    """{slot, index, at}  把 A/B 的某一页插到成品第 at 位；at 省略则追加。all=true 时整叠插入。"""
    data = request.get_json(silent=True) or {}
    try:
        if data.get("all"):
            session.insert_many(data.get("slot"), data.get("at"))
        else:
            session.insert_out(data.get("slot"), data.get("index"), data.get("at"))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/out/move")
def api_out_move():
    data = request.get_json(silent=True) or {}
    try:
        session.move_out(data.get("from"), data.get("to"))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/out/remove")
def api_out_remove():
    data = request.get_json(silent=True) or {}
    positions = data.get("positions")
    if positions is None and data.get("at") is not None:
        positions = [data.get("at")]
    try:
        session.remove_out(positions or [])
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/out/rotate")
def api_out_rotate():
    data = request.get_json(silent=True) or {}
    positions = data.get("positions")
    if positions is None and data.get("at") is not None:
        positions = [data.get("at")]
    try:
        session.rotate_out(positions or [], int(data.get("delta", 0)))
    except Exception as exc:
        return _json_error(str(exc))
    return jsonify(session.snapshot())


@app.post("/api/out/clear")
def api_out_clear():
    session.clear_out()
    return jsonify(session.snapshot())


@app.post("/api/out/blank")
def api_out_blank():
    """扫描成品里的空白页，**只读、不改状态**。

    {preset:'strict'|'normal'|'loose'}
    返回体就是状态快照，另加一个 blank 字段装扫描详情：
      blank.pages  —— 命中的空白页（默认列表）
      blank.rows   —— 成品全部页，每页都带墨迹指标（界面「显示全部页」用）
    删除不由这个接口负责：界面把用户勾选的位置提交给 /api/out/remove。
    """
    data = request.get_json(silent=True) or {}
    try:
        scan = session.scan_blank(data.get("preset"))
    except Exception as exc:
        return _json_error(str(exc))
    snap = session.snapshot()
    snap["blank"] = scan
    return jsonify(snap)


@app.post("/api/out/undo")
def api_out_undo():
    """撤销最近一次删除（含「删空白页」和手动删除）。"""
    try:
        restored = session.undo_remove()
    except Exception as exc:
        return _json_error(str(exc))
    snap = session.snapshot()
    snap["restored"] = restored
    return jsonify(snap)


@app.post("/api/swap")
def api_swap():
    session.swap_slots()
    return jsonify(session.snapshot())


@app.get("/api/thumb/<slot>/<int:index>")
def api_thumb(slot: str, index: int):
    """缩略图。index 是叠内位置；r=旋转角度（默认取该页当前角度），size=长边像素。

    前端还会带一个 p=物理页码，那只是让浏览器缓存能区分「同一位置、不同物理页」
    （源叠倒序后会出现这种情况），服务端不读它，一律按叠序自己算。
    """
    slot = slot.upper()
    raw_rot = request.args.get("r")
    rot = None if raw_rot in (None, "") else engine.norm_rot(raw_rot)
    try:
        size = int(request.args.get("size") or engine.DEFAULT_THUMB_SIDE)
    except ValueError:
        size = engine.DEFAULT_THUMB_SIDE
    try:
        png = session.thumb(slot, index, rot, size)
    except Exception as exc:
        return _json_error(str(exc), 404)
    resp = app.response_class(png, mimetype="image/png")
    # URL 已完整决定内容（位置 + 物理页码 + 旋转 + 尺寸），可以安全长缓存。
    # 注意：改动 thumbUrl 的参数时必须同步更新这里的前提，否则会命中过期图片。
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.post("/api/export")
def api_export():
    """写出成品 PDF，返回可下载的 URL 与磁盘路径。"""
    data = request.get_json(silent=True) or {}
    snap = session.snapshot()
    if not snap["out_count"]:
        return _json_error("成品还是空的，先点「生成成品」或从 A/B 拖页进来")

    name = engine.safe_filename(data.get("name") or ("拼接_%s" % time.strftime("%Y%m%d_%H%M%S")), "拼接")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = os.path.join(OUTPUT_DIR, name)

    try:
        session.export_pdf(path)
    except Exception as exc:
        return _json_error("导出失败：%s" % exc)

    return jsonify({
        "ok": True,
        "name": name,
        "path": path,
        "size": os.path.getsize(path),
        "pages": snap["out_count"],
        "url": "/api/download/%s" % name,
        "preview": "/api/download/%s?inline=1" % name,
    })


@app.get("/api/download/<path:name>")
def api_download(name: str):
    """下载 / 预览刚导出的成品。"""
    target = os.path.join(OUTPUT_DIR, engine.safe_filename(name))
    if not os.path.isfile(target):
        abort(404)
    inline = request.args.get("inline") == "1"
    return send_file(target, mimetype="application/pdf",
                     as_attachment=not inline, download_name=os.path.basename(target))


@app.post("/api/open_output")
def api_open_output():
    """在资源管理器里打开输出目录。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(OUTPUT_DIR)                             # noqa: S606
        elif sys.platform == "darwin":
            os.system('open "%s"' % OUTPUT_DIR)
        else:
            os.system('xdg-open "%s"' % OUTPUT_DIR)
    except Exception as exc:
        return _json_error("打开目录失败：%s" % exc)
    return jsonify({"ok": True, "path": OUTPUT_DIR})


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PDF 单面扫描交错拼接器")
    parser.add_argument("--port", type=int, default=None, help="端口，默认 8770 起自动找空闲")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--debug", action="store_true", help="Flask 调试模式")
    args = parser.parse_args(argv)

    port = args.port or _free_port()
    url = "http://127.0.0.1:%d/" % port

    print("=" * 58)
    print("  PDF 单面扫描交错拼接器")
    print("  地址：%s" % url)
    print("  上传目录：%s" % UPLOAD_DIR)
    print("  输出目录：%s" % OUTPUT_DIR)
    print("  停止服务：Ctrl + C")
    print("=" * 58)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=port, debug=args.debug, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
