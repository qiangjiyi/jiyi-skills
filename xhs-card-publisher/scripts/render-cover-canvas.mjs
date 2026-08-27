#!/usr/bin/env node
import { copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";

const exec = promisify(execFile);
const args = process.argv.slice(2);
const value = (key) => args[args.indexOf(key) + 1];
const output = value("--output");
if (!output) throw new Error("Usage: --output <cover.png>");

const root = path.dirname(new URL(import.meta.url).pathname);
const temp = `${output}.tmp`;
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
await rm(temp, { recursive: true, force: true });
await mkdir(temp, { recursive: true });
await copyFile(path.join(root, "..", "assets", "LXGWWenKai-Regular.ttf"), path.join(temp, "LXGWWenKai-Regular.ttf"));

const html = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;overflow:hidden;background:#F9F5EE}canvas{display:block}</style><canvas id="cover" width="1728" height="2304"></canvas><script>
const c=document.querySelector('#cover'),x=c.getContext('2d');
const W=1728,H=2304; const C={ink:'#17243A',muted:'#637087',paper:'#F9F5EE',white:'#FFFFFF',yellow:'#FFE8A3',blue:'#BFE0FF',pink:'#FFD1D7',red:'#F2706A',cobalt:'#3C73D9',line:'#D9D6D0'};
const font='"LXGW WenKai", "PingFang SC", sans-serif';
const sans='"PingFang SC", "Hiragino Sans GB", sans-serif';
function rr(a,b,w,h,r,fill,stroke){x.beginPath();x.roundRect(a,b,w,h,r);if(fill){x.fillStyle=fill;x.fill()}if(stroke){x.lineWidth=3;x.strokeStyle=stroke;x.stroke()}}
function text(t,a,b,size,color,weight='400',family=font){x.fillStyle=color;x.font=weight+' '+size+'px '+family;x.textBaseline='alphabetic';x.fillText(t,a,b)}
function wrap(t,a,b,max,size,lh,color,weight='400',family=font){x.font=weight+' '+size+'px '+family;let line='',y=b;for(const ch of t){if(x.measureText(line+ch).width>max&&line){text(line,a,y,size,color,weight,family);line=ch;y+=lh}else line+=ch}if(line)text(line,a,y,size,color,weight,family);return y}
function dot(a,b,r,color){x.beginPath();x.arc(a,b,r,0,Math.PI*2);x.fillStyle=color;x.fill()}
async function draw(){const f=new FontFace('LXGW WenKai','url(LXGWWenKai-Regular.ttf)');await f.load();document.fonts.add(f);x.fillStyle=C.paper;x.fillRect(0,0,W,H);
// restrained background geometry
rr(110,112,1508,2080,58,C.white);rr(134,138,1458,2028,46,null,C.line);dot(1458,226,10,C.red);dot(1500,226,10,C.yellow);dot(1542,226,10,C.blue);
// header
rr(190,222,78,78,24,C.ink);text('吉',211,278,40,C.white,'700',sans);text('吉义AI',292,274,42,C.ink,'700',sans);rr(488,238,104,40,20,C.blue);text('AI',516,267,23,C.ink,'700',sans);text('AI Agent · 记忆系统',190,360,30,C.muted,'500',sans);
// eyebrow and title
rr(190,452,210,54,27,C.yellow);text('一次讲清',226,490,28,C.ink,'700',sans);
text('复杂项目跨会话，',190,650,94,C.ink,'700',sans);text('AI 到底该记住什么？',190,780,94,C.ink,'700',sans);
wrap('别把所有信息，塞进同一个记忆盒子。',196,868,1250,42,64,C.muted,'400',font);
// rule
x.strokeStyle=C.ink;x.lineWidth=5;x.lineCap='round';x.beginPath();x.moveTo(190,960);x.lineTo(1538,960);x.stroke();
// Three conceptual cards
const cards=[
  {n:'01',tag:'短期记忆',sub:'这一轮，正在干什么',fill:C.yellow,icon:'工作台',y:1060},
  {n:'02',tag:'长期记忆',sub:'跨会话，仍要保留什么',fill:C.blue,icon:'档案柜',y:1372},
  {n:'03',tag:'Skills',sub:'同类任务，以后怎么做',fill:C.pink,icon:'操作手册',y:1684}
];
for(const card of cards){rr(190,card.y,1348,246,32,C.white,C.line);rr(216,card.y+26,150,194,26,card.fill);text(card.n,248,card.y+90,32,C.ink,'700',sans);text(card.icon,242,card.y+160,34,C.ink,'700',font);text(card.tag,410,card.y+94,56,C.ink,'700',sans);text(card.sub,412,card.y+164,35,C.muted,'400',font);rr(1356,card.y+85,116,66,33,C.ink);text('→',1391,card.y+131,40,C.white,'700',sans)}
// conclusion
rr(190,2028,1348,108,30,C.ink);text('项目事实 → 长期记忆   可复用流程 → Skills',240,2097,38,C.white,'500',font);
document.documentElement.dataset.ready='true'} draw();
</script>`;
const page = path.join(temp, "cover.html");
await writeFile(page, html);
await exec(chrome, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1728,2304", `--screenshot=${path.resolve(output)}`, `file://${page}`], { maxBuffer: 8 * 1024 * 1024 });
await rm(temp, { recursive: true, force: true });
