#!/usr/bin/env node
// 实测 fetch_content 结果里每个画质在源地址上的真实大小，选出最大的那个。
// 后端给的分辨率 / 体积不可靠（实测「original 1080P 66MB」实际指向 576P 的 6MB 文件），
// 所以以 CDN 返回的 Content-Range 总字节数为准。零第三方依赖（Node 18+ 自带 fetch）。
//
// 用法:
//   node pick-variant.mjs <fetch_content 输出的 JSON 文件>
// 输出:
//   stderr: 各画质实测表
//   stdout: {"variant_key":..., "bytes":..., "width":..., "height":..., "variants":[...]}
//           没有画质可选（文章、图文等）时输出 {"variant_key":null,"variants":[]}
// 退出码: 0 成功；2 参数或文件错误；3 所有画质都探测失败

import { readFileSync } from "node:fs";

const UA = "MicroMessenger/8.0.49"; // 微信 CDN 不带这个 UA 会拒绝
const PROBE_TIMEOUT_MS = 15_000;

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}

// 收集所有带 variant_key 和 url 的对象，按 variant_key 去重
function collectVariants(node, out = new Map()) {
  if (Array.isArray(node)) {
    for (const v of node) collectVariants(v, out);
  } else if (node && typeof node === "object") {
    if (node.variant_key && typeof node.url === "string" && node.url && !out.has(node.variant_key)) {
      out.set(node.variant_key, {
        variant_key: node.variant_key,
        spec: node.spec ?? null,
        width: node.width ?? null,
        height: node.height ?? null,
        reported_bytes: node.size || null,
        url: node.url,
      });
    }
    for (const v of Object.values(node)) collectVariants(v, out);
  }
  return out;
}

// 只请求第 1 个字节，从 Content-Range 读总大小
async function probeBytes(url) {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": UA, Range: "bytes=0-0" },
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    res.body?.cancel().catch(() => {});
    const range = res.headers.get("content-range");
    const total = range && Number(range.split("/")[1]);
    if (Number.isFinite(total) && total > 0) return total;
    const len = Number(res.headers.get("content-length"));
    return res.status === 200 && len > 1 ? len : null;
  } catch {
    return null;
  }
}

const file = process.argv[2];
if (!file) die(2, "用法: pick-variant.mjs <fetch_content 输出的 JSON 文件>");
let data;
try { data = JSON.parse(readFileSync(file, "utf8")); }
catch (e) { die(2, `读取 ${file} 失败: ${e.message}`); }

const variants = [...collectVariants(data).values()];
if (variants.length === 0) {
  console.log(JSON.stringify({ variant_key: null, variants: [] }));
  process.exit(0);
}

// CDN 偶尔超时，失败的再探测一次
await Promise.all(variants.map(async (v) => {
  v.bytes = (await probeBytes(v.url)) ?? (await probeBytes(v.url));
}));

// 实测成功的一律排在探测失败的前面：后端报的大小不可信，只在全部探测失败时才用来排序。
// 同样大时取像素多的
const effective = (v) => v.bytes ?? v.reported_bytes ?? 0;
variants.sort((a, b) =>
  (b.bytes ? 1 : 0) - (a.bytes ? 1 : 0) ||
  effective(b) - effective(a) ||
  (b.width * b.height || 0) - (a.width * a.height || 0));

for (const v of variants) {
  const mb = v.bytes ? (v.bytes / 1048576).toFixed(1) + " MB" : "探测失败";
  console.error(`${v.variant_key.padEnd(10)} 标称 ${v.width}x${v.height}  实测 ${mb}`);
}
if (variants.every((v) => !v.bytes && !v.reported_bytes)) die(3, "所有画质都探测失败");

const best = variants[0];
console.log(JSON.stringify({
  variant_key: best.variant_key,
  bytes: best.bytes,
  width: best.width,
  height: best.height,
  variants: variants.map(({ url, ...rest }) => rest),
}));
