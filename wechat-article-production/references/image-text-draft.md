# 微信贴图草稿

仅在用户明确请求“微信贴图 / 图片消息 / 小绿书草稿”时使用。本模式只调用草稿箱新增接口，禁止调用 `freepublish` 或任何正式发布接口。

## 输入与产物

- 图片：一个目录内的 `PNG`、`JPG/JPEG`、`GIF` 或 `BMP`，按文件名升序上传；数量必须为 1–20，首图即封面。
- 标题：必填，最多 32 个字符。
- 说明：可选纯文本；不得写 HTML 或 Markdown。
- 结果：在调用方指定的输出目录写 `publish-result.json`。不要把图片压缩成 zip。

小红书卡片可直接复用其 `cards/` 目录，且应保留原始卡片文件，不改写图片或正文。

## 运行

先运行 dry-run；成功后才进行一次真实创建。真实调用需要用户明确授权，或请求本身已明确要求“创建草稿”。

```bash
node <THIS_SKILL_ROOT>/scripts/publish-wechat-image-text.mjs \
  --title "贴图标题" \
  --caption-file <PACKAGE_DIR>/caption.txt \
  --images-dir <XHS_CARD_DIR>/cards \
  --account <ACCOUNT> \
  --result-file <PACKAGE_DIR>/publish-result.json \
  --dry-run

node <THIS_SKILL_ROOT>/scripts/publish-wechat-image-text.mjs \
  --title "贴图标题" \
  --caption-file <PACKAGE_DIR>/caption.txt \
  --images-dir <XHS_CARD_DIR>/cards \
  --account <ACCOUNT> \
  --result-file <PACKAGE_DIR>/publish-result.json \
  --yes
```

不要将 `--images-dir` 与重复的 `--image` 混用。需要保持自定义顺序时，使用重复 `--image` 按传入顺序逐张指定。

## 接口边界

每张图经 `cgi-bin/material/add_material?type=image` 上传为永久素材，返回的 `media_id` 被写入：

```json
{
  "articles": [{
    "article_type": "newspic",
    "title": "贴图标题",
    "content": "纯文本说明",
    "image_info": {
      "image_list": [{ "image_media_id": "永久素材 MediaID" }]
    }
  }]
}
```

再将该请求提交到 `cgi-bin/draft/add`。`newspic` 不使用长文的 HTML 正文上传接口 `media/uploadimg`，也不使用 `thumb_media_id`。
