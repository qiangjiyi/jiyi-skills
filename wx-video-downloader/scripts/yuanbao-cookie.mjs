#!/usr/bin/env node
// 从本机 Chrome 读取 yuanbao.tencent.com 的登录 Cookie，以 CookieCloud 格式推给本机后端
// （POST http://127.0.0.1:2022/api/cookies/update），免去「Chrome 打开元宝让代理抓 Cookie」这一步。
// 只读 Chrome 的 Cookies 库副本；Cookie 值只发往本机回环地址，不打印、不落盘。
// 首次运行 macOS 会弹窗请求访问钥匙串「Chrome Safe Storage」，点「始终允许」后不再询问。
//
// 用法: node yuanbao-cookie.mjs        退出码 0=已推送，1=失败（原因见 stderr）
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { createDecipheriv, createHash, pbkdf2Sync } from "node:crypto";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

const DOMAIN = "yuanbao.tencent.com";
const CHROME_DIR = join(homedir(), "Library/Application Support/Google/Chrome");
const API = "http://127.0.0.1:2022/api/cookies/update";

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

function chromeKey() {
  let pass;
  try {
    pass = execFileSync("security", ["find-generic-password", "-w", "-s", "Chrome Safe Storage"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    fail("读取钥匙串「Chrome Safe Storage」失败（弹窗被拒绝或未安装 Chrome）");
  }
  return pbkdf2Sync(pass, "saltysalt", 1003, 16, "sha1");
}

function decrypt(buf, key, hostKey, dbVersion) {
  if (!buf || buf.length === 0) return "";
  const prefix = buf.subarray(0, 3).toString();
  if (prefix !== "v10" && prefix !== "v11") return buf.toString();
  const d = createDecipheriv("aes-128-cbc", key, Buffer.alloc(16, " "));
  let out = Buffer.concat([d.update(buf.subarray(3)), d.final()]);
  // Chrome 130+（Cookies 库 meta.version >= 24）在明文前加了 32 字节的 SHA256(host_key)
  if (dbVersion >= 24 && out.length >= 32 &&
      out.subarray(0, 32).equals(createHash("sha256").update(hostKey).digest())) {
    out = out.subarray(32);
  }
  return out.toString("utf8");
}

function readProfile(cookieFile, key) {
  const tmp = mkdtempSync(join(tmpdir(), "yb-cookie-"));
  try {
    const db = join(tmp, "Cookies");
    copyFileSync(cookieFile, db);
    if (existsSync(cookieFile + "-wal")) copyFileSync(cookieFile + "-wal", db + "-wal");
    const conn = new DatabaseSync(db, { readOnly: true });
    const ver = Number(conn.prepare("SELECT value FROM meta WHERE key='version'").get()?.value || 0);
    const stmt = conn.prepare(
      "SELECT host_key, name, path, encrypted_value, value, is_secure, is_httponly, expires_utc " +
      "FROM cookies WHERE host_key = ? OR host_key = ? OR host_key = '.tencent.com'"
    );
    stmt.setReadBigInts(true); // expires_utc 超出 Number 安全范围
    const rows = stmt.all(DOMAIN, "." + DOMAIN);
    conn.close();
    return rows.map((r) => ({
      domain: r.host_key,
      name: r.name,
      value: r.value || decrypt(Buffer.from(r.encrypted_value), key, r.host_key, ver),
      path: r.path,
      secure: !!r.is_secure,
      httpOnly: !!r.is_httponly,
      // Chrome 时间戳是 1601-01-01 起的微秒
      expirationDate: r.expires_utc ? Number(r.expires_utc / 1000000n) - 11644473600 : undefined,
    })).filter((c) => c.value);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

if (!existsSync(CHROME_DIR)) fail("未找到 Chrome 用户目录");
const profiles = readdirSync(CHROME_DIR)
  .filter((p) => p === "Default" || p.startsWith("Profile "))
  .map((p) => ({ name: p, file: [join(CHROME_DIR, p, "Network/Cookies"), join(CHROME_DIR, p, "Cookies")].find(existsSync) }))
  .filter((p) => p.file);

const key = chromeKey();
// 取元宝 Cookie 最多的 profile（即实际登录元宝的那个）
let best = { name: "", cookies: [] };
for (const p of profiles) {
  // 登录态 hy_token / hy_user 挂在 .tencent.com 上，其余在 yuanbao.tencent.com
  const cookies = readProfile(p.file, key)
    .filter((c) => c.domain.includes("yuanbao") || c.name.startsWith("hy_"));
  if (cookies.length > best.cookies.length) best = { name: p.name, cookies };
}
if (!best.cookies.some((c) => c.name === "hy_token")) fail("Chrome 中没有 yuanbao.tencent.com 的 Cookie，请先在 Chrome 登录元宝（微信扫码）");

const resp = await fetch(API, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  // 明文 CookieCloud 格式：三个字段缺一不可，否则报「CookieCloud 数据格式错误」
  body: JSON.stringify({
    cookie_data: { [DOMAIN]: best.cookies },
    local_storage_data: {},
    update_time: new Date().toISOString(),
  }),
});
const text = await resp.text();
if (!resp.ok || /"action":"error"/.test(text)) fail(`后端拒绝 Cookie 更新: ${text.slice(0, 200)}`);
console.log(`已从 Chrome「${best.name}」推送 ${best.cookies.length} 个元宝 Cookie 到后端`);
