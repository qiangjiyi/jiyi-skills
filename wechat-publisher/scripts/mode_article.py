"""Article (news) mode: render markdown to themed HTML, upload body images,
rewrite the HTML with mmbiz URLs, upload a cover, and POST a news draft."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from lib.account import account_value, resolve_account
from lib.env_loader import merged_env
from lib.errors import PublishError
from lib.proxy_client import (
    add_draft,
    get_access_token,
    upload_body_image,
    upload_image,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
RENDER_SCRIPT = SKILL_DIR / "scripts" / "render_markdown.mjs"

# Body image <img> replacement style. We borrow this from baoyu-post-to-wechat's
# wechat-api.ts to keep the article's body look consistent with theirs.
BODY_IMG_STYLE = "display: block; width: 100%; margin: 1.5em auto;"


def _run_renderer(args, markdown_path: Path, base_dir: Path) -> dict:
    """Invoke render_markdown.mjs and return its parsed JSON output."""
    cmd = ["node", str(RENDER_SCRIPT)]
    if args.theme:
        cmd += ["--theme", args.theme]
    if args.color:
        cmd += ["--color", args.color]
    if args.no_cite:
        cmd += ["--no-cite"]
    cmd += ["--markdown", str(markdown_path)]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(base_dir),
            capture_output=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as err:
        raise PublishError(f"node runtime not found: {err}") from err
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise PublishError(f"render_markdown.mjs failed: {err or 'exit ' + str(proc.returncode)}")
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as err:
        raise PublishError(f"render_markdown.mjs returned non-JSON: {err}") from err


def _resolve_cover_path(cover_hint: str | None, base_dir: Path) -> Path | None:
    if not cover_hint:
        return None
    p = Path(cover_hint).expanduser()
    if not p.is_absolute():
        p = base_dir / p
    return p if p.exists() and p.is_file() else None


def _resolve_fallback_cover(base_dir: Path) -> Path | None:
    p = base_dir / "imgs" / "cover.png"
    return p if p.exists() and p.is_file() else None


def _rewrite_html_with_mmbiz(html: str, replacements: list[tuple[str, str]]) -> str:
    """Replace each `WECHATIMGPH_N` plain-text occurrence with a styled <img> tag.

    baoyu-md renders image placeholders as raw text inside <p> blocks (browser
    path replaces them via paste); for the API path we substitute the full tag.
    """
    out = html
    for placeholder, mmbiz_url in replacements:
        tag = f'<img src="{mmbiz_url}" style="{BODY_IMG_STYLE}" />'
        # Escape regex metachars in the placeholder, then replace ALL occurrences.
        pattern = re.escape(placeholder)
        out = re.sub(pattern, tag, out)
    return out


def _confirm_article(account: str, title: str, summary: str, image_count: int, cover: Path | None) -> None:
    print("Ready to publish WeChat article (news) draft:")
    print(f"  account:  {account}")
    print(f"  title:    {title}")
    print(f"  summary:  {summary}")
    print(f"  body images: {image_count}")
    if cover:
        print(f"  cover:    {cover}")
    answer = input("Publish now? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise PublishError("cancelled")


def run(args) -> int:
    markdown_arg = args.markdown or args.markdown_pos
    if not markdown_arg:
        raise PublishError("article mode requires a markdown path (positional or --markdown)")

    markdown_path = Path(markdown_arg).expanduser().resolve()
    if not markdown_path.exists() or not markdown_path.is_file():
        raise PublishError(f"markdown not found: {markdown_path}")
    base_dir = markdown_path.parent

    # 1. Render markdown → HTML + metadata
    rendered = _run_renderer(args, markdown_path, base_dir)
    title = (args.title or rendered.get("title") or "").strip()
    author = (args.author or rendered.get("author") or "").strip()
    summary = (args.summary or rendered.get("summary") or "").strip()
    html = rendered.get("html") or ""
    content_images = rendered.get("contentImages") or []

    if not title:
        raise PublishError("title is empty (provide --title or a markdown H1/frontmatter.title)")
    if not html:
        raise PublishError("rendered HTML is empty")

    # 2. Resolve account + load env
    # Article mode has no source-file account; default to CLI flag or single-configured.
    env, used_env = merged_env(base_dir, args.env_file, SKILL_DIR)
    account = resolve_account(args.account, {}, env)
    api_base = env.get("WECHAT_API_BASE", "").strip() or "https://api.weixin.qq.com"
    proxy_url = env.get("WECHAT_PROXY_URL", "").strip()

    # 3. Resolve cover (priority: --cover > frontmatter hint > imgs/cover.png > first body image)
    explicit_cover = Path(args.cover).expanduser() if args.cover else None
    if explicit_cover and not explicit_cover.is_absolute():
        explicit_cover = (Path.cwd() / explicit_cover).resolve()
    cover_path: Path | None = None
    cover_source = ""
    if explicit_cover and explicit_cover.exists() and explicit_cover.is_file():
        cover_path = explicit_cover
        cover_source = "--cover"
    else:
        hinted = _resolve_cover_path(rendered.get("coverHint"), base_dir)
        if hinted:
            cover_path = hinted
            cover_source = "frontmatter"
        else:
            fb = _resolve_fallback_cover(base_dir)
            if fb:
                cover_path = fb
                cover_source = "imgs/cover.png"

    # 4. Dry-run: print the resolved plan and return
    if args.dry_run:
        print(json.dumps({
            "mode": "article",
            "account": account,
            "env_file": str(used_env) if used_env else None,
            "api_base": api_base,
            "proxy": bool(proxy_url),
            "markdown": str(markdown_path),
            "theme": args.theme or "default",
            "color": args.color,
            "cite_status": not args.no_cite,
            "title": title,
            "author": author,
            "summary": summary,
            "body_image_count": len(content_images),
            "cover": str(cover_path) if cover_path else None,
            "cover_source": cover_source,
            "html_bytes": len(html),
        }, ensure_ascii=False, indent=2))
        return 0

    # 5. Interactive confirmation
    if not args.yes:
        _confirm_article(account, title, summary, len(content_images), cover_path)

    # 6. Get access token
    token = get_access_token(env, account, api_base, proxy_url)

    # 7. Upload body images, collect mmbiz URLs, then rewrite HTML
    replacements: list[tuple[str, str]] = []
    for idx, img in enumerate(content_images, start=1):
        local_path = Path(img.get("localPath") or "")
        if not local_path or not local_path.exists():
            raise PublishError(f"body image not found: {img.get('originalPath')}")
        print(f"[{idx}/{len(content_images)}] upload body image {local_path.name}")
        mmbiz_url = upload_body_image(proxy_url, api_base, token, local_path)
        replacements.append((img["placeholder"], mmbiz_url))
    html = _rewrite_html_with_mmbiz(html, replacements)

    # 8. Upload cover (material) → thumb_media_id
    if cover_path is None and content_images:
        # Fall back to using the first body image as cover (uploaded as material)
        first = Path(content_images[0].get("localPath") or "")
        if first and first.exists():
            cover_path = first
            cover_source = "first-body-image"
    if cover_path is None:
        raise PublishError("no cover image (provide --cover, frontmatter.coverImage, or imgs/cover.png)")

    print(f"upload cover ({cover_source}): {cover_path}")
    thumb_media_id = upload_image(proxy_url, api_base, token, cover_path)

    # 9. Compose article payload
    article: dict = {
        "article_type": "news",
        "title": title,
        "content": html,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    if author:
        article["author"] = author
    if summary:
        # WeChat accepts `digest` for news type (max ~120 chars). Truncate to be safe.
        article["digest"] = summary[:120]

    # 10. Submit
    draft_media_id = add_draft(api_base, proxy_url, token, [article])
    print(json.dumps({
        "ok": True,
        "mode": "article",
        "account": account,
        "draft_media_id": draft_media_id,
        "title": title,
        "body_image_count": len(content_images),
        "cover_source": cover_source,
    }, ensure_ascii=False))
    return 0
