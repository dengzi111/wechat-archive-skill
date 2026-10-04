#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解密微信 4.x (SQLCipher 4) 的数据库。

用法:
    python decrypt_db.py --db-dir <db_storage 或副本> --key <64位hex passphrase> --out <输出目录>
    python decrypt_db.py --db-dir ... --key ... --verify-only      # 只校验密钥对不对

参数依据（微信 4.x / WCDB / SQLCipher 4）:
    page_size = 4096, reserve = 80 (IV16 + HMAC64)
    KDF = PBKDF2-HMAC-SHA512, 256000 轮, salt = 每个库文件前 16 字节
    mac_key = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2 轮, 32 字节)
    第 1 页布局: [0:16]=salt [16:4016]=密文 [4016:4032]=IV [4032:4096]=HMAC-SHA512

注意: 微信 3.9 用的是 SHA1 / 64000 轮 / reserve 48，与本脚本不兼容。
"""
import argparse
import glob
import hashlib
import hmac
import os
import struct
import sys

from Crypto.Cipher import AES

PAGE = 4096
IV_OFF = 4016
HMAC_OFF = 4032


def derive(salt: bytes, passphrase: bytes) -> tuple:
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)
    return enc_key, mac_key


def page1_ok(data: bytes, passphrase: bytes) -> bool:
    if len(data) < PAGE:
        return False
    salt = data[:16]
    enc_key, mac_key = derive(salt, passphrase)
    mac = hmac.new(mac_key, data[16:HMAC_OFF] + struct.pack("<I", 1), hashlib.sha512).digest()
    if not hmac.compare_digest(mac, data[HMAC_OFF:PAGE]):
        return False
    iv = data[IV_OFF:HMAC_OFF]
    pt = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(data[16:IV_OFF])
    return pt.startswith(b"SQLite format 3\x00")


def decrypt_file(path: str, out_path: str, passphrase: bytes) -> bool:
    data = open(path, "rb").read()
    if len(data) < PAGE:
        return False
    salt = data[:16]
    enc_key, mac_key = derive(salt, passphrase)
    out = bytearray()
    n = len(data) // PAGE
    for i in range(n):
        pg = data[i * PAGE:(i + 1) * PAGE]
        pgno = i + 1
        if pgno == 1:
            mac = hmac.new(mac_key, pg[16:HMAC_OFF] + struct.pack("<I", pgno), hashlib.sha512).digest()
            if not hmac.compare_digest(mac, pg[HMAC_OFF:PAGE]):
                return False
            iv = pg[IV_OFF:HMAC_OFF]
            body = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(pg[16:IV_OFF])
            out += b"SQLite format 3\x00" + body + b"\x00" * 80
        else:
            mac = hmac.new(mac_key, pg[:HMAC_OFF] + struct.pack("<I", pgno), hashlib.sha512).digest()
            if not hmac.compare_digest(mac, pg[HMAC_OFF:PAGE]):
                return False
            iv = pg[IV_OFF:HMAC_OFF]
            out += AES.new(enc_key, AES.MODE_CBC, iv).decrypt(pg[:IV_OFF]) + b"\x00" * 80
    tail = data[n * PAGE:]
    if tail:
        out += tail
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "wb").write(bytes(out))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", required=True, help="db_storage 目录（建议用副本）")
    ap.add_argument("--key", required=True, help="64 位 hex passphrase")
    ap.add_argument("--out", default="decrypted", help="输出目录")
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()

    key = a.key.strip().lower()
    if len(key) != 64:
        sys.exit("key 必须是 64 位 hex")
    passphrase = bytes.fromhex(key)

    dbs = sorted(glob.glob(os.path.join(a.db_dir, "**", "*.db"), recursive=True))
    # 跳过 wal/shm
    dbs = [p for p in dbs if not p.endswith(("-wal", "-shm"))]
    if not dbs:
        sys.exit("没找到 .db 文件")

    ok_first = [p for p in dbs if page1_ok(open(p, "rb").read(4096), passphrase)]
    print(f"共 {len(dbs)} 个库，密钥校验通过 {len(ok_first)} 个")
    if not ok_first:
        sys.exit("密钥不对（HMAC 校验全部失败）")
    if a.verify_only:
        return

    success = fail = 0
    for p in dbs:
        rel = os.path.relpath(p, a.db_dir)
        outp = os.path.join(a.out, rel)
        try:
            if decrypt_file(p, outp, passphrase):
                success += 1
            else:
                fail += 1
                print("  跳过（校验失败）:", rel)
        except Exception as e:
            fail += 1
            print("  失败:", rel, e)
    print(f"完成: 成功 {success}，失败 {fail} -> {a.out}")


if __name__ == "__main__":
    main()
