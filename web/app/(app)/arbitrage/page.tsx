import { notFound } from "next/navigation";
import { ARBITRAGE_ENABLED } from "@/lib/arbitrage";
import { Scanner } from "./Scanner";

export default function Page() {
  if (!ARBITRAGE_ENABLED) notFound();
  return <Scanner />;
}
