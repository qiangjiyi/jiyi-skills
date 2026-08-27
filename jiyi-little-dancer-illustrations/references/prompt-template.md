# 生图提示词模板

每张图单独生成。根据正文内容替换变量，不要把多张图拼在一起。

```text
Generate one standalone 16:9 horizontal Chinese article illustration.

Visual DNA:
Pure white background. Minimalist black hand-drawn line art. Slightly wobbly pen lines. Lots of empty white space. Sparse pink, orange, blue, and red handwritten Chinese annotations. Clean absurd product-sketch feeling with a light sense of dance rhythm. No gradients, no shadows, no paper texture, no complex background, no commercial vector style, no PPT infographic look, no realistic child portrait, no cute mascot poster, no children's teaching poster.

IP identity lock — must remain unchanged in every image:
Use the supplied visual reference image `assets/little-dancer-reference-sheet.png` (or the package copy `illustrations/ip-reference.png`) for character identity. Do not copy its background or pose. Keep the same original little girl: dark brown/black hair in two small side ponytails, a small top bun with a pale-pink bow headband, a natural round child face, medium-large bright eyes, a small natural mouth, pale-pink short-sleeve T-shirt with a simple pink bow graphic on the chest, pale-pink pants with one small orange heart on the outer thigh, and white sneakers with small pink details. Keep child-friendly proportions and slightly irregular black hand-drawn lines. 小舞伴 must perform the core conceptual action, not decorate the scene.

Identity drift is not allowed: do not change the hair into a different style, remove or replace the bow headband, make anime giant eyes, redesign the face, replace the T-shirt and pants with a skirt, long-sleeve top, dress, tracksuit, or dark outfit, remove the chest bow or orange heart, or replace the white sneakers. Vary only pose, movement, gaze, one main expression, and the article-specific metaphor objects.

Movement and expression:
Primary movement: {从 motion-library.md 选择一个动作}
Expression: {一个主表情}
Gaze direction: {小舞伴正在看向什么}

Theme:
{正文配图主题}

Structure type:
{结构类型：Workflow / 系统局部 / 前后对比 / 角色状态 / 概念隐喻 / 方法分层 / 地图路线 / 小漫画分镜}

Core idea:
{这张图要表达的核心意思}

Composition:
{具体画面：小舞伴在哪里、正在做什么、主要物件是什么、信息如何流动、动作如何改变结构}

Suggested elements:
{元素1} / {元素2} / {元素3} / {元素4}

Chinese handwritten labels:
{标注词1} / {标注词2} / {标注词3} / {标注词4} / {可选标注词5}

Color use:
Black for main line art and 小舞伴. Pink for clothing and character accents. Orange for main flow, action path, or arrows. Red only for key warnings, problems, or results. Blue only for secondary notes or feedback.

Constraints:
One image explains only one core structure. Keep the main subject around 40%-60% of the canvas. Preserve at least 35% blank white space. Use at most 5-8 short handwritten Chinese labels. Do not write a title in the top-left corner. Do not write the structure type on the image. Do not make it a formal diagram, course slide, dense explainer, realistic child portrait, or cute mascot poster. Do not copy a real photo background, clothing logo, adult companion, or video location. Do not reuse prior case compositions. Invent a fresh visual metaphor for this specific article. Keep 小舞伴's movement clear, healthy, age-appropriate, and conceptually necessary.

Reference handling:
The reference image must be supplied to the image backend as an actual visual input, not merely mentioned as text. If the backend cannot accept a reference for a new image, use its supported edit/variation workflow or stop with a blocked handoff. Never claim identity consistency from text-only generation.
```

## 图像编辑提示

去掉左上角标题：

```text
Edit the provided image. Remove only the handwritten title "{要删除的文字}" and its underline from the top-left corner. Fill that area with the same clean white background. Preserve everything else exactly: 小舞伴's appearance, movement, expression, labels, paths, line style, composition, aspect ratio, and image quality. Do not add any new text or objects.
```

增强动作参与：

```text
Regenerate this illustration with the same core meaning and simple layout, but make 小舞伴 more central to the conceptual action. Use one clear movement from the motion library—such as opening both arms, leaning forward with arms back, stepping sideways, peeking into a black box, or lifting one foot—and make the movement change or explain the structure. Keep the character original, child-friendly, hand-drawn, clean, sparse, and not photorealistic or cute-mascot-like.
```
