"""Newspic (image-text) mode: short post with 1-20 images, plain text content.

Behavior matches the original publish_newspic.py 1:1. Moved here as part of
the wechat-publisher refactor so that the CLI can dispatch to either mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lib.account import resolve_account
from lib.env_loader import merged_env
from lib.errors import PublishError
from lib.proxy_client import (
    DEFAULT_API_BASE,
    add_draft,
    get_access_token,
    upload_image,
)
from lib.source_loader import load_source, validate_newspic_source

SKILL_DIR = Path(__file__).resolve().parent.parent


def _confirm_newspic(account: str, title: str, content: str, images: list[Path]) -> None:
    print("Ready to publish WeChat newspic draft:")
    print(f"  account: {account}")
    print(f"  title: {title}")
    print(f"  content: {content}")
    print(f"  images: {len(images)}")
    answer = input("Publish now? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise PublishError("cancelled")


def run(args) -> int:
    # Resolve source file (positional arg) the same way the original script did.
    direct = args.title is not None or args.content is not None or bool(args.image)
    if args.source:
        source_path = Path(args.source).expanduser().resolve()
        source = load_source(source_path)
        base_dir = source_path.parent
    elif direct:
        source = {}
        base_dir = Path.cwd()
    else:
        source_path = (Path.cwd() / "source.md").resolve()
        if not source_path.exists():
            raise PublishError("no source given and source.md not found in current directory")
        source = load_source(source_path)
        base_dir = source_path.parent

    # CLI overrides source fields.
    if args.title is not None:
        source["title"] = args.title
    if args.content is not None:
        source["content"] = args.content
    if args.author is not None:
        source["author"] = args.author
    if args.digest is not None:
        source["digest"] = args.digest
    if args.image:
        source["images"] = list(args.image)

    env, used_env = merged_env(base_dir, args.env_file, SKILL_DIR)
    account = resolve_account(args.account, source, env)
    title, content, author, digest, images = validate_newspic_source(source, base_dir)
    api_base = env.get("WECHAT_API_BASE") or DEFAULT_API_BASE
    proxy_url = env.get("WECHAT_PROXY_URL", "")

    if args.dry_run:
        print(json.dumps({
            "mode": "newspic",
            "account": account,
            "env_file": str(used_env) if used_env else None,
            "api_base": api_base,
            "proxy": bool(proxy_url),
            "draft": {
                "article_type": "newspic",
                "title": title,
                "author": author,
                "digest": digest,
                "content": content,
                "images": [str(p) for p in images],
            },
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.yes:
        _confirm_newspic(account, title, content, images)

    token = get_access_token(env, account, api_base, proxy_url)

    media_ids: list[str] = []
    for i, image in enumerate(images, start=1):
        print(f"[{i}/{len(images)}] upload {image}")
        media_ids.append(upload_image(proxy_url, api_base, token, image))

    item: dict = {
        "article_type": "newspic",
        "title": title,
        "content": content,
        "image_info": {"image_list": [{"image_media_id": mid} for mid in media_ids]},
    }
    if author:
        item["author"] = author
    if digest:
        item["digest"] = digest

    draft_media_id = add_draft(api_base, proxy_url, token, [item])
    print(json.dumps({
        "ok": True,
        "mode": "newspic",
        "account": account,
        "draft_media_id": draft_media_id,
    }, ensure_ascii=False))
    return 0
