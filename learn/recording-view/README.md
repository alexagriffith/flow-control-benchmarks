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
