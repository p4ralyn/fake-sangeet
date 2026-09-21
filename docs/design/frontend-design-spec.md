# Fake Sangeet — frontend design spec

Binding for every screen in `apps/web`. If a screen contradicts this file, the file wins.

**Reference material** (render of the club's own creatives) lives in
`docs/design/reference/`. Look at `poster-1.jpg` before writing any CSS. That
poster is what students will already have seen on Instagram; the site has to
feel like the same event.

## The event

**Fake Sangeet — "Band Baaja No Baarat."**
A mock Indian wedding thrown by **Inferno** (SNU's dance society) and
**Snuphoria** (SNU's music society), at **UAC Terrace, 15 October 2026**.

Baarat procession with dhol, live performances, DJ night, mehendi and diya
painting stalls, tambola and card games, a Sufi set to close, and a gorilla
mascot. Bride side and groom side. Diwali is the core of the theme.

This is not a wedding invitation. It is a **college festival poster**.

## The idea

**A retro Bollywood film poster you can buy a ticket from.**

Their creative is saffron ground, marigold extruded lettering, hot magenta
mandala borders, a scarlet film strip, and collage cutouts of hands, lotuses,
diyas and jewellery. Loud, maximalist, hand-made. We build the same thing as an
interface.

The one bold element is still the **pass** — but it is now a **film-strip
ticket**. The perforation becomes sprocket holes, tying it to the reel that
runs through the poster. That is the screen everything else stays quieter than.

## Correction to the previous spec

The earlier version of this file banned "marigold gradient washes" and "mandala
watermarks" as generic-festival clichés, and built a restrained letterpress
invitation in ivory and deep maroon.

That was wrong for this client. Those motifs are not a cliché here — they are
literally the society's own brand language, drawn by hand for this event. The
restrained treatment read as a wedding stationer, not a student festival.

What survives from that spec: **no drop shadows for depth**, crisp edges,
sentence case, honest copy, tabular numerals, the accessibility floor. What is
deliberately reversed: the palette, the display typography, and the ban on
ornament.

## Colour

Sampled from `reference/poster-1.jpg`. Light-only — there is no dark mode and no
theme toggle. `:root` is the only palette definition.

```css
:root {
  --saffron:  #E9A93C;  /* page ground — golden ochre, the poster's field */
  --marigold: #FFC61A;  /* display lettering and highlights */
  --scarlet:  #C8102E;  /* primary actions, the film strip */
  --magenta:  #D6006E;  /* mandala borders, secondary accent */
  --maroon:   #5E0C18;  /* all body text, and the extrusion shadow */
  --cream:    #FFF3DC;  /* card surfaces — text sits here, never on saffron */
}
```

**The contrast rule that makes this work:** saffron is a *ground*, not a text
background. Body text never sits directly on it. Every block of reading text
lives on a `--cream` card, where `--maroon` gives 11:1. Marigold is for display
type at 2rem and above, always carrying a hard `--maroon` offset shadow, which
is what makes it legible and what makes it look like the poster.

Verify every pairing you ship and report the ratios. Minimums: body 4.5:1,
display 3:1. If a pairing fails, fix the pairing — do not lower the floor.

## Type

- **Display — Baloo Bhai 2** (Indian Type Foundry). Heavy, even-weight, drawn
  alongside Devanagari. With a hard offset shadow it reads as the poster's
  chunky marigold lettering. Event name, screen titles, the pass.
  Fallback: `"Baloo Bhai 2", "Baloo 2", Impact, sans-serif`.
- **Body and UI — Mukta** (also ITF). Already in use, highly legible on a phone.
  Keep it.

The extrusion is the signature. Display type gets a hard, un-blurred offset:

```css
text-shadow: 3px 3px 0 var(--maroon);   /* scale with font size; never blur */
```

That is not a drop shadow for depth — it is poster lettering. Blurred shadows
remain banned.

Scale (1.25 minor third, 16px base):
`0.8 / 1 / 1.25 / 1.563 / 1.953 / 2.441 / 3.052 rem`

Sentence case for UI and copy. The event wordmark may set in caps, because that
is how the poster sets it — that is a wordmark, not a label.

## Ornament — now required, not banned

Take these from the reference, drawn as inline SVG so they scale and take
`currentColor`. No raster watermarks, no external icon library.

- **Mandala quarter-rosettes** anchored in page corners, as in `deck-1.jpg`.
- **A film-strip band** — a rectangle with evenly spaced sprocket holes —
  replacing the old rule as the section divider, and forming the pass edge.
- **Sparkle / firework line bursts**, small, used sparingly near headings.

Ornament sits *behind* content and never reduces text contrast. It must not
intercept pointer events, and it is hidden from assistive tech
(`aria-hidden="true"`).

## Header

```
┌──────────────────────────────────────────────────────────┐
│  [S]  ×  [inferno]        FAKE SANGEET          identity │
│                        band baaja no baarat              │
└──────────────────────────────────────────────────────────┘
```

- **Top left:** the Snuphoria mark, a large `×` between, then the Inferno mark.
  The `×` is a real display glyph at collab scale — the societies' own decks use
  it that way. Not a small times-sign, not the letter x.
- **Centre:** the event wordmark, `FAKE SANGEET`, in display type with the
  extrusion, and the tagline **band baaja no baarat** beneath it in Mukta.
- **Right:** the signed-in identity and sign out.

At 360px the lockup stacks rather than shrinking into illegibility: logos and
`×` on one row, wordmark and tagline beneath, identity control still reachable.
Both logos need real `alt` text naming the society.

## Layout

Mobile-first, single centred column, `max-width: 34rem` for participant screens.
The admin queue is the one wide surface.

Ceremonial surfaces (landing, pass) centre their content. Working surfaces
(forms, admin queue) are left-aligned and scannable.

Card edges stay crisp: `2px solid var(--maroon)`, `border-radius: 2px`. The
heavier border replaces the old hairline — it matches the poster's inked
outlines and it holds up against a saturated ground.

## Motion

One orchestrated moment: the pass reveal — the film-strip border draws, then the
QR resolves. Once, on first view, never under `prefers-reduced-motion: reduce`.

Nothing else animates on scroll. Motion that answers an action — a countdown
ticking, a hold expiring — is welcome because it shows what changed.

## Voice

Unchanged, and still binding. Plain, warm, specific. Sentence case. Active
voice. The button says what happens and keeps its name through the flow. Copy is
specified in `participant-screens.md` and is final — this re-skin changes how
screens look, not what they say.

Urgency stays factual: "Only 1 seat left — singles only", never "Hurry!".

## Quality floor — non-negotiable

- Responsive to 360px, no horizontal page scroll.
- Visible keyboard focus everywhere: a 2px `--maroon` outline with 2px offset,
  which must stay visible against both cream and saffron.
- Every input has a real `<label>`. Placeholders are not labels.
- Touch targets ≥ 44px.
- Colour never carries meaning alone — pair it with an icon and a word.
- **No blurred shadows, no gradients.** Flat colour, hard offsets, inked edges.
  Brightness comes from the palette, not from glows.
