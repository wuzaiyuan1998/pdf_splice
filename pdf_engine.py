# -*- coding: utf-8 -*-
"""
pdf_engine.py —— PDF 单面扫描交错拼接核心引擎

场景：
    打印机只能单面扫描。一次扫描得到「第一叠」（正面），翻面后得到「第二叠」（背面）。
    本引擎把两叠按页交错成一份完整 PDF。

能力：
    * 载入 A / B 两叠 PDF（一个槽位也支持上传多个文件，按顺序合并成一叠）
    * 每页独立旋转（90° 步进），整叠旋转
    * 源叠整体倒序
    * 成品页序：自动交错 / 顺序拼接 / 按页脚页码自动排序
    * 拖动插入、拖动重排、删除、成品页单独旋转
    * 识别成品里的空白页（给出每页墨迹指标，供人工勾选后手动删除，可撤销）
    * 缩略图渲染（带 LRU 缓存）
    * 导出成品 PDF

本文件不依赖 Web 层，可单独当命令行工具用：
    python pdf_engine.py --a 奇数页.pdf --b 偶数页.pdf --out 成品.pdf
    python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --reverse-second
    python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --concat
    python pdf_engine.py --a A.pdf --b B.pdf --out 成品.pdf --drop-blank normal
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import Counter, OrderedDict
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

# ---- 空白页判定 --------------------------------------------------------------
#
# 扫描件的空白页不是纯白：纸面有灰度、有 JPEG 噪点，边缘还常有黑边和装订阴影。
# 所以判定用两个指标叠加，任一超标都认为「有内容」：
#
#   1. dark_ratio —— 比纸张底色暗 70 级以上的像素占比（真正的墨迹）。
#      纸张底色取 97 分位，这样曝光偏灰的扫描件也能自适应。
#   2. block_drop —— 把页面切成 24×24 块后，最暗那一块的均值比底色低多少。
#      这一条用来区分「有字」和「均匀噪点」：整页只有一枚小页码时 dark_ratio 很低
#      （0.013%），但墨迹集中在一小块里，block_drop 会明显抬头（实测 6.6~13.7），
#      而撒满整页的淡淡噪点分摊到每块几乎不影响均值（实测 0.4）。
#
# 实测（A4 页渲染到长边 700px）：
#   正文页        dark 0.32%~3.35% / drop 25.6~42.1
#   只有一枚页码   dark 0.013%      / drop 6.6
#   空白·噪点·偏灰 dark 0           / drop 0~0.4
# 所以「噪点」与「有内容」在 drop 上有 16 倍以上的间距，阈值取在中间地带最稳。

BLANK_ANALYZE_SIDE = 700       # 分析时把长边渲染成多少像素（越大越准、越慢）
BLANK_GRID = 24                # 分块数（block_drop 用）
BLANK_EDGE_CROP = 0.02         # 四周裁掉的比例（避开扫描黑边 / 装订阴影）
BLANK_BG_QUANTILE = 0.97       # 纸张底色取这个分位，避免被边缘阴影拉低
BLANK_DARK_DELTA = 70          # 「深色」的定义：比底色暗这么多级

BLANK_PRESETS: Dict[str, dict] = {
    "strict": {"label": "严格", "dark": 0.0001, "drop": 1.5,
               "note": "只删几乎全白的页；扫描噪点多一点就可能漏掉"},
    "normal": {"label": "标准", "dark": 0.0003, "drop": 5.0,
               "note": "删空白页和扫描噪点页；有墨迹、只有页码的页会保留"},
    "loose": {"label": "宽松", "dark": 0.0010, "drop": 22.0,
               "note": "内容极少的页也删，可能误删只有页码的页"},
}
BLANK_DEFAULT_PRESET = "normal"


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


# ---------------------------------------------------------------- 空白页判定


def _dark_table(cut: int) -> bytes:
    """造一张 256 字节的翻译表：小于 cut 的映射成 1，其余为 0。

    配合 bytes.translate().count(1) 用 C 速度数深色像素，比逐字节判断快一个量级。
    """
    return bytes(1 if v < cut else 0 for v in range(256))


def page_ink_stats(path: str, file_index: int, side: int = BLANK_ANALYZE_SIDE) -> dict:
    """
    分析 PDF 里某一页（**文件页码**）的墨迹情况，返回判定空白页所需的指标。

    只依赖 PyMuPDF 和标准库，不引入 numpy 之类的新依赖。
    """
    doc = open_pdf(path)
    try:
        if not 0 <= file_index < doc.page_count:
            raise IndexError("页码越界：%s > %s" % (file_index, doc.page_count))
        page = doc[file_index]
        rect = page.rect
        long_side = max(rect.width, rect.height) or 1.0
        zoom = min(side / long_side, 4.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                              colorspace=pymupdf.csGRAY, alpha=False)
    finally:
        doc.close()

    w, h, raw = pix.width, pix.height, pix.samples
    mx, my = int(w * BLANK_EDGE_CROP), int(h * BLANK_EDGE_CROP)
    bw, bh = w - 2 * mx, h - 2 * my
    if bw < 16 or bh < 16:                       # 页面太小就不裁边了
        mx = my = 0
        bw, bh = w, h
    body = b"".join(raw[(my + i) * w + mx: (my + i) * w + mx + bw] for i in range(bh))
    total = bw * bh

    # 纸张底色：抽样取高分位（抽样是为了快，分位数不需要逐像素精确）
    step = max(1, total // 150000)
    sample = body[::step] if step > 1 else body
    freq = Counter(sample)
    target = len(sample) * BLANK_BG_QUANTILE
    cum = 0
    bg = 255
    for value in range(256):
        cum += freq.get(value, 0)
        if cum >= target:
            bg = value
            break

    cut = max(0, bg - BLANK_DARK_DELTA)
    dark = body.translate(_dark_table(cut)).count(1)

    # 最暗块：分块求均值，取最暗的一块
    bx = max(1, bw // BLANK_GRID)
    by = max(1, bh // BLANK_GRID)
    cols = max(1, bw // bx)
    rows = max(1, bh // by)
    darkest = 255.0
    for r in range(rows):
        base = r * by * bw
        for c in range(cols):
            start = c * bx
            chunk = b"".join(body[base + k * bw + start: base + k * bw + start + bx]
                             for k in range(by))
            mean = sum(chunk) / len(chunk)
            if mean < darkest:
                darkest = mean

    return {
        "width": bw,
        "height": bh,
        "total": total,
        "bg": bg,                                  # 纸张底色
        "cut": cut,                                # 深色阈值
        "dark": dark,                              # 深色像素数
        "dark_ratio": dark / total,                # 深色像素占比
        "darkest_block": round(darkest, 1),        # 最暗块的均值
        "block_drop": round(bg - darkest, 1),      # 最暗块比底色低多少
    }


def blank_preset(name: Optional[str] = None) -> dict:
    """取空白页判定的灵敏度配置，非法值抛 ValueError。"""
    key = str(name or BLANK_DEFAULT_PRESET).strip().lower()
    cfg = BLANK_PRESETS.get(key)
    if cfg is None:
        raise ValueError("灵敏度只能是 %s（收到 %r）" % (" / ".join(BLANK_PRESETS), name))
    return dict(cfg, key=key)


def is_blank(stats: dict, cfg: dict) -> bool:
    """两个指标都低于阈值才算空白页。"""
    return stats["dark_ratio"] <= cfg["dark"] and stats["block_drop"] <= cfg["drop"]


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
    """
    一叠源 PDF。

    `order` 记录「叠里的第 i 页」对应 PDF 文件里的第几页；`rots` 也按**叠内位置**索引。
    两者初始都是恒等映射，点「倒序」后同时反转。

    这样成品里的 `index` 始终表示「叠内第几页」而不是文件页码，倒序叠序时只要把成品的
    index 一起镜像，成品指向的物理页就不会变 —— 否则倒序会静默改掉成品的内容。
    """

    slot: str
    name: str = ""
    path: str = ""
    size: int = 0
    page_count: int = 0
    rots: List[int] = field(default_factory=list)
    order: List[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.order and self.page_count:
            self.order = list(range(self.page_count))

    @property
    def loaded(self) -> bool:
        return bool(self.path) and self.page_count > 0

    @property
    def reversed(self) -> bool:
        """这一叠当前是否处于倒序状态。"""
        return self.page_count > 0 and self.order != list(range(self.page_count))

    def file_index(self, pos: int) -> int:
        """叠内第 pos 页 → PDF 文件里的页码。"""
        return self.order[pos]

    def pos_of_file(self) -> List[int]:
        """file_index 的反向映射：PDF 文件页码 → 叠内位置。"""
        back = [0] * self.page_count
        for pos, file_index in enumerate(self.order):
            back[file_index] = pos
        return back

    def as_dict(self) -> dict:
        pages = [{"index": i, "label": "%s%d" % (self.slot, i + 1)} for i in range(self.page_count)]
        return {
            "slot": self.slot,
            "name": self.name,
            "loaded": self.loaded,
            "page_count": self.page_count,
            "size": self.size,
            "rots": list(self.rots),
            "order": list(self.order),
            "reversed": self.reversed,
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
        self._ink_cache: "OrderedDict[tuple, dict]" = OrderedDict()   # 空白页指标缓存
        self._undo: Optional[dict] = None                             # 最近一次删除的撤销凭据

    # -------------------------------------------------- 内部

    def _touch(self) -> None:
        self.rev += 1

    def _drop_undo(self) -> None:
        """成品结构发生变化后，之前那次删除就不能原样恢复了。"""
        self._undo = None

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

    def _ink_stats(self, path: str, file_index: int) -> dict:
        """带缓存的墨迹分析：改灵敏度重扫时不必重新渲染。"""
        key = (path, file_index, BLANK_ANALYZE_SIDE)
        hit = self._ink_cache.get(key)
        if hit is not None:
            self._ink_cache.move_to_end(key)
            return hit
        stats = page_ink_stats(path, file_index)
        self._ink_cache[key] = stats
        self._ink_cache.move_to_end(key)
        while len(self._ink_cache) > THUMB_CACHE_MAX:
            self._ink_cache.popitem(last=False)
        return stats

    def _capture_undo(self, positions: Sequence[int], label: str) -> List[Tuple[int, Item]]:
        """记下这次要删的页（位置 + 页副本），供 undo_remove 原样插回。"""
        entries: List[Tuple[int, Item]] = []
        for p in sorted({int(p) for p in positions}):
            if 0 <= p < len(self.out):
                it = self.out[p]
                entries.append((p, Item(it.slot, it.index, it.rot)))
        if entries:
            self._undo = {"label": label, "entries": entries}
        return entries

    def _pop_positions(self, positions: Sequence[int]) -> int:
        """从成品里删掉这些位置（调用方负责先 _capture_undo）。"""
        removed = 0
        for p in sorted({int(p) for p in positions}, reverse=True):
            if 0 <= p < len(self.out):
                self.out.pop(p)
                removed += 1
        if removed:
            self.mode = "manual"
            self._touch()
        return removed

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
                "undo": self.undo_info(),
            }

    def thumb(self, slot: str, index: int, rot: Optional[int] = None, size: int = DEFAULT_THUMB_SIDE) -> bytes:
        with self.lock:
            doc = self._doc(slot)
            index = self._check_index(doc, index)
            rot = norm_rot(doc.rots[index] if rot is None else rot)
            size = max(MIN_THUMB_SIDE, min(int(size), MAX_THUMB_SIDE))
            # index 是叠内位置，先翻译成文件页码；缓存键用文件页码，
            # 这样倒序后同一位置自动渲染成正确的那张图，不会命中旧缓存
            file_index = doc.file_index(index)
            key = (doc.path, file_index, rot, size)
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            data = render_page_png(doc.path, file_index, rot, size)
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
            self._ink_cache.clear()
            self._drop_undo()
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
            self._ink_cache.clear()
            self._drop_undo()
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

    def reverse(self, slot: str) -> None:
        """
        把这一叠的页序整个倒过来（再点一次即还原）。

        成品**保持原样**：成品页记录的是「叠内第几页」，这里把成品里来自该叠的 index
        一起做镜像，让每一页仍然指向原来那个物理页。只在叠序上做文章，不动成品内容 ——
        想按新顺序重排成品，重新 build() 即可。
        """
        with self.lock:
            doc = self._doc(slot)
            n = doc.page_count
            doc.order.reverse()
            doc.rots.reverse()
            for it in self.out:
                if it.slot == doc.slot:
                    it.index = n - 1 - it.index
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
            self._drop_undo()
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
                # detect_page_numbers 按「文件页码」返回，这里换算成叠内位置
                nums = detect_page_numbers(doc.path, doc.page_count)
                back = doc.pos_of_file()
                for file_index, num in enumerate(nums):
                    pos = back[file_index]
                    pool.append((slot, pos, num, doc.rots[pos]))

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
            self._drop_undo()
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
            self._drop_undo()
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
            self._drop_undo()
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
            self._drop_undo()
            self._touch()

    def remove_out(self, positions: Sequence[int]) -> int:
        """删除成品里的若干页（可撤销一次）。界面勾选空白页后的删除也走这里。"""
        with self.lock:
            entries = self._capture_undo(positions, "删除")
            if not entries:
                return 0
            self._undo["label"] = "删除 %d 页" % len(entries)
            return self._pop_positions([p for p, _ in entries])

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
            self._drop_undo()
            self._touch()

    # -------------------------------------------------- 空白页

    def scan_blank(self, preset: Optional[str] = None) -> dict:
        """
        扫描成品每一页的墨迹指标、标出哪些是空白页，**不修改任何状态**
        （界面据此让用户勾选，再由用户手动删）。

        返回里两组数据都给：
            pages —— 只有命中的页（界面默认列表、命令行提示用）
            rows  —— 成品全部页，每项都带墨迹指标（界面「显示全部页」时用）
        同一页被成品引用多次时只分析一次（靠 _ink_stats 缓存）。
        """
        cfg = blank_preset(preset)
        with self.lock:
            if not self.out:
                raise ValueError("成品还是空的，先点「生成成品」或从 A/B 拖页进来")
            seen: Dict[tuple, dict] = {}
            rows: List[dict] = []
            for pos, it in enumerate(self.out):
                doc = self.docs.get(it.slot)
                if doc is None or not doc.loaded:
                    continue
                file_index = doc.file_index(it.index)          # 叠内位置 → 文件页码
                key = (doc.path, file_index)
                stats = seen.get(key)
                if stats is None:
                    stats = self._ink_stats(doc.path, file_index)
                    seen[key] = stats
                rows.append({
                    "pos": pos,
                    "slot": it.slot,
                    "index": it.index,
                    "label": "%s%d" % (it.slot, it.index + 1),
                    "blank": is_blank(stats, cfg),
                    "dark_ratio": round(stats["dark_ratio"], 6),
                    "block_drop": stats["block_drop"],
                    "bg": stats["bg"],
                })
            blanks = [r for r in rows if r["blank"]]
            return {
                "preset": cfg["key"],
                "label": cfg["label"],
                "note": cfg["note"],
                "scanned": len(rows),
                "count": len(blanks),
                "removed": 0,
                "positions": [r["pos"] for r in blanks],
                "pages": blanks,
                "rows": rows,
            }

    def remove_blank(self, preset: Optional[str] = None) -> dict:
        """
        扫描并**直接删掉全部命中的**空白页，返回扫描详情（含 removed 数量）。可撤销一次。

        这条路径只给命令行 --drop-blank 用（一次跑完、不留交互）。
        界面是「先勾选、再由用户点删除」，走 scan_blank + remove_out。
        """
        with self.lock:
            scan = self.scan_blank(preset)
            positions = scan["positions"]
            if positions:
                entries = self._capture_undo(positions, "删除空白页")
                self._undo["label"] = "删除空白页 %d 张" % len(entries)
                scan["removed"] = self._pop_positions(positions)
            return scan

    def undo_remove(self) -> int:
        """撤销最近一次删除，把页面按原位置插回去。"""
        with self.lock:
            undo = self._undo
            if not undo:
                raise ValueError("没有可撤销的删除（成品结构改动后就无法撤销了）")
            for pos, item in undo["entries"]:
                at = max(0, min(int(pos), len(self.out)))
                self.out.insert(at, Item(item.slot, item.index, item.rot))
            restored = len(undo["entries"])
            self._undo = None
            self.mode = "manual"
            self._touch()
            return restored

    def undo_info(self) -> dict:
        with self.lock:
            undo = self._undo
            return {
                "available": bool(undo),
                "count": len(undo["entries"]) if undo else 0,
                "label": undo["label"] if undo else "",
            }

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
            self._drop_undo()
            self._touch()

    # -------------------------------------------------- 导出

    def export_pdf(self, out_path: str) -> str:
        """按当前成品页序写出 PDF。"""
        with self.lock:
            if not self.out:
                raise ValueError("成品是空的，先点「生成」或从 A/B 拖页进来")
            items = list(self.out)
            paths = {slot: self.docs[slot].path for slot in ("A", "B")}
            orders = {slot: list(self.docs[slot].order) for slot in ("A", "B")}

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
                file_index = orders[it.slot][it.index]      # 叠内位置 → 文件页码
                out.insert_pdf(doc, from_page=file_index, to_page=file_index, links=True)
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
    parser.add_argument("--reverse-a", action="store_true", help="A 叠页序倒过来")
    parser.add_argument("--reverse-b", action="store_true", help="B 叠页序倒过来")
    parser.add_argument("--first", default="A", choices=["A", "B"], help="交错时哪一叠在前")
    parser.add_argument("--concat", action="store_true", help="顺序拼接而不是交错")
    parser.add_argument("--rotate-a", type=int, default=0, help="A 叠统一旋转角度")
    parser.add_argument("--rotate-b", type=int, default=0, help="B 叠统一旋转角度")
    parser.add_argument("--autosort", action="store_true", help="按页脚页码自动排序")
    parser.add_argument("--drop-blank", nargs="?", const=BLANK_DEFAULT_PRESET,
                        choices=sorted(BLANK_PRESETS),
                        help="删掉成品里的空白页，可指定灵敏度：" + " / ".join(sorted(BLANK_PRESETS)))
    args = parser.parse_args(argv)

    sess = Session()
    sess.load("A", os.path.abspath(args.a))
    if args.b:
        sess.load("B", os.path.abspath(args.b))
    if args.rotate_a:
        sess.rotate("A", args.rotate_a)
    if args.rotate_b:
        sess.rotate("B", args.rotate_b)
    if args.reverse_a:
        sess.reverse("A")
    if args.reverse_b:
        sess.reverse("B")

    if args.autosort:
        ok, note = sess.autosort()
        print(note)
        if not ok:
            return 2
    else:
        sess.build(mode="concat" if args.concat else "interleave",
                   first=args.first, reverse_second=args.reverse_second)

    if args.drop_blank:
        scan = sess.remove_blank(args.drop_blank)
        print("空白页（%s）：扫 %d 页，删 %d 页，剩 %d 页"
              % (scan["label"], scan["scanned"], scan["removed"], len(sess.out)))

    save_to = os.path.abspath(args.out)
    sess.export_pdf(save_to)
    print("已生成：%s（%d 页，%.2f MB）" % (save_to, len(sess.out), os.path.getsize(save_to) / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
