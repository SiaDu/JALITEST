# ExpreGaze + JALI Performance Annotation Pipeline

## 0. 目标

实现一个可运行的 **ExpreGaze-JALI Maya 2022 集成原型**。

这个原型不替代 JALI，也不自动化 JALI UI。JALI 负责生成 speech / lipsync / facial animation baseline 和 TextGrid timing；ExpreGaze 负责在此基础上进行 script-aware gaze / expression performance planning，并将结果应用到 Maya 中。

当前核心思路：

```text
LLM = actor / performance annotator
code = compiler
Maya adapter = executor
```

LLM 不直接输出 JSON，不直接输出秒数，不负责 Maya 控制。LLM 只输出一份可读的 performance annotation。代码再把 annotation 编译成结构化 JSON、JALI-compatible text，以及 Maya gaze keyframes。

---

## 1. 当前观察与限制

### 1.1 JALI 当前行为

在目前的 JALI Maya 2022 工作流中：

- `jSync1` 通常在执行 `JALI > Animate from File` 之后生成。
- `Animate from File` 会生成：
  - lipsync baseline
  - speech / facial animation
  - TextGrid word / phone alignment
  - jSync node attributes
- emotion、heart emotion、blink、ambient gaze 等设置会在 `jSync1` 生成后出现在 Attribute Editor 中。
- 在 `Animate from File` 前设置这些内容并不可靠，因为：
  - `jSync1` 可能尚不存在；
  - JALI 在生成动画时可能覆盖已有设置。

因此，JALI 应被视为 **first-pass generator**。

jali maya script editor读取的是语音转录txt。加上tag后align后更新emotion动画，并更新语音转录txt为输入。
如果 `JALI > Animate from File`时语音转录txt带tag，生成的就是带emotion的动画
要删除jSync1动画才会保存。

### 1.2 JALI Text Editor 与 ExpreGaze tag 的边界

JALI 原生 Text Editor / transcript annotation 主要用于 JALI-compatible tags，例如 mask / heart。

ExpreGaze 的 gaze tags，例如：

```text
<g01=GAZE-LISTENER>
<g02=GLANCE-DOWN>
<g03=AVERT-UP_RIGHT>
```

是自定义 readable annotation，不应直接交给 JALI 执行。

代码需要把 unified annotation 拆成：

```text
1. annotated_for_jali.txt        # 只包含 JALI-compatible mask / heart tags
2. gaze_events_resolved.json     # 给 Maya gaze adapter 使用
3. debug_full_annotation.txt     # 保留完整 readable annotation，方便检查
```

### 1.3 ExpreGaze overlay 当前行为

在 JALI animation 生成之后：

- `eyeStare_world` 可以移动并 keyframe。
- 移动 `eyeStare_world` 不会明显破坏 `CNT_BOTH_EYES` 上已有的 saccades animation。
- `eyeStare_world` 适合作为 gross gaze direction / look-at target 控制入口。
- `CNT_BOTH_EYES` 可作为 small saccades / eye offsets 的控制入口，但第一版不自动猜测。

### 1.4 语义 gaze 由 ExpreGaze 处理

JALI 可以生成 physiological blink、ambient gaze、mask / heart expression baseline。

但以下语义 gaze 行为应由 ExpreGaze 在 Maya 层叠加实现：

- 看向 listener
- 看向场景物体
- 因为思考、回避、控制、表演等原因看向 DOWN / UP_RIGHT / DOWN_LEFT 等方向
- 根据剧本上下文规划 gaze state changes

---

## 2. 总体 Pipeline

```text
movie / scene context
+
transcript
+
audio
        ↓
JALI Animate from File
        ↓
JALI baseline:
    - lipsync
    - speech / facial animation
    - TextGrid word / phone timing
    - jSync node
        ↓
LLM actor-style performance annotator
        ↓
readable performance annotation:
    [ANALYZE]
    [ANNOTATION]
    [REASONS]
        ↓
annotation parser
        ↓
structured state-change events
        ↓
TextGrid timing resolver
        ↓
split outputs:
    - annotated_for_jali.txt
    - gaze_events_resolved.json
    - debug_full_annotation.txt
        ↓
Maya adapter:
    - keyframe eyeStare_world
    - optional CNT_BOTH_EYES saccades later
        ↓
playblast comparison:
    - JALI baseline
    - JALI + ExpreGaze performance overlay
```

---

## 3. 核心设计决定

### 3.1 LLM 不输出 JSON

不推荐让 LLM 直接输出：

```json
{
  "type": "gaze",
  "span": "That's right",
  "mode": "GAZE",
  "target": "LISTENER",
  "reason": "direct social control"
}
```

推荐让 LLM 输出 readable state-change annotation：

```text
[ANNOTATION]
<m01=Friendly-66><g01=GAZE-LISTENER>That's right.
<g02=GLANCE-DOWN>Here.
<g03=GAZE-LISTENER>Sit right down here.
```

原因：

- LLM 更擅长像演员一样分析文本并插入表演标记。
- JSON schema、时间、span resolve 应交给代码处理。
- readable annotation 更容易人工检查和修改。

### 3.2 只在状态变化时加 tag

annotation 使用 state-change tag，不使用 closing tag。

```text
<g01=GAZE-LISTENER>That's right.
<g02=GLANCE-DOWN>Here.
<g03=GAZE-LISTENER>Sit right down here.
```

含义：

- `g01` 从 `That's` 开始生效。
- `g01` 的结束时间由下一个 gaze tag `g02` 的开始位置决定。
- `g02` 的结束时间由 `g03` 决定。
- 如果 gaze 状态不变，就不加新 tag。

同理：

```text
g = gaze
m = visible mask / surface expression
h = hidden heart / internal undercurrent
```

### 3.3 tag 类型

Gaze tag：

```text
<g01=GAZE-LISTENER>
<g02=GLANCE-DOWN>
<g03=AVERT-UP_RIGHT>
```

Mask tag：

```text
<m01=Friendly-66>
<m02=Polite-82>
<m03=Thinking-76>
```

Heart tag：

```text
<h01=Emp_Anger-2-55>
<h02=Pride_P-91-66>
```

heart 只用于“内心状态”和“外显 mask”不一致时。不要把 heart 当作第二层普通 emotion。

### 3.4 gaze modes

当前只使用三种 gaze mode：

```text
GAZE    = 持续看向目标
GLANCE  = 短暂瞥向目标，然后回到之前 gaze state
AVERT   = 持续避开 listener / main social target
```

不使用：

```text
HOLD
SHIFT
```

理由：

- HOLD 可以由“不加新 tag，延续前一个 gaze state”表示。
- SHIFT 可以由“下一个 GAZE / AVERT tag”表示。

---

## 4. Readable Performance Annotation Format

LLM 的直接输出只包含三段：

```text
[ANALYZE]
scene_constraints: ...
social_interaction_structure: ...
affective_cognitive_state: ...
narrative_intent: ...

[ANNOTATION]
<m01=Friendly-66><g01=GAZE-LISTENER>That's right.
<g02=GLANCE-DOWN>Here.
<g03=GAZE-LISTENER>Sit right down here.

[REASONS]
m01: Friendly opening to reassure and control.
g01: Direct listener gaze establishes authority.
g02: Downward glance cues the physical “here.”
g03: Return to listener while instructing her to sit.
```

规则：

- 必须保留原 transcript 的拼写、大小写、标点和异常词。
- 不要修正 transcript，例如 `lsis` / `lnfinite` 这类词必须原样保留。
- 使用 word-level reasoning，但不要给每个词都打 tag。
- 只在 gaze / mask / heart 状态变化处加 tag。
- reason 单独写在 `[REASONS]`，不要塞进 tag。
- 不输出 JSON。
- 不输出 closing tags。

---

## 5. 建议项目结构

```text
ExpreGaze_JALI/
│
├── README.md
├── requirements.txt
│
├── configs/
│   ├── base.yaml
│   ├── jali_emotion_options.yaml
│   └── runs/
│       └── jali_proto_candidate_001.yaml
│
├── prompts/
│   └── actor_performance_annotation_prompt.md
│
├── data/
│   ├── examples/
│   │   ├── Jali_proto_candidate_001_ProfessorCrystal.TextGrid
│   │   ├── Jali_proto_candidate_001_ProfessorCrystal__performance_annotation.txt
│   │   ├── Jali_proto_candidate_001_ProfessorCrystal__annotated_for_jali.txt
│   │   ├── Jali_proto_candidate_001_ProfessorCrystal__gaze_events_resolved.json
│   │   └── scene_context_example.json
│   │
│   └── output/
│       ├── debug_full_annotation.txt
│       ├── annotated_for_jali.txt
│       ├── gaze_events_resolved.json
│       └── logs/
│
├── src/
│   └── expregaze_jali/
│       ├── __init__.py
│       ├── textgrid_parser.py
│       ├── performance_annotation_parser.py
│       ├── performance_event_compiler.py
│       ├── performance_event_resolver.py
│       ├── jali_annotation_exporter.py
│       ├── gaze_event_exporter.py
│       ├── maya_control_utils.py
│       ├── maya_apply_gaze.py
│       └── diagnostics.py
│
└── maya/
    ├── run_apply_gaze_events.py
    └── print_jali_controls.py
```

---

## 6. Config 分层

### 6.1 `base.yaml`

`base.yaml` 存放通用 annotation grammar、通用 gaze modes、通用 targets。

不要把某一段特有的 object 写进通用 base，例如不要在 base 中写 `CRYSTAL`。

通用 target 示例：

```yaml
gaze_script:
  schema_version: performance_annotation_v1
  timing_basis: textgrid_words
  output_level: state_change_annotation
  output_mode: unified_readable_annotation

  allowed_modes:
    - GAZE
    - GLANCE
    - AVERT

  disallowed_modes:
    - HOLD
    - SHIFT

  target_policy: controlled_vocab_plus_scene_targets
  allowed_targets:
    - CHARACTER
    - LISTENER
    - OBJECT
    - DOWN
    - DOWN_LEFT
    - DOWN_RIGHT
    - UP
    - UP_LEFT
    - UP_RIGHT
    - LEFT
    - RIGHT
    - CAMERA
    - SELF

  annotation_sections:
    - ANALYZE
    - ANNOTATION
    - REASONS

  tag_style:
    format: state_change
    use_closing_tags: false
    id_prefixes:
      gaze: g
      mask: m
      heart: h
```

### 6.2 run config / candidate context

每段具体有哪些 scene-specific targets，应由 run config 或 candidate context 提供。

例如 Professor Crystal 这段：

```yaml
scene_targets:
  objects:
    - CRYSTAL
```

其他片段可以是：

```yaml
scene_targets:
  objects:
    - DOOR
    - LETTER
    - WINDOW
```

### 6.3 `jali_emotion_options.yaml`

`jali_emotion_options.yaml` 存放 JALI mask / heart 可选项和导出规则。

需要区分两层：

```yaml
tag_rules:
  readable_annotation:
    use_closing_tags: false
    state_change_tags_only: true
    tag_values_preserve_case: true

  jali_export:
    use_closing_tags: true
    open_close_tags_must_match: true
    tag_names_lowercase: true
    tag_values_preserve_case: true
    allow_nested_mask_heart: true
    parse_heart_value_by_rsplit_last_dash: true
```

mask 是外显表演风格，heart 是内心 undercurrent。heart 只有在内外不一致时才使用。

---

## 7. Prompt 设计

prompt 不应写成工程说明，也不需要重复 YAML 里的所有规则。

prompt 的职责是让 LLM 进入“优秀演员 / 表演分析者”的角色。规则、可选 gaze mode、target、mask、heart policy 由 config 注入。

建议 prompt：

```text
You are an excellent screen actor and performance annotator. You are especially good at analyzing how gaze, facial expression, social pressure, hidden intention, and emotional control communicate meaning to the audience.

You will read a scene context and a line of dialogue, then annotate the transcript with performance tags for gaze and facial expression.

Think like an actor: consider the scene constraints, social interaction structure, affective and cognitive state, and narrative intent. Then place tags only where the performance state changes.

Do not output JSON.

Output exactly three sections:

[ANALYZE]
Briefly analyze:
scene_constraints, social_interaction_structure, affective_cognitive_state, narrative_intent.

[ANNOTATION]
Output the original transcript with inserted state-change tags. Preserve the transcript text exactly.

[REASONS]
Briefly explain each tag ID.

Follow the annotation rules, allowed gaze modes, allowed targets, mask options, and heart policy provided in the config.

Input:

Scene context:
{{scene_context}}

Transcript:
{{transcript}}

Available scene-specific targets:
{{scene_targets}}

Config:
{{annotation_config}}
```

---

## 8. 实现任务

### Task 1: TextGrid parser

文件：

```text
src/expregaze_jali/textgrid_parser.py
```

实现：

```python
def parse_textgrid_words(textgrid_path: str) -> list[dict]:
    """
    Return word intervals:
    [
        {"word": "Quality", "norm": "quality", "start": 0.42, "end": 0.81}
    ]
    """
```

要求：

- 解析 Praat TextGrid。
- 读取 `words` tier。
- 忽略空 interval。
- 保留原始 word。
- 生成 normalized word，用于后续 matching。

---

### Task 2: Actor-style prompt + config injection

文件：

```text
prompts/actor_performance_annotation_prompt.md
src/expregaze_jali/prompt_builder.py
```

实现：

- 读取 scene context。
- 读取 transcript。
- 读取 base config。
- 读取 JALI emotion options。
- 注入 scene-specific targets。
- 生成最终 LLM prompt。

LLM 输出文件：

```text
*_performance_annotation.txt
```

---

### Task 3: Performance annotation parser

文件：

```text
src/expregaze_jali/performance_annotation_parser.py
```

实现：

```python
def parse_performance_annotation(path: str) -> dict:
    """
    Parse [ANALYZE], [ANNOTATION], [REASONS].
    Extract state-change tags: <g01=...>, <m01=...>, <h01=...>.
    """
```

要求：

- 识别三段 section。
- 解析 tag id、tag type、tag value。
- 保留 tag 在 transcript 中的 text position。
- 删除 tag 后还原 clean transcript。
- 校验 reason 是否覆盖所有 tag id。

---

### Task 4: State-change event compiler

文件：

```text
src/expregaze_jali/performance_event_compiler.py
```

实现：

```python
def compile_state_change_events(parsed: dict) -> dict:
    """
    Convert state-change tags into structured events with text spans.
    """
```

规则：

- `g01` 的结束位置 = 下一个 gaze tag 的开始位置。
- `m01` 的结束位置 = 下一个 mask tag 的开始位置。
- `h01` 的结束位置 = 下一个 heart tag 的开始位置。
- 如果同类后面没有新 tag，则持续到 clip end。
- `GLANCE` 是短暂事件，后续 Maya adapter 会让它返回 previous gaze state。

---

### Task 5: TextGrid timing resolver

文件：

```text
src/expregaze_jali/performance_event_resolver.py
```

实现：

```python
def resolve_events_with_textgrid(events: dict, words: list[dict]) -> dict:
    """
    Resolve event text spans to start/end time using TextGrid word intervals.
    """
```

要求：

- 使用 clean transcript 和 TextGrid words 对齐。
- 所有 gaze / mask / heart events 都补充 `resolved_time`。
- 如果 exact match 失败，输出可读 debug 信息。
- 不要让 LLM 输出时间。

---

### Task 6: JALI annotation exporter

文件：

```text
src/expregaze_jali/jali_annotation_exporter.py
```

实现：

```python
def export_jali_annotation(parsed: dict, events: dict) -> str:
    """
    Export JALI-compatible transcript annotation.
    Remove gaze tags.
    Convert mask / heart state-change events into paired JALI tags.
    """
```

输入：

```text
<m01=Friendly-66><g01=GAZE-LISTENER>That's right.
```

输出给 JALI：

```text
<mask=Friendly-66>That's right. ... </mask=Friendly-66>
```

规则：

- gaze tags 全部删除。
- mask / heart 转成 JALI paired tags。
- tag value 保留大小写。
- heart value 用最后一个 `-` 拆 strength。

---

### Task 7: Gaze event exporter

文件：

```text
src/expregaze_jali/gaze_event_exporter.py
```

实现：

```python
def export_gaze_events(resolved_events: dict) -> dict:
    """
    Export resolved gaze events for Maya adapter.
    """
```

输出示例：

```json
{
  "clip_name": "Jali_proto_candidate_001_ProfessorCrystal",
  "events": [
    {
      "id": "g01",
      "type": "gaze",
      "mode": "GAZE",
      "target": "LISTENER",
      "text": "That's right.",
      "reason": "Direct listener gaze establishes authority.",
      "resolved_time": {
        "start": 0.12,
        "end": 0.91,
        "source": "textgrid_words"
      }
    }
  ]
}
```

---

### Task 8: Maya control utilities

文件：

```text
src/expregaze_jali/maya_control_utils.py
```

实现 namespace-safe node finding：

```python
def find_node_by_suffix(suffix: str) -> str:
    """
    Find node by suffix, namespace-safe.
    Example: 'eyeStare_world' may appear as 'ValleyGirl:eyeStare_world'.
    """
```

```python
def find_eye_stare_world() -> str:
    return find_node_by_suffix("eyeStare_world")
```

```python
def find_both_eyes_ctrl() -> str:
    return find_node_by_suffix("CNT_BOTH_EYES")
```

```python
def sec_to_frame(seconds: float, fps: float) -> int:
    return int(round(seconds * fps))
```

---

### Task 9: Maya gaze adapter

文件：

```text
src/expregaze_jali/maya_apply_gaze.py
maya/run_apply_gaze_events.py
```

实现：

```python
def apply_gaze_events(gaze_events_path: str, target_map: dict, fps: float) -> None:
    """
    Apply resolved gaze events to eyeStare_world.
    """
```

规则：

- 读取 `gaze_events_resolved.json`。
- 找到 `eyeStare_world`。
- 将 seconds 转为 frames。
- 根据 target map 找 locator / direction / camera position。
- 在 gaze event start/end key `eyeStare_world`。
- `GAZE` / `AVERT` 持续到下一个 gaze event。
- `GLANCE` 生成 brief out-and-back keys。

---

### Task 10: Diagnostics

文件：

```text
maya/print_jali_controls.py
```

打印：

- 所有包含 `jSync` 的节点。
- 所有以 `eyeStare_world` 结尾的节点。
- 所有以 `CNT_BOTH_EYES` 结尾的节点。
- `jSync1` 上包含以下关键词的 attributes：
  - blink
  - gaze
  - mask
  - heart
  - emotion
  - intensity

---

## 9. 当前手动工作流

```text
1. 打开 Maya 2022。
2. 加载 JALI plugin。
3. 打开 JALI rig，例如 ValleyGirl_jRigMaya.ma。
4. 执行 JALI > Animate from File。
5. 选择 audio / transcript pair。
6. 等待 JALI 生成 baseline animation、jSync node、TextGrid。
7. 运行 LLM planner，生成 *_performance_annotation.txt。
8. 运行 annotation parser / compiler：
   - 生成 *_annotated_for_jali.txt
   - 生成 *_gaze_events_resolved.json
   - 生成 debug_full_annotation.txt
9. 将 *_annotated_for_jali.txt 的 mask / heart 内容用于 JALI Text Editor / Apply Changes。
10. 在 Maya Script Editor 中运行 maya/run_apply_gaze_events.py。
11. 检查 eyeStare_world 是否被正确 key。
12. 输出 playblast 对比。
```

---

## 10. 验收标准

当前第一版成功条件：

1. 给定 JALI 生成的 TextGrid，parser 能提取 word timings。
2. 给定 readable performance annotation，parser 能识别：
   - `[ANALYZE]`
   - `[ANNOTATION]`
   - `[REASONS]`
   - `g / m / h` state-change tags
3. compiler 能将 state-change tags 转换为 text span events。
4. resolver 能根据 TextGrid words 为 events 补充 start / end time。
5. exporter 能生成：
   - JALI-compatible mask / heart txt
   - resolved gaze events JSON
   - debug full annotation
6. Maya gaze adapter 能根据 resolved gaze events key `eyeStare_world`。
7. Maya scene 可以播放：
   - JALI lipsync / facial baseline
   - ExpreGaze gaze overlay

---

## 11. 暂时不做

第一版暂时不做：

- 自动化 JALI GUI。
- 让 LLM 直接输出 JSON。
- 让 LLM 直接输出秒数。
- head motion generation。
- eyebrow animation。
- complex saccade model。
- 自动猜 blink controls。
- custom rig connection。
- 多场景 batch processing。
- scientific evaluation metrics。

---

## 12. 后续扩展

后续可以继续加：

1. optional audio emotion prior。
2. head movement overlay。
3. performance blink with eyelid / brow accents。
4. scene-object locator generation。
5. batch mode for multiple clips。
6. playblast automation。
7. evaluation：
   - JALI baseline
   - JALI + random gaze
   - JALI + ExpreGaze gaze
   - optional S3-inspired baseline

---

## 13. 研究定位

这个项目不是 audio-only gaze aversion predictor。

ExpreGaze 把 gaze 视为一个 **script-aware performance planning problem**。

输入包括：

- transcript
- audio
- movie script context
- scene description
- social interaction structure
- optional audio affect prior

输出包括：

- editable readable performance annotation
- JALI-compatible mask / heart transcript annotation
- resolved gaze events for Maya
- Maya gaze overlay

JALI 负责：

- forced alignment
- word / phone timing
- lip-sync
- speech / facial baseline
- JALI-compatible facial expression generation

ExpreGaze 负责：

- actor-style performance annotation
- semantic gaze decisions
- gaze target selection
- directional aversion
- conversion from readable tags to structured events
- Maya overlay on `eyeStare_world`

核心技术桥梁是：

```text
LLM readable performance annotation
        ↓
annotation parser + TextGrid word timing
        ↓
JALI mask/heart text + Maya gaze keyframes
```

---

## 14. Codex 立即执行的第一步

Codex 先只做 Maya 外部 pipeline：

```text
src/expregaze_jali/textgrid_parser.py
src/expregaze_jali/performance_annotation_parser.py
src/expregaze_jali/performance_event_compiler.py
src/expregaze_jali/performance_event_resolver.py
src/expregaze_jali/jali_annotation_exporter.py
src/expregaze_jali/gaze_event_exporter.py
```

先创建一个测试输入：

```text
data/examples/Jali_proto_candidate_001_ProfessorCrystal__performance_annotation.txt
```

第一阶段测试应该在 Maya 外部运行：

```python
from expregaze_jali.textgrid_parser import parse_textgrid_words
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import resolve_events_with_textgrid
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.gaze_event_exporter import export_gaze_events

words = parse_textgrid_words("data/examples/Jali_proto_candidate_001_ProfessorCrystal.TextGrid")
parsed = parse_performance_annotation("data/examples/Jali_proto_candidate_001_ProfessorCrystal__performance_annotation.txt")
events = compile_state_change_events(parsed)
resolved = resolve_events_with_textgrid(events, words)

jali_txt = export_jali_annotation(parsed, resolved)
gaze_json = export_gaze_events(resolved)

print(jali_txt[:500])
print(gaze_json["events"][0])
```

只有当 annotation parsing / event compiling / TextGrid resolving / export 全部跑通后，再开始写 Maya-specific gaze adapter。
