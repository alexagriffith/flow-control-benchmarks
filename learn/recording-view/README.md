# Recording view

Serve the repository and open `learn/recording-view/?page=8` (zero-based page number). Designed for a 1440 × 810 or 1920 × 1080 recording viewport.

- Recordings contain one scene heading and the visual. Page narration is hidden; diagram labels, measured chart labels/sources and the simulation disclosure remain. Intro diagrams and evidence cards use their embedded heading; architecture scenes use the page heading.
- **Clean frame** hides the recording toolbar. Escape restores it. Add `&clean=1` to start without it.
- Use the chapter selector and Back/Next before recording. Existing learner keyboard controls still operate inside the frame.
- Replay retains its actual controls and simulation disclosure. Evidence chapters enlarge the actual measured chart and retain its source and limitations.
- The regular learner is loaded in a same-origin iframe. Only that document receives the recording stylesheet. No scene, benchmark data, or original learner file is copied or modified.
- This requires an HTTP server (same-origin iframe access); opening the wrapper with `file://` is unsupported.
- The adapter depends on documented selectors in `recording-view.js`. A missing required selector shows an error rather than silently producing incomplete footage. Recheck the wrapper after structural learner changes.
- This is a presentation view, not a video encoder. Browser or Playwright recording captures the existing animations at their normal speed.

Run the recording-view browser check with the repository served on the URL supplied to `check_recording_view.py`. Screenshots and a source hash are written to the output directory. No changes to the regular learner are needed to enable or remove this package.

## Chapter 3 motion export

Open `threshold-demo.html` for the eight-second Chapter 3 recording. It reads the current learner's SVG plots in a hidden same-origin iframe; measured points and schematic paths are unchanged. The measured curves reveal together by concurrency, followed by the schematic comparison and candidate-region emphasis. The completed result holds for one second.

Use **Play** or the time slider to review, then **Export frames** and **Download frames**. Encode the 480 JPEGs at 60 fps with ffmpeg. Export is deterministic and excludes the preview controls. Check the encoded clip's labels and progression, not only its opening/final frames; the previous 12-second recording contained long near-static holds. This recording-only page does not modify the normal learner or its automatic playback.

## Chapter 4 admission animation

`policy-demo.html` exports 840 frames at 60 fps (14 seconds, 1000×650). Each ceiling sits on its queue's exit path. Dots approach the check while queued; crossing it reserves admission pressure. Pressure includes transit, so it rises before the dot settles inside the pool. Completions move right and release pressure at the end of that movement. The vertical arrangement inside the pool is illustrative spacing, not separate endpoints.

Run `node learn/recording-view/check-policy-motion.mjs` from the repository root before exporting. It checks the renderer's shared geometry at every frame for request conservation, collisions, backward motion, position jumps and crossing the admission check. The model separately checks ceilings, priority and slot reuse. Inspect rendered phone-size frames and the encoded video too; mechanical checks do not establish visual acceptance. The normal learner is unchanged.

## Chapter 5 fairness comparison

`fairness-comparison.html` is the published 21.6-second comparison: Round Robin and Global Strict, both using First-Come, First-Served ordering. It preserves the accepted canvas style and request identities; the policy-lab expands this into nine controlled combinations without changing that published clip.

## Interactive policy comparison

`../policy-lab/` provides Round Robin, Global Strict, and Program-Aware (LAS), each paired with FCFS, EDF, or SLO Deadline. `?suite=1&ordering=edf` (or `fcfs`, `slo`) exports a 32.4-second comparison at 60 fps. Play, pause, reset, seek, and policy changes use the same deterministic state model as export.

Run `node learn/policy-lab/check.mjs`. It checks all nine dispatch orders, deadline tie breaks, LAS normalization, request identity, collisions, and backward motion over 5,832 frames. Compare selected request labels with the visible queue-head deadline during each departure. Inputs, assumptions and pinned router source are available in the page's details.

The maintained learner is `flow-control-interactive.html`; older journey links redirect and retain their fragment. Keep the policy-lab files byte-identical to the inference hub's `shared-inference/policy-lab/` copy. Published-video courses contain only confirmed published posts; preparing a comparison is not publication.
