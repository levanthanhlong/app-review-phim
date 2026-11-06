# PLAN: Make the "Nhân vật" tab easier to name people in

Status: **implemented.** For an existing project, use **Cập nhật ảnh** in the Nhân vật tab, or run
`recap characters --project P` to generate the new portraits using the face cache.
Talk to the user in Vietnamese. Read `docs/CLAUDE.md` and `docs/SPEC_RecapStudio.md` §4.3c and §7.2 (3b) first.

## Why

The `characters` stage groups faces into people and shows each one as a card with 6 face pictures, so
the user can name them (the names only help `describe` and `script` call people correctly; nothing
else uses them). The user says many pictures are too unclear to tell who is who:

- pictures are 160 px, cropped tight around the face (`PICTURE_SIZE = 160`, `PICTURE_MARGIN = 0.3`);
- the 6 samples are the *biggest* faces (`w*h*score`), not the sharpest, so dark or blurred frames win
  (the Thai test film is dark; many samples are smeared).

## The three changes (do all three, in this order: 3, 2, 1)

### 3. Pick the sharpest, well-lit faces as samples (worker)

- `worker/recap/characters.py`: `build_characters` keeps choosing `samples` by size, but as
  **candidates**: keep the top `CANDIDATES = 20` (new constant) instead of `SAMPLES = 6`. It stays pure
  (no pixels).
- `worker/recap/stages/characters.py`, `_pictures`: for each character, crop every candidate from the
  frames, score it, keep the best `SAMPLES` (6). Score = sharpness (variance of the Laplacian of the
  grey face crop, `cv2.Laplacian(gray, cv2.CV_64F).var()`), with very dark crops (mean grey < ~40) or
  very blurred ones pushed to the end. Put the pure scoring in a small function
  (e.g. `rank_samples(crops) -> order`) that is unit-tested with synthetic images (sharp checkerboard
  beats the same image blurred; a black image loses).
- Pictures still get written as `work/characters/faces/...`, only for the chosen 6. `characters.json`
  keeps listing them under `pictures` (relative to `work/`).
- Frames: `_pictures` already re-extracts frames when pictures are missing (`self._frames`), and the
  `finally` in `run` deletes `work/characters/frames`. Keep that. Faces stay cached in
  `work/characters/faces.npz` (key = video signature + `FACES_VERSION`). **Do not bump
  `FACES_VERSION`**: that would re-detect all faces and reset the user's naming (`_keep_or_reset_choices`).

### 2. Bigger pictures showing more of the person (worker + app)

- `worker/recap/stages/characters.py`: `PICTURE_SIZE = 320`; crop wider than the face so hair,
  shoulders and upper body show (e.g. margin ~0.8 around the box, shifted down so the face sits in the
  upper third; clamp to the frame). Portrait 3:4 is fine (e.g. 240×320) if it looks better; then the
  app must not force squares.
- Write the new pictures to a **new folder** (e.g. `PICTURES_DIR = "work/characters/faces2"`) so old
  160 px files are not reused (`_pictures` only crops pictures that are missing). Old folder can be
  left alone (or removed by the stage once the new ones exist).
- `app/RecapStudio/CharacterCard.swift`: pictures are drawn with `Thumbnail(url:cornerRadius:)` at
  `pictureSize = 64`. Show the first (best) picture large (~160 pt) and the other five small beside or
  under it; keep the card readable in the `LazyVGrid(columns: [GridItem(.adaptive(minimum: 320))])`
  of `CharactersView.swift` (raise the minimum if needed).
- Kịch bản never names people by clothes (rule in `write_script.md` / `narration_guide.md`); the wider
  crop is only for the user's eyes.

### 1. Click a card to watch the scenes the person is in (app only, most useful)

- Data: `work/characters.json` → each character has `beats` (ids, film order). Beat times come from
  `SceneBeats.load(from:)` (RecapKit, `SceneBeat.start/end`). Video: `work/proxy_480p.mp4`
  (`SceneBrowserView.proxyPath`).
- Reuse `PlayerController` (`app/RecapStudio/PlayerController.swift`): `load(url)`, then
  `play(from: start, to: end)` plays a range and pauses. `SceneBrowserView` does the same
  (`playSelected`) with `VideoPlayer(player: controller.player)` from AVKit.
- New view (e.g. `app/RecapStudio/CharacterScenesView.swift`): opened by clicking a card (sheet or a
  detail column in `CharactersView`'s `HSplitView`, which today shows `HeardNames` on the right).
  Shows: the player (16:9), the character's id + current name, and the list of its beats (keyframe
  thumbnail `SceneBeat.keyframeURL(in:)`, time, dialogue, description). Clicking a beat plays it;
  open the view playing the first beat. Keep the name field / gender picker reachable from there
  (bind to the same `CharacterChoices` draft) so the user can name while watching.
- Optional, only if cheap: beats can be 10–30 s long while the person appears only part of it. The
  stage could also write each character's face times (seconds; the `Face.time` values already exist)
  into `characters.json` as `"times"`, and the view would play from `time - 2 s` to `time + 3 s`.
  If added, decode it as optional in `FilmCharacter` (old reports lack it).
- Stop playback when the view closes (`controller.pause()` in `onDisappear`).

## What must not change

- Character ids, grouping and `faces_key`: the user's saved naming (`work/character_choices.json`)
  must survive a re-run (there is a test: `test_the_users_naming_survives_a_rerun_with_the_same_faces`).
- The `characters` stage fingerprint must not change in a way that marks `describe`/`script` stale:
  `describe` and `script` fingerprints depend only on the cast signature (names per beat), so new
  pictures do not stale them. The new portraits directory is a required stage output. Running
  `characters` normally on a project with legacy pictures generates the portraits from its face cache.
- `cast_by_beat`, the `describe` names line, the script cast rule: untouched.

## Tests (TDD: write them first, see them fail)

- Worker (`worker/tests/`): pure ranking test (sharp > blurred > black); `build_characters` returns
  up to `CANDIDATES` candidates; stage test in `test_characters_stage.py` (it uses a fake reader and a
  generated clip): pictures are 320 px, there are at most 6 per character, they are in the new folder,
  and the naming survives the re-run.
- Swift (`app/RecapKit/Tests/RecapKitTests/CharactersTests.swift`): if `times` is added, decoding with
  and without it.
- Commands: `cd worker && .venv/bin/python -m pytest -q` (all ~494 must pass);
  `/usr/bin/xcrun swift test --package-path app/RecapKit`;
  `cd app && xcodegen generate && xcodebuild -project RecapStudio.xcodeproj -scheme RecapStudio -destination "platform=macOS,arch=arm64" -derivedDataPath build build`.
- After editing source files run `codegraph sync` from the repo root.

## Try it for real

On `output/Thất Tình (Thái Lan).recap` (already named: C01 Cha (nữ), C05 Hiro (nam), …):

```
cd worker && .venv/bin/python -m recap characters --project "../output/Thất Tình (Thái Lan).recap"
```

Check: `work/character_choices.json` unchanged; describe/script still `done` in `project.json`;
look at a few new pictures (e.g. combine them into one image with PIL and Read it) and compare with
the old 160 px ones. Report to the user how many seconds the re-run took.

## Constraints of this machine and project

- Everything lives on `/Volumes/ExternalSSD`; the internal disk is almost full. Temporary big files
  go to `/Volumes/ExternalSSD/tmp`, never the internal disk.
- **Do not run anything on a project the app is processing** (check with
  `ps aux | grep "[-]m recap"`). At the time of writing the app was running
  `run --from describe` on `output/Tee Yod- Quỷ Ăn Tạng Phần 1 Vietsub.recap`.
- The user's app runs from `/Applications/RecapStudio.app`; builds go to `app/build/` and do not
  replace it. Installing is the user's call (`./make_dmg.sh`).
- Do not commit unless the user asks. Never mention cost or tokens.
- Update `docs/SPEC_RecapStudio.md` §4.3c (pictures: size, how samples are chosen) and §7.2 3b (the
  scenes view) when done.
