import type { Metadata } from "next";
import { AuthForm } from "@/components/AuthForm";

export const metadata: Metadata = { title: "Register · basket" };

export default function Page() {
  return <AuthForm mode="register" />;
}
