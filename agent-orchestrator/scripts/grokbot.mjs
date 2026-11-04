#!/usr/bin/env node
// 操作本机 Grok Bot 桌面端（macOS）：发消息 + 读回复，代替「人工转交」。
//
// Grok Bot 没有 CLI / API，也没有带参数的 deep link，所以：
//   发送：剪贴板 + System Events 粘贴回车（终端需要「辅助功能」权限）；粘贴前确认前台就是 Grok Bot，否则中止；发送后恢复原剪贴板
//   读取：桌面端把对话同步在 ~/Library/Application Support/Grok Bot/sand-client-persistence 下的 transcript blob（文件名是 base32 编码的键）
// 发送会把 Grok Bot 窗口切到前台，用户正在打字时可能切不过去，脚本会报错而不是把消息粘到别的窗口。
//
// 用法：
//   node grokbot.mjs send "消息"      发送并等待回复（默认最多 10 分钟）
//   node grokbot.mjs send @brief.md   从文件读消息
//   node grokbot.mjs tail [条数]      打印最近几条对话
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const DIR = join(homedir(), 'Library/Application Support/Grok Bot/sand-client-persistence');
const APP = 'Grok Bot';

function decodeKey(file) {
  const s = file.replace(/\.blob$/, '').toUpperCase();
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of s) {
    const i = alphabet.indexOf(ch);
    if (i < 0) return '';
    bits += i.toString(2).padStart(5, '0');
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes).toString('utf8');
}

/** 最近的对话条目：{ id, ts, who: 'me' | 'grok' | 其他 kind, text } */
function readEntries() {
  const file = readdirSync(DIR).find((f) => f.endsWith('.blob') && decodeKey(f).includes('.transcript.replicas.'));
  if (!file) throw new Error('没找到 Grok Bot 对话记录，确认桌面端已登录并打开过对话');
  return JSON.parse(readFileSync(join(DIR, file), 'utf8')).value.entries.map((e) => ({
    id: e.id,
    ts: e.timestampMs,
    who: e.kind === 'message' && e.role === 'user' ? 'me' : e.kind === 'send-message' ? 'grok' : e.kind,
    text: e.content ?? e.message?.content ?? '',
  }));
}

const osa = (script) => execFileSync('osascript', ['-e', script], { encoding: 'utf8' }).trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * 发送一条消息并等待回复：先确认消息出现在对话里，再收集 Grok 的回复；回复后 quietMs 内无新消息视为说完。
 * @returns {Promise<{ id, ts, text }[]>}
 */
async function sendToGrokBot(text, { timeoutMs = 10 * 60_000, quietMs = 20_000, onReply = null } = {}) {
  const before = new Set(readEntries().map((e) => e.id));
  const sentAt = Date.now();
  const saved = execFileSync('pbpaste', { encoding: 'utf8' });
  try {
    execFileSync('pbcopy', { input: text });
    osa(`tell application "${APP}" to activate`);
    await sleep(800);
    // 粘贴前必须确认前台就是 Grok Bot：用户正在别的窗口打字时 activate 可能不生效，
    // 盲目粘贴 + 回车会把消息发进用户当前的应用（实测发生过：被粘进了 Claude Code 终端）
    const front = osa('tell application "System Events" to get name of first application process whose frontmost is true');
    if (front !== APP) throw new Error(`Grok Bot 没能切到前台（当前前台：${front}），已取消发送，请稍后重试`);
    osa('tell application "System Events" to keystroke "v" using command down');
    await sleep(500);
    osa('tell application "System Events" to key code 36');
  } finally {
    await sleep(500);
    execFileSync('pbcopy', { input: saved });
  }

  let delivered = false;
  let lastReplyAt = 0;
  const replies = [];
  while (Date.now() - sentAt < timeoutMs) {
    await sleep(3000);
    for (const e of readEntries()) {
      if (before.has(e.id)) continue;
      before.add(e.id);
      if (e.who === 'me') delivered = true;
      if (e.who === 'grok') {
        replies.push({ id: e.id, ts: e.ts, text: e.text });
        lastReplyAt = Date.now();
        onReply?.(e);
      }
    }
    if (!delivered && Date.now() - sentAt > 30_000) throw new Error('30 秒内对话里没出现这条消息：输入框可能没聚焦，请看一眼 Grok Bot 窗口');
    if (replies.length && Date.now() - lastReplyAt > quietMs) return replies;
  }
  if (!replies.length) throw new Error(`消息已送达，但 ${timeoutMs / 60_000} 分钟内没有回复`);
  return replies;
}

const fmt = (ts) => new Date(ts).toLocaleString('zh-CN', { hour12: false });
const [cmd, arg] = process.argv.slice(2);
if (cmd === 'send' && arg) {
  const text = arg.startsWith('@') ? readFileSync(arg.slice(1), 'utf8') : arg;
  sendToGrokBot(text, { onReply: (e) => console.log(`\n[grok ${fmt(e.ts)}]\n${e.text}`) })
    .catch((e) => { console.error(e.message); process.exit(1); });
} else if (cmd === 'tail') {
  for (const e of readEntries().slice(-(Number(arg) || 5))) console.log(`\n[${e.who} ${fmt(e.ts)}]\n${e.text}`);
} else {
  console.error('用法：grokbot.mjs send "消息" | send @file | tail [n]');
  process.exit(1);
}
