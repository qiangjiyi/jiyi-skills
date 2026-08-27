const MAX_HTML_BYTES = 1_000_000;

const FORBIDDEN_TAGS = /<\s*\/?\s*(?:html|head|body|script|style|div|svg|figure|figcaption)\b/i;
const FORBIDDEN_ATTRIBUTES = /\b(?:class|id)\s*=/i;
const FORBIDDEN_CSS = /(?:\bposition\s*:\s*(?:fixed|absolute|sticky|outside)\b|\bfloat\s*:|\bdisplay\s*:\s*grid\b|@(?:media|keyframes)\b|--[a-z0-9_-]+\s*:)/i;
const MALFORMED_IMAGE_QUOTE = /<img\b[^>]*\bsrc\s*=\s*[“”‘’]/i;

function decodeHtmlAttribute(value) {
  return String(value)
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">");
}

function imageSources(html, errors) {
  const sources = [];
  for (const match of html.matchAll(/<img\b[^>]*>/gis)) {
    const tag = match[0];
    if (/\bsrc\s*=\s*[“”‘’]/i.test(tag)) {
      errors.push("<img> src attributes must use ASCII quotes");
      continue;
    }
    const src = tag.match(/(?:^|\s)src\s*=\s*(?:(['"])(.*?)\1|([^\s>]+))/is);
    if (!src || !(src[2] || src[3])) {
      errors.push("every <img> must have a non-empty src");
      continue;
    }
    sources.push(decodeHtmlAttribute(src[2] || src[3]));
  }
  return sources;
}

export function inspectWechatHtml(html) {
  const value = String(html ?? "");
  const errors = [];
  if (!value.trim()) errors.push("article HTML is empty");
  if (Buffer.byteLength(value, "utf8") > MAX_HTML_BYTES) {
    errors.push(`article HTML exceeds ${MAX_HTML_BYTES} bytes`);
  }
  if (/<\s*\/?\s*(?:!doctype|html|head|body)\b/i.test(value)) {
    errors.push("article HTML must be a body fragment");
  }
  if (FORBIDDEN_TAGS.test(value)) {
    errors.push("article HTML contains a WeChat-incompatible tag (html/head/body/script/style/div/svg/figure/figcaption)");
  }
  const markup = [...value.matchAll(/<[^>]+>/g)].map((match) => match[0]).join("\n");
  if (FORBIDDEN_ATTRIBUTES.test(markup)) {
    errors.push("article HTML must not contain class or id attributes");
  }
  if (FORBIDDEN_CSS.test(markup)) {
    errors.push("article HTML contains unsupported CSS (position/float/grid/media/keyframes/CSS variables)");
  }
  if (MALFORMED_IMAGE_QUOTE.test(value)) {
    errors.push("article HTML contains a malformed <img> src attribute");
  }
  const placeholder = value.match(/\{\{[^}]+\}\}|【[^】]*(?:待补|插入)[^】]*】/);
  if (placeholder) errors.push(`article HTML contains unresolved placeholder: ${placeholder[0]}`);
  const sources = imageSources(value, errors);
  return { ok: errors.length === 0, errors: [...new Set(errors)], sources };
}

export function assertWechatHtml(html) {
  const inspection = inspectWechatHtml(html);
  if (!inspection.ok) throw new Error(inspection.errors.join("; "));
  return inspection.sources;
}
