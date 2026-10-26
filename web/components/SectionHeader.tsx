type Props = { num: string; label: string; title?: string; lede?: string; tight?: boolean };

/** Hairline rule with an orange tick, a numbered eyebrow, and an optional headline. */
export function SectionHeader({ num, label, title, lede, tight }: Props) {
  return (
    <header className={tight ? "sec tight" : "sec"}>
      <div className="rule" />
      <div className="eyebrow">
        <b>{num}</b>
        {label}
      </div>
      {title && (
        <h2>
          {title}
          <span className="dot">.</span>
        </h2>
      )}
      {lede && <p className="lede">{lede}</p>}
    </header>
  );
}
