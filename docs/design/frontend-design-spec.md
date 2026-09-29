# Fake Sangeet — frontend design spec

Binding for every screen in `apps/web`. If a screen contradicts this file, the file wins.

**Reference material** is the societies' own creative: the A2 film-reel poster
(`docs/design/reference/poster-1.jpg`), the spons deck (yellow ground, rani
scalloped plaque, hanging marigolds) and the pitch deck (corner mandalas,
firework sparkles). The site has to feel like the same event students saw on
Instagram.

## The event

**Fake Sangeet — "Band Baaja No Baraat."**
A mock Indian wedding thrown by **Inferno** (SNU's dance society) and
**Snuphoria** (SNU's music society), at **UAC Terrace, 15 October 2026**.

This is not a wedding invitation. It is a **college festival poster**.

## The idea

**A retro Bollywood film poster you can hold your ticket in.**

The one signature is **the poster's scarlet film reel**. It snakes behind the
landing title, becomes the header bar, the ops rail, the scanner's bands, the
section dividers, the loading spinner, and — the one bold element — the pass,
which is a length of film cut into a ticket. Everything else stays quieter than
the pass.

## History

1. A restrained letterpress invitation (ivory, maroon). Wrong for this client —
   reversed.
2. The first poster treatment: right palette, but flat-only (no shadows, no
   texture) and one bordered card on an empty field. Read sparse.
3. **This version (v2, Sep 2026).** Same identity, loosened rules: hard offset
   shadows on cut-outs, paper grain, the reel as the running motif, a design
   system of primitives. Design record:
   `docs/superpowers/specs/2026-09-29-frontend-poster-v2-design.md`.

## Colour

Light-only — no dark mode, no theme toggle. `src/styles/tokens.css` is the only
palette definition.

| token | hex | role |
|---|---|---|
| `--ochre` | `#E7A93A` | page ground, with a printed grain |
| `--marigold` | `#FFC61A` | display fill, highlights, the active frame |
| `--scarlet` | `#C8102E` | the reel, primary actions |
| `--rani` | `#E0147C` | the scalloped plaque, warnings as a fill |
| `--ink` | `#4A0A14` | text, outlines, extrusions, hard shadows |
| `--paper` | `#FFF3DC` | reading surfaces |
| `--state-good` | `#2F5D1E` | status only: inside, admitted, approved |
| `--state-warn` | `#B3005F` | status text on paper (rani is too light for text) |

**Contrast rules.** Long reading text lives on paper. Ink may sit on bare ochre
(7.5:1). Rani is never a text colour. Scarlet is never text on ochre (2.8:1).
Checked pairings (body needs 4.5, large/display 3):

| pairing | ratio |
|---|---|
| ink on paper | 14.2 |
| ink on ochre | 7.5 |
| muted on paper | 6.4 |
| scarlet on paper / paper on scarlet | 5.4 |
| state-good on paper | 7.1 |
| state-warn on paper | 6.2 |
| paper on rani (large only) | 4.2 |
| marigold on scarlet (large only) | 3.7 |

The hero's marigold-on-rani title (2.9) is legible only because every letter
carries an ink outline (ink on rani 3.4, marigold on ink 9.9). Never set
marigold on rani without that outline.

## Type

- **Display — Shrikhand.** Heavy, italic, hand-painted Indian signage; the
  closest free face to the poster's movie-title lettering. Titles, the holder's
  name on the pass, the gate name, scan verdicts. Via `DisplayTitle`: fill,
  8-way ink outline, stacked extrusion, all hard `text-shadow`s in `em`.
- **Body, UI and numerals — Mukta.** 400–800. Stat numbers at 800, tabular.

Sentence case everywhere. No `text-transform`. Scale: 1.25, 16px base, tokens
`--text-xs` … `--text-4xl`.

## Depth and texture

- **Hard offset shadows only** (`--lift-sm/--lift/--lift-lg`, and
  `drop-shadow(Npx Npx 0)` for masked shapes). Never a blur, never a glow.
- Only **cut-outs** get a lift: the pass, the hero plaque and date tag, primary
  buttons, the decision card, the ops rail. Reading panels stay flat — a shadow
  on every panel is the SaaS-card look this is avoiding.
- **Texture** is pattern, never a wash behind text: the ochre ground's paper
  grain (inline SVG turbulence), the rubber-stamp ink mask, the halftone on
  scan result fields.

## Components — `src/components/`, imported from the barrel

| | |
|---|---|
| `Paper` | reading surface; `lift`, `tilt`, `tape`, `pad` |
| `Plaque` | spons-deck frame: scooped corners, ink outline, dotted inner rule; `rani/scarlet/ink/paper` |
| `DisplayTitle` | Shrikhand with extrusion; tone `marigold` (dark grounds), `ink` (ochre), `scarlet` (paper) |
| `FilmStrip` | sprocket band, or a length of film around children |
| `Reel` | the snaking hero reel, drawn on once |
| `Garland`, `MandalaCorner`, `Sparkle` | ornament: aria-hidden, no pointer events, behind content |
| `Button`, `ButtonLink`, `buttonClass` | `primary/secondary/marigold/good/danger/quiet`, `md/lg`, `block` |
| `Field`, `Checkbox` | labelled input with hint/error wired; inked checkbox |
| `PhotoPicker` | viewfinder frame for the ID photo (the input stays with the screen) |
| `Stamp` | every "what state is this in" answer: glyph + word + tone |
| `Notice` | inline message: `info/wait/good/warn/error`, glyph tab + words |
| `Stat`, `StatGrid` | ticket-stub stat tiles; `feature` for the one number a board is about |
| `Loading`, `ReelSpinner`, `EmptyState` | waiting and nothing-to-show states |

Screens compose these. A screen that hand-rolls a button, a stat tile or a
status line is a bug.

## Layout

Mobile-first. Participant screens: one centred column, `--content-max: 34rem`.
`/ops` and `/gate`: `--wide-max: 72rem`, ops rail on the left (a strip across
the top below 860px). Ceremonial surfaces (landing, pass) centre; working
surfaces (forms, review queue) are left-aligned.

Header: the reel as a bar — lockup on an ink tile left, `event.svg` on a small
paper plaque centre with the tagline, identity right. At 640px and below the
lockup and identity share a row and the wordmark takes the second.

## Motion

- **One orchestrated moment per ceremonial screen.** Landing: the reel draws
  on, the plaque stamps in, the date tag pins on. Pass (first view only): the
  ticket drops, the film advances through its sprockets, the QR is projected
  on, the state is stamped.
- **Motion that answers an action** is welcome: buttons press into their
  shadow, the decision card is dealt in, scan results thump on, the checkbox
  tick draws, the decision countdown drains.
- The scanner's sweep line and the header reel (while a scan is checking) are
  the only loops, and both say something is live.
- Everything is off under `prefers-reduced-motion: reduce` (global rule in
  `base.css`).

## Voice

Plain, warm, specific. Sentence case. Active voice. The button says what
happens and keeps its name through the flow. Copy is specified in
`participant-screens.md` and is final — a re-skin changes how screens look, not
what they say.

## Quality floor — non-negotiable

- Responsive to 360px, no horizontal page scroll (`#root` clips overflowing
  ornament with `overflow-x: clip`).
- Visible keyboard focus everywhere: 3px ink outline plus a 7px marigold ring,
  visible on paper, ochre, scarlet and rani.
- Every input has a real `<label>` (visually hidden where the design has none).
- Touch targets ≥ 44px; primary actions 48–72px.
- Colour never carries meaning alone — a glyph and a word ride with it.
- No blurred shadows, no glows.
