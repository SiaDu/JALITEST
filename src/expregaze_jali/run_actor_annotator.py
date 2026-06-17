from __future__ import annotations

from expregaze_jali.run_actor_llm import _build_openai_request


def main() -> None:
    raise RuntimeError(
        "run_actor_annotator.py is deprecated. Use the numbered scripts instead: "
        "00_build_actor_prompt.sh, 01_run_actor_llm.sh, 02_parse_textgrid.sh, "
        "03_compile_actor_annotation.sh, 04_validate_actor_outputs.sh."
    )


if __name__ == "__main__":
    main()
