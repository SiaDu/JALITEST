#!/usr/bin/env python3
"""Generate paper-ready user-study figures from the analysis CSV summaries."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Study A task outcomes
    df = pd.read_csv(args.input_dir / "studya_task_summary.csv")
    labels = df["measure"].tolist()
    x = np.arange(len(labels)); w = 0.36
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    ax.bar(x-w/2, df["Direct_mean"], w, yerr=df["Direct_sd"], capsize=4, label="Direct Generation")
    ax.bar(x+w/2, df["Plan_mean"], w, yerr=df["Plan_sd"], capsize=4, label="Editable Performance Plan")
    ax.set_ylabel("Mean rating (1–7)"); ax.set_ylim(1, 7)
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=10, ha="right")
    ax.set_title("Study A: Final Performance Ratings"); ax.legend(); ax.grid(axis="y", alpha=0.2)
    fig.tight_layout(); fig.savefig(args.output_dir / "fig_studya_task_outcomes.png", dpi=220); plt.close(fig)

    # Study A workflow
    df = pd.read_csv(args.input_dir / "studya_workflow_summary.csv")
    labels = ["Express direction","Local revision / preserve","Revision clarity","Final-direction control","Dyadic coordination","Visual ↔ semantic iteration","Creative ownership","Explore alternatives","Effort worthwhile"]
    y = np.arange(len(labels)); h = 0.34
    fig, ax = plt.subplots(figsize=(9.5, 7.0))
    ax.barh(y+h/2, df["Direct_mean"], h, xerr=df["Direct_sd"], capsize=3, label="Direct Generation")
    ax.barh(y-h/2, df["Plan_mean"], h, xerr=df["Plan_sd"], capsize=3, label="Editable Performance Plan")
    ax.set_xlabel("Mean rating (1–7)"); ax.set_xlim(1, 7); ax.set_yticks(y); ax.set_yticklabels(labels); ax.invert_yaxis()
    ax.set_title("Study A: Workflow Ratings"); ax.legend(); ax.grid(axis="x", alpha=0.2)
    fig.tight_layout(); fig.savefig(args.output_dir / "fig_studya_workflow_items.png", dpi=220); plt.close(fig)

    # NASA-TLX
    df = pd.read_csv(args.input_dir / "studya_tlx_summary.csv")
    labels = ["Mental","Physical","Temporal","Performance*","Effort","Frustration"]
    x = np.arange(len(labels)); w = 0.36
    fig, ax = plt.subplots(figsize=(9.2, 5.8))
    ax.bar(x-w/2, df["Direct_mean"], w, yerr=df["Direct_sd"], capsize=3, label="Direct Generation")
    ax.bar(x+w/2, df["Plan_mean"], w, yerr=df["Plan_sd"], capsize=3, label="Editable Performance Plan")
    ax.set_ylabel("Raw NASA-TLX score (0–100)"); ax.set_xticks(x); ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_title("Study A: Raw NASA-TLX"); ax.legend(); ax.grid(axis="y", alpha=0.2)
    ax.text(0.01, -0.20, "*Performance: 0 = perfect, 100 = failure.", transform=ax.transAxes, fontsize=9)
    fig.tight_layout(); fig.savefig(args.output_dir / "fig_studya_nasa_tlx.png", dpi=220); plt.close(fig)

    # Context ablation
    df = pd.read_csv(args.input_dir / "studyb_context_summary.csv")
    labels = ["Overall fit","Intent","Affect","Gaze","Timing"]
    x = np.arange(len(labels)); w = 0.36
    fig, ax = plt.subplots(figsize=(9.0, 5.6))
    ax.bar(x-w/2, df["DialogueOnly_mean"], w, yerr=df["DialogueOnly_sd"], capsize=3, label="Dialogue-Only")
    ax.bar(x+w/2, df["FullContext_mean"], w, yerr=df["FullContext_sd"], capsize=3, label="Full-Context")
    ax.set_ylabel("Mean appropriateness rating (1–7)"); ax.set_ylim(1, 7); ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title("Study B: Context Ablation"); ax.legend(); ax.grid(axis="y", alpha=0.2)
    fig.tight_layout(); fig.savefig(args.output_dir / "fig_studyb_context_ablation.png", dpi=220); plt.close(fig)

    # Preferences
    df = pd.read_csv(args.input_dir / "studyb_preference_summary.csv")
    df = df[df["preference"].isin(["FullContext", "DialogueOnly", "No preference"])]
    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    ax.bar(df["preference"], df["count"]); ax.set_ylabel("Number of scene-level preferences")
    ax.set_title("Study B: Preferred Starting Plan"); ax.grid(axis="y", alpha=0.2)
    fig.tight_layout(); fig.savefig(args.output_dir / "fig_studyb_preferences.png", dpi=220); plt.close(fig)


if __name__ == "__main__":
    main()
