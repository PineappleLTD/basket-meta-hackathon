import { BasketView } from "@/components/BasketView";
import { Hero } from "@/components/Hero";
import { NewsFeed } from "@/components/NewsFeed";
import { ThemePicker } from "@/components/ThemePicker";
import { buildBasket, DEFAULT_THEME, getNewsSignals } from "@/lib/data";

type Props = { searchParams: Promise<{ theme?: string | string[] }> };

// The theme lives in the URL (?theme=...), so a basket survives refreshes and can be shared.
export default async function Page({ searchParams }: Props) {
  const { theme: param } = await searchParams;
  const theme = (Array.isArray(param) ? param[0] : param)?.trim() || DEFAULT_THEME;
  const basket = await buildBasket(theme);
  const news = await getNewsSignals(basket);

  return (
    <main className="page">
      <Hero />
      <ThemePicker theme={theme} />
      <BasketView theme={theme} basket={basket} />
      <NewsFeed news={news} />
      <footer className="foot">Basket is a prototype. Mock data, illustrative prices, not investment advice.</footer>
    </main>
  );
}
