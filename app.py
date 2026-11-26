#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地版机电 AI 诊断助手（最小可运行版）

特点：
- 双击 启动.bat 就能开，不需要联网、不需要 API Key
- 模型跑在本机 Ollama 上（默认 DeepSeek-R1-Distill-Qwen-14B Q4_K_M）
- 左侧「故障向导」点选设备与现象即可提问，不用自己会写提示词
- 回答按"现象判断 → 可能原因 → 排查顺序 → 安全提醒"组织，并给出可点选的追问

命令行模式（用于测试或录屏）：
    python app.py --cli "数控车床外圆出现振纹，可能的原因和排查顺序？"
"""

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
PREFERRED = "modelscope.cn/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF:latest"

SYSTEM_PROMPT = """你是"机电 AI 诊断助手"，服务对象是机械/机电专业的学生和现场维修人员。你熟悉机械传动、液压气动、电气控制、数控机床、轴承与润滑、装配与工艺参数。

回答规则：
1. 先用一句话判断问题属于哪类故障（机械/电气/液压/控制/工艺参数）。
2. 按可能性从高到低列 3-5 条原因，每条用一句话说明"为什么会导致这个现象"。
3. 给出排障顺序：从最简单、最安全的检查开始；涉及转动部件或带电作业的，先写停机断电。
4. 信息不足时，先问 1-3 个最关键的问题，不要一次问一大堆。
5. 涉及人身或设备安全时，单独写一段【安全提醒】。
6. 不要编造具体机型的参数、手册页码、零件编号；不确定就直说不确定。
7. 全部用中文，多用短句和编号，控制在 500 字以内。"""

DEVICES = ["数控车床", "数控铣床/加工中心", "磨床", "普通机床", "液压系统",
           "气动系统", "电机与驱动", "皮带/链轮/齿轮传动", "其他设备"]

SYMPTOMS = ["异常振动", "异响", "发热烫手", "漏油漏气", "精度超差",
            "无法启动", "动作慢无力", "报警停机", "异味冒烟"]

FOLLOWUPS = ["怎么确认到底是哪一条原因？", "需要哪些工具和备件？", "现在能不能先临时应急处理？"]


# ---------------------------------------------------------------- Ollama 接口
def list_models(timeout=5):
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def short_name(name):
    return name.split("/")[-1].replace(":latest", "")


def pick_model():
    models = list_models()
    if PREFERRED in models:
        return PREFERRED
    for m in models:
        if "14b" in m.lower():
            return m
    return models[0] if models else PREFERRED


def chat_stream(model, messages, on_text, on_done, on_error, stop_flag):
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.3, "num_ctx": 6144, "num_predict": 1400},
    }
    req = urllib.request.Request(
        OLLAMA + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                if stop_flag.is_set():
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if obj.get("message", {}).get("content"):
                    on_text(obj["message"]["content"])
                if obj.get("done"):
                    break
        on_done()
    except urllib.error.URLError as e:
        on_error("连不上本机模型服务（Ollama）。\n请确认 Ollama 已启动，再试一次。\n\n原始错误：%s" % e)
    except Exception as e:
        on_error("请求出错：%s" % e)


# ---------------------------------------------------------------- 文本清理
_MD_MARKS = ("**", "__", "###", "##", "#")


def clean_md(t):
    t = t.replace("**", "").replace("__", "")
    out = []
    for line in t.split("\n"):
        s = line.lstrip()
        if s.startswith("#"):
            line = s.lstrip("#").strip()
        out.append(line)
    return "\n".join(out)


class MdCleaner:
    def __init__(self):
        self.buf = ""

    def feed(self, text):
        s = self.buf + text
        hold = 0
        for m in _MD_MARKS:
            for L in range(min(len(m), len(s)), 0, -1):
                if s.endswith(m[:L]):
                    hold = max(hold, L)
        vis = s[: len(s) - hold] if hold else s
        self.buf = s[len(s) - hold:] if hold else ""
        return clean_md(vis)

    def flush(self):
        rest, self.buf = clean_md(self.buf), ""
        return rest


# ---------------------------------------------------------------- 命令行模式
def cli_mode(question):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    model = pick_model()
    print("模型：", model)
    print("服务：", OLLAMA)
    print("-" * 60)
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    got = []

    def on_text(t):
        got.append(t)
        sys.stdout.write(t)
        sys.stdout.flush()

    t0 = time.time()
    chat_stream(model, msgs, on_text, lambda: None, lambda e: print("\n[错误]", e), threading.Event())
    print("\n" + "-" * 60)
    print("耗时 %.1f 秒，输出 %d 字" % (time.time() - t0, len("".join(got))))


# ---------------------------------------------------------------- 图形界面
def gui_mode(demo_question=None):
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    root = tk.Tk()
    root.title("本地版机电 AI 诊断助手")
    root.configure(bg="#161a1f")
    root.minsize(1000, 620)

    bg, panel, card = "#161a1f", "#1b2126", "#222a33"
    fg, sub, accent = "#e8eaed", "#9aa4b2", "#2e7dff"

    ui = tkfont.Font(family="Microsoft YaHei UI", size=11)
    out_font = tkfont.Font(family="Microsoft YaHei UI", size=12)
    small = tkfont.Font(family="Microsoft YaHei UI", size=9)
    chip = tkfont.Font(family="Microsoft YaHei UI", size=10)

    # ---------------- 顶栏
    top = tk.Frame(root, bg="#1e242b")
    top.pack(fill="x")
    tk.Label(top, text="机电 AI 诊断助手", bg="#1e242b", fg=fg,
             font=("Microsoft YaHei UI", 14, "bold")).pack(side="left", padx=16, pady=10)
    model_var = tk.StringVar(value="检测中…")
    model_box = ttk.Combobox(top, textvariable=model_var, width=30, state="readonly", font=small)
    model_box.pack(side="right", padx=10, pady=10)
    tk.Label(top, text="本机模型", bg="#1e242b", fg=sub, font=small).pack(side="right")

    status = tk.Label(root, text="正在检查本机模型服务…", anchor="w", bg="#12161a",
                      fg="#ffcc66", font=small, padx=16, pady=5)
    status.pack(fill="x")

    # ---------------- 主体：左侧向导 + 右侧问答
    outer = tk.Frame(root, bg=bg)
    outer.pack(fill="both", expand=True)

    side = tk.Frame(outer, bg=panel, width=310)
    side.pack(side="left", fill="y")
    side.pack_propagate(False)

    main = tk.Frame(outer, bg=bg)
    main.pack(side="left", fill="both", expand=True)

    tk.Label(side, text="故障向导", bg=panel, fg=fg,
             font=("Microsoft YaHei UI", 12, "bold"), anchor="w").pack(fill="x", padx=14, pady=(14, 2))
    tk.Label(side, text="选设备、点现象，不用会写提示词", bg=panel, fg=sub, font=small,
             anchor="w").pack(fill="x", padx=14, pady=(0, 10))

    state = {
        "busy": False, "stop": threading.Event(), "messages": [],
        "last_answer": "", "model_map": {},
        "device": tk.StringVar(value=""),
        "syms": {},
    }

    def section(title):
        tk.Label(side, text=title, bg=panel, fg=sub, font=small, anchor="w").pack(fill="x", padx=14, pady=(8, 4))

    # 设备：单选 chip
    section("① 是什么设备")
    devbox = tk.Frame(side, bg=panel)
    devbox.pack(fill="x", padx=10)
    for i, d in enumerate(DEVICES):
        rb = tk.Radiobutton(devbox, text=d, variable=state["device"], value=d, indicatoron=0,
                            bg=card, fg=fg, selectcolor=accent, activebackground="#2b3540",
                            activeforeground=fg, font=chip, relief="flat", bd=0,
                            padx=8, pady=4, anchor="w", cursor="hand2", highlightthickness=0)
        rb.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
    devbox.grid_columnconfigure(0, weight=1)
    devbox.grid_columnconfigure(1, weight=1)

    # 现象：多选 chip
    section("② 出现了什么现象（可多选）")
    symbox = tk.Frame(side, bg=panel)
    symbox.pack(fill="x", padx=10)
    for i, s in enumerate(SYMPTOMS):
        v = tk.IntVar(value=0)
        state["syms"][s] = v
        cb = tk.Checkbutton(symbox, text=s, variable=v, indicatoron=0, bg=card, fg=fg,
                            selectcolor=accent, activebackground="#2b3540", activeforeground=fg,
                            font=chip, relief="flat", bd=0, padx=8, pady=4, anchor="w",
                            cursor="hand2", highlightthickness=0)
        cb.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
    symbox.grid_columnconfigure(0, weight=1)
    symbox.grid_columnconfigure(1, weight=1)

    # 补充说明
    section("③ 补充一句（可留空）")
    extra = tk.Entry(side, bg=card, fg=fg, insertbackground=fg, relief="flat",
                     font=chip, width=28)
    extra.pack(fill="x", padx=14, ipady=6)
    tk.Label(side, text="例：加工 Φ50 外圆、转速 800，最近刚换过刀", bg=panel, fg="#6b7583",
             font=small, anchor="w", wraplength=270, justify="left").pack(fill="x", padx=14, pady=(4, 8))

    def build_question():
        dev = state["device"].get().strip()
        syms = [k for k, v in state["syms"].items() if v.get()]
        if not dev or not syms:
            status.configure(text="● 先在左边选一个设备，再点至少一个现象", fg="#ffcc66")
            return None
        q = "设备：%s。现象：%s。" % (dev, "、".join(syms))
        ex = extra.get().strip()
        if ex and ex != PLACEHOLDER_EXTRA:
            q += "补充：%s。" % ex
        q += "请按「判断故障类别 → 可能原因 → 排查顺序 → 安全提醒」的顺序回答。"
        return q

    # ---------------- 右侧：输出 + 输入
    body = tk.Frame(main, bg=bg)
    out = tk.Text(body, bg="#0f1317", fg=fg, insertbackground=fg, font=out_font, wrap="word",
                  relief="flat", padx=16, pady=12, spacing1=2, spacing3=4)
    sb = tk.Scrollbar(body, command=out.yview, bg=panel)
    out.configure(yscrollcommand=sb.set)
    out.tag_configure("q", foreground="#7fb3ff", font=("Microsoft YaHei UI", 12, "bold"))
    out.tag_configure("a", foreground=fg)
    out.tag_configure("hint", foreground=sub, font=small)
    out.tag_configure("sec", foreground="#ffcc66", font=("Microsoft YaHei UI", 12, "bold"))
    sb.pack(side="right", fill="y")
    out.pack(side="left", fill="both", expand=True)
    out.configure(state="disabled")
    out.configure(state="normal")
    out.insert("end", "怎么用\n", "sec")
    out.insert("end", "1. 左边选设备 → 点现象 → 可写一句补充 → 点「开始诊断」\n"
                      "2. 回答出来后，下面会出现 3 个追问按钮，点一下继续问\n"
                      "3. 全程离线：模型在本机跑，断网也能用，数据不出这台电脑\n\n", "hint")
    out.configure(state="disabled")

    # 追问按钮区（先建后 pack，保证顺序：回答 → 追问 → 输入 → 按钮栏）
    follow = tk.Frame(main, bg=bg)
    follow_btns = []
    for f in FOLLOWUPS:
        b = tk.Button(follow, text=f, bg=card, fg=fg, font=small, relief="flat", padx=10, pady=6,
                      state="disabled", cursor="hand2", activebackground="#2b3540",
                      command=lambda t=f: send_text(t))
        b.pack(side="left", padx=(0, 8))
        follow_btns.append(b)

    # 输入区
    PLACEHOLDER = "也可以直接在这里打字提问，例如：主轴异响，转速越高越明显…"
    PLACEHOLDER_EXTRA = ""

    inf = tk.Frame(main, bg=bg)
    entry = tk.Text(inf, height=3, bg="#1b2128", fg="#6b7583", insertbackground=fg, font=ui,
                    relief="flat", padx=12, pady=8, wrap="word")
    entry.pack(fill="x")
    entry.insert("1.0", PLACEHOLDER)

    def ph_in(_=None):
        if entry.get("1.0", "end").strip() == PLACEHOLDER:
            entry.delete("1.0", "end")
            entry.configure(fg=fg)

    def ph_out(_=None):
        if not entry.get("1.0", "end").strip():
            entry.insert("1.0", PLACEHOLDER)
            entry.configure(fg="#6b7583")

    entry.bind("<FocusIn>", ph_in)
    entry.bind("<FocusOut>", ph_out)

    bar = tk.Frame(main, bg=bg)
    body.pack(fill="both", expand=True, padx=14, pady=(10, 6))
    bar.pack(side="bottom", fill="x", padx=14, pady=(8, 12))
    inf.pack(side="bottom", fill="x", padx=14)
    follow.pack(side="bottom", fill="x", padx=14, pady=(0, 6))

    # ---------------- 逻辑
    def append(text, tag=None):
        out.configure(state="normal")
        out.insert("end", text, tag or "a")
        out.see("end")
        out.configure(state="disabled")

    def refresh_models():
        models = list_models()
        if not models:
            status.configure(text="● 未检测到本机模型服务（Ollama 没开？）  双击「启动模型服务.bat」后再试", fg="#ff6b6b")
            model_box["values"] = []
            model_var.set("无模型")
            return
        state["model_map"] = {short_name(m): m for m in models}
        model_box["values"] = list(state["model_map"].keys())
        model_var.set(short_name(pick_model()))
        status.configure(text="● 本地就绪：断网可用 · 共 %d 个模型 · 首次回答需等模型加载（约 10-30 秒）" % len(models), fg="#5ad48c")

    def send_text(q):
        if state["busy"] or not q:
            return
        model = state.get("model_map", {}).get(model_var.get(), model_var.get())
        append("\n你：" + q + "\n\n", "q")
        append("助手：")
        state["busy"] = True
        state["stop"].clear()
        send_btn.configure(state="disabled", text="生成中…")
        stop_btn.configure(state="normal")
        for b in follow_btns:
            b.configure(state="disabled")
        state["last_answer"] = ""
        if not state["messages"]:
            state["messages"] = [{"role": "system", "content": SYSTEM_PROMPT}]
        state["messages"].append({"role": "user", "content": q})
        buf = []
        cleaner = MdCleaner()
        evq = queue.Queue()

        def worker():
            chat_stream(model, state["messages"],
                        lambda t: evq.put(("t", t)),
                        lambda: evq.put(("done", None)),
                        lambda e: evq.put(("err", e)),
                        state["stop"])

        threading.Thread(target=worker, daemon=True).start()

        def finish():
            state["busy"] = False
            send_btn.configure(state="normal", text="开始诊断")
            stop_btn.configure(state="disabled")
            for b in follow_btns:
                b.configure(state="normal")

        def pump():
            try:
                while True:
                    kind, val = evq.get_nowait()
                    if kind == "t":
                        buf.append(val)
                        state["last_answer"] += val
                        append(cleaner.feed(val))
                    elif kind == "err":
                        append("\n[错误] " + str(val) + "\n", "hint")
                        finish()
                        return
                    elif kind == "done":
                        append(cleaner.flush())
                        state["messages"].append({"role": "assistant", "content": "".join(buf)})
                        append("\n")
                        finish()
                        return
            except queue.Empty:
                pass
            root.after(40, pump)

        pump()

    def send_from_entry(_=None):
        q = entry.get("1.0", "end").strip()
        if not q or q == PLACEHOLDER:
            q = build_question()
            if not q:
                return
        entry.delete("1.0", "end")
        ph_out()
        send_text(q)

    def start_wizard():
        q = build_question()
        if not q:
            return
        send_text(q)

    def stop():
        state["stop"].set()

    def clear():
        if state["busy"]:
            return
        state["messages"] = []
        state["last_answer"] = ""
        out.configure(state="normal")
        out.delete("1.0", "end")
        out.configure(state="disabled")
        entry.delete("1.0", "end")
        ph_out()
        for b in follow_btns:
            b.configure(state="disabled")

    def copy_last():
        txt = state["last_answer"].strip()
        if txt:
            root.clipboard_clear()
            root.clipboard_append(txt)
            status.configure(text="已复制上一条回答到剪贴板", fg="#5ad48c")

    send_btn = tk.Button(bar, text="开始诊断", command=start_wizard, bg=accent, fg="white", font=ui,
                         relief="flat", padx=20, pady=8, cursor="hand2", activebackground="#1f66d6")
    send_btn.pack(side="right", padx=(8, 0))
    stop_btn = tk.Button(bar, text="停止", command=stop, bg="#2a323b", fg=fg, font=ui,
                         relief="flat", padx=14, pady=8, state="disabled", cursor="hand2")
    stop_btn.pack(side="right", padx=(8, 0))
    tk.Button(bar, text="发送输入框内容", command=send_from_entry, bg="#2a323b", fg=fg, font=ui,
              relief="flat", padx=14, pady=8, cursor="hand2").pack(side="right", padx=(8, 0))
    tk.Button(bar, text="复制回答", command=copy_last, bg="#2a323b", fg=fg, font=ui,
              relief="flat", padx=14, pady=8, cursor="hand2").pack(side="right", padx=(8, 0))
    tk.Button(bar, text="清空对话", command=clear, bg="#2a323b", fg=fg, font=ui,
              relief="flat", padx=14, pady=8, cursor="hand2").pack(side="right")
    tk.Label(bar, text="Ctrl+Enter 也能发送 · 数据全部留在本机", bg=bg, fg=sub, font=small).pack(side="left")

    entry.bind("<Control-Return>", send_from_entry)
    entry.bind("<Control-KP_Enter>", send_from_entry)

    def fit_window():
        try:
            import ctypes
            import ctypes.wintypes as wt
            r = wt.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                w, h = r.right - r.left, r.bottom - r.top
                root.geometry("%dx%d+%d+%d" % (max(1000, w - 24), max(620, h - 96), r.left + 8, r.top + 4))
                return
        except Exception:
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d+0+0" % (sw, sh - 60))

    root.after(120, fit_window)
    refresh_models()

    if demo_question:
        def _demo():
            if demo_question == "WIZARD":
                state["device"].set("数控车床")
                for k in ("异常振动", "精度超差"):
                    state["syms"][k].set(1)
                extra.delete(0, "end")
                extra.insert(0, "加工Φ50外圆、转速800，刚换过刀")
                start_wizard()
                return
            entry.delete("1.0", "end")
            entry.configure(fg=fg)
            entry.insert("1.0", demo_question)
            send_from_entry()
        root.after(900, _demo)

    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        cli_mode(sys.argv[2] if len(sys.argv) > 2 else "数控车床加工外圆出现明显振纹，可能的原因和排查顺序是什么？")
    elif len(sys.argv) > 1 and sys.argv[1] == "--demo":
        gui_mode(sys.argv[2] if len(sys.argv) > 2 else "数控车床加工外圆时出现明显振纹，最可能的原因和排查顺序是什么？")
    else:
        gui_mode()
