# unigate — frontend design spec

Binding for every screen in `apps/web`. If a screen contradicts this file, the file wins.

## Subject and job

A single university collaboration event with a wedding-celebration character.
~150 seats, sold as SINGLE (1 seat) or COUPLE (2 seats). Students buy on a phone,
pay over UPI outside the platform, upload a screenshot, get approved by a human,
verify a student ID, and arrive at a door where a volunteer scans a QR.

Three audiences, in order of traffic:
1. **A student on a phone**, mid-WhatsApp-scroll, deciding in under a minute.
2. **Two or three admins** grinding a review queue for an evening.
3. **Gate volunteers** at 9pm, tired, one-handed, possibly offline (M6).

The design's job: get a student from "I want to come" to "QR in hand" with no
support message, and get an admin through hundreds of screenshots fast.

## The idea

**The interface is an invitation, and the pass is a card with a tear-off stub.**

Indian wedding invitations are defined by their *borders and rules* — zari thread,
foil edges, generous margins, centered classical setting — not by gradients or
ornament washes. We take the structural vernacular, not the clip art.

Spend all boldness on the **pass card**: paper-coloured, zari-ruled, with a real
perforation and the QR on the stub. Everything around it stays disciplined.

## Explicitly rejected

These are the clichés and the generated-design tells. Do not ship them.

- Marigold/saffron gradient washes, mandala or paisley watermark SVGs, script
  "wedding" typefaces, elephant or diya iconography.
- `#F4F1EA`-family cream with a terracotta accent (`#D97757` and neighbours).
- Identical rounded cards with the same soft grey shadow under each.
- ALL-CAPS tracked eyebrow labels above headings.
- Meta strings joined with middle dots (`A · B · C`).
- `→` appended to button or link text.
- Accenting one word of a headline in a different colour or italic.
- `01 / 02 / 03` numbered chips — except in the purchase flow, which genuinely
  *is* a sequence, and even there they are set as invitation rules, not chips.
- **Any gradient, anywhere.** Real zari is thread. Gold is a flat line colour.
- **Any box-shadow.** Paper sits, it does not float. The zari hairline is the
  only elevation device in the system.

## Colour

Six tokens. **Light-only** (amended — see below): the app commits to one
bright identity regardless of OS colour scheme. A festival ticket page looks
the same for everyone, including a buyer whose phone is in dark mode.

```css
:root {
  --paper:   #FFF8ED;  /* near-white warm ivory ground */
  --ink:     #241009;  /* deep brown-black, reads as letterpress, not tinted black */
  --kumkum:  #D6002A;  /* vivid celebratory red — the primary. NOT orange, NOT terracotta */
  --zari:    #B8862A;  /* foil gold — hairlines, rules and borders ONLY, never a fill */
  --haldi:   #946600;  /* turmeric — the single urgency accent (countdown, low stock) */
  --mehendi: #4C6B1F;  /* olive — verified, approved, checked-in */
}
```

> **Amendment (bright re-skin):** the original spec defined a muted light
> palette plus a dark-mode override (`@media (prefers-color-scheme: dark)`
> and `:root[data-theme="dark"]`). Both are deleted. There is no dark mode
> and no theme toggle — `:root` is the only palette definition, ever.
> `body` still gets an explicit `background: var(--paper)`.

Contrast floor: body text ≥ 4.5:1, large display ≥ 3:1, verified by
computing WCAG 2.1 ratios (not eyeballed):

| Token      | Hex       | On `--paper` | Use                                   |
|------------|-----------|--------------|----------------------------------------|
| `--ink`    | `#241009` | 17.26:1      | body text                              |
| `--kumkum` | `#D6002A` | 5.11:1       | links, errors, primary button (as bg, with `--paper` text also 5.11:1) |
| `--haldi`  | `#946600` | 4.78:1       | urgency text (countdown, state labels) |
| `--mehendi`| `#4C6B1F` | 5.80:1       | approved/checked-in text               |
| `--zari`   | `#B8862A` | 3.07:1       | borders/hairlines only — never text    |

`--zari` stays a *line* colour and must never carry body text — its ratio is
reported for border legibility only, not as a text-contrast pass.

## Type

Two families, clearly distinct, both with real fallbacks.

- **Display — Marcellus.** Roman inscriptional capitals-and-lowercase; it is what
  a good bilingual invitation is actually set in, and it sits beside Devanagari
  without fighting it. Used for the event name, screen titles, and the pass.
  Fallback: `"Marcellus", "Cormorant Garamond", Georgia, serif`.
- **Body and UI — Mukta.** By Indian Type Foundry, drawn for Devanagari/Latin
  harmony. Highly legible at form sizes on a phone. This is a choice grounded in
  the subject, not a default reach for Inter.
  Fallback: `"Mukta", ui-sans-serif, system-ui, sans-serif`.

Scale (1.25 minor third, 16px base):
`0.8 / 1 / 1.25 / 1.563 / 1.953 / 2.441 / 3.052 rem`

Rules:
- Body line length under 66 characters. Body line-height 1.6.
- Display sets tight: line-height 1.1, letter-spacing `0.01em` (Marcellus is
  already generous).
- Sentence case everywhere, including buttons. No all-caps.
- Numbers that matter — countdown, amount, seats left, short code — set in
  `font-variant-numeric: tabular-nums` so they do not jitter as they change.

## Layout

Mobile-first, single centred column, `max-width: 34rem` for participant screens.
The admin queue is the one wide surface (`min-width` two-pane at ≥ 60rem).

Content is **centred** on ceremonial surfaces (the invitation hero, the pass) and
**left-aligned** on working surfaces (forms, the admin queue). That contrast is
deliberate: ceremony is symmetric, work is scannable.

```
 INVITATION (landing)            PASS
┌───────────────────────┐   ┌───────────────────────┐
│ ══════ zari rule ═════ │   │ ═══════════════════  │
│                       │   │   Aarav Sharma        │
│     You're invited    │   │   Couple · seat 1 of 2│
│                       │   │                       │
│   SANGAM NIGHT        │   │ ╌╌╌ perforation ╌╌╌╌ │
│   Marcellus, large    │   │                       │
│                       │   │      ███ QR ███       │
│   Friday 14 November  │   │       7K3-QD9         │
│   Main Auditorium     │   │ ═══════════════════  │
│                       │   └───────────────────────┘
│ ══════ zari rule ═════ │    ↑ the one bold element
│                       │
│  [ Reserve your seat ]│
│   42 of 150 seats left│
└───────────────────────┘
```

The **zari band** is the section divider — a 2px rule in `--zari` with a 1px
`--paper` gap inside it, giving a double-rule that reads as foil edging. It
replaces every `<hr>`, every card shadow, and every "section" wrapper.

Card edges: `border: 1px solid var(--zari)`, `border-radius: 2px`. Not 12px, not
16px. Invitation stock has a crisp edge.

## Motion

**One orchestrated moment: the pass reveal.** When a pass is first issued, the
card draws its zari border, then the QR resolves in. Once, on first view.

Nothing else animates on scroll or hover-fades. Motion that answers an action —
a countdown ticking, a hold expiring, an approval landing, a drawer opening — is
welcome because it shows what changed. Everything respects
`prefers-reduced-motion: reduce`.

## Voice

Plain, warm, specific. Sentence case. Active voice. The button says what happens
and keeps its name through the flow.

- "Reserve your seat" → "Seat reserved", not "Submit" → "Success".
- Never "verification failed". Say what to do: "There's glare on the card. Move
  away from direct light and retry." (LLD-v2 §1 has the full table — use those
  strings verbatim when the ID flow lands.)
- Urgency is factual, never manufactured: "Only 1 seat left — singles only",
  not "Hurry! Almost gone!"
- Empty and error states give direction: an expired hold says what happened and
  exactly what to do next, and self-expires client-side rather than sitting there
  looking valid.
- Money is unambiguous: the exact amount appears as copyable text *and* inside
  the UPI deep link, so nobody pays ₹499 for a couple ticket.

## Quality floor — non-negotiable

- Responsive to 360px. Nothing scrolls horizontally except the admin table, which
  scrolls inside its own container.
- Visible keyboard focus everywhere: `outline: 2px solid var(--kumkum); outline-offset: 2px`.
  Never `outline: none` without an equally visible replacement.
- Every input has a real `<label>`. Placeholders are not labels.
- Touch targets ≥ 44px.
- The admin queue is keyboard-first: `a` approve, `r` reject, `j`/`k` navigate,
  auto-advance. A reviewer should never need the mouse.
- Colour never carries meaning alone — approved/denied states pair the colour
  with an icon and a word.
