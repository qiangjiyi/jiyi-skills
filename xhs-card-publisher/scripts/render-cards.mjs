#!/usr/bin/env node
import { execFile } from "node:child_process";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { promisify } from "node:util";
import path from "node:path";
import { pathToFileURL } from "node:url";

const exec = promisify(execFile);
const argv = process.argv.slice(2);
function value(key) {
  const i = argv.indexOf(key);
  if (i < 0) return undefined;
  const next = argv[i + 1];
  if (!next || next.startsWith("--")) return undefined;
  return next;
}
const input = value("--input");
const output = value("--output");
const title = value("--title") || "未命名文章";
const name = value("--name") || "吉义AI";
const bio = value("--bio") || "关注我，把 AI 变成你的生产力";
const avatar = value("--avatar");
if (!input || !output) throw new Error("Usage: --input <article.md> --output <cards-dir> [--title <title>] [--name <name>] [--bio <profile-text>] [--avatar <path>]");

const skillRoot = path.dirname(path.dirname(new URL(import.meta.url).pathname));
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const rawSource = await readFile(path.resolve(input), "utf8");

// 去掉开头 YAML frontmatter（--- ... ---），避免 frontmatter 字段被当成正文渲染
const strippedSource = rawSource.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");

const inputAbs = path.resolve(input);
const inputDir = path.dirname(inputAbs);

function resolveImage(src) {
  if (/^(?:file|https?|data):/i.test(src)) return src;
  const abs = path.isAbsolute(src) ? src : path.resolve(inputDir, src);
  return pathToFileURL(abs).href;
}

const temp = path.join(path.resolve(output), ".render-tmp");
await rm(temp, { recursive: true, force: true });
await mkdir(temp, { recursive: true });
await mkdir(path.resolve(output), { recursive: true });
await copyFile(path.join(skillRoot, "assets", "LXGWWenKai-Regular.ttf"), path.join(temp, "LXGWWenKai-Regular.ttf"));

const avatarAbs = avatar
  ? (path.isAbsolute(avatar) ? avatar : path.resolve(process.cwd(), avatar))
  : path.join(skillRoot, "assets", "default-avatar.jpg");
const avatarExt = path.extname(avatarAbs) || ".jpg";
const avatarInTemp = path.join(temp, "avatar" + avatarExt);
await copyFile(avatarAbs, avatarInTemp);
const avatarUrl = pathToFileURL(avatarInTemp).href;

function escapeHtml(text) { return String(text).replace(/[&<>"]/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c])); }
function inline(text) {
  let s = escapeHtml(text);
  s = s.replace(/\{\{bg:(#[0-9a-fA-F]{3,8})\|([\s\S]*?)\}\}/g, '<mark style="--mark:$1">$2</mark>');
  s = s.replace(/\{\{color:(#[0-9a-fA-F]{3,8})\|([\s\S]*?)\}\}/g, '<span style="color:$1">$2</span>');
  s = s.replace(/\{\{underline:(solid|dashed)\|([\s\S]*?)\}\}/g, '<span class="under $1">$2</span>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
  return s;
}
function splitTableRow(line) {
  const value = String(line || "").trim();
  if (!value.includes("|")) return null;
  const input = value.replace(/^\|/, "").replace(/\|$/, "");
  const cells = []; let cell = ""; let markerDepth = 0;
  for (let index = 0; index < input.length; index += 1) {
    if (input.startsWith("{{", index)) { markerDepth += 1; cell += "{{"; index += 1; continue; }
    if (input.startsWith("}}", index) && markerDepth) { markerDepth -= 1; cell += "}}"; index += 1; continue; }
    if (input[index] === "|" && markerDepth === 0) { cells.push(cell.trim()); cell = ""; continue; }
    cell += input[index];
  }
  cells.push(cell.trim());
  return cells.length > 1 ? cells : null;
}
function isTableDivider(line) {
  const cells = splitTableRow(line);
  return Boolean(cells?.length) && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}
function markdownToBlocks(markdown) {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n"); const blocks = []; let paragraph=[]; let list=[];
  const flush = () => { if(paragraph.length) blocks.push(`<p>${inline(paragraph.join(" "))}</p>`); paragraph=[]; if(list.length) blocks.push(`<ul>${list.map(x=>`<li>${inline(x)}</li>`).join("")}</ul>`), list=[]; };
  for (let index = 0; index < lines.length; index += 1) { const line=lines[index].trim(); if (!line) { flush(); continue; }
    const header = splitTableRow(line);
    if (header && isTableDivider(lines[index + 1])) {
      flush(); const rows = []; index += 2;
      while (index < lines.length) { const row = splitTableRow(lines[index]); if (!row) break; rows.push(row); index += 1; }
      index -= 1;
      const columns = Math.max(header.length, ...rows.map((row) => row.length));
      const cells = (row, tag) => Array.from({ length: columns }, (_, column) => `<${tag}>${inline(row[column] || "")}</${tag}>`).join("");
      blocks.push(`<section class="md-table"><table><thead><tr>${cells(header, "th")}</tr></thead><tbody>${rows.map((row) => `<tr>${cells(row, "td")}</tr>`).join("")}</tbody></table></section>`);
      continue;
    }
    const image=line.match(/^!\[([^\]]*)\]\(([^)]+)\)$/); if(image){ flush(); blocks.push(`<figure><img src="${escapeHtml(resolveImage(image[2]))}" alt="${escapeHtml(image[1])}"></figure>`); continue; }
    const heading=line.match(/^(#{1,3})\s+(.+)$/); if(heading){ flush(); const n=heading[1].length; blocks.push(`<h${n}>${inline(heading[2])}</h${n}>`); continue; }
    if(line.startsWith("> ")){ flush(); blocks.push(`<blockquote>${inline(line.slice(2))}</blockquote>`); continue; }
    const item=line.match(/^[-*+]\s+(.+)$/); if(item){ if(paragraph.length) flush(); list.push(item[1]); continue; }
    paragraph.push(line);
  } flush(); return blocks.join("\n");
}
const body = markdownToBlocks(strippedSource);
const avatarUrlJson = JSON.stringify(avatarUrl);
const nameJson = JSON.stringify(name);
const bioJson = JSON.stringify(bio);
const html = `<!doctype html><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>
@font-face{font-family:"LXGW WenKai";src:url("LXGWWenKai-Regular.ttf") format("truetype");font-display:block}
*{box-sizing:border-box} html,body{margin:0;width:864px;background:#fff;font-family:"LXGW WenKai","PingFang SC","Hiragino Sans GB","Noto Sans CJK SC",sans-serif;color:#1d2a40} #source{position:absolute;left:-9999px;top:0;width:756px;height:auto;overflow:visible;visibility:hidden}.page{width:864px;min-height:1152px;height:auto;background:#fff;padding:34px 54px;overflow:visible;position:relative}.header{position:relative;height:82px;display:flex;align-items:center;gap:16px;margin:0 0 24px;font-family:"PingFang SC","Hiragino Sans GB",sans-serif}.avatar{width:72px;height:72px;border:2px solid #fff;border-radius:50%;background:#d8edc0;background-size:cover;background-position:center;box-shadow:0 3px 10px #1d2a4014;flex:0 0 72px}.byline{min-width:0;display:flex;flex-direction:column;justify-content:center;gap:5px}.identity{display:flex;align-items:center;gap:8px;min-width:0}.byline b{display:block;font-size:27px;font-weight:750;line-height:1.15;color:#1d2a40}.gold-badge{display:block;width:26px;height:26px;flex:0 0 26px;filter:drop-shadow(0 1px 1px #a6750026)}.gold-badge svg{display:block;width:100%;height:100%}.profile-bio{display:block;max-width:570px;color:#7b8492;font-family:"LXGW WenKai","PingFang SC",sans-serif;font-size:20px;font-weight:600;line-height:1.25;letter-spacing:.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.content{position:relative;min-height:976px;height:auto;overflow:visible;font-size:31px;line-height:1.78;letter-spacing:.018em}.content>*{break-inside:avoid;margin:0 0 14px}.content>*:first-child{margin-top:0}.content>*:last-child{margin-bottom:26px}.content p{margin-top:20px;margin-bottom:20px}.content p.continuation{margin-top:0}.content h1{position:relative;margin:34px 0 22px;padding:0 0 22px;font-family:"PingFang SC","Hiragino Sans GB",sans-serif;font-size:50px;line-height:1.32;font-weight:750;letter-spacing:-.02em}.content h2{display:table;margin:28px 0 18px;padding:7px 16px 8px;border-radius:12px;background:#fff0bd;color:#1d2a40;font-size:35px;line-height:1.38;font-weight:700}.content h3{margin:22px 0 10px;color:#315b9a;font-size:32px;line-height:1.45;font-weight:700}.content blockquote{margin:22px 0;padding:16px 20px;border:0;border-left:7px solid #f59e8b;border-radius:0 16px 16px 0;background:#fff7f2;color:#38465b}.content ul{margin:12px 0 18px;padding:16px 22px 16px 50px;border-radius:16px;background:#f1f6ff}.content li{padding-left:7px;margin:3px 0}.content li::marker{color:#f17869}.content figure{height:567px;margin:0;padding:0;border:0;border-radius:0;background:transparent;box-shadow:none}.content *:has(+ figure){margin-bottom:0!important}.content figure + *{margin-top:0!important}.content img{display:block;width:100%;height:567px;object-fit:contain;margin:auto;border-radius:0}.content .md-table{margin:22px 0 28px;border:1px solid #cfd9e5;border-radius:14px;overflow:hidden;background:#fff}.content table{width:100%;border-collapse:collapse;table-layout:fixed;font-family:"LXGW WenKai","PingFang SC","Hiragino Sans GB",sans-serif;font-size:20px;line-height:1.48;letter-spacing:.012em}.content th,.content td{padding:12px 10px;border-right:1px solid #cfd9e5;border-bottom:1px solid #cfd9e5;text-align:left;vertical-align:top;overflow-wrap:anywhere;word-break:break-word}.content th:last-child,.content td:last-child{border-right:0}.content tbody tr:last-child td{border-bottom:0}.content th{background:#e8f1fc;color:#1d2a40;font-size:21px;font-weight:800}.content td{background:#fff;color:#263550}.content strong{padding:0 2px;color:#17243a;font-weight:800;background:linear-gradient(transparent 58%,#ffe6a2 58%)}.content mark{padding:0 4px;border-radius:7px;background:var(--mark);color:inherit}.under{text-decoration-line:underline;text-decoration-color:#ef8c7b;text-decoration-thickness:4px;text-underline-offset:10px}.md-table .under{text-decoration-thickness:2px;text-underline-offset:3px;text-decoration-skip-ink:auto}.under.dashed{text-decoration-style:dashed}.page:not(.active){display:block;visibility:hidden;position:absolute;left:-9999px;top:0}</style><main id="source" class="content">${body}</main><main id="pages"></main><script>
const src=document.querySelector('#source'), host=document.querySelector('#pages'); const q=new URLSearchParams(location.search), only=Number(q.get('page')||0); const nodes=[...src.children];
function make(){const p=document.createElement('section');p.className='page';p.innerHTML='<header class="header"><span class="avatar" style="background-image:url(' + ${avatarUrlJson} + ')"></span><span class="byline"><span class="identity"><b>' + ${nameJson} + '</b><i class="gold-badge" aria-label="认证"><svg viewBox="0 0 32 32" aria-hidden="true"><path fill="#F6B800" d="M16 1 19.5 5.5 25.5 4 27 10 31 13.5 27.5 18 29 24 23 25.5 19.5 31 16 27.5 12.5 31 9 25.5 3 24 4.5 18 1 13.5 5 10 6.5 4 12.5 5.5Z"/><path d="m9.2 16.7 4.1 4.1 9.5-9.5" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg></i></span><span class="profile-bio">' + ${bioJson} + '</span></span></header><article class="content"></article>';host.append(p);return p.querySelector('.content')}
const BASE_CONTENT_HEIGHT=976, EXTENDED_CONTENT_HEIGHT=1464, BOTTOM_SAFE=26; const measuredHeight=(article)=>{const last=article.lastElementChild;if(!last)return 0;return Math.ceil(last.offsetTop+last.offsetHeight+BOTTOM_SAFE)}; const pointAt=(root,offset)=>{const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let left=offset,node;while(node=walker.nextNode()){if(left<=node.data.length)return [node,left];left-=node.data.length}return [root,root.childNodes.length]}; const paragraphPart=(node,start,end,continued)=>{const range=document.createRange(),a=pointAt(node,start),b=pointAt(node,end),part=document.createElement('p');range.setStart(a[0],a[1]);range.setEnd(b[0],b[1]);part.append(range.cloneContents());if(continued)part.className='continuation';return part}; const sentenceEnds=(text,start)=>{const ends=[];for(let i=start;i<text.length;i+=1)if(/[。！？]/.test(text[i]))ends.push(i+1);if(!ends.length||ends[ends.length-1]!==text.length)ends.push(text.length);return ends}; const fitEnd=(node,start,candidates,article)=>{let best=start;for(const end of candidates){const part=paragraphPart(node,start,end,start>0);article.append(part);const fits=measuredHeight(article)<=BASE_CONTENT_HEIGHT;article.removeChild(part);if(!fits)break;best=end}return best}; let content=make(); for(const node of nodes){if(node.tagName==='P'){const text=node.textContent;let start=0;while(start<text.length){const ends=sentenceEnds(text,start),best=fitEnd(node,start,ends,content);if(best>start){content.append(paragraphPart(node,start,best,start>0));start=best;if(start<text.length)content=make();continue}const nextPage=make(),nextBest=fitEnd(node,start,ends,nextPage);if(nextBest>start){content=nextPage;content.append(paragraphPart(node,start,nextBest,start>0));start=nextBest;if(start<text.length)content=make();continue}let low=start+1,high=ends[0],fallback=start;while(low<=high){const end=Math.floor((low+high)/2),part=paragraphPart(node,start,end,start>0);nextPage.append(part);if(measuredHeight(nextPage)<=BASE_CONTENT_HEIGHT){fallback=end;nextPage.removeChild(part);low=end+1}else{nextPage.removeChild(part);high=end-1}}if(fallback===start)throw new Error('单句超过标准卡片高度，请缩短内容。');content=nextPage;content.append(paragraphPart(node,start,fallback,start>0));start=fallback;if(start<text.length)content=make()}}else{const clone=node.cloneNode(true),isImage=node.tagName==='FIGURE',limit=isImage?EXTENDED_CONTENT_HEIGHT:BASE_CONTENT_HEIGHT;content.append(clone);if(measuredHeight(content)>limit){content.removeChild(clone);content=make();content.append(clone);if(measuredHeight(content)>BASE_CONTENT_HEIGHT)throw new Error(isImage?'3:4 配图超过单张卡片允许高度，请缩小配图或拆分文章。':'一个内容块超过标准卡片高度，请缩短内容或拆分表格。')}}} const pages=[...document.querySelectorAll('.page')]; const heights=pages.map((p)=>Math.max(1152,Math.ceil(measuredHeight(p.querySelector('.content'))+174))); pages.forEach((p,i)=>p.classList.toggle('active',!only||only===i+1)); document.documentElement.dataset.pages=pages.length; document.documentElement.dataset.pageHeights=heights.join(',');
</script>`;
const htmlPath = path.join(temp, "render.html"); await writeFile(htmlPath, html);
const chromeArgs = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2", "--window-size=864,1152"];
const dumped = await exec(chrome, [...chromeArgs, "--dump-dom", `file://${htmlPath}`], { maxBuffer: 20 * 1024 * 1024 });
const count = Number((dumped.stdout.match(/data-pages="(\d+)"/) || [])[1]); if (!count) throw new Error("Pagination failed: no card pages generated.");
const heights = (dumped.stdout.match(/data-page-heights="([\d,]+)"/) || [])[1]?.split(",").map(Number) || [];
if (heights.length !== count || heights.some((height) => !Number.isFinite(height) || height < 1152 || height > 1640)) throw new Error("Pagination failed: invalid page dimensions.");
for (let i=1; i<=count; i+=1) await exec(chrome, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2", `--window-size=864,${heights[i - 1]}`, `--screenshot=${path.join(path.resolve(output), `${String(i).padStart(2,"0")}.png`)}`, `file://${htmlPath}?page=${i}`], { maxBuffer: 20 * 1024 * 1024 });
const manifestPath = path.join(path.dirname(path.resolve(output)), "manifest.json");
let manifest={}; try { manifest=JSON.parse(await readFile(manifestPath,"utf8")); } catch {}
manifest={...manifest,status:"rendered",rendered_at:new Date().toISOString(),title,author:name,source:path.basename(input),cards:Array.from({length:count},(_,i)=>`cards/${String(i+1).padStart(2,"0")}.png`),dimensions:{width:1728,baseHeight:2304,maxHeight:3280,pageHeights:heights.map((height)=>height * 2)}};
await writeFile(manifestPath, JSON.stringify(manifest,null,2)+"\n"); await rm(temp,{recursive:true,force:true}); console.log(JSON.stringify({pages:count,output:path.resolve(output),manifest:manifestPath,name,avatar:pathToFileURL(avatarAbs).href}));
