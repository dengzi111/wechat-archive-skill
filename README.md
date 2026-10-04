# 微信聊天记录导出 · WeChat Archive Skill

把微信 PC 端（4.x）的聊天记录完整归档成长期可用的文件：**文字 + 语音 + 图片**，一条不落。

一个 Agent Skill（AgentSkills 目录结构），可在 DeepSeek Harness、Claude Code、Codex 等宿主里使用。

## 它能做什么

| 产出 | 说明 |
|------|------|
| `聊天记录.docx` | 按日期分节、按时间逐条；图片缩略图内嵌在对应消息位置；语音附转写文字与音频文件名 |
| `聊天记录.html` | 微信式左右气泡；语音可直接点击播放；图片点击放大看原图 |
| `聊天记录.csv` | 表格版，可导入 Excel 筛选统计 |
| `voices/*.mp3` | 语音消息（SILK）转成的 mp3，文件名=时间_发送人 |
| `图片/` | 微信原图（不是截屏），文件名含原始消息时间与 local_id，可与正文一一对应 |

## 为什么值得单独做成 skill

普通「导出聊天记录」的教程只讲怎么解密数据库，但真做起来会连续踩四个坑，这个 skill 把答案都固化了：

1. **密钥**：微信 4.1.10+ 不再缓存明文密钥，普通权限读进程内存会被拒，必须提权 + 只读扫描；同时记录了几条走不通的路，省掉重复试错。
2. **发送者**：`real_sender_id` 在 message_0/1/2 里含义不同，必须逐库解析 `Name2Id`，否则整段对话张冠李戴。
3. **图片**：本地 `.dat` 的 AES 密钥极难稳定抓到，正确姿势是**用纯键盘 UI 自动化让微信自己把原图另存出来**（`←` 翻页 → `Ctrl+S` → `回车`）——不读内存、不注入、不触发风控。
4. **对账**：微信保存的文件名里带着原始时间戳和消息 `local_id`，可以 100% 精确对回聊天记录。

## 用法

把本目录放进宿主的 skills 目录（DSH：`~/.dsh/skills/`），然后直接说：

> 帮我把和 XX 的微信聊天记录导出成 Word，语音和图片也要

流程见 `SKILL.md`，细节见 `references/`，可执行脚本在 `scripts/`：

```bash
# 1) 解密数据库（先拿到 64 位 hex passphrase）
python scripts/decrypt_db.py --db-dir ./db_storage_copy --key <hex> --out ./decrypted

# 2) 导出某个联系人的消息 + 语音
python scripts/export_chat.py --decrypted ./decrypted --list          # 先看有哪些会话
python scripts/export_chat.py --decrypted ./decrypted --target "昵称" --out ./export

# 3) 让微信自己把原图另存出来（纯键盘自动化）
powershell -ExecutionPolicy Bypass -File scripts/save_images_ui.ps1 -Count 200 -Direction prev
#    <微信保存目录> = 你在微信「另存为」里选定的目录（先手动存一张，之后微信会记住）
# 4) 把图片对回消息
python scripts/map_images.py --saved "<微信保存目录>" --messages ./export/messages.json

# 5) 生成 Word / HTML / CSV
python scripts/build_docs.py --messages ./export/messages.json --images "<微信保存目录>" \
       --voices ./export/voices --out ./成品 --title "与 XX 的聊天记录"
```

依赖：`pycryptodome zstandard pysilk-mod lameenc python-docx Pillow`

## 安全与合规

- 只用于**用户自己设备上、自己账号**的数据；产物只留在本机。
- **读微信进程内存会触发风控**（实测会导致要求重新登录）。skill 优先使用纯 UI 自动化路径，并把内存方案的失败记录与风险写在 `references/key-extraction.md`，供需要时权衡。
- 出现风控提示后立即停止扫描、删除工具文件。
