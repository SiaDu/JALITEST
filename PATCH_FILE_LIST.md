# JALITEST blink/lid patch file list

## Replace existing files

1. `src/expregaze_jali/performance_annotation_parser.py`
   - Adds support for `l##`, `pb##`, `bs##` tags.
   - Old version only parsed `g/m/h`.

2. `src/expregaze_jali/performance_event_compiler.py`
   - Adds event compilation for:
     - `lid_state`
     - `performative_blink`
     - `blink_suppression`
   - Keeps `performative_blink` as an anchor event, not a state-change event.

3. `src/expregaze_jali/performance_event_resolver.py`
   - Adds resolved event arrays for new event types.

4. `configs/maya/jali_proto_candidate_001_gaze.yaml`
   - Updates `fps` to `30.0`.
   - Keeps gaze adapter settings from the current patch.

5. `data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__script.txt`
   - Replaces the old gaze/mask script with the new unified annotation including:
     - `l##`
     - `pb##`
     - `bs##`

## New files

6. `src/expregaze_jali/eye_performance_event_exporter.py`
   - Exports:
     - `lid_state_events`
     - `performative_blink_events`
     - `blink_suppression_events`
     - generated `regulatory_blink_events`

7. `src/expregaze_jali/maya_apply_eye_performance.py`
   - Applies blink/lid events to:
     - `LIDS_jSync_plusMinus.Down_upLids_jSync`
   - Clears old keys before applying.
   - Can disable JALI automatic blinks.

8. `src/maya/run_apply_eye_performance_events.py`
   - Maya runner for the eye performance overlay.

9. `configs/maya/jali_proto_candidate_001_eye.yaml`
   - Maya config for eyelid/blink application.

10. `data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__eye_performance_events_resolved.json`
    - Sample resolved eye performance events for immediate Maya testing.

11. `README_BLINK_UPDATE.md`
    - README section to paste/merge into the main README.

## Old/stale generated files to regenerate, not manually edit

- `*_annotated_for_jali.txt`
- `*_gaze_events_resolved.json`
- `*_debug_full_annotation.txt`

They are generated outputs. After this patch, regenerate them from the updated script so they match the new parser/compiler.
