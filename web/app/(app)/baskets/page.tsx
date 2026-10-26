import { redirect } from "next/navigation";

// The basket list lives in the app sidebar now.
export default function Page() {
  redirect("/basket");
}
