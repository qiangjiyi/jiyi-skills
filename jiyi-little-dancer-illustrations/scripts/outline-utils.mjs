export const REQUIRED_OUTLINE_FIELDS = [
  "放置锚点",
  "主题",
  "核心意思",
  "结构类型",
  "小舞伴动作",
  "表情与身体状态",
  "视线方向",
  "建议元素",
  "中文标注词",
  "构图与信息流",
];

const FIELD_PATTERN = /^\s*[-*]\s*([^：:]+)[：:]\s*(.+?)\s*$/;
const SHOT_PATTERN = /^##\s+Shot\s+(\d+)\s*[—–-]\s*(.+?)\s*$/;

export function parseOutline(content) {
  const lines = content.split(/\r?\n/);
  const headings = [];
  lines.forEach((line, index) => {
    const match = line.match(SHOT_PATTERN);
    if (match) headings.push({ number: Number(match[1]), title: match[2], line: index });
  });

  const errors = [];
  if (headings.length === 0) {
    errors.push("outline must contain headings like ## Shot 01 — ...");
    return { shots: [], errors };
  }
  if (headings.length > 9) errors.push(`outline contains ${headings.length} shots; maximum is 9`);

  const shots = headings.map((heading, index) => {
    const end = headings[index + 1]?.line ?? lines.length;
    const fields = {};
    for (const line of lines.slice(heading.line + 1, end)) {
      const match = line.match(FIELD_PATTERN);
      if (match) fields[match[1].trim()] = match[2].trim();
    }
    for (const field of REQUIRED_OUTLINE_FIELDS) {
      if (!fields[field]) errors.push(`Shot ${String(heading.number).padStart(2, "0")} missing field: ${field}`);
    }
    if (heading.number !== index + 1) errors.push(`shot numbers must be sequential; expected ${index + 1}, received ${heading.number}`);
    return { ...heading, fields };
  });

  return { shots, errors };
}

export function cleanAnchor(value) {
  return value
    .trim()
    .replace(/^正文中出现的精确短语[：:]\s*/, "")
    .replace(/^放在[“「『]?/, "")
    .replace(/[”」』]?之后[。.]?$/, "")
    .replace(/^[“「『]|[”」』]$/g, "")
    .trim();
}
