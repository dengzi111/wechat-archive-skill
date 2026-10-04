# 微信 4.x 消息数据库：结构与类型速查

## 库的位置与用途（`xwechat_files\<wxid_hash>\db_storage\`）

| 路径 | 用途 |
|------|------|
| `contact\contact.db` | 联系人：`username / nick_name / remark / alias`；群在 `chat_room` |
| `contact\contact_fts.db` | 联系人全文索引（一般不用） |
| `session\session.db` | 会话列表 `SessionTable`（最近会话、未读数、草稿） |
| `message\message_0/1/2.db` | **消息分库**，每个联系人一张 `Msg_<md5(username)>` 表 |
| `message\biz_message_0.db` | 公众号消息 |
| `message\media_0/1.db` | **语音本体**：`VoiceInfo(chat_name_id, create_time, local_id, voice_data, data_index)` |
| `message\message_resource.db` | 消息资源索引：`MessageResourceInfo(chat_id, message_local_id, message_create_time, message_local_type, packed_info)`；**图片 md5 藏在 `packed_info` 里**，protobuf 标记 `\x12\x22\x0a\x20` 后跟 32 字节 ASCII hex |
| `hardlink\hardlink.db` | `image/file/video_hardlink_info_v4`：md5 → 本地文件名；`dir2id` 把数字映射回 目录名 |
| `msg\attach\<md5(username)>\<年-月>\Img\*.dat` | 加密图片（**会被微信清理**，看运气） |
| `cache\<年-月>\Message\<md5(username)>\Bubble\*.dat` | 最近查看过的图片缓存（同样是加密的 V2 `.dat`） |

## 消息表结构

```sql
CREATE TABLE Msg_xxx(
  local_id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 会话内自增序号
  server_id INTEGER, local_type INTEGER, sort_seq INTEGER,
  real_sender_id INTEGER,                      -- 指向本库 Name2Id 的 rowid
  create_time INTEGER,                         -- Unix 秒
  status, upload_status, download_status, server_seq, origin_source,
  source TEXT, message_content TEXT, compress_content TEXT,
  packed_info_data BLOB,
  WCDB_CT_message_content INTEGER,             -- 4 = message_content 是 zstd 压缩
  WCDB_CT_source INTEGER);
```

**坑：`real_sender_id` 的含义每个分库各自定义。** 必须逐库读 `Name2Id(rowid → user_name)` 解析，不能拿 message_0 的映射去解释 message_1/2 的行号。

## 消息类型（`local_type & 0xFFFF`）

| 值 | 含义 | `message_content` 形态 |
|----|------|------------------------|
| 1 | 文本 | 明文（或 zstd） |
| 3 | 图片 | XML：`<img md5="…" aeskey="…" length=… cdnthumb…>` |
| 34 | 语音 | XML：`<voicemsg voicelength="毫秒" voiceformat="4" ...>`，同级 `<voicetrans transtext="微信自带转写">` |
| 42 | 名片 | XML：`nickname=…` |
| 43 | 视频 | XML：`<videomsg md5=… newmd5=… playlength=…>` |
| 47 | 表情 | XML：`<emoji md5="…" productid=…>` |
| 48 | 位置 | XML：`<location poiname=… x=… y=…>` |
| 49 | 链接/文件/引用/转账… | XML：`<title><des><url><type>`，`<refermsg>` 是被引用的原消息 |
| 50 | 语音/视频通话 | `<voipmsg>`，`<![CDATA[通话时长 07:10]]>` |
| 10000 | 系统消息 | 明文（撤回提示、加好友提示等） |

完整 `local_type` 会带上高位标记（例如 `244813135921`），**取低 16 位**再判断。

## 本地媒体文件格式

### 语音（SILK v3）
`VoiceInfo.voice_data` = `\x02` + `#!SILK_V3…`（注意开头那个 `0x02`）。
`pysilk.decode(blob, 24000)` 能直接吃整个 blob（**不要**去掉前导字节），得到 24kHz 单声道 16bit PCM，再用 `lameenc` 编码成 mp3。

### 图片（V2 `.dat`，**建议绕开**）
```
[0:6]   = 07 08 56 32 08 07        # 魔数
[6:10]  = aes_size (LE uint32)
[10:14] = xor_size (LE uint32)
[14]    = 01                        # 填充标记
[15:15+aligned_aes_size] = AES-128-ECB 密文（aligned = aes_size 向上补齐到 16 的倍数）
[中间]  = 明文
[末尾 xor_size 字节] = XOR（每文件密钥不同，可用结尾 JPEG 的 FFD9 反推）
```
第一段 AES 用的是微信内的一把 16 字节密钥，**只在「显示图片」的瞬间存在内存里**，实测极难稳定抓到。
因此正确做法是让微信自己导出原图：见 `scripts/save_images_ui.ps1`。

### `hardlink.db.dir2id`
`dir1/dir2` 是数字索引，映射到具体的「账号哈希目录」和「年-月」目录，用来拼出 `msg\attach\<dir1>\<dir2>\Img\<file_name>` 的完整路径。
