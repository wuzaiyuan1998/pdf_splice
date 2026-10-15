# -*- coding: utf-8 -*-
"""
pdf_engine.py —— PDF 单面扫描交错拼接核心引擎

场景：
    打印机只能单面扫描。一次扫描得到「第一叠」（正面），翻面后得到「第二叠」（背面）。
    本引擎把两叠按页交错成一份完整 PDF。

能力：
    * 载入 A / B 两叠 PDF（一个槽位也支持上传多个文件，按顺序合并成一叠）
    * 每页独立旋转（90° 步进），整叠旋转
    * 成品页序：自动交错 / 顺序拼接 / 按页脚页码自动排序
    * 拖动插入、拖动重排、删除、成品页单独旋转
    * 缩略图渲染（带 LRU 缓存）
    * 导出成品 PDF

本文件不依赖 Web 层，可单独当命令行工具用：
    python pdf_engine.py --a 奇数页.pdf --b 偶数页.pdf --out 成品.pdf
    python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --reverse-second
    python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --concat
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf

# ---------------------------------------------------------------- 常量

THUMB_CACHE_MAX = 500          # 缩略图缓存条目上限
MIN_THUMB_SIDE = 60
MAX_THUMB_SIDE = 1600
DEFAULT_THUMB_SIDE = 240
SAVE_KW = dict(garbage=4, deflate=True)

ROT_CHOICES = (0, 90, 180, 270)

# 页脚 / 页眉里常见的「3/6」「第 3 页 / 共 6 页」形式
NUM_SLASH_RE = re.compile(r"(\d{1,4})\s*[/／]\s*(\d{1,4})")
NUM_CN_RE = re.compile(r"第\s*(\d{1,4})\s*页")


# ---------------------------------------------------------------- 小工具


def norm_rot(value) -> int:
    """把任意角度归一化到 0 / 90 / 180 / 270（向下取到 90 的整数倍）。"""
    try:
        deg = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    deg = int(round(deg / 90.0)) * 90
    return deg % 360


def safe_filename(name: str, fallback: str = "file") -> str:
    """清掉文件名里的非法字符，保留中文。"""
    name = os.path.basename(str(name or "")).strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = name.strip(". ")
    return name[:120] or fallback


def open_pdf(path: str) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if getattr(doc, "needs_pass", False):
        doc.close()
        raise ValueError("PDF 已加密，需要密码才能打开：%s" % os.path.basename(path))
    if doc.page_count == 0:
        doc.close()
        raise ValueError("PDF 里没有任何页面：%s" % os.path.basename(path))
    return doc


def probe_pdf(path: str) -> Tuple[int, List[int]]:
    """读取页数与每页原始旋转角。"""
    doc = open_pdf(path)
    try:
        count = doc.page_count
        rots = [norm_rot(doc[i].rotation) for i in range(count)]
        return count, rots
    finally:
        doc.close()


def merge_files(paths: Sequence[str], dst: str) -> str:
    """把多个 PDF 按顺序合并成一个文件（用于「一个槽位上传多个文件」）。"""
    out = pymupdf.open()
    try:
        for p in paths:
            src = open_pdf(p)
            try:
                out.insert_pdf(src)
            finally:
                src.close()
        out.save(dst, **SAVE_KW)
    finally:
        out.close()
    return dst


def render_page_png(path: str, index: int, rot: int = 0, size: int = DEFAULT_THUMB_SIDE) -> bytes:
    """把某一页渲染成 PNG 字节流。size 为长边像素目标值。"""
    doc = open_pdf(path)
    try:
        if not 0 <= index < doc.page_count:
            raise IndexError("页码越界：%s > %s" % (index, doc.page_count))
        page = doc[index]
        if rot:
            page.set_rotation(norm_rot(rot))
        rect = page.rect
        long_side = max(rect.width, rect.height) or 1.0
        size = max(MIN_THUMB_SIDE, min(int(size), MAX_THUMB_SIDE))
        zoom = min(size / long_side, 4.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def detect_page_numbers(path: str, page_count: int) -> List[Optional[int]]:
    """
    尝试从每页的页脚 / 页眉读出「n/N」形式的页码，返回每页识别到的 n（识别不到为 None）。
    页脚和页眉都会扫，所以上下颠倒扫描的页面也能认出来。
    """
    doc = open_pdf(path)
    result: List[Optional[int]] = []
    try:
        for i in range(page_count):
            page = doc[i]
            rect = page.rect
            bands = (
                pymupdf.Rect(0, rect.height * 0.88, rect.width, rect.height),   # 页脚
                pymupdf.Rect(0, 0, rect.width, rect.height * 0.12),             # 页眉
            )
            found: Optional[int] = None
            for band in bands:
                try:
                    text = page.get_text("text", clip=band)
                except Exception:
                    continue
                hits = NUM_SLASH_RE.findall(text) or []
                if hits:
                    found = int(hits[-1][0])
                    break
                hits_cn = NUM_CN_RE.findall(text) or []
                if hits_cn:
                    found = int(hits_cn[-1])
                    break
            result.append(found)
    finally:
        doc.close()
    return result


# ---------------------------------------------------------------- 数据模型


@dataclass
class Item:
    """成品里的一页：来自哪个槽位的第几页、以什么角度摆放。"""

    slot: str
    index: int
    rot: int = 0

    @property
    def label(self) -> str:
        return "%s%d" % (self.slot, self.index + 1)

    def as_dict(self) -> dict:
        return {
            "slot": self.slot,
            "index": self.index,
            "rot": norm_rot(self.rot),
            "label": self.label,
        }


@dataclass
class DocState:
    """一叠源 PDF。"""

    slot: str
    name: str = ""
    path: str = ""
    size: int = 0
    page_count: int = 0
    rots: List[int] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return bool(self.path) and self.page_count > 0

    def as_dict(self) -> dict:
        pages = [{"index": i, "label": "%s%d" % (self.slot, i + 1)} for i in range(self.page_count)]
        return {
            "slot": self.slot,
            "name": self.name,
            "loaded": self.loaded,
            "page_count": self.page_count,
            "size": self.size,
            "rots": list(self.rots),
            "pages": pages,
        }


# ---------------------------------------------------------------- 会话 / 状态机


class Session:
    """
    一次浏览器会话的完整状态：A 叠、B 叠、成品页序、拼接参数、缩略图缓存。
    所有公开方法都加锁，Flask 多线程下安全。
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.docs: Dict[str, DocState] = {"A": DocState("A"), "B": DocState("B")}
        self.out: List[Item] = []
        self.mode = "interleave"        # interleave | concat | manual
        self.first = "A"                # 交错时哪一叠先
        self.reverse_second = False     # 后一叠是否倒序取页
        self.rev = 0                    # 状态版本号，前端用来判断是否需要刷新
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()

    # -------------------------------------------------- 内部

    def _touch(self) -> None:
        self.rev += 1

    def _doc(self, slot: str) -> DocState:
        slot = str(slot or "").upper()
        doc = self.docs.get(slot)
        if doc is None:
            raise ValueError("槽位必须是 A 或 B，收到：%s" % slot)
        if not doc.loaded:
            raise ValueError("槽位 %s 还没有载入 PDF" % slot)
        return doc

    def _check_index(self, doc: DocState, index) -> int:
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise ValueError("页码无效：%r" % (index,))
        if not 0 <= index < doc.page_count:
            raise ValueError("页码越界：%s（共 %d 页）" % (index, doc.page_count))
        return index

    def _cache_put(self, key: tuple, value: bytes) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > THUMB_CACHE_MAX:
            self._cache.popitem(last=False)

    # -------------------------------------------------- 查询

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "rev": self.rev,
                "docs": {k: v.as_dict() for k, v in self.docs.items()},
                "out": [it.as_dict() for it in self.out],
                "out_count": len(self.out),
                "mode": self.mode,
                "first": self.first,
                "reverse_second": self.reverse_second,
            }

    def thumb(self, slot: str, index: int, rot: Optional[int] = None, size: int = DEFAULT_THUMB_SIDE) -> bytes:
        with self.lock:
            doc = self._doc(slot)
            index = self._check_index(doc, index)
            rot = norm_rot(doc.rots[index] if rot is None else rot)
            size = max(MIN_THUMB_SIDE, min(int(size), MAX_THUMB_SIDE))
            key = (doc.path, index, rot, size)
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            data = render_page_png(doc.path, index, rot, size)
            self._cache_put(key, data)
            return data

    # -------------------------------------------------- 源文件

    def load(self, slot: str, path: str, name: str = "") -> DocState:
        """载入 / 替换某一叠。替换后该叠在成品中的页会被移除。"""
        slot = str(slot or "").upper()
        if slot not in self.docs:
            raise ValueError("槽位必须是 A 或 B")
        count, rots = probe_pdf(path)
        with self.lock:
            doc = DocState(
                slot=slot,
                name=name or os.path.basename(path),
                path=path,
                size=os.path.getsize(path),
                page_count=count,
                rots=rots,
            )
            self.docs[slot] = doc
            self.out = [it for it in self.out if it.slot != slot]
            self._cache.clear()
            self._touch()
            return doc

    def clear_slot(self, slot: str) -> None:
        slot = str(slot or "").upper()
        with self.lock:
            if slot not in self.docs:
                raise ValueError("槽位必须是 A 或 B")
            self.docs[slot] = DocState(slot)
            self.out = [it for it in self.out if it.slot != slot]
            self._cache.clear()
            self._touch()

    # -------------------------------------------------- 旋转

    def rotate(self, slot: str, delta: int, index: Optional[int] = None) -> None:
        """
        旋转源页。index 为 None 表示整叠旋转。
        成品里由该页生成、且自身未被单独旋转过的页会跟着转，避免「转了源页成品却没动」。
        """
        delta = norm_rot(delta)
        if delta == 0:
            return
        with self.lock:
            doc = self._doc(slot)
            targets = range(doc.page_count) if index is None else [self._check_index(doc, index)]
            for i in targets:
                old = norm_rot(doc.rots[i])
                new = norm_rot(old + delta)
                doc.rots[i] = new
                for it in self.out:
                    if it.slot == doc.slot and it.index == i and norm_rot(it.rot) == old:
                        it.rot = new
            self._touch()

    # -------------------------------------------------- 成品生成

    def build(
        self,
        mode: Optional[str] = None,
        first: Optional[str] = None,
        reverse_second: Optional[bool] = None,
    ) -> List[Item]:
        """按当前设置重建整个成品页序。"""
        with self.lock:
            if mode:
                self.mode = str(mode)
            if first:
                self.first = "B" if str(first).upper() == "B" else "A"
            if reverse_second is not None:
                self.reverse_second = bool(reverse_second)

            a, b = self.docs["A"], self.docs["B"]
            items: List[Item] = []

            if self.mode == "concat":
                order = [a, b] if self.first == "A" else [b, a]
                for doc in order:
                    if not doc.loaded:
                        continue
                    items += [Item(doc.slot, i, doc.rots[i]) for i in range(doc.page_count)]
            else:
                d1 = a if self.first == "A" else b
                d2 = b if self.first == "A" else a
                idx2 = list(range(d2.page_count))
                if self.reverse_second:
                    idx2.reverse()
                total = max(d1.page_count, len(idx2))
                for k in range(total):
                    if k < d1.page_count:
                        items.append(Item(d1.slot, k, d1.rots[k]))
                    if k < len(idx2):
                        j = idx2[k]
                        items.append(Item(d2.slot, j, d2.rots[j]))
                self.mode = "interleave"

            self.out = items
            self._touch()
            return list(self.out)

    def autosort(self) -> Tuple[bool, str]:
        """
        读页脚 / 页眉的「n/N」页码，把两叠页面按真实页码重排。
        适合扫描件自带页码的情况，能自动纠正「反面那叠倒序」等问题。
        """
        with self.lock:
            pool: List[Tuple[str, int, Optional[int], int]] = []
            for slot in ("A", "B"):
                doc = self.docs[slot]
                if not doc.loaded:
                    continue
                nums = detect_page_numbers(doc.path, doc.page_count)
                for i, num in enumerate(nums):
                    pool.append((slot, i, num, doc.rots[i]))

            total_pages = len(pool)
            if total_pages < 2:
                return False, "至少要在 A 或 B 里载入一个 PDF"

            known = [p for p in pool if p[2] is not None]
            if len(known) < max(2, int(total_pages * 0.6)):
                return False, "只有 %d/%d 页能读出页码，放弃自动排序（可手动拖动）" % (len(known), total_pages)

            numbers = [p[2] for p in known]
            if len(set(numbers)) != len(numbers):
                return False, "读到的页码有重复，无法确定顺序，请手动拖动"

            items = [Item(slot, i, rot) for slot, i, num, rot in sorted(known, key=lambda p: p[2])]

            # 识别不到页码的页，按源顺序追加到后面，避免丢页
            missing = [p for p in pool if p[2] is None]
            for slot, i, _num, rot in missing:
                items.append(Item(slot, i, rot))

            self.out = items
            self.mode = "manual"
            self._touch()
            return True, "按页码排序完成：识别 %d/%d 页，结果 %d 页" % (len(known), total_pages, len(items))

    # -------------------------------------------------- 成品编辑

    def insert_out(self, slot: str, index: int, at: Optional[int] = None) -> int:
        with self.lock:
            doc = self._doc(slot)
            index = self._check_index(doc, index)
            pos = len(self.out) if at is None else max(0, min(int(at), len(self.out)))
            self.out.insert(pos, Item(doc.slot, index, doc.rots[index]))
            self.mode = "manual"
            self._touch()
            return pos

    def insert_many(self, slot: str, at: Optional[int] = None) -> int:
        """把整叠按顺序插入成品。"""
        with self.lock:
            doc = self._doc(slot)
            pos = len(self.out) if at is None else max(0, min(int(at), len(self.out)))
            for offset, i in enumerate(range(doc.page_count)):
                self.out.insert(pos + offset, Item(doc.slot, i, doc.rots[i]))
            self.mode = "manual"
            self._touch()
            return pos

    def move_out(self, frm: int, to: int) -> None:
        """把成品第 frm 页移动到「插入位置 to」（to 以移动前的列表计）。"""
        with self.lock:
            n = len(self.out)
            frm = int(frm)
            to = int(to)
            if not 0 <= frm < n:
                raise ValueError("要移动的页码越界：%s" % frm)
            to = max(0, min(to, n))
            item = self.out.pop(frm)
            if to > frm:
                to -= 1
            self.out.insert(to, item)
            self.mode = "manual"
            self._touch()

    def remove_out(self, positions: Sequence[int]) -> int:
        with self.lock:
            idx = sorted({int(p) for p in positions}, reverse=True)
            removed = 0
            for p in idx:
                if 0 <= p < len(self.out):
                    self.out.pop(p)
                    removed += 1
            if removed:
                self.mode = "manual"
                self._touch()
            return removed

    def rotate_out(self, positions: Sequence[int], delta: int) -> None:
        delta = norm_rot(delta)
        if delta == 0:
            return
        with self.lock:
            for p in positions:
                p = int(p)
                if 0 <= p < len(self.out):
                    self.out[p].rot = norm_rot(self.out[p].rot + delta)
            self._touch()

    def clear_out(self) -> None:
        with self.lock:
            self.out = []
            self._touch()

    def swap_slots(self) -> None:
        """交换 A / B 两叠（连成品里的来源标记一起换）。"""
        with self.lock:
            a, b = self.docs["A"], self.docs["B"]
            a.slot, b.slot = "B", "A"
            self.docs = {"A": b, "B": a}
            self.first = "B" if self.first == "A" else "A"
            for it in self.out:
                if it.slot == "A":
                    it.slot = "B"
                elif it.slot == "B":
                    it.slot = "A"
            self._touch()

    # -------------------------------------------------- 导出

    def export_pdf(self, out_path: str) -> str:
        """按当前成品页序写出 PDF。"""
        with self.lock:
            if not self.out:
                raise ValueError("成品是空的，先点「生成」或从 A/B 拖页进来")
            items = list(self.out)
            paths = {slot: self.docs[slot].path for slot in ("A", "B")}

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        out = pymupdf.open()
        handles: Dict[str, pymupdf.Document] = {}
        try:
            for it in items:
                src_path = paths.get(it.slot)
                if not src_path:
                    continue
                doc = handles.get(it.slot)
                if doc is None:
                    doc = open_pdf(src_path)
                    handles[it.slot] = doc
                if not 0 <= it.index < doc.page_count:
                    continue
                out.insert_pdf(doc, from_page=it.index, to_page=it.index, links=True)
                rot = norm_rot(it.rot)
                if rot:
                    out[-1].set_rotation(rot)
            if out.page_count == 0:
                raise ValueError("没有可导出的页面")
            meta = {
                "title": os.path.splitext(os.path.basename(out_path))[0],
                "producer": "pdf_splice",
                "creator": "pdf_splice",
            }
            try:                                   # 不同 PyMuPDF 版本接口略有差异
                meta["creationDate"] = pymupdf.get_pdf_now()
                meta["modDate"] = meta["creationDate"]
            except Exception:
                pass
            out.set_metadata(meta)
            out.save(out_path, **SAVE_KW)
        finally:
            for doc in handles.values():
                doc.close()
            out.close()
        return out_path


# ---------------------------------------------------------------- 命令行


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="PDF 单面扫描交错拼接（无界面版）")
    parser.add_argument("--a", required=True, help="第一叠 PDF（正面）")
    parser.add_argument("--b", help="第二叠 PDF（背面）")
    parser.add_argument("--out", required=True, help="输出 PDF 路径")
    parser.add_argument("--reverse-second", action="store_true", help="后一叠倒序取页")
    parser.add_argument("--first", default="A", choices=["A", "B"], help="交错时哪一叠在前")
    parser.add_argument("--concat", action="store_true", help="顺序拼接而不是交错")
    parser.add_argument("--rotate-a", type=int, default=0, help="A 叠统一旋转角度")
    parser.add_argument("--rotate-b", type=int, default=0, help="B 叠统一旋转角度")
    parser.add_argument("--autosort", action="store_true", help="按页脚页码自动排序")
    args = parser.parse_args(argv)

    sess = Session()
    sess.load("A", os.path.abspath(args.a))
    if args.b:
        sess.load("B", os.path.abspath(args.b))
    if args.rotate_a:
        sess.rotate("A", args.rotate_a)
    if args.rotate_b:
        sess.rotate("B", args.rotate_b)

    if args.autosort:
        ok, note = sess.autosort()
        print(note)
        if not ok:
            return 2
    else:
        sess.build(mode="concat" if args.concat else "interleave",
                   first=args.first, reverse_second=args.reverse_second)

    save_to = os.path.abspath(args.out)
    sess.export_pdf(save_to)
    print("已生成：%s（%d 页，%.2f MB）" % (save_to, len(sess.out), os.path.getsize(save_to) / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
