#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鉴己 · 笔记洞察 · 命令行版（自建六维 AI 洞察，兼容 flomo 导出格式）

只用标准库，无需 pip install。
读取 flomo 导出的 CSV / HTML / 纯文本，按指定洞察维度调用任意
OpenAI 兼容接口（智谱 / 硅基流动 / DeepSeek / Kimi / 本地 Ollama）。

用法：
  # 看有哪些洞察
  python jianyi_insight.py --list

  # 跑单个洞察（默认最近 90 天）
  python jianyi_insight.py -f flomo.csv --mode 默认洞察

  # 六个洞察一次跑完
  python jianyi_insight.py -f flomo.html --all

  # 只打印拼好的 prompt，不花 token
  python jianyi_insight.py -f flomo.csv --mode cbt --dry-run

  # 不联网、本地模型
  python jianyi_insight.py -f flomo.csv --all \
      --api-base http://localhost:11434/v1/chat/completions --model qwen2.5:7b
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from html.parser import HTMLParser

# ---------------------------------------------------------------- 提示词

COMMON = """你在分析一个人的私人笔记。这些笔记跨度很长、很碎、没有上下文。用户发给你时，每条笔记开头已标注编号 [n]。

规则：
1. 只根据笔记文本推断，不要脑补情节。
2. 每个结论都必须用笔记编号引用证据，写在结论句末尾，格式为 [n]，可多个如 [2][5]。
3. 正文里不要整段摘抄笔记原文；最多保留不超过 10 个字的关键短语（加引号）。
4. 不要写"你是一个热爱思考的人""保持好奇心"这类正确的废话。
5. 说不清的地方就说"这条线索太弱，不下结论"。
6. 全程用第二人称"你"，语气像一个了解你的朋友，不是心理咨询师，也不是导师。
7. 输出用 Markdown，不超过 800 字。宁可少说，不要说满。
"""

MODES = {
    "insight": {
        "name": "默认洞察",
        "desc": "挖掘笔记背后隐藏的思维模式与深层内在矛盾",
        "prompt": """你是一个擅长从碎片笔记里读出"写字的人"的观察者。你读的不是内容，是习惯、停顿和反复。

【任务】
在我给你的笔记里，找出 3-5 个反复出现的思维模式。每个模式必须包含：
1. 一句判断句命名（例：你在用忙碌回避一个更难的决定）
2. 证据：用笔记编号引用，如 [3][7]，2-3 条
3. 它在保护你什么
4. 它让你付出了什么

【最后】
指出笔记里最大的一个内在矛盾：两个同时成立、但互相拉扯的诉求。用一句话说清，不要解释。

【禁止】
不要总结笔记的主题分布，不要给行动建议，不要重复讲道理。同一个意思只说一遍。
如果某个模式只有一条证据，标注"样本不足"。""",
    },
    "value": {
        "name": "价值澄清",
        "desc": "从笔记里找出你真正看重的东西，从混乱回到清晰",
        "prompt": """你不听一个人说自己重视什么，你看他在什么事情上不计成本地花时间、在什么事上会生气、在什么时刻反复回头写同一件事。

【任务】
从笔记中提炼 3-5 条"你真正在意的价值"。每条给：
1. 价值名（用一个具体的词，不要用"成长""自由"这种大词，除非笔记里就是这么写的）
2. 证据：用笔记编号引用 [n]，至少 2 条
3. 这条价值在你实际生活里的排序（第几位，凭什么排这里）
4. 冲突：它和你"嘴上说的优先级"是否矛盾

【最后】
给出一个追问：如果只能保留三件事，你会删掉哪些？把这句话留给我自己填，不要替我回答。

【禁止】
不要输出价值观清单式的鸡汤，不要把"在意"和"应该在意"混为一谈。""",
    },
    "invert": {
        "name": "逆向思考",
        "desc": "通过芒格的逆向思维来考察笔记中的关键目标",
        "prompt": """芒格的逆向思维：不要问"怎么才能成功"，要问"什么会让我彻底失败，然后避开它"。大多数人死于愚蠢，不是死于不够聪明。

【任务】
第一步：从笔记中识别 2-3 个真实目标。
注意——不是笔记里"写出来的目标"，而是行为指向的目标（时间和情绪花在哪里，目标就在哪里）。

第二步：对每个目标，列出 5 个最可能让它失败的原因。要求：从笔记里找已经出现的早期信号，不是通用风险清单。

第三步：对每个目标给出：
1. 哪个失败信号已经出现（用笔记编号引用）
2. 哪个还没出现（这是你目前的安全边际）
3. 一个"避免愚蠢"的动作——注意是避免愚蠢，不是追求聪明

【禁止】
不要给"提升效率""坚持复盘"这类建议。一个建议如果换个人也成立，就说明它没用。""",
    },
    "second": {
        "name": "二阶思考",
        "desc": "从笔记中识别出问题，并提炼出问题之上的问题",
        "prompt": """一阶思考是"我该怎么办"。二阶思考是"我为什么会问这个问题"。
大多数反复出现的问题，都不是问题本身，是上游某个东西的投影。

【任务】
从笔记中识别 3 个我最近在反复纠结的问题（一阶问题，表面问题）。每个给：
1. 一阶问题：用笔记编号引用，如 [4]
2. 二阶问题：这个问题之所以难，真正卡住我的是什么
3. 三阶推演：如果二阶问题解决了，我会面对什么新的问题（很多人到这里就不想解决它了）
4. 判断：这是"待解决的问题"，还是"我一直在供养的问题"（靠反复想它获得某种安慰）

【最后】
把所有一阶问题往上归并：它们有没有共同的源头？用一句话说。

【禁止】
不要把我纠结的每一件小事都上升成哲学问题。有的问题就是小事，直说。""",
    },
    "cbt": {
        "name": "CBT 疗法",
        "desc": "识别笔记中的思维陷阱并提供具体的改善建议",
        "prompt": """你熟悉认知行为疗法（CBT）里的认知扭曲类型：非黑即白、过度概括、心理过滤、否定正面、读心术、灾难化、情绪化推理、应该式陈述、贴标签、个人化、比较思维。

【任务】
在笔记里找出 3-5 处认知扭曲。每处按这个结构写：
1. 编号引用，如 [2]
2. 扭曲类型：中文名 + 英文名
3. 反证：笔记里有没有与这句话矛盾的事实？——优先用我自己写过的话来反驳我，这比我听别人讲道理有效
4. 一个重构句式：把原句改写成一句更准确的表述。例："我总是搞砸" → "这周三件事我搞砸了两件，周四那次我做对了"

【最后】
如果只能改一句话，改哪一句？为什么是这一句？

【免责声明】
在结尾加一行：这不是心理治疗。如果这种低落状态持续两周以上，请找专业人士。（这行必须保留，不要美化它）""",
    },
    "mbti": {
        "name": "MBTI 分析",
        "desc": "从你的笔记内容中解读真实的 MBTI 人格类型",
        "prompt": """MBTI 的价值不在四个字母，在于它描述了你获取信息和做决定时的默认路径。
不要从"我以为我是什么型"出发，从笔记里的判断方式推断。

【任务】
逐维度分析，每个维度包含：
1. 维度：E/I、S/N、T/F、J/P
2. 支持证据：用笔记编号引用，至少 2 条（写的是判断方式，不是自我描述）
3. 反证据：有没有相反的证据
4. 倾向强度：弱 / 中 / 强
5. 一句话解释这个维度在你的笔记里怎么体现

【最后】
1. 四字母结论 + 置信度（低/中/高）
2. 最可能的误判点：例如"你以为自己是 I，其实只是长期社交耗竭的 E"
3. 这个类型在压力下的"劣势功能失控"表现，在你的笔记里有没有出现

【禁止】
不要生肖星座式的性格夸赞。每个维度都必须有反面可能。如果某个维度证据不足，直接写"该维度证据不足，不给结论"。""",
    },
}

ALIASES = {}
for k, v in MODES.items():
    ALIASES[k] = k
    ALIASES[v["name"]] = k
    ALIASES[v["name"].replace(" ", "")] = k
ALIASES.update({"默认": "insight", "价值": "value", "逆向": "invert",
                "二阶": "second", "cbt疗法": "cbt", "CBT": "cbt"})

# ---------------------------------------------------------------- 解析

DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


def norm_date(s):
    """把 2026-03-14 / 2026/3/14 / 2026年3月14日 统一成 2026-03-14"""
    m = DATE_RE.search(s or "")
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return None


class MemoHTMLParser(HTMLParser):
    """宽容地解析 flomo 导出的 HTML：抓 .memo 块里的 .content 和 .time"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.memos = []
        self.stack = []
        self.cur = None
        self.capture = None
        self.buf = []
        self.time_buf = []

    @staticmethod
    def _has(cls, *names):
        """按 class token 精确匹配，避免 'memos' 被 'memo' 误命中"""
        toks = cls.split()
        for t in toks:
            base = t.split("__")[0]
            for n in names:
                if base == n or base.startswith(n + "-") or base.startswith(n + "_"):
                    return True
        return False

    def handle_starttag(self, tag, attrs):
        cls = (" ".join(v for k, v in attrs if k == "class") or "").lower()
        self.stack.append((tag, cls))
        if tag == "div" and self._has(cls, "memo", "note"):
            if self.cur is None:
                self.cur = {"content": "", "date": ""}
                self.capture = "memo"
        elif self.cur is not None:
            if tag in ("div", "p", "span") and self._has(cls, "content", "richtext") and self.capture != "content":
                self.capture = "content"
                self.buf = []
            elif tag in ("div", "span") and self._has(cls, "time", "date") and not self.cur["date"]:
                self.capture = "time"
                self.time_buf = []

    def handle_endtag(self, tag):
        if not self.stack:
            return
        t, cls = self.stack.pop()
        if self.cur is None:
            return
        if self.capture == "content" and tag in ("div", "p") and self._has(cls, "content", "richtext"):
            text = "".join(self.buf).strip()
            if text:
                self.cur["content"] = (self.cur["content"] + "\n" + text).strip()
            self.capture = "memo"
        elif self.capture == "time" and tag in ("div", "span"):
            if not self.cur["date"]:
                self.cur["date"] = "".join(self.time_buf).strip()
            self.capture = "memo"
        elif tag == "div" and self._has(cls, "memo", "note"):
            if self.cur and self.cur["content"]:
                self.memos.append(self.cur)
            self.cur = None
            self.capture = None

    def handle_data(self, data):
        if self.cur is None:
            return
        if self.capture == "content":
            self.buf.append(data)
        elif self.capture == "time":
            self.time_buf.append(data)


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "ignore")


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]

    def find(*keys):
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return -1

    ci = find("内容", "content", "memo", "笔记")
    di = find("时间", "time", "date", "日期")
    ti = find("标签", "tag")
    if ci < 0:
        ci = 0
    out = []
    for r in rows[1:]:
        if ci >= 0 and ci < len(r):
            c = r[ci].strip()
            if not c:
                continue
            d = r[di].strip() if 0 <= di < len(r) else ""
            if 0 <= ti < len(r) and r[ti].strip():
                c += " " + r[ti].strip()
            out.append({"content": c, "date": norm_date(d) or d})
    return out


def parse_html(text):
    p = MemoHTMLParser()
    p.feed(text)
    out = []
    for m in p.memos:
        c = re.sub(r"\n{2,}", "\n", m["content"]).strip()
        if len(c) > 2:
            out.append({"content": c, "date": norm_date(m["date"]) or m["date"]})
    return out


def parse_plain(text):
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        lines = b.split("\n", 1)
        d = norm_date(lines[0])
        if d and len(lines) > 1:
            out.append({"content": lines[1].strip(), "date": d})
        else:
            out.append({"content": b, "date": norm_date(b) or ""})
    return out


def load_memos(path):
    ext = os.path.splitext(path)[1].lower()
    text = read_text(path)
    if ext == ".csv":
        memos = parse_csv(text)
    elif ext in (".html", ".htm"):
        memos = parse_html(text)
    else:
        memos = parse_plain(text)
    if not memos:
        memos = parse_plain(text)
    return memos


def filter_days(memos, days):
    if not days or days <= 0:
        return memos
    cut = datetime.now() - timedelta(days=days)
    kept = []
    for m in memos:
        d = norm_date(m.get("date", ""))
        if not d:
            kept.append(m)
        elif datetime.strptime(d, "%Y-%m-%d") >= cut:
            kept.append(m)
    return kept


def build_notes_text(memos, max_chars):
    """优先保留最近的笔记；超长时从最早的开始丢。返回 (编号文本, items)"""
    items = [(m.get("date", "") or "", m["content"]) for m in memos]
    total = sum(len(c) + 12 for _, c in items)
    while total > max_chars and len(items) > 1:
        d, c = items.pop(0)
        total -= len(c) + 12
    blocks = []
    for i, (d, c) in enumerate(items, 1):
        blocks.append("[%d] %s" % (i, (d + "\n" + c) if d else c))
    return "\n\n".join(blocks), items


KB_PREFIX = {"00": "global", "01": "insight", "02": "value",
             "03": "invert", "04": "second", "05": "cbt", "06": "mbti"}


def load_kb_for_mode(path, mode_key, max_chars=9000):
    """按维度加载专业资料：通用资料（00）+ 该维度资料（0x）。

    path 可以是单个文件（直接全文用），也可以是 knowledge 目录。
    """
    if not path:
        return ""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return read_text(path).strip()

    if os.path.isdir(path):
        parts = []
        for fn in sorted(os.listdir(path)):
            if not fn.lower().endswith((".md", ".txt")):
                continue
            m = re.match(r"(\d\d)", fn)
            key = KB_PREFIX.get(m.group(1)) if m else None
            if key in ("global", mode_key):
                parts.append(read_text(os.path.join(path, fn)).strip())
        text = "\n\n".join(p for p in parts if p)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…（资料过长，已截断）"
        return text
    return ""


# ---------------------------------------------------------------- 调用模型

def call_model(api_base, api_key, model, messages, temperature=0.7, timeout=300):
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(api_base, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:400]
        raise SystemExit("接口返回 HTTP %s：%s" % (e.code, body))
    except urllib.error.URLError as e:
        raise SystemExit("连不上接口（%s）。检查 --api-base，或本地 Ollama 是否已启动。" % e.reason)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise SystemExit("响应格式异常：%s" % json.dumps(data, ensure_ascii=False)[:400])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="鉴己 · 笔记洞察：自建六维 AI 洞察（兼容 flomo 导出格式），任意免费模型接口驱动",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python jianyi_insight.py -f flomo.csv --all --model glm-4-flash")
    ap.add_argument("-f", "--file", help="flomo 导出的 CSV / HTML / 纯文本")
    ap.add_argument("-m", "--mode", default="insight",
                    help="洞察维度：insight/value/invert/second/cbt/mbti，也可直接写中文名")
    ap.add_argument("--all", action="store_true", help="六个洞察一次跑完")
    ap.add_argument("-d", "--days", type=int, default=90, help="只分析最近 N 天（0=全部，默认 90）")
    ap.add_argument("-o", "--out", default="", help="输出目录（默认 ./jianyi-insights）")
    ap.add_argument("--max-chars", type=int, default=18000, help="单次送入的最大字数")
    ap.add_argument("--api-base", default=os.environ.get("JIANI_AI_BASE",
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions"))
    ap.add_argument("--api-key", default=os.environ.get("JIANI_AI_KEY", ""))
    ap.add_argument("--model", default=os.environ.get("JIANI_AI_MODEL", "glm-4-flash"))
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调用模型")
    ap.add_argument("--kb", default="", help="专业资料：单个 md 文件或目录（默认自动用同目录的 knowledge/）")
    ap.add_argument("--no-kb", action="store_true", help="不加载任何专业资料")
    ap.add_argument("--list", action="store_true", help="列出所有洞察维度")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读笔记")
    args = ap.parse_args()

    if args.list:
        print("可用洞察维度：\n")
        for k, v in MODES.items():
            print("  %-8s %-10s %s" % (k, v["name"], v["desc"]))
        print("\n用法：--mode insight 或 --mode 默认洞察，或 --all 一次跑完")
        return

    # 取笔记
    if args.stdin:
        memos = parse_plain(sys.stdin.read())
    elif args.file:
        if not os.path.exists(args.file):
            raise SystemExit("找不到文件：%s" % args.file)
        memos = load_memos(args.file)
    else:
        ap.print_help()
        print("\n[!] 需要 --file 或 --stdin")
        return

    if not memos:
        raise SystemExit("没解析出笔记。用 --stdin 直接粘贴，或检查导出文件。")

    memos = filter_days(memos, args.days)
    if not memos:
        raise SystemExit("最近 %d 天没有笔记，试试 --days 0" % args.days)

    notes_text, note_items = build_notes_text(memos, args.max_chars)
    span = "最近 %d 天" % args.days if args.days else "全部时间"
    print("解析到 %d 条笔记（%s），送入 %d 字" % (len(memos), span, len(notes_text)))

    # 选维度
    if args.all:
        keys = list(MODES.keys())
    else:
        key = ALIASES.get(args.mode) or ALIASES.get(args.mode.strip()) or ALIASES.get(args.mode.lower())
        if not key:
            raise SystemExit("未知维度：%s（用 --list 查看）" % args.mode)
        keys = [key]

    if not args.api_key and "localhost" not in args.api_base and "127.0.0.1" not in args.api_base:
        if not args.dry_run:
            raise SystemExit(
                "缺少 API Key。三种方式：\n"
                "  1) --api-key sk-xxx\n"
                "  2) 环境变量 set JIANI_AI_KEY=sk-xxx\n"
                "  3) --dry-run 只打印 prompt，自己粘到任意 AI 里跑")

    outdir = args.out or os.path.join(os.getcwd(), "jianyi-insights")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    kb_dir = args.kb or os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
    if not args.no_kb and os.path.isdir(kb_dir):
        print("专业资料：%s（--no-kb 可关闭）" % kb_dir)

    for key in keys:
        mode = MODES[key]
        kb = "" if args.no_kb else load_kb_for_mode(kb_dir, key)

        sys_content = COMMON
        if kb:
            sys_content += ("\n\n【专业资料 · 判定标准以本段为准，"
                            "不要引用资料中没有的术语】\n" + kb)
        sys_content += "\n" + mode["prompt"]

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content":
                "以下是我的笔记，每条开头已标注编号 [n]，分析中引用证据一律使用编号：\n\n---\n%s\n---\n\n请按要求输出「%s」的分析。"
                % (notes_text, mode["name"])},
        ]
        if kb:
            print("   「%s」附带资料 %d 字" % (mode["name"], len(kb)))

        if args.dry_run:
            print("\n" + "=" * 64)
            print("【%s】" % mode["name"])
            print("=" * 64)
            print(messages[0]["content"])
            print("\n--- 我的笔记 ---\n")
            print(notes_text)
            continue

        print("→ 正在生成「%s」..." % mode["name"], end="", flush=True)
        t0 = datetime.now()
        content = call_model(args.api_base, args.api_key, args.model,
                             messages, args.temperature)
        cost = (datetime.now() - t0).total_seconds()
        print(" 完成 %.1fs" % cost)

        # 引用笔记脚注：只列正文中实际引用的编号
        cited = sorted({int(x) for x in re.findall(r"\[(\d{1,3})\]", content)})
        refs = []
        for n in cited:
            if 1 <= n <= len(note_items):
                d, c = note_items[n - 1]
                refs.append("[%d] %s" % (n, (d + " " + c) if d else c))
        if refs:
            content += "\n\n---\n\n引用笔记：\n" + "\n\n".join(refs)

        header = ("# %s\n\n> 生成时间：%s ｜ 笔记 %d 条（%s）｜ 模型 %s\n\n---\n\n"
                  % (mode["name"], datetime.now().strftime("%Y-%m-%d %H:%M"),
                     len(memos), span, args.model))
        body = header + content + "\n"
        path = os.path.join(outdir, "%s-%s.md" % (key, stamp))
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        print("   已保存：%s" % path)
        print()

    if not args.dry_run:
        print("全部完成，输出目录：%s" % outdir)


if __name__ == "__main__":
    main()
