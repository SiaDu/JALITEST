#!/usr/bin/env python3
"""
Analyze the conversational character animation user study questionnaires.

Formal analysis policy
----------------------
- Participant IDs are explicit. The default snapshot includes U01-U10 only.
- Do not include synthetic supervisor-preview questionnaires in formal analysis.
- When additional real participants are collected, rerun with --include-ids
  (for example: U01-U16) after verifying those files are genuine observations.

The script parses three checkbox encodings found in the collected DOCX files:
Word content-control checkboxes, red-shaded flattened checkboxes, and
yellow-highlighted flattened checkboxes.

Study A (paper naming): Direct Generation vs Editable Performance Plan.
Study B (paper naming): Dialogue-Only vs Full-Context plan appropriateness.

Statistics:
- participant-level paired means;
- two-sided Wilcoxon signed-rank tests;
- Holm adjustment within outcome families;
- paired rank-biserial correlation, positive when the second condition is higher.

Dependencies: python-docx, lxml, pandas, numpy, scipy, matplotlib.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from scipy.stats import rankdata, wilcoxon

WORKFLOW_NAMES = [
    "express_direction",
    "local_revision_preserve",
    "revision_clarity",
    "control_final_direction",
    "coordinate_dyad",
    "visual_semantic_iteration",
    "creative_ownership",
    "explore_alternatives",
    "effort_worthwhile",
]
TLX_NAMES = ["mental", "physical", "temporal", "performance", "effort", "frustration"]
CONTEXT_CRITERIA = ["overall", "intent", "affect", "gaze", "timing"]
DIAG_NAMES = ["tag_level_match", "interpretation_helpful", "dual_layout_coordination"]

ROLE_MAP = {
    "专业动画师": "professional_animator",
    "动画 / 视觉特效学生": "student",
    "技术动画师 / 技术总监": "technical_animator",
    "游戏 / 电影动画从业者": "game_film",
    "研究人员": "researcher",
}


def el_text(el) -> str:
    return "".join(el.xpath('.//*[local-name()="t"]/text()'))


def element_selected(el) -> bool:
    for c in el.xpath('.//*[local-name()="checked"]'):
        if c.get("{http://schemas.microsoft.com/office/word/2010/wordml}val") in ("1", "true", "on"):
            return True
    txt = el_text(el)
    if "☒" in txt or "■" in txt:
        return True
    for x in el.xpath('.//*[local-name()="highlight"]'):
        val = (x.get(qn("w:val")) or "").lower()
        if val not in ("", "none", "auto"):
            return True
    fills = [(x.get(qn("w:fill")) or "").lower() for x in el.xpath('.//*[local-name()="shd"]')]
    return "e06666" in fills


def para_segments(para):
    out = []
    for ch in para._p:
        if etree.QName(ch).localname in ("r", "sdt", "hyperlink"):
            txt = el_text(ch)
            if txt:
                out.append((txt, element_selected(ch)))
    return out


def selected_checkbox_labels(para):
    labels, current = [], None
    for txt, selected in para_segments(para):
        if "☐" in txt or "☒" in txt:
            if current:
                labels.append(current)
            current = {
                "selected": selected or "☒" in txt,
                "label": txt.replace("☐", "").replace("☒", "").strip(),
            }
        elif current is not None:
            current["label"] += txt
    if current:
        labels.append(current)
    for item in labels:
        item["label"] = " ".join(item["label"].split())
    return labels


def cell_selected(cell) -> bool:
    return element_selected(cell._tc)


def rating_from_row(row):
    selected = [i for i in range(1, min(8, len(row.cells))) if cell_selected(row.cells[i])]
    return selected[0] if len(selected) == 1 else None


def cell_all_text(cell) -> str:
    return el_text(cell._tc).strip()


def get_task_ids(doc):
    ids = []
    for para in doc.paragraphs:
        full = el_text(para._p)
        if "场景 / 任务 ID" in full:
            m = re.search(r"(?:Seq|sec|S)\s*([1-6])", full, re.I)
            ids.append(f"Seq{m.group(1)}" if m else None)
    return ids[:4]


def get_years(doc):
    for para in doc.paragraphs:
        full = el_text(para._p)
        if "您有多少年与动画制作" in full:
            rest = full.split("？", 1)[-1].strip()
            m = re.match(r"\s*(\d+)", rest)
            if m:
                return float(m.group(1))
            nums = [int(x) for x in re.findall(r"\d+", rest)]
            if len(nums) == 1:
                return float(nums[0])
    return None


def get_roles(doc):
    roles = []
    for para in doc.paragraphs:
        if "专业动画师" in para.text or "游戏 / 电影动画从业者" in para.text:
            for item in selected_checkbox_labels(para):
                if item["selected"]:
                    for label, key in ROLE_MAP.items():
                        if label in item["label"]:
                            roles.append(key)
    return sorted(set(roles))


def get_binary(doc, prefix):
    for para in doc.paragraphs:
        if para.text.strip().startswith(prefix):
            for item in selected_checkbox_labels(para):
                if item["selected"]:
                    if item["label"].startswith("是"):
                        return True
                    if item["label"].startswith("否"):
                        return False
    return None


def tlx_table(table):
    out = {}
    for i, name in enumerate(TLX_NAMES, start=1):
        txt = cell_all_text(table.rows[i].cells[3])
        m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", txt)
        out[name] = float(m.group(1)) if m else None
    return out


def context_table(table):
    out = {}
    for i, name in enumerate(CONTEXT_CRITERIA, start=1):
        def parse(c):
            s = cell_all_text(c)
            nums = [int(x) for x in re.findall(r"(?<!\d)([1-7])(?!\d)", s)]
            if "输入" in s and len(nums) > 1:
                return None
            return nums[0] if len(nums) == 1 else None
        out[name] = {"A": parse(table.rows[i].cells[1]), "B": parse(table.rows[i].cells[2])}
    return out


def get_preferences(doc):
    prefs = []
    for para in doc.paragraphs:
        if "您更愿意用哪个方案作为制作这个场景动画的起点" not in para.text:
            continue
        chosen = [x["label"] for x in selected_checkbox_labels(para) if x["selected"]]
        if len(chosen) != 1:
            prefs.append("Ambiguous" if chosen else None)
        elif "方案 A" in chosen[0]:
            prefs.append("A")
        elif "方案 B" in chosen[0]:
            prefs.append("B")
        else:
            prefs.append("None")
    return prefs[:6]


def parse_questionnaire(path: Path):
    doc = Document(path)
    t = doc.tables
    task_ids = get_task_ids(doc)
    tasks = []
    for idx, table_idx in enumerate([0, 1, 4, 5]):
        table = t[table_idx]
        tasks.append({
            "task": idx + 1,
            "condition": "Direct" if idx < 2 else "Plan",
            "scene": task_ids[idx] if idx < len(task_ids) else None,
            "match": rating_from_row(table.rows[1]),
            "satisfaction": rating_from_row(table.rows[2]),
        })
    workflow = {}
    for condition, table_idx in [("Direct", 2), ("Plan", 6)]:
        workflow[condition] = [rating_from_row(t[table_idx].rows[i]) for i in range(1, 10)]
    return {
        "pid": path.stem,
        "roles": get_roles(doc),
        "years": get_years(doc),
        "maya": get_binary(doc, "3."),
        "other_keyframe": get_binary(doc, "4."),
        "facial": get_binary(doc, "5."),
        "procedural": get_binary(doc, "6."),
        "jali": get_binary(doc, "8."),
        "tasks": tasks,
        "workflow": workflow,
        "tlx": {"Direct": tlx_table(t[3]), "Plan": tlx_table(t[7])},
        "diagnostics": [rating_from_row(t[8].rows[i]) for i in range(1, 4)],
        "context": [context_table(t[9+i]) for i in range(6)],
        "preferences": get_preferences(doc),
    }


def rank_biserial(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None and not (pd.isna(a) or pd.isna(b))]
    d = np.array([b - a for a, b in pairs], dtype=float)
    d = d[d != 0]
    if len(d) == 0:
        return 0.0
    ranks = rankdata(np.abs(d), method="average")
    wp = ranks[d > 0].sum()
    wm = ranks[d < 0].sum()
    return float((wp - wm) / (wp + wm))


def paired_summary(x, y):
    pairs = [(float(a), float(b)) for a, b in zip(x, y) if a is not None and b is not None and not (pd.isna(a) or pd.isna(b))]
    xa = np.array([a for a, _ in pairs])
    ya = np.array([b for _, b in pairs])
    result = {
        "n": len(pairs),
        "mean_x": float(xa.mean()),
        "sd_x": float(xa.std(ddof=1)),
        "median_x": float(np.median(xa)),
        "mean_y": float(ya.mean()),
        "sd_y": float(ya.std(ddof=1)),
        "median_y": float(np.median(ya)),
        "mean_diff": float((ya-xa).mean()),
        "r_rb": rank_biserial(xa, ya),
    }
    try:
        test = wilcoxon(ya, xa, zero_method="wilcox", alternative="two-sided", method="auto")
        result.update({"wilcoxon_W": float(test.statistic), "p_raw": float(test.pvalue)})
    except ValueError:
        result.update({"wilcoxon_W": None, "p_raw": 1.0})
    return result


def holm_adjust(values):
    p = np.asarray(values, dtype=float)
    order = np.argsort(p)
    out = np.empty(len(p))
    running = 0.0
    for k, idx in enumerate(order):
        running = max(running, (len(p) - k) * p[idx])
        out[idx] = min(1.0, running)
    return out


def apply_holm(stats):
    keys = list(stats)
    adj = holm_adjust([stats[k]["p_raw"] for k in keys])
    for k, p in zip(keys, adj):
        stats[k]["p_holm"] = float(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questionnaire-dir", type=Path, required=True)
    ap.add_argument("--assignment-manifest", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument(
        "--include-ids",
        default=",".join(f"U{i:02d}" for i in range(1, 11)),
        help="Comma-separated participant IDs to include (default: U01-U10).",
    )
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    included_ids = [x.strip() for x in args.include_ids.split(",") if x.strip()]
    if not included_ids:
        raise ValueError("--include-ids resolved to an empty participant list")
    missing = [pid for pid in included_ids if not (args.questionnaire_dir / f"{pid}.docx").exists()]
    if missing:
        raise FileNotFoundError(f"Missing questionnaire files for: {', '.join(missing)}")
    data = {pid: parse_questionnaire(args.questionnaire_dir / f"{pid}.docx") for pid in included_ids}
    assignment = json.loads(args.assignment_manifest.read_text(encoding="utf-8"))

    task_stats = {}
    for measure in ("match", "satisfaction"):
        direct = [np.mean([t[measure] for t in data[pid]["tasks"][:2] if t[measure] is not None]) for pid in included_ids]
        plan = [np.mean([t[measure] for t in data[pid]["tasks"][2:] if t[measure] is not None]) for pid in included_ids]
        task_stats[measure] = paired_summary(direct, plan)
    apply_holm(task_stats)

    workflow_stats = {}
    for j, name in enumerate(WORKFLOW_NAMES):
        workflow_stats[name] = paired_summary(
            [data[pid]["workflow"]["Direct"][j] for pid in included_ids],
            [data[pid]["workflow"]["Plan"][j] for pid in included_ids],
        )
    apply_holm(workflow_stats)

    tlx_stats = {}
    for name in TLX_NAMES:
        tlx_stats[name] = paired_summary(
            [data[pid]["tlx"]["Direct"][name] for pid in included_ids],
            [data[pid]["tlx"]["Plan"][name] for pid in included_ids],
        )
    apply_holm(tlx_stats)

    context_rows, preference_rows = [], []
    for pid in included_ids:
        for scene_idx, (pair, rating, pref) in enumerate(zip(assignment[pid], data[pid]["context"], data[pid]["preferences"]), start=1):
            a_code, b_code = pair
            a_cond = "DialogueOnly" if re.match(r"S\dD\d", a_code) else "FullContext"
            b_cond = "DialogueOnly" if re.match(r"S\dD\d", b_code) else "FullContext"
            row = {"participant": pid, "scene": scene_idx, "A_source": a_code, "B_source": b_code}
            for criterion in CONTEXT_CRITERIA:
                row[f"{criterion}_{a_cond}"] = rating[criterion]["A"]
                row[f"{criterion}_{b_cond}"] = rating[criterion]["B"]
            context_rows.append(row)
            if pref == "A":
                decoded = a_cond
            elif pref == "B":
                decoded = b_cond
            elif pref == "None":
                decoded = "None"
            else:
                decoded = pref
            preference_rows.append({"participant": pid, "scene": scene_idx, "preference": decoded})

    ctx = pd.DataFrame(context_rows)
    context_stats = {}
    for criterion in CONTEXT_CRITERIA:
        direct, full = [], []
        for pid in included_ids:
            sub = ctx[ctx.participant == pid]
            direct.append(sub[f"{criterion}_DialogueOnly"].mean())
            full.append(sub[f"{criterion}_FullContext"].mean())
        context_stats[criterion] = paired_summary(direct, full)
    apply_holm(context_stats)

    summary = {
        "usable_n": len(included_ids),
        "included_ids": included_ids,
        "analysis_note": "Participant IDs are explicitly supplied; verify all included files are real human-subject observations.",
        "study_a_task": task_stats,
        "study_a_workflow": workflow_stats,
        "study_a_tlx": tlx_stats,
        "study_b_context": context_stats,
        "study_b_preferences": pd.DataFrame(preference_rows)["preference"].value_counts(dropna=False).to_dict(),
    }
    (args.output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ctx.to_csv(args.output_dir / "studyb_context_decoded.csv", index=False)
    pd.DataFrame(preference_rows).to_csv(args.output_dir / "studyb_preferences_decoded.csv", index=False)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
