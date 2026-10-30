"use client";

import { useEffect, useState } from "react";
import type { TitleState } from "@/components/BasketsContext";

const TYPE_MS = 28;

// A basket's name. While its title is generated it shimmers; when the title arrives it's typed out.
export function BasketName({ name, state }: { name: string; state?: TitleState }) {
  const typing = state === "typing";
  const [shown, setShown] = useState(typing ? 0 : name.length);

  useEffect(() => {
    if (!typing || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(name.length);
      return;
    }
    setShown(0);
    const timer = setInterval(() => {
      setShown((n) => {
        if (n + 1 >= name.length) clearInterval(timer);
        return n + 1;
      });
    }, TYPE_MS);
    return () => clearInterval(timer);
  }, [name, typing]);

  if (state === "pending") return <span className="name-pending">{name}</span>;
  return (
    <span aria-label={name}>
      <span aria-hidden="true">{name.slice(0, shown)}</span>
      {shown < name.length && <span className="name-caret" aria-hidden="true" />}
    </span>
  );
}
