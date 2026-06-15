# ExpreGaze + JALI 最小可运行 Pipeline 实现计划

## 0. 目标

实现一个最小可运行的 **ExpreGaze-JALI Maya 2022 集成原型**。

这个原型的目标不是替代 JALI，也不是完全自动化 JALI UI，而是：

1. 使用 JALI 作为 speech / lipsync / facial animation baseline。
2. 使用 JALI 生成的 TextGrid 获取 word / phone timing。
3. 使用 LLM 生成可编辑的 gaze / blink / emotion performance script。
4. 使用 Maya adapter 将 gaze / blink / emotion overlay 应用到已有的 JALI animation 上。
5. 最终输出 playblast 对比：

   * JALI baseline
   * JALI + ExpreGaze overlay

---

## 1. 当前观察与限制

以下内容基于当前 Maya/JALI 测试结果。

### 1.1 JALI 当前行为

在目前的 JALI Maya 2022 工作流里：

* `jSync1` 通常在执行 `JALI > Animate from File` 之后生成。
* `Animate from File` 会生成：

  * lipsync baseline
  * speech / facial animation
  * TextGrid word / phone alignment
  * jSync node attributes
* emotion、heart emotion、blink、ambient gaze 等设置会在 `jSync1` 生成后出现在 Attribute Editor 中。
* 这些设置如果在 `Animate from File` 前设置并不可靠，因为：

  * `jSync1` 可能尚不存在；
  * JALI 在生成动画时可能覆盖已有设置。

因此，本 MVP 应该把 JALI 视为 **first-pass generator**。

### 1.2 ExpreGaze overlay 当前行为

在 JALI animation 生成之后：

* `eyeStare_world` 可以移动并 keyframe。
* 移动 `eyeStare_world` 不会明显破坏 `CNT_BOTH_EYES` 上已有的 saccades animation。
* `eyeStare_world` 适合作为 gross gaze direction / look-at target 控制入口。
* `CNT_BOTH_EYES` 可以作为 small saccades / eye offsets 的控制入口。

### 1.3 JALI gaze / blink 的限制

当前 JALI 可以支持：

* physiological blink generation
* ambient gaze
* emotion / mask settings
* heart emotion settings
* intensity / strength settings

但以下语义 gaze 行为应由 ExpreGaze 在 Maya 层叠加实现：

* gaze-on / gaze-off
* 看向 listener
* 因为回避、内疚、思考而看向 down-left / up-right 等方向
* 看向场景中的具体物体

---

## 2. 总体 Pipeline

```text
Movie script context
当前台词 + 前后文 + 当前场景段落 + 整部电影简略故事
+
audio
+
transcript
        ↓
JALI Animate from File
        ↓
JALI baseline:
    - lipsync
    - speech / facial animation
    - TextGrid word / phone timing
    - jSync1 node
        ↓
可选 audio emotion model:
    - segment-level emotion probabilities
    - 只作为 weak affect prior
        ↓
LLM semantic performance planner
        ↓
Editable performance script:
    - gaze events
    - blink events
    - JALI mask emotion settings
    - JALI heart emotion settings
    - intensity / strength
    - reason / confidence
        ↓
Maya adapter:
    - parse TextGrid
    - resolve span → start / end
    - set jSync1 attributes
    - keyframe eyeStare_world
    - optional CNT_BOTH_EYES saccades
        ↓
Playblast demo:
    - JALI baseline
    - JALI + ExpreGaze overlay
```

---

## 3. 核心设计决定

LLM 不应该直接输出秒数作为主要 timing 格式。

不推荐：

```json
{
  "type": "gaze",
  "start": 1.24,
  "end": 2.61,
  "target": "down_left"
}
```

推荐：

```json
{
  "type": "gaze",
  "span": "cannot tell you the truth",
  "state": "gaze_off",
  "target": "down_left",
  "reason": "avoidance / guilt"
}
```

然后由 adapter 使用 JALI 生成的 `merchant1.TextGrid` 将 span 转换为真实时间。

内部处理后可以补充：

```json
"resolved_time": {
  "start": 1.24,
  "end": 2.61,
  "source": "TextGrid"
}
```

这样可以让 gaze script 更可编辑，也更容易 debug。

---

## 4. 建议项目结构

```text
ExpreGaze_JALI/
│
├── README.md
├── requirements.txt
│
├── data/
│   ├── examples/
│   │   ├── merchant1_performance_script.json
│   │   ├── merchant1.TextGrid
│   │   └── scene_context_example.json
│   │
│   └── output/
│       ├── resolved_performance_script.json
│       └── logs/
│
├── prompts/
│   └── llm_performance_planner_prompt.md
│
├── src/
│   └── expregaze_jali/
│       ├── __init__.py
│       ├── textgrid_parser.py
│       ├── span_resolver.py
│       ├── performance_schema.py
│       ├── jali_attr_utils.py
│       ├── maya_control_utils.py
│       ├── maya_apply_gaze.py
│       └── diagnostics.py
│
└── maya/
    ├── run_apply_performance_script.py
    └── print_jali_controls.py
```

---

## 5. Performance Script JSON 格式

先创建一个 example JSON。

```json
{
  "meta": {
    "clip_name": "merchant1",
    "fps": 30,
    "rig": "ValleyGirl",
    "textgrid": "C:/Users/sia/Documents/maya/projects/JALI_test/scenes/sounds/merchant1.TextGrid",
    "audio": "C:/Users/sia/Documents/maya/projects/JALI_test/scenes/sounds/merchant1.wav"
  },
  "scene_context": {
    "environment": "courtroom",
    "current_scene_summary": "The speaker is delivering a formal speech while trying to maintain composure.",
    "social_interaction": "speaker addressing listener / audience",
    "salient_objects": [
      {
        "name": "listener_face",
        "type": "character",
        "priority": 1.0
      },
      {
        "name": "floor",
        "type": "directional_anchor",
        "priority": 0.4
      }
    ]
  },
  "audio_affect_prior": {
    "enabled": false,
    "segments": []
  },
  "jali_settings": {
    "calculate_blinks": false,
    "calculate_ambient_gaze": false,
    "calculate_masks": true,
    "calculate_heart": true,
    "mask_bearing": "Nervous",
    "mask_intensity": "Measured (80%)",
    "heart_source": "Angry",
    "heart_strength": "Slight (10%)",
    "mask_frequency": "Very Often (25)",
    "mask_decay": "1400ms"
  },
  "targets": {
    "listener_face": {
      "type": "locator",
      "node": "listener_face_LOC"
    },
    "down_left": {
      "type": "direction",
      "position": [-4.0, 1.0, 8.0]
    },
    "down_right": {
      "type": "direction",
      "position": [4.0, 1.0, 8.0]
    },
    "up_right": {
      "type": "direction",
      "position": [4.0, 5.0, 8.0]
    },
    "camera": {
      "type": "camera",
      "node": "persp"
    }
  },
  "events": [
    {
      "type": "gaze",
      "span": "the quality of mercy",
      "state": "gaze_on",
      "target": "listener_face",
      "transition": "soft",
      "reason": "direct address to listener",
      "confidence": 0.82
    },
    {
      "type": "gaze",
      "span": "is not strained",
      "state": "gaze_off",
      "target": "down_left",
      "transition": "quick_aversion",
      "reason": "reflective inward thought",
      "confidence": 0.74
    },
    {
      "type": "blink",
      "anchor": "strained",
      "offset": -0.05,
      "duration": 0.12,
      "strength": 0.8,
      "reason": "blink during gaze transition"
    },
    {
      "type": "saccade",
      "span": "quality of mercy",
      "target": "listener_face",
      "amplitude": 0.08,
      "frequency": 2,
      "reason": "avoid frozen stare during long fixation"
    }
  ]
}
```

---

## 6. 实现任务

### Task 1: TextGrid parser

文件：

```text
src/expregaze_jali/textgrid_parser.py
```

实现：

```python
def parse_textgrid_words(textgrid_path: str) -> list[dict]:
    """
    Return a list of word intervals:
    [
        {"word": "the", "start": 0.12, "end": 0.24},
        {"word": "quality", "start": 0.24, "end": 0.71}
    ]
    """
```

要求：

* 解析 Praat TextGrid。
* 读取 `words` tier。
* 忽略空 interval。
* 标准化 quotation marks 和 punctuation。
* 保留原始 word 和 normalized word。

输出示例：

```python
[
    {
        "word": "Quality",
        "norm": "quality",
        "start": 0.42,
        "end": 0.81
    }
]
```

MVP 阶段不要引入重依赖，先用 plain Python parser。

---

### Task 2: Span resolver

文件：

```text
src/expregaze_jali/span_resolver.py
```

实现：

```python
def resolve_span(span: str, words: list[dict], occurrence: int = 0) -> dict:
    """
    Resolve a text span to start/end time using TextGrid word intervals.

    Return:
    {
        "span": "...",
        "start": 1.24,
        "end": 2.61,
        "matched_words": [...]
    }
    """
```

要求：

* normalize span text。
* 移除 punctuation。
* 匹配连续 words。
* 支持重复 span，通过 `occurrence` 指定第几次出现。
* 如果找不到 exact match，要抛出可读错误。
* 如果 exact match 失败，可以输出 near matches 方便 debug。

示例：

```python
resolve_span("the quality of mercy", words)
```

返回 `the` 的 start 和 `mercy` 的 end。

---

### Task 3: Resolve all events

文件：

```text
src/expregaze_jali/performance_schema.py
```

实现：

```python
def resolve_performance_script(data: dict, words: list[dict]) -> dict:
    """
    Add resolved_time to all events that use span or anchor.
    """
```

规则：

* `gaze` event 如果有 `span`，resolve start/end。
* `saccade` event 如果有 `span`，resolve start/end。
* `blink` event 如果有 `anchor`，resolve anchor word timing。
* blink time = anchor word start + offset，除非之后添加 `anchor_position`。
* 所有 resolved time 都写回 event 里，不只存在临时变量里。

blink resolved output 示例：

```json
{
  "type": "blink",
  "anchor": "strained",
  "offset": -0.05,
  "resolved_time": {
    "time": 1.73,
    "source_word": "strained"
  }
}
```

---

### Task 4: JALI attribute utilities

文件：

```text
src/expregaze_jali/jali_attr_utils.py
```

实现 robust setters for `jSync1`。

目前已知 attributes 包括：

```python
JSYNC_ATTRS = {
    "calculate_blinks": "calculate_blinks",
    "long_blink_variation": "long_blink_variation",
    "blink_frequency": "blink_frequency",
    "blink_closure": "blink_closure",
    "calculate_ambient_gaze": "calculate_ambient_gaze",
    "ambient_gaze_intensity": "ambient_gaze_intensity",

    "calculate_masks": "calculate_masks",
    "mask_bearing": "mask_bearing",
    "mask_intensity": "mask_intensity",
    "mask_frequency": "mask_frequency",
    "mask_onset": "mask_onset",
    "mask_decay": "mask_decay",
    "mask_start": "mask_start",
    "mask_end": "mask_end",

    "calculate_heart": "calculate_heart",
    "heart_source": "heart_source",
    "heart_strength": "heart_strength",
    "heart_start": "heart_start",
    "heart_end": "heart_end"
}
```

实现：

```python
def set_enum_by_label(node: str, attr: str, label: str) -> None:
    """
    Set Maya enum attr by visible label.

    Must support enum strings like:
    'Trace (5%)=5:Slight (10%)=10:Measured (80%)=80'
    """
```

注意：Maya enum value 不一定等于 list index，因为 enum item 里可能有 `=value`。

实现：

```python
def set_jali_settings(jsync_node: str, settings: dict) -> None:
    """
    Apply jali_settings from performance script to jSync1.
    """
```

需要处理：

* bool
* int
* float
* enum string labels

遇到 unknown setting 时 print warning，不要直接 crash。

---

### Task 5: Maya control utilities

文件：

```text
src/expregaze_jali/maya_control_utils.py
```

实现 namespace-safe node finding。

函数：

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

同时实现：

```python
def sec_to_frame(seconds: float, fps: float) -> int:
    return int(round(seconds * fps))
```

```python
def key_translate_world(node: str, frame: int, position: list[float]) -> None:
    """
    Move node in world space and key translate.
    """
```

---

### Task 6: Apply gaze events

文件：

```text
src/expregaze_jali/maya_apply_gaze.py
```

实现：

```python
def apply_gaze_event(event: dict, targets: dict, fps: float) -> None:
    """
    Use eyeStare_world for gross gaze direction.
    """
```

规则：

* 读取 `event["resolved_time"]["start"]` 和 `event["resolved_time"]["end"]`。
* 将 seconds 转换为 frames。
* 解析 target：

  * 如果 target type 是 `locator`，使用 locator world position。
  * 如果 target type 是 `direction`，使用 explicit position。
  * 如果 target type 是 `camera`，使用 camera world position。
* 在 start 和 end 帧移动并 key `eyeStare_world`。
* MVP 先用 simple stepped / linear keyframes。
* 后续再加 easing。

示例：

```python
apply_gaze_event(
    {
        "type": "gaze",
        "target": "down_left",
        "resolved_time": {"start": 1.1, "end": 1.8}
    },
    targets,
    fps=30
)
```

---

### Task 7: Apply saccades

文件：

```text
src/expregaze_jali/maya_apply_gaze.py
```

实现：

```python
def apply_saccade_event(event: dict, fps: float) -> None:
    """
    Add small local offsets to CNT_BOTH_EYES.
    """
```

MVP 规则：

* 只在 fixation duration > 0.5s 时加 saccades。
* 在 event interval 内加 1–3 个小 keyframes。
* movement 不要太大。
* 只使用 `CNT_BOTH_EYES`。
* 默认不自动加，只有 event type 明确为 `"saccade"` 时才执行。

---

### Task 8: Performance blink placeholder

文件：

```text
src/expregaze_jali/maya_apply_gaze.py
```

实现 placeholder：

```python
def apply_blink_event(event: dict, fps: float, blink_map: dict | None = None) -> None:
    """
    Apply performance blink if blink control mapping is provided.
    Otherwise print a warning and skip.
    """
```

MVP 阶段不要猜 blink controls。

如果没有 blink control mapping，脚本应该 warning 后跳过，而不是失败。

---

### Task 9: Main Maya runner

文件：

```text
maya/run_apply_performance_script.py
```

这是需要在 Maya Script Editor 里运行的脚本。

实现：

```python
import sys

# User should edit this path if needed
PROJECT_ROOT = r"C:/Users/sia/Documents/projects/ExpreGaze_JALI"
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.expregaze_jali.maya_apply_gaze import apply_performance_script

apply_performance_script(
    performance_script_path=r"C:/Users/sia/Documents/projects/ExpreGaze_JALI/data/examples/merchant1_performance_script.json"
)
```

主函数：

```python
def apply_performance_script(performance_script_path: str) -> None:
    """
    1. Load JSON.
    2. Parse TextGrid.
    3. Resolve event spans.
    4. Find jSync1.
    5. Apply JALI settings.
    6. Apply gaze events.
    7. Apply saccades.
    8. Apply blink placeholders.
    9. Save resolved JSON to data/output.
    """
```

---

## 7. 当前 MVP 的手动工作流

MVP 阶段不要自动化 JALI Animate from File。

用户手动流程：

```text
1. 打开 Maya 2022。
2. 加载 JALI plugins。
3. 打开 JALI rig，例如 ValleyGirl_jRigMaya.ma。
4. 执行 JALI > Animate from File。
5. 选择 audio / transcript pair。
6. 等待 JALI 生成：
   - baseline animation
   - jSync1
   - TextGrid
7. 确认 TextGrid 存在。
8. 在 Maya Script Editor 中运行 maya/run_apply_performance_script.py。
9. 检查：
   - jSync1 settings 是否被更新
   - eyeStare_world 是否被 key
   - 如果 event 里要求 saccade，CNT_BOTH_EYES 是否被加 key
10. 输出 playblast。
```

---

## 8. Diagnostics Script

文件：

```text
maya/print_jali_controls.py
```

实现 diagnostic script，打印：

* 所有包含 `jSync` 的节点
* 所有以 `eyeStare_world` 结尾的节点
* 所有以 `CNT_BOTH_EYES` 结尾的节点
* `jSync1` 上包含以下关键词的 attributes：

  * blink
  * gaze
  * mask
  * heart
  * emotion
  * intensity

示例：

```python
import maya.cmds as cmds

keywords = ["jSync", "eyeStare", "CNT_BOTH_EYES", "blink", "gaze", "mask", "heart", "emotion"]

for node in cmds.ls():
    if any(k.lower() in node.lower() for k in keywords):
        print(node, cmds.nodeType(node))

if cmds.objExists("jSync1"):
    for attr in cmds.listAttr("jSync1") or []:
        low = attr.lower()
        if any(k in low for k in ["blink", "gaze", "mask", "heart", "emotion", "intensity"]):
            print("jSync1 attr:", attr)
```

---

## 9. MVP 验收标准

MVP 成功条件：

1. 给定 JALI 生成的 `merchant1.TextGrid`，parser 能提取 word timings。
2. 给定 span，例如 `"the quality of mercy"`，resolver 能返回正确 start/end time。
3. 给定 performance script JSON，Maya adapter 能：

   * 设置 `jSync1.calculate_blinks = false`
   * 设置 `jSync1.calculate_ambient_gaze = false`
   * 设置 `mask_bearing`
   * 设置 `mask_intensity`
   * 设置 `heart_source`
   * 设置 `heart_strength`
   * 将 `eyeStare_world` key 到至少两个 target
   * 如果 event 中要求 saccade，则给 `CNT_BOTH_EYES` 添加小幅 saccade keys
4. 最终 Maya scene 可以播放：

   * JALI lipsync
   * ExpreGaze gaze overlay
5. 可以输出两个 playblast：

   * JALI baseline
   * JALI + ExpreGaze

---

## 10. MVP 阶段不做的内容

暂时不要实现：

* 完整自动化 JALI GUI。
* 完整 LLM API integration。
* audio emotion model integration。
* eyebrow animation。
* head motion generation。
* complex saccade model。
* custom rig connection。
* 多场景 batch processing。
* scientific evaluation metrics。

这些必须等最小 pipeline 稳定后再加。

---

## 11. 后续扩展

MVP 跑通后可以继续加：

1. LLM planner module。
2. optional audio emotion prior。
3. head movement overlay。
4. performance blink with eyelid / brow accents。
5. scene-object locator generation。
6. batch mode for multiple clips。
7. playblast automation。
8. evaluation：

   * JALI baseline
   * JALI + random gaze
   * JALI + ExpreGaze LLM gaze
   * optional S3-inspired baseline

---

## 12. 研究定位

这个项目不是 audio-only gaze aversion predictor。

ExpreGaze 把 gaze 视为一个 **script-aware performance planning problem**。

输入包括：

* transcript
* audio
* movie script context
* scene description
* social interaction structure
* optional audio affect prior

输出包括：

* editable span-based gaze script
* JALI-compatible emotion / heart settings
* Maya gaze / blink overlay

JALI 负责：

* forced alignment
* word / phone timing
* lip-sync
* speech / facial baseline
* jSync emotion and heart settings

ExpreGaze 负责：

* semantic gaze-on/off decisions
* gaze target selection
* directional aversion
* performance blink timing
* optional saccades

核心技术桥梁是：

```text
LLM span-based performance script
        ↓
JALI TextGrid word timing
        ↓
Maya keyframes on eyeStare_world / CNT_BOTH_EYES
```

---

## 13. Codex 立即执行的第一步

Codex 先只做这三个文件：

```text
src/expregaze_jali/textgrid_parser.py
src/expregaze_jali/span_resolver.py
src/expregaze_jali/performance_schema.py
```

然后创建：

```text
data/examples/merchant1_performance_script.json
```

第一阶段测试应该在 Maya 外部运行：

```python
from src.expregaze_jali.textgrid_parser import parse_textgrid_words
from src.expregaze_jali.span_resolver import resolve_span

words = parse_textgrid_words("data/examples/merchant1.TextGrid")
print(resolve_span("the quality of mercy", words))
```

只有当这个测试跑通后，再开始写 Maya-specific scripts。
