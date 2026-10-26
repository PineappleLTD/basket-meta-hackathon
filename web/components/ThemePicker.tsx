import Form from "next/form";
import Link from "next/link";
import { EXAMPLE_THEMES } from "@/lib/data";
import { SectionHeader } from "./SectionHeader";

export function ThemePicker({ theme }: { theme: string }) {
  return (
    <section>
      <SectionHeader num="01" label="Your theme" tight />
      {/* GET form: submitting navigates to /?theme=..., no client state needed. */}
      <Form action="/basket" scroll={false} className="theme-form">
        <input
          key={theme}
          name="theme"
          defaultValue={theme}
          placeholder="AI regulation, US recession 2027…"
          aria-label="Theme"
          autoComplete="off"
        />
        <button type="submit">Build basket</button>
      </Form>
      <div className="chips">
        <span className="eyebrow">Or try</span>
        {EXAMPLE_THEMES.map((t) => (
          <Link key={t} href={{ query: { theme: t } }} scroll={false} className="chip" aria-current={t === theme || undefined}>
            {t}
          </Link>
        ))}
      </div>
    </section>
  );
}
