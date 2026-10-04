#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已解密的微信 4.x 数据库里导出某个联系人的全部消息（含语音 mp3）。

用法:
    python export_chat.py --decrypted <解密目录> --target "<昵称/备注/wxid 关键字>" --out <输出目录>
    python export_chat.py --decrypted ... --list                 # 只列会话，供人工确认
    python export_chat.py --decrypted ... --wxid wxid_xxx        # 直接用 wxid（已确认时）

产出（<输出目录>）:
    messages.json   全部消息（时间、发送人、类型、文本）
    voices/*.mp3    语音（SILK -> PCM -> mp3）
    chat.csv        表格版

依赖: pip install pycryptodome zstandard pysilk-mod lameenc
"""
import argparse
import csv
import datetime
import hashlib
import html
import json
import os
import re
import sqlite3
import sys

import zstandard

try:
    import pysilk
    import lameenc
    HAVE_SILK = True
except Exception:
    HAVE_SILK = False

DCTX = zstandard.ZstdDecompressor()


def ts_str(v):
    try:
        return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def decompress(blob, ct):
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    b = bytes(blob)
    if ct == 4:
        try:
            b = DCTX.decompress(b, max_output_size=1 << 26)
        except Exception:
            pass
    return b.decode("utf-8", "replace")


def render(t, c):
    c = c or ""
    if t == 1:
        return c.strip()
    if t == 3:
        return "[图片]"
    if t == 34:
        vl = re.search(r'voicelength="(\d+)"', c)
        secs = int(vl.group(1)) / 1000 if vl else 0
        tr = re.search(r'transtext="([^"]*)"', c)
        s = f"[语音 {secs:.0f}秒]"
        return s + (" 转写：" + html.unescape(tr.group(1)) if tr else "")
    if t == 43:
        return "[视频]"
    if t == 47:
        return "[表情]"
    if t == 48:
        p = re.search(r'poiname="([^"]*)"', c)
        return "[位置] " + (p.group(1) if p else "")
    if t == 49:
        title = re.search(r"<title>(.*?)</title>", c, re.S)
        url = re.search(r"<url>(.*?)</url>", c, re.S)
        des = re.search(r"<des>(.*?)</des>", c, re.S)
        parts = []
        for x in (title, des):
            if x:
                v = html.unescape(re.sub(r"<[^>]+>", "", x.group(1))).strip()
                if v and v not in parts:
                    parts.append(v[:300])
        if url and url.group(1).strip():
            parts.append(html.unescape(url.group(1).strip()))
        return "[链接/文件] " + " | ".join(parts)
    if t == 50:
        d = re.search(r"<!\[CDATA\[(.*?)\]\]>", c, re.S)
        return "[通话] " + (d.group(1) if d else "")
    if t == 10000:
        return html.unescape(re.sub(r"<[^>]+>", "", c)).strip() or "[系统消息]"
    if t == 42:
        n = re.search(r'nickname="([^"]*)"', c)
        return "[名片] " + (n.group(1) if n else "")
    return c[:160]


def list_chats(dec):
    """列出有消息记录的会话（按消息数排序）"""
    cpath = os.path.join(dec, "contact", "contact.db")
    contacts = {}
    if os.path.exists(cpath):
        con = sqlite3.connect(cpath)
        for u, n, r, al in con.execute("SELECT username, nick_name, remark, alias FROM contact"):
            contacts[u] = (n or "", r or "", al or "")
        con.close()
    agg = {}
    for fn in sorted(os.listdir(os.path.join(dec, "message"))):
        if not fn.startswith("message_") or not fn.endswith(".db"):
            continue
        con = sqlite3.connect(os.path.join(dec, "message", fn))
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"):
            try:
                cnt = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                mn, mx = con.execute(f'SELECT MIN(create_time), MAX(create_time) FROM "{t}"').fetchone()
            except Exception:
                continue
            u = t[4:]
            a = agg.setdefault(u, {"count": 0, "first": ts_str(mn), "last": ts_str(mx), "tables": []})
            a["count"] += cnt
            a["tables"].append((fn, t))
        con.close()
    out = []
    for u, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        n, r, al = contacts.get(u, ("", "", ""))
        out.append((u, n, r, al, a))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decrypted", required=True)
    ap.add_argument("--target", help="昵称/备注/别名 关键字")
    ap.add_argument("--wxid", help="直接指定 wxid/chatroom")
    ap.add_argument("--out", default="export")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()

    chats = list_chats(a.decrypted)
    if a.list:
        for i, (u, n, r, al, info) in enumerate(chats[:a.top], 1):
            print(f"{i:>3}. {info['count']:>7} 条  {info['first'][:16]} ~ {info['last'][:16]}  "
                  f"{u}  昵称={n!r} 备注={r!r} 别名={al!r}")
        return

    if a.wxid:
        target = a.wxid
    else:
        if not a.target:
            sys.exit("需要 --target 或 --wxid（或用 --list 先看有哪些会话）")
        hits = [(u, n, r, al, info) for (u, n, r, al, info) in chats
                if a.target in n or a.target in r or a.target in al or a.target == u]
        if not hits:
            sys.exit("没匹配到会话；用 --list 看看")
        if len(hits) > 1 and (hits[0][4]["count"] == hits[1][4]["count"]):
            print("匹配到多个同名会话，请用 --wxid 指定：")
            for u, n, r, al, info in hits[:10]:
                print(f"   {u}  昵称={n!r} 备注={r!r}  {info['count']} 条")
            return
        target = hits[0][0]
        print(f"目标会话: {target}  昵称={hits[0][1]!r} 备注={hits[0][2]!r}  {hits[0][4]['count']} 条")

    os.makedirs(a.out, exist_ok=True)
    vdir = os.path.join(a.out, "voices")
    os.makedirs(vdir, exist_ok=True)

    # 语音索引： create_time -> [(local_id, blob)]
    vmap = {}
    for fn in os.listdir(os.path.join(a.decrypted, "message")):
        if not fn.startswith("media_") or not fn.endswith(".db"):
            continue
        con = sqlite3.connect(os.path.join(a.decrypted, "message", fn))
        n2i = {r[0]: r[1] for r in con.execute("SELECT rowid, user_name FROM Name2Id")}
        ids = [k for k, v in n2i.items() if v == target]
        if ids:
            ph = ",".join("?" * len(ids))
            for lid, ct, blob in con.execute(
                    f"SELECT local_id, create_time, voice_data FROM VoiceInfo WHERE chat_name_id IN ({ph})", ids):
                vmap.setdefault(ct, []).append((lid, bytes(blob)))
        con.close()

    # 我自己的 wxid = 账号目录名（wxid_xxx_hash），从 contact 里取第一行通常是本机账号
    me = None
    cpath = os.path.join(a.decrypted, "contact", "contact.db")
    if os.path.exists(cpath):
        con = sqlite3.connect(cpath)
        try:
            me = con.execute("SELECT username FROM contact ORDER BY id LIMIT 1").fetchone()[0]
        except Exception:
            pass
        con.close()

    rows = []
    tbl = "Msg_" + hashlib.md5(target.encode()).hexdigest()
    for fn in sorted(os.listdir(os.path.join(a.decrypted, "message"))):
        if not fn.startswith(("message_", "biz_message_")) or not fn.endswith(".db"):
            continue
        con = sqlite3.connect(os.path.join(a.decrypted, "message", fn))
        cur = con.cursor()
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE name=?", (tbl,)).fetchone():
            con.close()
            continue
        n2i = {r[0]: r[1] for r in cur.execute("SELECT rowid, user_name FROM Name2Id")}
        cur.row_factory = sqlite3.Row
        for r in cur.execute(f'SELECT * FROM "{tbl}"'):
            d = dict(r)
            t = int(d.get("local_type") or 0) & 0xFFFF
            who = n2i.get(d.get("real_sender_id"), f"id{d.get('real_sender_id')}")
            ct = d.get("create_time") or 0
            rec = {"db": fn, "local_id": d.get("local_id"), "type": t, "create_time": ct,
                   "time": ts_str(ct), "sender": who, "is_me": bool(me and who == me),
                   "text": render(t, decompress(d.get("message_content"), d.get("WCDB_CT_message_content")))}
            if t == 34:
                cand = vmap.get(ct)
                if cand:
                    name = f"{rec['time'][:4]}{rec['time'][5:7]}{rec['time'][8:10]}_" \
                           f"{rec['time'][11:13]}{rec['time'][14:16]}{rec['time'][17:19]}_" \
                           f"{'我' if rec['is_me'] else '对方'}.mp3"
                    dst = os.path.join(vdir, name)
                    rec["voice"] = name
                    if HAVE_SILK and not os.path.exists(dst):
                        try:
                            pcm = pysilk.decode(cand[0][1], 24000)
                            enc = lameenc.Encoder()
                            enc.set_bit_rate(64); enc.set_in_sample_rate(24000)
                            enc.set_channels(1); enc.set_quality(5)
                            open(dst, "wb").write(enc.encode(pcm) + enc.flush())
                        except Exception as e:
                            rec["voice_error"] = str(e)
                    elif not HAVE_SILK:
                        rec["voice_error"] = "缺少 pysilk-mod / lameenc"
            rows.append(rec)
        con.close()

    rows.sort(key=lambda x: x["create_time"] or 0)
    json.dump(rows, open(os.path.join(a.out, "messages.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "chat.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "发送人", "类型", "内容", "语音文件"])
        for r in rows:
            w.writerow([r["time"], "我" if r["is_me"] else "对方", r["type"], r["text"], r.get("voice", "")])

    nv = sum(1 for r in rows if r.get("voice"))
    print(f"导出完成: {len(rows)} 条消息，{nv} 条语音 -> {a.out}")


if __name__ == "__main__":
    main()
