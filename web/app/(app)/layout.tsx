import { AppHeader } from "@/components/AppHeader";
import { BasketSidebar } from "@/components/BasketSidebar";
import { BasketsProvider } from "@/components/BasketsContext";

// Shell for the signed-in app: top bar, baskets sidebar, and the current page beside it.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <BasketsProvider>
      <div className="shell">
        <AppHeader />
        <div className="shell-body">
          <BasketSidebar />
          <main className="shell-main">{children}</main>
        </div>
      </div>
    </BasketsProvider>
  );
}
