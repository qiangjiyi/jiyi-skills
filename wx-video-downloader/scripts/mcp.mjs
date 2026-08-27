#!/usr/bin/env node
// wx-video-downloader 的轻量 MCP 客户端：对 http://127.0.0.1:2022/mcp 做
// streamable HTTP JSON-RPC 调用，零第三方依赖（Node 18+ 自带 fetch）。
//
// 用法:
//   node mcp.mjs tools                                  # 列出工具名
//   node mcp.mjs call <tool> '<json参数>'                # 调用工具并打印结果文本
//   node mcp.mjs call <tool>                            # 无参工具
// 环境变量:
//   WX_VIDEO_MCP_URL  覆盖端点（默认 http://127.0.0.1:2022/mcp）
//
// 退出码: 0 成功；2 网络/协议错误；3 工具返回 isError。
// 输出约定: 成功时把 result.content[0].text 原样打到 stdout（服务端给的就是 JSON 字符串）。

const MCP_URL = process.env.WX_VIDEO_MCP_URL || "http://127.0.0.1:2022/mcp";
const TIMEOUT_MS = Number(process.env.WX_VIDEO_TIMEOUT_MS || 320_000); // fetch_content 默认等 300s，留余量

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}

function parseArgs(argv) {
  const [cmd, tool, jsonArgs] = argv;
  if (cmd !== "tools" && cmd !== "call") {
    die(2, "用法: mcp.mjs tools | mcp.mjs call <tool> '<json参数>'");
  }
  let args = {};
  if (cmd === "call") {
    if (!tool) die(2, "缺少工具名");
    if (jsonArgs !== undefined) {
      try { args = JSON.parse(jsonArgs); }
      catch { die(2, `参数不是合法 JSON: ${jsonArgs}`); }
    }
  }
  return { cmd, tool, args };
}

// 解析响应：streamable HTTP 可能返回 application/json，也可能返回 text/event-stream
async function readRpc(res, id) {
  const ctype = res.headers.get("content-type") || "";
  if (ctype.includes("text/event-stream")) {
    const text = await res.text();
    for (const line of text.split("\n")) {
      if (!line.startsWith("data:")) continue;
      try {
        const obj = JSON.parse(line.slice(5).trim());
        if (obj.id === id) return obj;
      } catch { /* 跳过非 JSON data 行 */ }
    }
    return null;
  }
  return await res.json().catch(() => null);
}

async function rpc(body, { sessionId, protocolVersion } = {}) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  if (protocolVersion) headers["MCP-Protocol-Version"] = protocolVersion;
  let res;
  try {
    res = await fetch(MCP_URL, {
      method: "POST", headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    die(2, `无法连接 MCP 端点 ${MCP_URL}: ${e.message}\n（服务未启动？先跑 scripts/service.sh start）`);
  }
  const newSession = res.headers.get("mcp-session-id") || undefined;
  const obj = await readRpc(res, body.id);
  if (!obj) die(2, `MCP 响应无法解析（HTTP ${res.status}）`);
  if (obj.error) die(2, `MCP 错误 ${obj.error.code}: ${obj.error.message}`);
  return { result: obj.result, newSession };
}

async function main() {
  const { cmd, tool, args } = parseArgs(process.argv.slice(2));

  // 1) initialize 握手
  const init = await rpc({
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: {
      protocolVersion: "2025-03-26", capabilities: {},
      clientInfo: { name: "wx-video-downloader", version: "1.0.0" },
    },
  });
  const session = { sessionId: init.newSession, protocolVersion: init.result?.protocolVersion };

  // 2) initialized 通知（协议礼节；无状态服务端可忽略）
  try {
    await fetch(MCP_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json", Accept: "application/json, text/event-stream",
        ...(session.sessionId ? { "Mcp-Session-Id": session.sessionId } : {}),
      },
      body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch { /* 通知失败不阻塞 */ }

  // 3) tools 或 call
  if (cmd === "tools") {
    const r = await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list" }, session);
    for (const t of r.result.tools ?? []) console.log(t.name);
    return;
  }
  const r = await rpc({
    jsonrpc: "2.0", id: 3, method: "tools/call",
    params: { name: tool, arguments: args },
  }, session);
  const text = (r.result?.content ?? [])
    .filter((c) => c.type === "text").map((c) => c.text).join("\n");
  if (r.result?.isError) die(3, `工具执行失败:\n${text}`);
  if (!text) die(3, "工具无文本输出");
  console.log(text);
}

main().catch((e) => die(2, e.stack || String(e)));
