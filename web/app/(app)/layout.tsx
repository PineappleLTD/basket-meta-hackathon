import { AccountProvider } from "@/components/AccountContext";
import { AppHeader } from "@/components/AppHeader";
import { BasketSidebar } from "@/components/BasketSidebar";
import { BasketsProvider } from "@/components/BasketsContext";
import { ChatProvider } from "@/components/ChatContext";

// Shell for the signed-in app: top bar, baskets sidebar, and the current page beside it.
// AccountProvider sits outside the header so it can show the account at a glance.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <BasketsProvider>
      <AccountProvider>
        <ChatProvider>
          <div className="shell">
            <AppHeader />
            <div className="shell-body">
              <BasketSidebar />
              <main className="shell-main">{children}</main>
            </div>
          </div>
        </ChatProvider>
      </AccountProvider>
    </BasketsProvider>
  );
}
