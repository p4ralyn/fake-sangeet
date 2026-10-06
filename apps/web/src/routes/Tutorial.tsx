import { useEffect, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ButtonLink, DisplayTitle, Notice, Paper, Sparkle, Stamp } from "../components";
import styles from "./Tutorial.module.css";

/**
 * How it works (`/tutorial`) — open to everyone, no sign-in.
 *
 * Linked from the "ticket ready" email, which is the first thing every
 * attendee receives, and from the landing page. It covers four things a
 * person will otherwise ask the desk about: getting the pass, the door (and
 * the one break), giving a pass away, and keeping it on the home screen.
 *
 * Every rule written here is one the server enforces, stated as it enforces
 * it: one transfer per ticket ever, transfers close at 15:00 on the day
 * (`events.starts_at` minus `closes_before_minutes`), one break per pass
 * (`access.max_breaks`), check-out is final. If one of those changes, change
 * this page with it.
 */

const SECTIONS = [
  { id: "pass", label: "Your pass" },
  { id: "door", label: "At the door" },
  { id: "transfer", label: "Giving your pass away" },
  { id: "home", label: "Add to home screen" },
] as const;

function Steps({ children }: { children: ReactNode }) {
  return <ol className={styles.steps}>{children}</ol>;
}

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className={styles.step}>
      <p className={styles.stepTitle}>{title}</p>
      <div className={styles.stepBody}>{children}</div>
    </li>
  );
}

function Section({
  id,
  title,
  intro,
  children,
}: {
  id: string;
  title: string;
  intro: string;
  children: ReactNode;
}) {
  return (
    <Paper as="section" pad="lg" className={styles.section} aria-labelledby={`${id}-title`}>
      <span id={id} className={styles.anchor} aria-hidden="true" />
      <h2 id={`${id}-title`} className={styles.sectionTitle}>
        {title}
      </h2>
      <p className={styles.intro}>{intro}</p>
      {children}
    </Paper>
  );
}

export function Tutorial() {
  // A link like /tutorial#home arrives through the router, which does not
  // scroll to the fragment the way a full page load would.
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <DisplayTitle tone="ink" className={styles.title}>
          <Sparkle className={styles.titleSparkle} />
          How it works
        </DisplayTitle>
        <p className={styles.lede}>
          Everything you need for Fake Sangeet — 15 October, UAC Terrace, 6pm — from your
          first sign-in to walking through the door.
        </p>
        <nav className={styles.toc} aria-label="On this page">
          {SECTIONS.map((section) => (
            <a key={section.id} className={styles.tocLink} href={`#${section.id}`}>
              {section.label}
            </a>
          ))}
        </nav>
      </header>

      <Section
        id="pass"
        title="Getting your pass"
        intro="Your ticket is already on the list. Two steps turn it into a pass you can scan."
      >
        <Steps>
          <Step title="Sign in with your SNU account">
            <p>
              Open <strong>fakesangeet.live</strong> (or the link in your email) and sign in with
              Google using the SNU account your ticket was bought with —{" "}
              <strong>yourNetID@snu.edu.in</strong>. Personal Gmail accounts won&rsquo;t work.
            </p>
          </Step>
          <Step title="Upload a photo of your student ID">
            <p>
              Take a photo of the whole card, flat and in good light. A person checks it, so it
              only has to be readable. Tick the consent box and press{" "}
              <strong>Send for checking</strong>. You get three tries.
            </p>
            <p>
              The photo is shown to the volunteer at the door so they can match it to you, and is
              deleted 45 days after the event.
            </p>
          </Step>
          <Step title="Wait for the check">
            <p>
              The team looks at every ID by hand. You&rsquo;ll get an email when it&rsquo;s
              approved — or, if they need a clearer photo, an email saying why, so you can send
              another.
            </p>
          </Step>
          <Step title="Your pass appears">
            <p>
              Sign in again and your pass is there: your name, a QR code, and a short code like{" "}
              <strong className={styles.code}>MM2-F4K</strong> the door can type in if a scan
              fails. A stamp shows where you are:
            </p>
            <p className={styles.stamps}>
              <Stamp glyph="○" size="sm">Not arrived</Stamp>
              <Stamp tone="good" glyph="●" size="sm">Inside</Stamp>
              <Stamp tone="warn" glyph="◑" size="sm">On a break</Stamp>
              <Stamp glyph="✓" size="sm">Departed</Stamp>
            </p>
          </Step>
        </Steps>

        <Notice tone="warn">
          <p>
            <strong>Please finish setting up your pass before 13 October.</strong>
          </p>
        </Notice>

        <h3 className={styles.subTitle}>Couple tickets</h3>
        <ul className={styles.bullets}>
          <li>
            <strong>If your partner was named when you bought it,</strong> they have their own
            ticket on their own account. They sign in as themselves and upload their own ID —
            there is nothing for you to forward.
          </li>
          <li>
            <strong>If they weren&rsquo;t,</strong> your account shows a second pass marked{" "}
            <strong>&ldquo;Your partner&rsquo;s pass&rdquo;</strong> with a padlock instead of a
            QR. Below it, under{" "}
            <strong>&ldquo;Bought an extra ticket for your partner? Transfer it to them
            now&rdquo;</strong>, type their NetID and confirm. They then sign in and upload their
            own ID to get their QR. A partner pass that hasn&rsquo;t been sent by{" "}
            <strong>3pm on 15 October</strong> stays locked.
          </li>
        </ul>
      </Section>

      <Section
        id="door"
        title="At the door, and your one break"
        intro="Nothing happens automatically at the door — a volunteer scans your QR, sees your ID photo, and decides."
      >
        <Steps>
          <Step title="Arriving">
            <p>
              Open your pass and hold up the QR. The volunteer checks your face against your ID
              photo and checks you in. Your stamp turns to <strong>Inside</strong>.
            </p>
          </Step>
          <Step title="Stepping out — your one break">
            <p>
              Need to step out? Show your QR on the way out and the volunteer marks you{" "}
              <strong>On a break</strong>. <strong>Each pass gets one break</strong> for the
              night.
            </p>
          </Step>
          <Step title="Coming back in">
            <p>
              Show your QR again. Your ID photo is checked again on the way back, then you&rsquo;re
              marked Inside.
            </p>
          </Step>
          <Step title="Leaving for the night">
            <p>
              Once your break is used, scanning out is a <strong>check-out</strong>, and{" "}
              <strong>check-out is final</strong> — the pass can&rsquo;t be used to come back in.
            </p>
          </Step>
        </Steps>
        <Notice tone="info">
          <p>
            Keep your student ID with you. If anything doesn&rsquo;t scan, the short code on your
            pass and your ID card are enough for the desk to find you.
          </p>
        </Notice>
      </Section>

      <Section
        id="transfer"
        title="Giving your pass to someone else"
        intro="Can't make it? You can hand your pass to another SNU student — once."
      >
        <Steps>
          <Step title="Open your pass">
            <p>
              Sign in, open <strong>Your pass</strong>, and find{" "}
              <strong>Give this pass to someone else</strong> below it.
            </p>
          </Step>
          <Step title="Type their NetID">
            <p>
              Enter their NetID — two letters and three digits, like <strong>ab123</strong> —
              and press <strong>Continue</strong>. Check it carefully on the next screen, then
              press <strong>Yes, send it</strong>.
            </p>
          </Step>
          <Step title="They finish it">
            <p>
              They sign in with their own SNU account and upload their own student ID, exactly as
              in &ldquo;Getting your pass&rdquo;. Their QR appears once their ID is checked.
            </p>
          </Step>
        </Steps>

        <h3 className={styles.subTitle}>The rules</h3>
        <ul className={styles.bullets}>
          <li>
            <strong>A pass can be transferred once, ever.</strong> The person you send it to
            can&rsquo;t pass it on again.
          </li>
          <li>
            <strong>Your QR stops working the moment you send it.</strong> It can&rsquo;t be
            undone from your side.
          </li>
          <li>
            <strong>They can&rsquo;t already have a ticket</strong> — one person, one pass.
          </li>
          <li>
            <strong>Transfers close at 3pm on 15 October</strong>, three hours before doors, so
            there&rsquo;s time to check the new holder&rsquo;s ID.
          </li>
          <li>
            <strong>On a couple ticket</strong> where your partner&rsquo;s pass hasn&rsquo;t been
            sent yet, transferring your own pass moves <strong>both</strong> passes to them — and
            they can still send the second one on to their partner.
          </li>
        </ul>
      </Section>

      <Section
        id="home"
        title="Add your pass to your home screen"
        intro="So it's one tap away at the door — no searching through tabs in a queue."
      >
        <div className={styles.platforms}>
          <div className={styles.platform}>
            <h3 className={styles.subTitle}>iPhone (Safari)</h3>
            <Steps>
              <Step title="Open your pass in Safari">
                <p>Sign in at fakesangeet.live and open Your pass.</p>
              </Step>
              <Step title="Tap Share">
                <p>The square with an arrow pointing up, at the bottom of the screen.</p>
              </Step>
              <Step title="Add to Home Screen">
                <p>Scroll down, tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.</p>
              </Step>
            </Steps>
          </div>
          <div className={styles.platform}>
            <h3 className={styles.subTitle}>Android (Chrome)</h3>
            <Steps>
              <Step title="Open your pass in Chrome">
                <p>Sign in at fakesangeet.live and open Your pass.</p>
              </Step>
              <Step title="Tap the ⋮ menu">
                <p>The three dots at the top right.</p>
              </Step>
              <Step title="Add to Home screen">
                <p>
                  Tap <strong>Add to Home screen</strong> (or <strong>Install app</strong>), then{" "}
                  <strong>Add</strong>.
                </p>
              </Step>
            </Steps>
          </div>
        </div>
        <p className={styles.note}>
          The icon is the Fake Sangeet फ. Your QR only shows while you&rsquo;re signed in, so if
          the shortcut opens to the sign-in page, sign in once and it remembers you.
        </p>
      </Section>

      <div className={styles.cta}>
        <ButtonLink to="/pass" variant="primary" size="lg">
          Go to your pass
        </ButtonLink>
        <p className={styles.help}>
          Stuck? Reach out to Pragun Kathuria (Associate Secretary 1, Snuphoria) on{" "}
          <a href="tel:+919000000000">9000000000</a>, or at{" "}
          <a href="mailto:pk944@snu.edu.in">pk944@snu.edu.in</a> or{" "}
          <a href="mailto:snuphoria@snu.edu.in">snuphoria@snu.edu.in</a>.
        </p>
      </div>
    </div>
  );
}
