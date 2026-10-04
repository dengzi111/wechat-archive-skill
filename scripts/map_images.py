#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把微信「另存为」出来的原图/视频对回聊天记录消息。

微信保存的文件名形如:
    微信图片_20250421173622_130_92.jpg
    │        └ 14 位 = 原消息时间 (2025-04-21 17:36:22)
    │                  └ 消息 local_id (130)
    └ 类型
    微信视频2026-10-05_011753_186.mp4   ← 视频用保存当天的日期，只用末尾 local_id 匹配

用法:
    python map_images.py --saved "<微信保存目录>" --messages <messages.json> [--out image_map.json]
"""
import argparse
import json
import os
import re
import sys

APAT = re.compile(r"微信(图片|视频)_(\d{14})_(\d+)_\d+\.(?:jpg|jpeg|png|gif|bmp)")
VPAT = re.compile(r"微信视频.*?_(\d+)\.(?:mp4|mov|avi)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saved", required=True)
    ap.add_argument("--messages", required=True)
    ap.add_argument("--out", default="image_map.json")
    a = ap.parse_args()

    imgs, vids = {}, {}
    for f in os.listdir(a.saved):
        m = APAT.match(f)
        if m:
            (imgs if m.group(1) == "图片" else vids)[(m.group(2), int(m.group(3)))] = f
            continue
        if f.startswith("微信视频"):
            v = VPAT.match(f)
            if v:
                vids[("", int(v.group(1)))] = f

    msgs = json.load(open(a.messages, encoding="utf-8"))
    mapping, missing = {}, []
    for m in msgs:
        t = (m.get("time") or "")
        stamp = t[:4] + t[5:7] + t[8:10] + t[11:13] + t[14:16] + t[17:19]
        if m["type"] == 3:
            f = imgs.pop((stamp, m["local_id"]), None)
        elif m["type"] == 43:
            f = vids.pop((stamp, m["local_id"]), None) or vids.pop(("", m["local_id"]), None)
        else:
            continue
        if f:
            mapping[f"{m['local_id']}|{m['create_time'] if 'create_time' in m else stamp}"] = f
        else:
            missing.append((m["type"], t, m["local_id"]))

    json.dump(mapping, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"命中 {len(mapping)} 条；聊天里有记录但没拿到文件的 {len(missing)} 条")
    if missing[:5]:
        print("  缺的样例:", missing[:5])
    if imgs:
        print(f"  注意: 有 {len(imgs)} 个文件没对上消息（可能属于别的会话）")


if __name__ == "__main__":
    main()
