---
name: wechat-chat-export
description: 保存与导出微信聊天记录：文字、语音 mp3、图片原图 → Word / HTML / CSV。
metadata:
  displayName: 微信聊天记录导出
---

# 微信聊天记录导出（Windows / 微信 4.x）

把一个人的完整聊天史导出成别人拿得走、永远打得开的文件。产出：Word（含图）、HTML（可播放语音、点开放大图）、CSV、语音 mp3、原图文件夹。

## 先读这一段：风险与红线

- **读微信进程内存会触发风控。** 微信 4.1.10+ 起不再在内存里缓存数据库密钥，扫描进程内存属于微信明确防护的行为——实测会导致「要求重新登录」。只做一次、由用户在场、严格限定范围；一旦出现风控提示立刻停止。
- **图片不要走内存那条路。** 图片密钥（V2 `.dat` 的 AES key）只在微信显示图片的瞬间存在内存里，多轮扫描也未必抓得到，性价比极低。
- **推荐路径**：数据库用「管理员提权 + 开源工具只读扫描」拿密钥；图片用**纯键盘 UI 自动化**（`Ctrl+S` 保存）——等同于人工手动保存，不读内存、不注入、不改微信。
- 全程只处理用户自己账号、自己设备上的数据；产物留在本机。

## 四条主线

```
主线 1  定位数据与解密数据库        → references/key-extraction.md
主线 2  导出消息 / 语音（文字 + mp3） → scripts/export_chat.py
主线 3  抢救图片（UI 自动化保存原图） → scripts/save_images_ui.ps1
主线 4  生成 Word / HTML / CSV        → scripts/build_docs.py
```

## 主线 1：定位与解密

1. 确定版本与数据目录：
   - 4.0+：`%USERPROFILE%\Documents\xwechat_files\<wxid_hash>\db_storage`
   - 3.9：`%USERPROFILE%\Documents\WeChat Files\<wxid>\Msg`（老格式，用 PyWxDump 那套）
2. 取数据库密钥（SQLCipher 4，PBKDF2-HMAC-SHA512 / 256000 轮）。可行做法与失败记录见 [references/key-extraction.md](references/key-extraction.md)。
3. 解密：拿到 64 位 hex passphrase 后用 `scripts/decrypt_db.py`，或直接用第三方工具（如 wcdb-key-tool）的 `extract --decrypt`。**解密 24 个库通常只要几十秒**，每个库都要 HMAC 校验通过才算成功。
4. 解密前**先复制**整个 `db_storage` 到工作目录（微信运行时也能复制，不必退登录），后续所有操作都在副本上做。

## 主线 2：导出消息与语音

```bash
python scripts/export_chat.py --decrypted <解密目录> --target "<昵称/备注/wxid>" --out <输出目录>
```

- 联系人定位：`contact.db` 里按 `nick_name/remark/alias` 模糊匹配；同名多义时用消息条数和时间范围让用户确认。
- 消息表名 = `Msg_<md5(wxid)>`，分布在 `message_0/1/2.db`；发送者用各库自己的 `Name2Id.rowid` 解析（**不同分库的行号含义不同，必须逐库解析**）。
- 内容解压：`WCDB_CT_message_content = 4` 表示 zstd 压缩。
- 语音：`media_*.db` 的 `VoiceInfo(chat_name_id, create_time, local_id, voice_data)`，`voice_data` 是 `\x02#!SILK_V3…`，用 `pysilk.decode(blob, 24000)` 解出 PCM 再用 `lameenc` 转 mp3。
- 消息类型、XML 字段含义见 [references/message-types.md](references/message-types.md)。

## 主线 3：图片（本 skill 的关键技巧）

微信本地图片是 V2 `.dat`（前 1KB AES-128-ECB + 其余 XOR），但**不需要解密**：让微信自己把原图保存出来。

```powershell
powershell -File scripts/save_images_ui.ps1 -Count 100
```

原理：在微信看图窗口里循环「`←` 翻上一条 → `Ctrl+S` 调出系统保存对话框 → `回车` 确认」。要点：

- **必须用键盘快捷键，不要用坐标点击。** 显示器有缩放时坐标会整体偏移（125% 缩放就会差 25%），肉眼看不到点击落在哪；`Ctrl+S` 与窗口位置无关。
- 每步都要判断「对话框是否出现」：没出现说明这张图是「图片已过期或被清理」，直接跳下一张。
- 用文件数增量做进度与卡死判据：连续 N 次没有新文件 = 已翻到头，或已在覆盖同名文件。
- 微信「另存为」保存目录里的文件名形如 `微信图片_20250421173622_130_92.jpg`：14 位数字是**原消息时间**，倒数第二个数字是**消息 local_id** —— 这是把图片对回聊天记录的唯一钥匙。
- 方向：先一路 `←` 翻到最早，再用 `→` 反向补一遍，覆盖两个方向。

对回正文：

```bash
python scripts/map_images.py --saved "<保存目录>" --messages <messages.json>
```

按 `(时间戳, local_id)` 双键匹配，命中率通常 >99%；未命中的就是过期图。

## 主线 4：生成成品

```bash
python scripts/build_docs.py --messages <messages.json> --images "<图片目录>" --voices "<语音目录>" --out <输出目录>
```

产出 Word（图片以缩略图内嵌在对应消息位置，几百张图也只有几 MB）、HTML（气泡排版 + `<audio>` 语音播放 + 图片点击放大，引用原图文件夹）、CSV。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| 解密工具报 HMAC 失败 | 微信 3.9 用 SHA1/64000 轮、reserve 48；4.x 用 SHA512/256000 轮、reserve 80，参数别混 |
| 找不到目标联系人 | 已删好友的 remark 会空，用 `nick_name` 或按消息条数排序找；同名时让用户挑 |
| 语音条数比消息少 | `VoiceInfo` 只保存过真正播放/下载过的语音，缺的无法补 |
| 图片 100% 缺失 | `msg/attach` 被微信清理过；走主线 3 让微信重新下载并保存 |
| 保存图片时一直无新文件 | 已到最早一张，或图片全部过期；换方向或收工 |
| 读进程内存后微信要求重登 | 立即停止所有扫描程序并删除工具；后续只用主线 3 那种纯 UI 方式 |
