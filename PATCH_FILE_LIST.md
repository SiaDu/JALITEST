# Prompt / blink strategy patch

I could not push directly through the GitHub connector because the GitHub integration returned `Resource not accessible by integration`.
Apply these files into the repo manually, then commit/push locally.

## Changed files

1. `configs/maya/jali_proto_candidate_001_jali_annotation.yaml`
   - disables JALI automatic blink during annotation injection
   - removes `ambient_gaze_intensity: 1` from annotation injection, because gaze adapter owns ambient gaze

2. `src/expregaze_jali/eye_performance_event_exporter.py`
   - keeps regulatory blink rules, but makes v1 default conservative
   - disables gaze/mask/heart/lid reset blinks by default
   - keeps long no-blink subtle blinks enabled
   - adds config flags for enabling reset blinks later

3. `src/expregaze_jali/process_performance_annotation.py`
   - passes regulatory blink config into the eye exporter
   - adds CLI flags:
     - `--regulatory-from-gaze`
     - `--regulatory-from-mask`
     - `--regulatory-from-heart`
     - `--regulatory-from-lid-state`
     - `--no-long-gap-regulatory-blinks`
     - `--regulatory-min-gap-frames`
     - `--long-gap-seconds`
     - `--gaze-blink-offset-frames`

4. `prompts/actor_performance_annotation_prompt.md`
   - new actor-first prompt
   - forces character/performance profile before tagging
   - restricts lid_state and performative blink overuse

5. `data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__script.txt`
   - regenerated annotation
   - fewer lid_state changes
   - only one performative blink
   - blink_suppression used for sustained mystical intensity

6. `README_BLINK_UPDATE.md`
   - updated documentation for actor-first prompt and conservative regulatory blink default

## After applying

Run:

```bash
./scripts/regenerate_jali_proto_candidate_001.sh
```

Then in Maya:

```python
exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\src\maya\run_apply_jali_annotation.py", encoding="utf-8").read())
exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\src\maya\run_apply_gaze_events.py", encoding="utf-8").read())
exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\src\maya\run_apply_eye_performance_events.py", encoding="utf-8").read())
```
